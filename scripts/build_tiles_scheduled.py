#!/usr/bin/env python3
"""Scheduled tile builder for fly.io deployment.

This script runs as a background process that periodically builds tiles
for all configured models and regions. It's designed to:

1. Build tiles based on model update frequencies
2. Clean up GRIB files to save disk space
3. Log progress and errors
4. Run continuously with appropriate sleep intervals

Usage:
    python scripts/build_tiles_scheduled.py

Environment variables:
    TILE_BUILD_INTERVAL_MINUTES: How often to check for new runs (default: 15)
    TILE_BUILD_MAX_HOURS_HRRR: Max forecast hours for HRRR (default: 24)
    TILE_BUILD_MAX_HOURS_GFS: Max forecast hours for GFS (default: 168)
"""

import datetime
import logging
import os
import subprocess
import sys
import time
import json
from typing import Optional

import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import repomap
from utils import format_forecast_hour

# Configure logging
os.makedirs('logs', exist_ok=True)
detailed_log_path = 'logs/scheduler_detailed.log'

# Root logger gets everything and sends to file
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(detailed_log_path)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
root_logger.addHandler(file_handler)

# Explicitly suppress noisy external libraries in ALL handlers
for logger_name in ["urllib3", "requests", "matplotlib", "cfgrib", "fiona", "rasterio"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.ERROR) # Extra quiet for cfgrib

# Main logger for scheduler (only to file)
logger = logging.getLogger("scheduler")
logger.setLevel(logging.INFO)
# No console handler added here

# Suppress noisy external libraries in console (but they'll still be in file if we lowered root level)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.ERROR)

# Configuration from environment with defaults
BUILD_INTERVAL_MINUTES = int(os.environ.get("TILE_BUILD_INTERVAL_MINUTES", "15"))
MAX_HOURS_HRRR = int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "48"))
MAX_HOURS_NAM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))
MAX_HOURS_GFS = int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))
MAX_HOURS_NBM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NBM", "168"))
KEEP_RUNS = int(os.environ.get("TILE_BUILD_KEEP_RUNS", "5"))
BUILD_VARIABLES_ENV = os.environ.get("TILE_BUILD_VARIABLES")
# Max builds per model per cycle - ensures all models get attention
MAX_BUILDS_PER_MODEL = int(os.environ.get("TILE_BUILD_MAX_PER_MODEL", "3"))
# Cleanup settings - keep cache small on constrained environments
MAX_GRIB_RUNS_TO_KEEP = int(os.environ.get("TILE_BUILD_GRIB_RUNS_KEEP", "1"))
MAX_TILE_RUNS_TO_KEEP = int(os.environ.get("TILE_BUILD_TILE_RUNS_KEEP", "12"))

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


def get_max_hours_for_run(model_id: str, run_id: str, default_max: int) -> int:
    """Get max forecast hours for a specific run, accounting for init-hour variations.

    Some models (like HRRR) have different forecast lengths depending on init hour:
    - HRRR synoptic runs (00, 06, 12, 18z): 48 hours
    - HRRR non-synoptic runs: 18 hours
    """
    model_config = repomap["MODELS"].get(model_id, {})
    max_hours_by_init = model_config.get("max_hours_by_init")

    if not max_hours_by_init:
        return default_max

    # Extract init hour from run_id (format: run_YYYYMMDD_HH)
    try:
        init_hour = run_id.split("_")[2]
        return max_hours_by_init.get(init_hour, max_hours_by_init.get("default", default_max))
    except (IndexError, KeyError):
        return default_max

# Models to build tiles for (in priority order)
MODELS_CONFIG = [
    {"id": "hrrr", "max_hours": MAX_HOURS_HRRR, "check_hours": 6},
    {"id": "nam_nest", "max_hours": MAX_HOURS_NAM, "check_hours": 12},
    {"id": "gfs", "max_hours": MAX_HOURS_GFS, "check_hours": 12},
    {"id": "nbm", "max_hours": MAX_HOURS_NBM, "check_hours": 12},
    {"id": "ecmwf_hres", "max_hours": 240, "check_hours": 12},
]

# Regions to build
REGIONS = list(repomap.get("TILING_REGIONS", {}).keys())


def check_run_available(model_id: str, date_str: str, init_hour: str) -> bool:
    """Check if a model run is available on NOMADS."""
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return False

    if model_config.get("source") == "herbie":
        return True

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
    """Find all runs in the lookback period that WE WANT to have according to policy."""
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    update_freq = model_config.get("update_frequency_hours", 1)
    required_runs = []

    for hours_ago in range(lookback_hours):
        check_time = now - datetime.timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")

        # Skip non-synoptic hours for models with 6-hourly updates
        if update_freq >= 6 and int(init_hour) % 6 != 0:
            continue
            
        # Policy Tier 1: All runs in last 12 hours
        # Policy Tier 2: Synoptic runs (00, 06, 12, 18) in last 72 hours
        is_recent = hours_ago <= 12
        is_synoptic = int(init_hour) % 6 == 0
        
        if is_recent or is_synoptic:
            # Check if available on NOMADS
            if check_run_available(model_id, date_str, init_hour):
                required_runs.append(f"run_{date_str}_{init_hour}")

    return required_runs


