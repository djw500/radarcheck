#!/usr/bin/env python3
"""Scheduled tile builder for fly.io deployment.

This script runs as a background process that periodically builds tiles
for all configured models and regions. It's designed to:

1. Enqueue tile-building jobs for each model/run/variable/hour
2. Drain the job queue inline (no separate worker process needed)
3. Clean up GRIB files and old tile runs to save disk space
4. Run continuously with appropriate sleep intervals

Usage:
    python scripts/build_tiles_scheduled.py
    python scripts/build_tiles_scheduled.py --once

Environment variables:
    TILE_BUILD_INTERVAL_MINUTES: How often to check for new runs (default: 15)
    TILE_BUILD_MAX_HOURS_HRRR: Max forecast hours for HRRR (default: 48)
    TILE_BUILD_MAX_HOURS_GFS: Max forecast hours for GFS (default: 168)
"""

import datetime
import logging
import os
import sys
import time
import json

import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import repomap
from utils import format_forecast_hour
from cache_builder import get_valid_forecast_hours, get_run_forecast_hours
from tile_db import init_db, delete_tile_run, delete_region_tiles
from ecmwf import herbie_run_available
from jobs import (
    init_db as init_jobs_db,
    enqueue,
    recover_stale,
    prune_completed,
)
from status_utils import _get_max_hours_for_run as get_max_hours_for_run

# Configure logging
os.makedirs('logs', exist_ok=True)
detailed_log_path = 'logs/scheduler_detailed.log'

_log_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

# Root logger captures everything
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# File handler — persistent record
file_handler = logging.FileHandler(detailed_log_path)
file_handler.setFormatter(_log_fmt)
root_logger.addHandler(file_handler)

# Stdout handler — so nohup/pipe captures timestamped output too
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(_log_fmt)
root_logger.addHandler(stdout_handler)

# Suppress noisy external libraries
for _noisy in ["urllib3", "requests", "matplotlib", "cfgrib", "fiona", "rasterio", "herbie"]:
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.ERROR)

logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)

# Configuration from environment with defaults
BUILD_INTERVAL_MINUTES = int(os.environ.get("TILE_BUILD_INTERVAL_MINUTES", "15"))
MAX_HOURS_HRRR = int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "48"))
MAX_HOURS_NAM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))
MAX_HOURS_GFS = int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))
MAX_HOURS_NBM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NBM", "168"))
BUILD_VARIABLES_ENV = os.environ.get("TILE_BUILD_VARIABLES")
# Max builds per model per cycle - ensures all models get attention
MAX_BUILDS_PER_MODEL = int(os.environ.get("TILE_BUILD_MAX_PER_MODEL", "3"))
# Cleanup settings - defaults are generous for local dev, fly.toml overrides for prod
MAX_GRIB_RUNS_TO_KEEP = int(os.environ.get("TILE_BUILD_GRIB_RUNS_KEEP", "2"))
# Tile retention: keep N synoptic (00/06/12/18z) + M hourly runs per model
MAX_SYNOPTIC_RUNS = int(os.environ.get("TILE_BUILD_SYNOPTIC_RUNS", "8"))
MAX_HOURLY_RUNS = int(os.environ.get("TILE_BUILD_HOURLY_RUNS", "12"))

STATUS_FILE = os.path.join(repomap["CACHE_DIR"], "scheduler_status.json")

