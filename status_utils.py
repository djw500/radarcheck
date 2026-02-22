import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone

from config import repomap
from jobs import init_db as init_jobs_db, count_by_status
from tile_db import init_db

STATUS_FILE = os.path.join(repomap["CACHE_DIR"], "scheduler_status.json")

# Scheduler model configuration - mirrors scripts/scheduler.py
SCHEDULED_MODELS = [
    {"id": "hrrr", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "48"))},
    {"id": "nam_nest", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))},
    {"id": "gfs", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))},
    {"id": "nbm", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_NBM", "168"))},
    {"id": "ecmwf_hres", "max_hours": int(os.environ.get("TILE_BUILD_MAX_HOURS_ECMWF_HRES", "240"))},
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

        # Skip off-cycle hours for models that don't run every hour
        if update_freq > 1 and int(init_hour) % update_freq != 0:
            continue

        # Policy: keep recent (12h) + synoptic runs (00/06/12/18Z)
        is_recent = hours_ago <= 12
        is_synoptic = int(init_hour) % 6 == 0

        if is_recent or is_synoptic:
            expected_runs.append(f"run_{date_str}_{init_hour}")

    return expected_runs


def get_run_grid():
    """Get per-model/run/variable job status summary from the jobs table.

    Returns a dict keyed by model_id with per-run, per-variable counts.
    Designed for a table view: rows=runs, columns=variables, cells=done/total.

    Structure:
    {
        "hrrr": {
            "name": "HRRR",
            "variables": ["t2m", "refc", ...],  # ordered list of vars with jobs
            "runs": [
                {
                    "run_id": "run_20260215_20",
                    "display": "02/15 20Z",
                    "variables": {
                        "t2m":  {"completed": 18, "pending": 0, "failed": 0, "processing": 0, "total": 18},
                        "refc": {"completed": 10, "pending": 5, "failed": 3, "processing": 0, "total": 18},
                        ...
                    },
                    "totals": {"completed": 200, "pending": 10, "failed": 5, "processing": 2, "total": 217}
                }
            ],
            "available_runs": ["run_20260215_18", ...]
        }
    }
    """
    conn = init_db(repomap.get("DB_PATH"))
    try:
        rows = conn.execute(
            """
            SELECT
                json_extract(args_json, '$.model_id') as model_id,
                json_extract(args_json, '$.run_id') as run_id,
                json_extract(args_json, '$.variable_id') as variable_id,
                status,
                COUNT(*) as cnt
            FROM jobs
            WHERE type = 'build_tile_hour'
            GROUP BY 1, 2, 3, 4
            ORDER BY model_id, run_id DESC, variable_id, status
            """,
        ).fetchall()
    finally:
        conn.close()

    # Build nested structure: model -> run -> variable -> {status: count}
    raw = {}
    for row in rows:
        model_id = row["model_id"]
        run_id = row["run_id"]
        var_id = row["variable_id"]
        status = row["status"]
        cnt = row["cnt"]

        raw.setdefault(model_id, {}).setdefault(run_id, {}).setdefault(var_id, {})
        raw[model_id][run_id][var_id][status] = cnt

    # Format into the output structure
    grid = {}
    for model_cfg in SCHEDULED_MODELS:
        model_id = model_cfg["id"]
        model_config = repomap["MODELS"].get(model_id, {})
        model_raw = raw.get(model_id, {})

        # Only show variables that actually have jobs in the DB for this model
        all_vars = set()
        for run_data in model_raw.values():
            all_vars.update(run_data.keys())

        # Order variables: use display order, then alphabetical for any extras
        preferred_order = ["t2m", "apcp", "prate", "asnow", "csnow", "snod"]
        ordered_vars = [v for v in preferred_order if v in all_vars]
        ordered_vars += sorted(all_vars - set(ordered_vars))

        # Build run list (newest first)
        run_list = []
        for run_id in sorted(model_raw.keys(), reverse=True):
            parts = run_id.split("_")
            display = f"{parts[1][4:6]}/{parts[1][6:8]} {parts[2]}Z" if len(parts) == 3 else run_id

            var_summaries = {}
            totals = {"completed": 0, "pending": 0, "failed": 0, "processing": 0, "total": 0}

            for var_id in ordered_vars:
                status_counts = model_raw[run_id].get(var_id, {})
                summary = {
                    "completed": status_counts.get("completed", 0),
                    "pending": status_counts.get("pending", 0),
                    "failed": status_counts.get("failed", 0),
                    "processing": status_counts.get("processing", 0),
                }
                summary["total"] = sum(summary.values())
                var_summaries[var_id] = summary
                for k in totals:
                    totals[k] += summary.get(k, 0)

            run_list.append({
                "run_id": run_id,
                "display": display,
                "variables": var_summaries,
                "totals": totals,
            })

        # Available runs for backfill dropdown (last 24h)
        expected_runs = _get_expected_runs(model_id, lookback_hours=24)

        grid[model_id] = {
            "name": model_config.get("name", model_id),
            "variables": ordered_vars,
            "runs": run_list,
            "available_runs": expected_runs,
        }

    return grid


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
