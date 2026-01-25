import os
import glob
import json
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import deque
from config import repomap
from cache_builder import get_valid_forecast_hours, get_run_forecast_hours

STATUS_FILE = os.path.join(repomap["CACHE_DIR"], "scheduler_status.json")

def scan_cache_status(region="ne"):
    """
    Scans the tile cache for the given region and returns the status of model runs.
    
    Returns:
        dict: {
            "model_id": {
                "name": "Model Name",
                "runs": {
                    "run_id": {
                        "status": "complete" | "partial",
                        "hours_present": int,
                        "expected_hours": int,
                        "last_modified": float (timestamp)
                    }
                }
            }
        }
    """
    tiles_dir = repomap["TILES_DIR"]
    region_config = repomap["TILING_REGIONS"].get(region)
    if not region_config:
        return {}
        
    res = region_config.get("default_resolution_deg", 0.1)
    # Directory structure: cache/tiles/{region}/{res}deg/{model}/{run}
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    base_dir = os.path.join(tiles_dir, region, res_dir)
    
    status = {}
    
    if not os.path.exists(base_dir):
        return status
        
    models = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    
    for model_id in models:
        model_config = repomap["MODELS"].get(model_id, {})
        status[model_id] = {
            "name": model_config.get("name", model_id),
            "runs": {}
        }
        
        model_path = os.path.join(base_dir, model_id)
        runs = [d for d in os.listdir(model_path) if d.startswith("run_")]
        
        for run_id in runs:
            run_path = os.path.join(model_path, run_id)
            
            # Check for a proxy variable (t2m) to determine completeness
            # This logic mirrors tiles_exist in build_tiles_scheduled.py
            npz_path = os.path.join(run_path, "t2m.npz")
            
            hours_present = 0
            expected_hours = model_config.get("max_forecast_hours", 24)
            run_status = "partial"
            last_modified = 0
            
            if os.path.exists(npz_path):
                last_modified = os.path.getmtime(npz_path)
                try:
                    with np.load(npz_path) as data:
                        if 'hours' in data:
                            hours_present = len(data['hours'])
                except Exception:
                    pass
            
            # Status determination
            # Allow some tolerance or specific logic? 
            # For now: >= 90% is complete
            if hours_present >= expected_hours * 0.9:
                run_status = "complete"
            elif hours_present == 0:
                run_status = "empty" # Or just don't list it? Better to list.
            
            status[model_id]["runs"][run_id] = {
                "status": run_status,
                "hours_present": hours_present,
                "expected_hours": expected_hours,
                "last_modified": last_modified
            }
            
    return status

# Scheduler model configuration - mirrors build_tiles_scheduled.py
SCHEDULED_MODELS = [
    {"id": "hrrr", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "48"))},
    {"id": "nam_nest", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))},
    {"id": "gfs", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))},
    {"id": "nbm", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_NBM", "168"))},
    {"id": "ecmwf_hres", "max_hours": 240},
]


def _get_max_hours_for_run(model_id: str, run_id: str, default_max: int) -> int:
    """Get max forecast hours for a specific run, accounting for init-hour variations."""
    model_config = repomap["MODELS"].get(model_id, {})
    max_hours_by_init = model_config.get("max_hours_by_init")

    if not max_hours_by_init:
        return default_max

    try:
        init_hour = run_id.split("_")[2]
        return max_hours_by_init.get(init_hour, max_hours_by_init.get("default", default_max))
    except (IndexError, KeyError):
        return default_max


def _get_expected_runs(model_id: str, lookback_hours: int = 72) -> list[str]:
    """Get list of runs we expect to have based on scheduler policy.

    Policy:
    - Tier 1: All runs in last 12 hours
    - Tier 2: Synoptic runs (00, 06, 12, 18z) in last 72 hours
    """
    model_config = repomap["MODELS"].get(model_id, {})
    if not model_config:
        return []

    now = datetime.now(timezone.utc)
    update_freq = model_config.get("update_frequency_hours", 1)
    expected_runs = []

    for hours_ago in range(lookback_hours):
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")

        # Skip non-synoptic hours for models with 6-hourly updates
        if update_freq >= 6 and int(init_hour) % 6 != 0:
            continue

        # Policy: keep recent (12h) + synoptic runs
        is_recent = hours_ago <= 12
        is_synoptic = int(init_hour) % 6 == 0

        if is_recent or is_synoptic:
            expected_runs.append(f"run_{date_str}_{init_hour}")

    return expected_runs


def _target_expected_hours(model_id: str, run_id: str, default_max: int) -> list[int]:
    """Compute the target expected hours for display purposes.

    Always apply hourly_override_first_hours if configured in the model, regardless of
    per-run detection, so the dashboard communicates the intended policy clearly.
    """
    max_hours = _get_max_hours_for_run(model_id, run_id, default_max)
    base = get_valid_forecast_hours(model_id, max_hours)
    model_cfg = repomap["MODELS"].get(model_id, {})
    hourly_first = int(model_cfg.get("hourly_override_first_hours", 0) or 0)
    if hourly_first <= 0:
        return base
    n = min(hourly_first, max_hours)
    hourly = list(range(1, n + 1))
    rest = [h for h in base if h > n]
    return hourly + rest


