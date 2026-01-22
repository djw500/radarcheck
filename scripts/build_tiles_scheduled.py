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
MAX_HOURS_HRRR = int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "24"))
MAX_HOURS_NAM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))
MAX_HOURS_GFS = int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))
MAX_HOURS_NBM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NBM", "168"))
KEEP_RUNS = int(os.environ.get("TILE_BUILD_KEEP_RUNS", "5"))

# Models to build tiles for (in priority order)
MODELS_CONFIG = [
    {"id": "hrrr", "max_hours": MAX_HOURS_HRRR, "check_hours": 6},
    {"id": "nam_nest", "max_hours": MAX_HOURS_NAM, "check_hours": 12},
    {"id": "gfs", "max_hours": MAX_HOURS_GFS, "check_hours": 12},
    {"id": "nbm", "max_hours": MAX_HOURS_NBM, "check_hours": 12},
]

# Regions to build
REGIONS = list(repomap.get("TILING_REGIONS", {}).keys())


def check_run_available(model_id: str, date_str: str, init_hour: str) -> bool:
    """Check if a model run is available on NOMADS."""
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return False

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
    npz_path = os.path.join(repomap["TILES_DIR"], region_id, res_dir, model_id, run_id, "t2m.npz")
    if not os.path.exists(npz_path):
        return False
        
    # Check if the run is complete enough
    try:
        import numpy as np
        d = np.load(npz_path)
        hours = d.get('hours', [])
        actual_hours = len(hours)
        
        # If we have at least 80% of expected hours, or it's an old run, consider it done.
        # Otherwise, we might want to retry to pick up new hours as they arrive on NOMADS.
        if actual_hours >= expected_max_hours * 0.8:
            return True
            
        # If the run is older than 6 hours, it's probably as complete as it will ever be
        try:
            # run_id format: run_YYYYMMDD_HH
            parts = run_id.split('_')
            run_dt = datetime.datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if (now - run_dt).total_seconds() > 6 * 3600:
                return True
        except:
            pass
            
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


def build_cycle():
    """Run one build cycle for all models and regions."""
    logger.info("Starting build cycle")
    builds_attempted = 0
    builds_succeeded = 0

    for model_cfg in MODELS_CONFIG:
        model_id = model_cfg["id"]
        max_hours = model_cfg["max_hours"]

        # Get all runs we SHOULD have according to tiered policy (last 72h)
        runs_to_process = get_required_runs(model_id, lookback_hours=72)
        
        if not runs_to_process:
            logger.warning(f"No available runs found for {model_id}")
            continue

        num_runs = len(runs_to_process)
        print(f"Checking {num_runs} required runs for {model_id} (last 72h)...")

        for i, run_id in enumerate(runs_to_process):
            # Skip if run is too new
            if not run_is_ready(run_id):
                print(f"[{model_id} {i+1}/{num_runs}] Skipping {run_id} (too new)")
                continue

            for region_id in REGIONS:
                # Skip if tiles already exist and are complete
                if tiles_exist(region_id, model_id, run_id, expected_max_hours=max_hours):
                    print(f"[{model_id} {i+1}/{num_runs}] Verified complete: {run_id}")
                    continue

                print(f"[{model_id} {i+1}/{num_runs}] Building/Completing {run_id}...")
                builds_attempted += 1
                if build_tiles_for_run(region_id, model_id, run_id, max_hours):
                    builds_succeeded += 1

                # Rate limit between builds
                time.sleep(2)

    print(f"Build cycle complete: {builds_succeeded}/{builds_attempted} succeeded")
    return builds_attempted, builds_succeeded


def cleanup_old_runs(max_runs_to_keep: int = 48):
    """Clean up old tile runs using a tiered retention policy to track evolution:
    - Keep ALL runs for the last 12 hours (high-res evolution)
    - Keep one run every 6 hours for the last 3 days (synoptic evolution)
    - Remove anything older or outside these buckets
    """
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

            now = datetime.datetime.now(datetime.timezone.utc)
            kept_runs = []
            runs_to_remove = []

            # Track which synoptic 6h buckets we've already filled (00, 06, 12, 18)
            # Key: (date, bucket_hour)
            filled_6h_buckets = set()

            for run_id in runs:
                try:
                    # run_id format: run_YYYYMMDD_HH
                    parts = run_id.split('_')
                    run_dt = datetime.datetime.strptime(f"{parts[1]}{parts[2]}", "%Y%m%d%H").replace(tzinfo=datetime.timezone.utc)
                    age_hours = (now - run_dt).total_seconds() / 3600
                    
                    # Tier 1: Keep everything from the last 12 hours
                    if age_hours <= 12:
                        print(f"  [KEEP] {model_id}/{run_id}: Recent (age: {age_hours:.1f}h)")
                        kept_runs.append(run_id)
                        continue
                    
                    # Tier 2: Keep synoptic runs (00, 06, 12, 18) for up to 3 days
                    if age_hours <= 72:
                        init_hour = int(parts[2])
                        bucket = (parts[1], (init_hour // 6) * 6)
                        if init_hour % 6 == 0 and bucket not in filled_6h_buckets:
                            print(f"  [KEEP] {model_id}/{run_id}: Synoptic bucket {bucket[1]}z (age: {age_hours:.1f}h)")
                            kept_runs.append(run_id)
                            filled_6h_buckets.add(bucket)
                            continue

                    # If it doesn't fit a tier, it's a candidate for removal
                    if len(kept_runs) < 5:
                        print(f"  [KEEP] {model_id}/{run_id}: Safety minimum")
                        kept_runs.append(run_id)
                    else:
                        print(f"  [DROP] {model_id}/{run_id}: Outside retention policy")
                        runs_to_remove.append(run_id)
                except Exception as e:
                    if len(kept_runs) < 5:
                        kept_runs.append(run_id)
                    else:
                        runs_to_remove.append(run_id)

            if runs_to_remove:
                logger.info(f"Cleanup summary for {model_id}: Keeping {len(kept_runs)}, Removing {len(runs_to_remove)}")
            
            for old_run in runs_to_remove:
                old_run_dir = os.path.join(model_dir, old_run)
                logger.info(f"Tiered cleanup: Removing old run {old_run}")
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
            build_cycle()

            # Cleanup old runs periodically using tiered policy
            cleanup_old_runs()

        except Exception as e:
            logger.exception(f"Error in build cycle: {e}")

        # Sleep until next cycle
        print(f"Sleeping {BUILD_INTERVAL_MINUTES} minutes until next cycle...")
        time.sleep(BUILD_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    # Support single-run mode via command line arg
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logger.info("Running single build cycle (--once mode)")
        build_cycle()
        cleanup_old_runs()
    else:
        main()