def tiles_exist_any(region_id: str, model_id: str) -> bool:
    """Check if any tiles exist for a model in a region."""
    res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    model_dir = os.path.join(repomap["TILES_DIR"], region_id, res_dir, model_id)
    if not os.path.isdir(model_dir):
        return False
    # Check for any run directory
    for item in os.listdir(model_dir):
        if item.startswith("run_") and os.path.isdir(os.path.join(model_dir, item)):
            return True
    return False


def tiles_exist(region_id: str, model_id: str, run_id: str, expected_max_hours: int = 24) -> bool:
    """Check if tiles already exist and are sufficiently complete for a specific run.
    If the run is very recent, we might want to retry if it has fewer hours than expected.
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
        
    # Check if the run is complete enough
    try:
        import numpy as np
        with np.load(npz_path) as d:
            if 'hours' not in d:
                return False
            hours = d['hours']
            actual_hours = len(hours)

        # If we have at least 90% of expected hours, consider it complete
        if actual_hours >= expected_max_hours * 0.9:
            return True

        # Get run age
        try:
            parts = run_id.split('_')
            run_dt = datetime.datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            age_hours = (now - run_dt).total_seconds() / 3600
        except:
            age_hours = 999  # Assume old if can't parse

        # If the run is older than 6 hours, NOMADS has published everything it will
        # Accept whatever hours we have as complete
        if age_hours > 6:
            return True

        # For runs < 6h old: require at least 50% of hours that SHOULD be available
        # NOMADS publishes ~1 hour of forecast per 5-10 minutes after init
        # So a 2h old run should have ~12-24 hours available
        hours_should_be_available = min(expected_max_hours, int(age_hours * 12))
        if hours_should_be_available > 0 and actual_hours >= hours_should_be_available * 0.5:
            return True

        return False
    except Exception:
        return False


def build_tiles_for_run(region_id: str, model_id: str, run_id: str, max_hours: int) -> bool:
    """Build tiles for a specific run. Returns True on success."""
    logger.info(f"Building tiles for {model_id}/{run_id} in region {region_id}")

    cmd = [
        sys.executable,
        "-u", # Unbuffered output
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build_tiles.py"),
        "--region", region_id,
        "--model", model_id,
        "--run", run_id,
        "--max-hours", str(max_hours),
        "--clean-gribs",
    ]

    # Restrict variables if configured (comma-separated list)
    if BUILD_VARIABLES_ENV:
        vars_list = [v.strip() for v in BUILD_VARIABLES_ENV.split(',') if v.strip()]
        if vars_list:
            cmd += ["--variables", *vars_list]

    try:
        # Stream output character by character to show real-time dots
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        if process.stdout:
            last_was_newline = True
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                
                line = line.strip()
                if line:
                    # Only print if it doesn't look like a standard library log line
                    # (which should have been redirected to file anyway)
                    if not any(x in line for x in ["[INFO]", "[WARNING]", "[ERROR]", "[DEBUG]"]):
                        print(f"    [{model_id}] {line}")
                    
                    # Still log everything to detailed file
                    logger.debug(f"[{model_id}] {line}")
        
        returncode = process.wait(timeout=3600)
        if returncode == 0:
            logger.info(f"Successfully built tiles for {model_id}/{run_id}")
            return True
        else:
            logger.error(f"Failed to build tiles for {model_id}/{run_id} (exit code: {returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout building tiles for {model_id}/{run_id}")
        return False
    except Exception as e:
        logger.error(f"Error building tiles for {model_id}/{run_id}: {e}")
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
    except:
        return True


def process_model(model_cfg: dict) -> tuple[int, int, list]:
    """Process a single model - find and build missing runs.

    Returns (builds_attempted, builds_succeeded, targets).
    """
    model_id = model_cfg["id"]
    max_hours = model_cfg["max_hours"]
    builds_attempted = 0
    builds_succeeded = 0
    targets = []

    # Get all runs we SHOULD have according to tiered policy (last 72h)
    runs_to_process = get_required_runs(model_id, lookback_hours=72)

    if not runs_to_process:
        logger.warning(f"No available runs found for {model_id}")
        return 0, 0, []

    for r in runs_to_process:
        targets.append(f"{model_id}/{r}")

    num_runs = len(runs_to_process)
    print(f"[{model_id}] Checking {num_runs} required runs...")

    for i, run_id in enumerate(runs_to_process):
        # Skip if run is too new
        if not run_is_ready(run_id):
            continue

        # Get run-specific max hours (e.g., HRRR synoptic vs non-synoptic)
        run_max_hours = get_max_hours_for_run(model_id, run_id, max_hours)

        for region_id in REGIONS:
            # Skip if tiles already exist and are complete
            if tiles_exist(region_id, model_id, run_id, expected_max_hours=run_max_hours):
                continue

            print(f"[{model_id}] Building {run_id}...")
            builds_attempted += 1
            if build_tiles_for_run(region_id, model_id, run_id, run_max_hours):
                builds_succeeded += 1
                print(f"[{model_id}] Completed {run_id}")

    return builds_attempted, builds_succeeded, targets


def build_cycle():
    """Run one build cycle for all models in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    logger.info("Starting build cycle")
    write_scheduler_status(state="running")

    total_attempted = 0
    total_succeeded = 0
    all_targets = []

    # Process all models in parallel
    with ThreadPoolExecutor(max_workers=len(MODELS_CONFIG)) as executor:
        futures = {executor.submit(process_model, cfg): cfg["id"] for cfg in MODELS_CONFIG}

        for future in as_completed(futures):
            model_id = futures[future]
            try:
                attempted, succeeded, targets = future.result()
                total_attempted += attempted
                total_succeeded += succeeded
                all_targets.extend(targets)
                if attempted > 0:
                    print(f"[{model_id}] Done: {succeeded}/{attempted} builds succeeded")
            except Exception as e:
                logger.error(f"Error processing {model_id}: {e}")

    write_scheduler_status(state="running", targets=all_targets)
    print(f"Build cycle complete: {total_succeeded}/{total_attempted} succeeded")
    return total_attempted, total_succeeded, all_targets