def write_scheduler_status(state="idle", last_run=None, next_run=None, targets=None, error=None):
    """Write current scheduler status to JSON."""
    status = {
        "state": state,
        "last_run": last_run,
        "next_run": next_run,
        "targets": targets or [],
        "last_error": str(error) if error else None,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
    except Exception as e:
        logger.error(f"Failed to write status file: {e}")


# Models to build tiles for (in priority order)
MODELS_CONFIG = [
    {"id": "hrrr", "max_hours": MAX_HOURS_HRRR, "check_hours": 6},
    {"id": "nam_nest", "max_hours": MAX_HOURS_NAM, "check_hours": 12},
    {"id": "gfs", "max_hours": MAX_HOURS_GFS, "check_hours": 12},
    {"id": "nbm", "max_hours": MAX_HOURS_NBM, "check_hours": 12},
    # {"id": "ecmwf_hres", "max_hours": 240, "check_hours": 12},
]

# Regions to build
REGIONS = list(repomap.get("TILING_REGIONS", {}).keys())


def check_run_available(model_id: str, date_str: str, init_hour: str) -> bool:
    """Check if a model run is available on NOMADS."""
    logger.info(f"Checking availability for {model_id} {date_str} {init_hour}")
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return False

    if model_config.get("source") == "herbie":
        # Check availability for first valid hour using Herbie
        first_hour = get_valid_forecast_hours(model_id, 24)[0]
        forecast_hour = format_forecast_hour(first_hour, model_id)
        availability_var = model_config.get("availability_check_var")
        return herbie_run_available(
            model_id=model_id,
            variable_id=availability_var,
            date_str=date_str,
            init_hour=init_hour,
            forecast_hour=forecast_hour,
            timeout=repomap["HEAD_REQUEST_TIMEOUT_SECONDS"],
        )

    fhour_str = format_forecast_hour(1, model_id)
    file_name = model_config["file_pattern"].format(init_hour=init_hour, forecast_hour=fhour_str)
    dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
    url = f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&{model_config['availability_check_var']}=on"

    try:
        r = requests.head(url, timeout=repomap["HEAD_REQUEST_TIMEOUT_SECONDS"])
        return r.status_code == 200
    except requests.RequestException:
        return False


def get_required_runs(model_id: str, lookback_hours: int = 72) -> list[str]:
    """Find all runs in the lookback period that WE WANT to have according to policy.

    Short-circuits NOMADS HEAD requests when tiles already exist for all regions.
    """
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    update_freq = model_config.get("update_frequency_hours", 1)
    required_runs = []

    # Precompute default max hours for the model from MODELS_CONFIG
    model_cfg_entry = next((m for m in MODELS_CONFIG if m["id"] == model_id), None)
    default_max = model_cfg_entry["max_hours"] if model_cfg_entry else 24

    for hours_ago in range(lookback_hours):
        check_time = now - datetime.timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")

        # Skip off-cycle hours for models that don't run every hour
        if update_freq > 1 and int(init_hour) % update_freq != 0:
            continue

        # Policy Tier 1: All runs in last 12 hours
        # Policy Tier 2: Synoptic runs (00, 06, 12, 18) in last 72 hours
        is_recent = hours_ago <= 12
        is_synoptic = int(init_hour) % 6 == 0

        if is_recent or is_synoptic:
            run_id = f"run_{date_str}_{init_hour}"
            run_max_hours = get_max_hours_for_run(model_id, run_id, default_max)

            # Short-circuit: skip NOMADS HEAD request if tiles exist for all regions
            all_complete = all(
                tiles_exist(region_id, model_id, run_id, expected_max_hours=run_max_hours)
                for region_id in REGIONS
            )
            if all_complete:
                required_runs.append(run_id)
                continue

            # Check if available on NOMADS
            if check_run_available(model_id, date_str, init_hour):
                required_runs.append(run_id)

    return required_runs


def tiles_exist(region_id: str, model_id: str, run_id: str, expected_max_hours: int = 24) -> bool:
    """Check if tiles already exist and match the exact expected forecast steps.

    Deterministic policy: require the hours array in a proxy variable (t2m)
    to exactly match the model's schedule up to expected_max_hours.
    """
    res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")

    # Check for t2m tiles as a proxy for the run
    base_run_dir = os.path.join(repomap["TILES_DIR"], region_id, res_dir, model_id, run_id)
    npz_path = os.path.join(base_run_dir, "t2m.npz")
    if not os.path.exists(npz_path):
        return False
    # Verify region bounds match current config (backfill trigger after region expansion)
    try:
        meta_path = os.path.join(base_run_dir, "t2m.meta.json")
        if os.path.exists(meta_path):
            import json
            with open(meta_path, "r") as f:
                meta = json.load(f)
            reg = repomap["TILING_REGIONS"][region_id]
            tol = 1e-6
            if (
                abs(float(meta.get("lat_min")) - float(reg["lat_min"])) > tol or
                abs(float(meta.get("lat_max")) - float(reg["lat_max"])) > tol or
                abs(float(meta.get("lon_min")) - float(reg["lon_min"])) > tol or
                abs(float(meta.get("lon_max")) - float(reg["lon_max"])) > tol or
                abs(float(meta.get("resolution_deg")) - float(res)) > tol
            ):
                return False
    except Exception:
        # If meta can't be read, force rebuild
        return False

    # Strict completeness check against model schedule
    try:
        import numpy as np
        with np.load(npz_path) as d:
            have = d.get('hours', np.array([], dtype=np.int32)).tolist()
        parts = run_id.split('_')
        expected = get_run_forecast_hours(model_id, parts[1], parts[2], expected_max_hours)
        return have == expected
    except Exception:
        return False


def run_is_ready(run_id: str, min_age_minutes: int = 45) -> bool:
    """Check if a run is old enough to likely have data available on NOMADS.
    NOMADS usually takes 45-60 minutes to start publishing GRIBs after the init hour.
    """
    try:
        parts = run_id.split('_')
        run_dt = datetime.datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        age_mins = (now - run_dt).total_seconds() / 60
        return age_mins >= min_age_minutes
    except Exception:
        return True


def enqueue_run_jobs(conn, region_id: str, model_id: str, run_id: str, max_hours: int) -> int:
    """Enqueue build_tile_hour jobs for every variable × forecast_hour.

    Idempotent: duplicate jobs are ignored via UNIQUE(type, args_hash) in jobs table.
    Returns the number of newly enqueued jobs.
    """
    parts = run_id.split("_")
    date_str, init_hour = parts[1], parts[2]
    forecast_hours = get_run_forecast_hours(model_id, date_str, init_hour, max_hours)

    # Determine variables to build
    if BUILD_VARIABLES_ENV:
        var_ids = [v.strip() for v in BUILD_VARIABLES_ENV.split(",") if v.strip()]
    else:
        var_ids = list(repomap["WEATHER_VARIABLES"].keys())

    resolution_deg = repomap["TILING_REGIONS"][region_id].get("default_resolution_deg", 0.1)
    enqueued = 0

    for variable_id in var_ids:
        variable_config = repomap["WEATHER_VARIABLES"].get(variable_id)
        if not variable_config:
            continue
        # Check for model-specific exclusions
        if model_id in variable_config.get("model_exclusions", []):
            continue

        for hour in forecast_hours:
            job_args = {
                "region_id": region_id,
                "model_id": model_id,
                "run_id": run_id,
                "variable_id": variable_id,
                "forecast_hour": hour,
                "resolution_deg": resolution_deg,
            }
            job_id = enqueue(conn, "build_tile_hour", job_args)
            if job_id is not None:
                enqueued += 1

    return enqueued


def process_model(model_cfg: dict, conn) -> tuple[int, list]:
    """Process a single model - find the latest available run and enqueue it.

    Only enqueues the single most recent available run per model.
    Use the status page "Enqueue" button for manual backfills.

    Returns (jobs_enqueued, targets).
    """
    model_id = model_cfg["id"]
    max_hours = model_cfg["max_hours"]
    jobs_enqueued = 0
    targets = []

    # Only look at recent runs (last 12h) — find the latest available one
    runs_to_process = get_required_runs(model_id, lookback_hours=12)

    if not runs_to_process:
        logger.info(f"[{model_id}] No available runs found in last 12h")
        return 0, []

    logger.info(f"[{model_id}] Found {len(runs_to_process)} candidate run(s): {runs_to_process}")

    # Take only the latest (first, since newest-first) incomplete run
    for run_id in runs_to_process:
        if not run_is_ready(run_id):
            logger.info(f"[{model_id}] {run_id} not ready yet (< 45 min old), skipping")
            continue

        run_max_hours = get_max_hours_for_run(model_id, run_id, max_hours)

        missing_regions = [
            r for r in REGIONS
            if not tiles_exist(r, model_id, run_id, expected_max_hours=run_max_hours)
        ]

        if not missing_regions:
            logger.info(f"[{model_id}] {run_id} already complete for all regions, skipping")
            continue

        # Found the latest incomplete run — enqueue it
        targets.append(f"{model_id}/{run_id}")
        logger.info(f"[{model_id}] Enqueuing {run_id} (missing regions: {missing_regions}, max_hours={run_max_hours})")

        for region_id in missing_regions:
            n = enqueue_run_jobs(conn, region_id, model_id, run_id, run_max_hours)
            logger.info(f"[{model_id}] Enqueued {n} jobs for {run_id} / {region_id}")
            jobs_enqueued += n
        break  # Only process the single latest run

    return jobs_enqueued, targets



def build_cycle():
    """Run one build cycle: enqueue jobs for all models, then drain the queue."""
    cycle_start = time.monotonic()
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("=" * 60)
    logger.info(f"Build cycle starting at {now_str}")
    write_scheduler_status(state="running")

    conn = init_jobs_db(repomap["DB_PATH"])
    try:
        # Recover any stale processing jobs from a previous crash
        recovered = recover_stale(conn)
        if recovered:
            logger.info(f"Recovered {recovered} stale jobs from previous crash")

        total_enqueued = 0
        all_targets = []

        # Enqueue phase: sequential per model (fast, no I/O besides HEAD requests)
        logger.info(f"--- Enqueue phase ({len(MODELS_CONFIG)} models) ---")
        for model_cfg in MODELS_CONFIG:
            try:
                enqueued, targets = process_model(model_cfg, conn)
                total_enqueued += enqueued
                all_targets.extend(targets)
            except Exception as e:
                logger.error(f"Error processing {model_cfg['id']}: {e}", exc_info=True)

        write_scheduler_status(state="running", targets=all_targets)

        # Prune old completed jobs
        pruned = prune_completed(conn)
        if pruned:
            logger.info(f"Pruned {pruned} old completed jobs from DB")

        elapsed = time.monotonic() - cycle_start
        logger.info(f"Enqueue cycle complete in {elapsed:.0f}s: {total_enqueued} new jobs queued for workers")
        logger.info("=" * 60)
        return total_enqueued, all_targets
    finally:
        conn.close()


def cleanup_old_gribs(max_runs_per_model: int = None):
    """Clean up old GRIB files to save disk space.

    GRIBs are only needed during tile building. A run's GRIBs are preserved
    while its tiles are still incomplete (workers may still be building them).
    Once tiles are complete, GRIBs are eligible for deletion based on retention policy.
    """
    import shutil

    if max_runs_per_model is None:
        max_runs_per_model = MAX_GRIB_RUNS_TO_KEEP

    grib_dir = repomap.get("GRIB_CACHE_DIR", "cache/gribs")
    if not os.path.isdir(grib_dir):
        return

    for model_id in os.listdir(grib_dir):
        model_dir = os.path.join(grib_dir, model_id)
        if not os.path.isdir(model_dir):
            continue

        runs = sorted(
            [r for r in os.listdir(model_dir) if r.startswith("run_") and os.path.isdir(os.path.join(model_dir, r))],
            reverse=True
        )

        # Determine which runs to delete (oldest beyond retention limit)
        candidates_for_deletion = runs[max_runs_per_model:]
        for run_id in candidates_for_deletion:
            # Never delete GRIBs for a run that still has incomplete tiles —
            # workers may still be fetching and processing them.
            model_cfg = next((m for m in MODELS_CONFIG if m["id"] == model_id), None)
            default_max = model_cfg["max_hours"] if model_cfg else 24
            run_max_hours = get_max_hours_for_run(model_id, run_id, default_max)
            still_building = any(
                not tiles_exist(region_id, model_id, run_id, expected_max_hours=run_max_hours)
                for region_id in REGIONS
            )
            if still_building:
                logger.info(f"GRIB cleanup: skipping {model_id}/{run_id} (tiles still incomplete)")
                continue

            old_run_dir = os.path.join(model_dir, run_id)
            logger.info(f"GRIB cleanup: removing {model_id}/{run_id}")
            try:
                shutil.rmtree(old_run_dir)
            except Exception as e:
                logger.error(f"Failed to remove {old_run_dir}: {e}")


def cleanup_old_runs():
    """Clean up old tile runs using tiered retention:
    - Keep up to MAX_SYNOPTIC_RUNS synoptic runs (00, 06, 12, 18z)
    - Keep up to MAX_HOURLY_RUNS recent hourly runs (for HRRR etc)
    """
    # Open DB connection once
    conn = init_db(repomap.get("DB_PATH"))
    try:
        for region_id in REGIONS:
            res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
            res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
            region_dir = os.path.join(repomap["TILES_DIR"], region_id, res_dir)

            if not os.path.isdir(region_dir):
                continue

            for model_id in os.listdir(region_dir):
                model_dir = os.path.join(region_dir, model_id)
                if not os.path.isdir(model_dir):
                    continue

                # Get runs sorted newest to oldest
                runs = sorted(
                    [r for r in os.listdir(model_dir) if r.startswith("run_") and os.path.isdir(os.path.join(model_dir, r))],
                    reverse=True
                )

                if not runs:
                    continue

                # Separate synoptic (00, 06, 12, 18z) from hourly runs
                synoptic_runs = []
                hourly_runs = []
                for run_id in runs:
                    try:
                        init_hour = int(run_id.split('_')[2])
                        if init_hour % 6 == 0:
                            synoptic_runs.append(run_id)
                        else:
                            hourly_runs.append(run_id)
                    except (IndexError, ValueError):
                        hourly_runs.append(run_id)

                # Keep top N synoptic + top M hourly
                keep_synoptic = set(synoptic_runs[:MAX_SYNOPTIC_RUNS])
                keep_hourly = set(hourly_runs[:MAX_HOURLY_RUNS])
                keep_all = keep_synoptic | keep_hourly

                runs_to_remove = [r for r in runs if r not in keep_all]

                if runs_to_remove:
                    logger.info(f"Tile cleanup {model_id}: keeping {len(keep_synoptic)} synoptic + {len(keep_hourly)} hourly, removing {len(runs_to_remove)}")

                for old_run in runs_to_remove:
                    old_run_dir = os.path.join(model_dir, old_run)
                    logger.info(f"Removing old tile run: {model_id}/{old_run}")
                    try:
                        import shutil
                        shutil.rmtree(old_run_dir)
                        # Remove from DB as well
                        delete_tile_run(conn, region_id, res, model_id, old_run)
                    except Exception as e:
                        logger.error(f"Failed to remove {old_run_dir}: {e}")
    finally:
        conn.close()


def main():
    """Main entry point for scheduled tile building."""
    logger.info("=" * 60)
    logger.info("Scheduled Tile Builder Starting")

    # Handle command line args
    clear_cache = "--clear" in sys.argv

    if clear_cache:
        logger.warning("CLEARING TILE CACHE requested via --clear flag")
        conn = init_db(repomap.get("DB_PATH"))
        try:
            for region_id in REGIONS:
                res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
                res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
                region_dir = os.path.join(repomap["TILES_DIR"], region_id, res_dir)
                if os.path.exists(region_dir):
                    logger.info(f"Removing {region_dir}...")
                    try:
                        import shutil
                        shutil.rmtree(region_dir)
                        delete_region_tiles(conn, region_id)
                    except Exception as e:
                        logger.error(f"Failed to clear cache: {e}")
        finally:
            conn.close()

    logger.info(f"Build interval: {BUILD_INTERVAL_MINUTES} minutes")
    logger.info(f"Models: {[m['id'] for m in MODELS_CONFIG]}")
    logger.info(f"Regions: {REGIONS}")
    logger.info("=" * 60)

    while True:
        try:
            # Run build cycle
            _, targets = build_cycle()

            # Cleanup old runs and GRIBs periodically
            cleanup_old_runs()
            cleanup_old_gribs()

            # Record success and sleep state
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            next_run = (now_utc + datetime.timedelta(minutes=BUILD_INTERVAL_MINUTES)).isoformat()
            write_scheduler_status(
                state="sleeping",
                last_run=now_utc.isoformat(),
                next_run=next_run,
                targets=targets
            )

        except Exception as e:
            logger.exception(f"Error in build cycle: {e}")
            write_scheduler_status(state="error", error=e)

        # Sleep until next cycle
        wake_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=BUILD_INTERVAL_MINUTES)
        logger.info(f"Sleeping {BUILD_INTERVAL_MINUTES}m — next cycle at {wake_at.strftime('%H:%M:%S UTC')}")
        time.sleep(BUILD_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    # Support single-run mode via command line arg
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logger.info("Running single enqueue cycle (--once mode)")
        build_cycle()
        cleanup_old_runs()
        cleanup_old_gribs()
    else:
        main()
