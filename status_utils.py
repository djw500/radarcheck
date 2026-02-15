import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone

from cache_builder import get_valid_forecast_hours
from config import repomap
from jobs import init_db as init_jobs_db, count_by_status
from tile_db import init_db, list_tile_models_db, list_tile_variables_db

STATUS_FILE = os.path.join(repomap["CACHE_DIR"], "scheduler_status.json")

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
    region_config = repomap["TILING_REGIONS"].get(region)
    if not region_config:
        return []

    res = region_config.get("default_resolution_deg", 0.1)
    results = []

    conn = init_db(repomap.get("DB_PATH"))
    try:
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

                vars_info = list_tile_variables_db(conn, region, res, model_id, run_id)
                t2m_info = vars_info.get("t2m")
                cached_hours = sorted([int(h) for h in (t2m_info["hours"] if t2m_info else [])])

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
    finally:
        conn.close()

    # Sort by init_time descending (newest first)
    results.sort(key=lambda x: x["init_time"], reverse=True)
    return results


def get_run_grid():
    """Get per-model/run/hour job status grid from the jobs table.

    Returns a dict keyed by model_id, each containing runs with per-hour
    aggregate job status.  Also includes available (not-yet-enqueued) runs
    from the expected-runs list for the backfill UI.

    Structure:
    {
        "hrrr": {
            "name": "HRRR",
            "runs": {
                "run_20260215_12": {
                    "init_time": "2026-02-15T12:00:00+00:00",
                    "hours": {1: "completed", 2: "failed", 3: "pending", ...},
                    "counts": {"completed": 40, "failed": 2, ...},
                    "total_hours": 48
                }
            },
            "available_runs": ["run_20260215_06", ...]  # for backfill dropdown
        }
    }
    """
    conn = init_db(repomap.get("DB_PATH"))
    try:
        # Query all build_tile_hour jobs, aggregate by model/run/hour
        rows = conn.execute(
            """
            SELECT
                json_extract(args_json, '$.model_id') as model_id,
                json_extract(args_json, '$.run_id') as run_id,
                CAST(json_extract(args_json, '$.forecast_hour') AS INTEGER) as forecast_hour,
                status,
                COUNT(*) as var_count
            FROM jobs
            WHERE type = 'build_tile_hour'
            GROUP BY 1, 2, 3, 4
            ORDER BY model_id, run_id DESC, forecast_hour, status
            """
        ).fetchall()
    finally:
        conn.close()

    # Aggregate: for each (model, run, hour), pick display status
    # Priority: processing > pending > failed > completed
    STATUS_PRIORITY = {"processing": 0, "pending": 1, "failed": 2, "completed": 3}

    grid = {}
    for row in rows:
        model_id = row["model_id"]
        run_id = row["run_id"]
        hour = row["forecast_hour"]
        status = row["status"]

        if model_id not in grid:
            model_config = repomap["MODELS"].get(model_id, {})
            grid[model_id] = {
                "name": model_config.get("name", model_id),
                "runs": {},
            }

        runs = grid[model_id]["runs"]
        if run_id not in runs:
            # Parse init time
            parts = run_id.split("_")
            try:
                init_time = datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H")
                init_time = init_time.replace(tzinfo=timezone.utc)
                init_iso = init_time.isoformat()
            except (IndexError, ValueError):
                init_iso = None
            runs[run_id] = {
                "init_time": init_iso,
                "hours": {},
                "counts": {},
            }

        run_data = runs[run_id]
        # Track count per status
        run_data["counts"][status] = run_data["counts"].get(status, 0) + row["var_count"]

        # Pick highest-priority status for this hour
        current = run_data["hours"].get(hour)
        if current is None or STATUS_PRIORITY.get(status, 99) < STATUS_PRIORITY.get(current, 99):
            run_data["hours"][hour] = status

    # Add expected hours count and available runs for backfill
    for model_cfg in SCHEDULED_MODELS:
        model_id = model_cfg["id"]
        if model_id not in grid:
            model_config = repomap["MODELS"].get(model_id, {})
            grid[model_id] = {
                "name": model_config.get("name", model_id),
                "runs": {},
            }

        # Compute total expected hours for each run
        for run_id, run_data in grid[model_id]["runs"].items():
            expected = _target_expected_hours(model_id, run_id, model_cfg["max_hours"])
            run_data["total_hours"] = len(expected)

        # Available runs for backfill dropdown (last 24h)
        expected_runs = _get_expected_runs(model_id, lookback_hours=24)
        grid[model_id]["available_runs"] = expected_runs

    return grid


def _parse_updated_at_to_timestamp(updated_at: str) -> float:
    try:
        dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0.0
    return dt.replace(tzinfo=timezone.utc).timestamp()


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


def get_job_queue_status():
    """Get job queue status counts (pending, processing, completed, failed).

    Returns:
        dict: {"pending": int, "processing": int, "completed": int, "failed": int}
    """
    try:
        conn = init_jobs_db(repomap.get("DB_PATH", "cache/jobs.db"))
        try:
            return count_by_status(conn)
        finally:
            conn.close()
    except Exception:
        return {}
