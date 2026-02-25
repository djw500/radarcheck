#!/usr/bin/env python3
"""Tile job scheduler for fly.io deployment.

Runs as a background process that periodically:
1. Checks for new model runs via Herbie inventory
2. Enqueues tile-building jobs for each model/run/variable/hour
3. Cleans up old Herbie GRIB cache and tile runs to save disk space

Workers (job_worker.py) process the queued jobs separately.

Usage:
    python scripts/scheduler.py
    python scripts/scheduler.py --once

Environment variables:
    TILE_BUILD_INTERVAL_MINUTES: How often to check for new runs (default: 15)
    TILE_BUILD_MAX_HOURS_<MODEL>: Override max forecast hours per model
"""

import datetime
import logging
import os
import sys
import time
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import repomap
from grib_fetcher import get_valid_forecast_hours, get_run_forecast_hours, check_availability
from tile_db import init_db, delete_tile_run, delete_region_tiles
from jobs import (
    init_db as init_jobs_db,
    enqueue,
    recover_stale,
    prune_completed,
    prune_failed,
    count_pending_by_model,
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
BUILD_VARIABLES_ENV = os.environ.get("TILE_BUILD_VARIABLES", "") or "apcp,asnow,snod,t2m"
# Tile retention: keep N synoptic (00/06/12/18z) + M hourly runs per model
# Global defaults; override per-model with TILE_BUILD_SYNOPTIC_RUNS_<MODEL>
DEFAULT_SYNOPTIC_RUNS = int(os.environ.get("TILE_BUILD_SYNOPTIC_RUNS", "8"))
DEFAULT_HOURLY_RUNS = int(os.environ.get("TILE_BUILD_HOURLY_RUNS", "12"))


def _get_retention(model_id):
    """Get (synoptic, hourly) retention counts for a model."""
    syn = int(os.environ.get(f"TILE_BUILD_SYNOPTIC_RUNS_{model_id.upper()}", DEFAULT_SYNOPTIC_RUNS))
    hr = int(os.environ.get(f"TILE_BUILD_HOURLY_RUNS_{model_id.upper()}", DEFAULT_HOURLY_RUNS))
    return syn, hr

# Safety cap: refuse to enqueue more if a model already has this many pending jobs.
# Prevents runaway enqueue from misconfigured hour limits.
MAX_PENDING_PER_MODEL = int(os.environ.get("TILE_BUILD_MAX_PENDING_PER_MODEL", "500"))

STATUS_FILE = os.path.join(repomap["CACHE_DIR"], "scheduler_status.json")


def _build_models_config():
    """Derive model list from config.py MODELS, with env var overrides for max_hours."""
    result = []
    for model_id, model_cfg in repomap["MODELS"].items():
        env_key = f"TILE_BUILD_MAX_HOURS_{model_id.upper()}"
        max_hours = int(os.environ.get(env_key, model_cfg["max_forecast_hours"]))
        result.append({"id": model_id, "max_hours": max_hours})
    return result


MODELS_CONFIG = _build_models_config()

# Regions to build
REGIONS = list(repomap.get("TILING_REGIONS", {}).keys())


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


def check_run_available(model_id: str, date_str: str, init_hour: str) -> bool:
    """Check if a model run is available via Herbie inventory."""
    logger.info(f"Checking availability for {model_id} {date_str} {init_hour}")
    first_hour = get_valid_forecast_hours(model_id, 24)[0]
    return check_availability(model_id, date_str, init_hour, first_hour)


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


def enqueue_run_jobs(conn, region_id: str, model_id: str, run_id: str, max_hours: int) -> int:
    """Enqueue build_tile_hour jobs for every variable * forecast_hour.

    Idempotent: duplicate jobs are ignored via UNIQUE(type, args_hash) in jobs table.
    Newer runs get higher priority so workers process fresh data first.
    Returns the number of newly enqueued jobs.
    """
    parts = run_id.split("_")
    date_str, init_hour = parts[1], parts[2]
    forecast_hours = get_run_forecast_hours(model_id, date_str, init_hour, max_hours)

    # Determine variables to build
    var_ids = [v.strip() for v in BUILD_VARIABLES_ENV.split(",") if v.strip()]

    resolution_deg = repomap["TILING_REGIONS"][region_id].get("default_resolution_deg", 0.1)

    # Compute priority: newer runs get strictly higher priority.
    # Use minutes (not hours) so runs 6h apart never collide.
    now = datetime.datetime.now(datetime.timezone.utc)
    run_dt = datetime.datetime.strptime(f"{date_str}{init_hour}", "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
    minutes_old = max(0, int((now - run_dt).total_seconds() / 60))
    priority = max(0, 100000 - minutes_old)

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
            job_id = enqueue(conn, "build_tile_hour", job_args, priority=priority)
            if job_id is not None:
                enqueued += 1

    return enqueued


def process_model(model_cfg: dict, conn, pending_counts: dict) -> tuple[int, list]:
    """Process a single model — find incomplete runs and enqueue them.

    Only enqueues up to the retention limit (synoptic + hourly) so we don't
    build tiles that cleanup will immediately delete.

    Returns (jobs_enqueued, targets).
    """
    model_id = model_cfg["id"]
    max_hours = model_cfg["max_hours"]
    freq = repomap["MODELS"][model_id].get("update_frequency_hours", 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    jobs_enqueued = 0
    targets = []

    # Safety cap: skip if already too many pending jobs for this model
    current_pending = pending_counts.get(model_id, 0)
    if current_pending >= MAX_PENDING_PER_MODEL:
        logger.warning(f"SKIP {model_id}: {current_pending} pending jobs already (cap={MAX_PENDING_PER_MODEL})")
        return 0, []

    # Retention limits: only enqueue this many runs (newest first)
    max_syn, max_hr = _get_retention(model_id)
    synoptic_found = 0
    hourly_found = 0

    for hours_ago in range(48):
        t = now - datetime.timedelta(hours=hours_ago)
        ih = int(t.strftime("%H"))
        if freq > 1 and ih % freq != 0:
            continue

        # Check retention budget
        is_synoptic = ih % 6 == 0
        if is_synoptic and synoptic_found >= max_syn:
            continue
        if not is_synoptic and hourly_found >= max_hr:
            continue

        date_str = t.strftime("%Y%m%d")
        init_hour = t.strftime("%H")
        run_id = f"run_{date_str}_{init_hour}"
        run_max = get_max_hours_for_run(model_id, run_id, max_hours)

        # Count toward budget whether tiles exist or not
        if is_synoptic:
            synoptic_found += 1
        else:
            hourly_found += 1

        if all(tiles_exist(r, model_id, run_id, run_max) for r in REGIONS):
            continue
        if not check_run_available(model_id, date_str, init_hour):
            continue

        for region_id in REGIONS:
            n = enqueue_run_jobs(conn, region_id, model_id, run_id, run_max)
            jobs_enqueued += n
        targets.append(f"{model_id}/{run_id}")

        # Early exit if both budgets exhausted
        if synoptic_found >= max_syn and hourly_found >= max_hr:
            break

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

        # Snapshot pending counts before enqueue phase (for safety cap)
        pending_counts = count_pending_by_model(conn)

        # Enqueue phase: sequential per model (fast, no I/O besides HEAD requests)
        logger.info(f"--- Enqueue phase ({len(MODELS_CONFIG)} models) ---")
        for model_cfg in MODELS_CONFIG:
            try:
                enqueued, targets = process_model(model_cfg, conn, pending_counts)
                total_enqueued += enqueued
                all_targets.extend(targets)
            except Exception as e:
                logger.error(f"Error processing {model_cfg['id']}: {e}", exc_info=True)

        write_scheduler_status(state="running", targets=all_targets)

        # Prune old completed and failed jobs
        pruned = prune_completed(conn)
        if pruned:
            logger.info(f"Pruned {pruned} old completed jobs from DB")
        pruned_failed = prune_failed(conn)
        if pruned_failed:
            logger.info(f"Pruned {pruned_failed} old failed jobs from DB")

        elapsed = time.monotonic() - cycle_start
        logger.info(f"Enqueue cycle complete in {elapsed:.0f}s: {total_enqueued} new jobs queued for workers")
        logger.info("=" * 60)
        return total_enqueued, all_targets
    finally:
        conn.close()


def cleanup_herbie_cache(max_age_days: int = 2):
    """Clean up old Herbie GRIB cache directories.

    Herbie stores downloads at HERBIE_SAVE_DIR/<model>/YYYYMMDD/.
    Delete date directories older than max_age_days.
    """
    import shutil

    herbie_dir = repomap.get("HERBIE_SAVE_DIR", "cache/herbie")
    if not os.path.isdir(herbie_dir):
        return

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
    cutoff_str = cutoff.strftime("%Y%m%d")

    for model_name in os.listdir(herbie_dir):
        model_dir = os.path.join(herbie_dir, model_name)
        if not os.path.isdir(model_dir):
            continue

        for date_dir in os.listdir(model_dir):
            date_path = os.path.join(model_dir, date_dir)
            if not os.path.isdir(date_path):
                continue
            # Herbie date dirs are YYYYMMDD
            if len(date_dir) == 8 and date_dir.isdigit() and date_dir < cutoff_str:
                logger.info(f"Herbie cache cleanup: removing {model_name}/{date_dir}")
                try:
                    shutil.rmtree(date_path)
                except Exception as e:
                    logger.error(f"Failed to remove {date_path}: {e}")


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

                # Keep top N synoptic + top M hourly (per-model)
                max_syn, max_hr = _get_retention(model_id)
                keep_synoptic = set(synoptic_runs[:max_syn])
                keep_hourly = set(hourly_runs[:max_hr])
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

    # Clean slate on restart: nuke everything (jobs, tiles, GRIBs).
    # The scheduler will rebuild from scratch each deploy.
    import shutil

    # 1. Nuke job queue
    conn = init_jobs_db(repomap["DB_PATH"])
    try:
        nuked = conn.execute("DELETE FROM jobs").rowcount
        conn.commit()
        if nuked:
            logger.info(f"Startup: nuked {nuked} jobs")
    finally:
        conn.close()

    # 2. Nuke tiles
    tiles_dir = repomap["TILES_DIR"]
    if os.path.isdir(tiles_dir):
        shutil.rmtree(tiles_dir)
        logger.info(f"Startup: nuked tiles dir {tiles_dir}")
    os.makedirs(tiles_dir, exist_ok=True)

    # 3. Nuke tile metadata DB
    tile_conn = init_db(repomap.get("DB_PATH"))
    try:
        for region_id in REGIONS:
            delete_region_tiles(tile_conn, region_id)
        logger.info("Startup: nuked tile metadata")
    finally:
        tile_conn.close()

    # 4. Nuke Herbie GRIB cache
    herbie_dir = repomap.get("HERBIE_SAVE_DIR", "cache/herbie")
    if os.path.isdir(herbie_dir):
        shutil.rmtree(herbie_dir)
        logger.info(f"Startup: nuked GRIB cache {herbie_dir}")
    os.makedirs(herbie_dir, exist_ok=True)

    logger.info("Startup: clean slate complete")

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
            cleanup_herbie_cache()

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
        cleanup_herbie_cache()
    else:
        main()