def get_scheduled_runs_status(region="ne"):
    """
    Get status of scheduled runs vs what's in cache.

    Returns:
        list: [
            {
                "model_id": str,
                "model_name": str,
                "run_id": str,
                "init_time": str (ISO format),
                "expected_hours": list[int],
                "expected_valid_start": str (ISO format),
                "expected_valid_end": str (ISO format),
                "cached_hours": list[int],
                "cached_valid_start": str or None,
                "cached_valid_end": str or None,
                "status": "complete" | "partial" | "missing"
            }
        ]
    """
    tiles_dir = repomap["TILES_DIR"]
    region_config = repomap["TILING_REGIONS"].get(region)
    if not region_config:
        return []

    res = region_config.get("default_resolution_deg", 0.1)
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    base_dir = os.path.join(tiles_dir, region, res_dir)

    results = []

    for model_cfg in SCHEDULED_MODELS:
        model_id = model_cfg["id"]
        default_max_hours = model_cfg["max_hours"]
        model_config = repomap["MODELS"].get(model_id, {})
        model_name = model_config.get("name", model_id)

        expected_runs = _get_expected_runs(model_id)

        for run_id in expected_runs:
            # Parse init time from run_id
            parts = run_id.split("_")
            try:
                init_time = datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H")
                init_time = init_time.replace(tzinfo=timezone.utc)
            except (IndexError, ValueError):
                continue

            # Get expected forecast hours for this run (policy target; ignores per-run detection)
            expected_hours = _target_expected_hours(model_id, run_id, default_max_hours)

            if not expected_hours:
                continue

            # Calculate expected valid time range
            expected_valid_start = init_time + timedelta(hours=expected_hours[0])
            expected_valid_end = init_time + timedelta(hours=expected_hours[-1])

            # Check what's actually in cache
            npz_path = os.path.join(base_dir, model_id, run_id, "t2m.npz")
            cached_hours = []
            if os.path.exists(npz_path):
                try:
                    with np.load(npz_path) as data:
                        if 'hours' in data:
                            cached_hours = data['hours'].tolist()
                except Exception:
                    pass

            # Calculate cached valid time range
            cached_valid_start = None
            cached_valid_end = None
            if cached_hours:
                cached_valid_start = (init_time + timedelta(hours=cached_hours[0])).isoformat()
                cached_valid_end = (init_time + timedelta(hours=cached_hours[-1])).isoformat()

            # Determine status
            if cached_hours == expected_hours:
                status = "complete"
            elif cached_hours:
                status = "partial"
            else:
                status = "missing"

            results.append({
                "model_id": model_id,
                "model_name": model_name,
                "run_id": run_id,
                "init_time": init_time.isoformat(),
                "expected_hours": expected_hours,
                "expected_valid_start": expected_valid_start.isoformat(),
                "expected_valid_end": expected_valid_end.isoformat(),
                "cached_hours": cached_hours,
                "cached_valid_start": cached_valid_start,
                "cached_valid_end": cached_valid_end,
                "status": status,
            })

    # Sort by init_time descending (newest first)
    results.sort(key=lambda x: x["init_time"], reverse=True)
    return results


def _get_dir_size(path):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += _get_dir_size(entry.path)
    return total

def get_disk_usage():
    """
    Calculates disk usage for cache directories.
    Returns:
        dict: {
            "total": int,
            "gribs": { "total": int, "model_id": int, ... },
            "tiles": { "total": int, "models": { "model_id": int } }
        }
    """
    grib_dir = repomap["GRIB_CACHE_DIR"]
    tiles_dir = repomap["TILES_DIR"]
    
    usage = {
        "total": 0,
        "gribs": {"total": 0},
        "tiles": {"total": 0, "models": {}}
    }
    
    # GRIBS
    if os.path.exists(grib_dir):
        usage["gribs"]["total"] = _get_dir_size(grib_dir)
        for model_id in os.listdir(grib_dir):
            model_path = os.path.join(grib_dir, model_id)
            if os.path.isdir(model_path):
                size = _get_dir_size(model_path)
                usage["gribs"][model_id] = size
    
    # TILES
    # Tiles structure is complex: tiles/{region}/{res}/{model}
    # We want to aggregate by model across all regions/resolutions
    if os.path.exists(tiles_dir):
        usage["tiles"]["total"] = _get_dir_size(tiles_dir)
        
        # Walk to find model directories
        # We assume model IDs are known from config to avoid scanning too deep blindly
        # Or we can iterate regions -> res -> models
        
        # Iterate known models and sum up their usage across all regions
        known_models = repomap["MODELS"].keys()
        
        for region in os.listdir(tiles_dir):
            region_path = os.path.join(tiles_dir, region)
            if not os.path.isdir(region_path): continue
            
            for res in os.listdir(region_path):
                res_path = os.path.join(region_path, res)
                if not os.path.isdir(res_path): continue
                
                for model_id in os.listdir(res_path):
                    if model_id in known_models:
                        model_path = os.path.join(res_path, model_id)
                        size = _get_dir_size(model_path)
                        usage["tiles"]["models"][model_id] = usage["tiles"]["models"].get(model_id, 0) + size

    usage["total"] = usage["gribs"]["total"] + usage["tiles"]["total"]
    return usage

def read_scheduler_logs(lines=100, log_path='logs/scheduler_detailed.log'):
    """Reads the last N lines from the scheduler log."""
    if not os.path.exists(log_path):
        return []
    
    try:
        with open(log_path, 'r') as f:
            return [line.rstrip('\n') for line in deque(f, lines)]
    except Exception:
        return []

def read_scheduler_status():
    """Read scheduler status from JSON."""
    if not os.path.exists(STATUS_FILE):
        return {}
    try:
        with open(STATUS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}