def cleanup_old_gribs(max_runs_per_model: int = None):
    """Clean up old GRIB files to save disk space.

    GRIBs are only needed during tile building, so we keep minimal runs cached.
    """
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

        # Keep only the most recent runs
        for old_run in runs[max_runs_per_model:]:
            old_run_dir = os.path.join(model_dir, old_run)
            logger.info(f"GRIB cleanup: Removing {model_id}/{old_run}")
            try:
                import shutil
                shutil.rmtree(old_run_dir)
            except Exception as e:
                logger.error(f"Failed to remove {old_run_dir}: {e}")


def cleanup_old_runs(max_runs_per_model: int = None):
    """Clean up old tile runs - keep only the N most recent per model."""
    if max_runs_per_model is None:
        max_runs_per_model = MAX_TILE_RUNS_TO_KEEP

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

            # Remove runs beyond the limit
            runs_to_remove = runs[max_runs_per_model:]
            if runs_to_remove:
                logger.info(f"Tile cleanup {model_id}: keeping {min(len(runs), max_runs_per_model)}, removing {len(runs_to_remove)}")

            for old_run in runs_to_remove:
                old_run_dir = os.path.join(model_dir, old_run)
                logger.info(f"Removing old tile run: {model_id}/{old_run}")
                try:
                    import shutil
                    shutil.rmtree(old_run_dir)
                except Exception as e:
                    logger.error(f"Failed to remove {old_run_dir}: {e}")


def main():
    """Main entry point for scheduled tile building."""
    logger.info("=" * 60)
    logger.info("Scheduled Tile Builder Starting")
    
    # Handle command line args
    clear_cache = "--clear" in sys.argv
    once_mode = "--once" in sys.argv
    
    if clear_cache:
        logger.warning("CLEARING TILE CACHE requested via --clear flag")
        for region_id in REGIONS:
            res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
            res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
            region_dir = os.path.join(repomap["TILES_DIR"], region_id, res_dir)
            if os.path.exists(region_dir):
                logger.info(f"Removing {region_dir}...")
                try:
                    import shutil
                    shutil.rmtree(region_dir)
                except Exception as e:
                    logger.error(f"Failed to clear cache: {e}")
    
    logger.info(f"Build interval: {BUILD_INTERVAL_MINUTES} minutes")
    logger.info(f"Models: {[m['id'] for m in MODELS_CONFIG]}")
    logger.info(f"Regions: {REGIONS}")
    logger.info("=" * 60)

    while True:
        try:
            # Run build cycle
            _, _, targets = build_cycle()

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
        print(f"Sleeping {BUILD_INTERVAL_MINUTES} minutes until next cycle...")
        time.sleep(BUILD_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    # Support single-run mode via command line arg
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logger.info("Running single build cycle (--once mode)")
        build_cycle()
        cleanup_old_runs()
        cleanup_old_gribs()
    else:
        main()
