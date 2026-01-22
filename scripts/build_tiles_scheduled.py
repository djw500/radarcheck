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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment with defaults
BUILD_INTERVAL_MINUTES = int(os.environ.get("TILE_BUILD_INTERVAL_MINUTES", "15"))
MAX_HOURS_HRRR = int(os.environ.get("TILE_BUILD_MAX_HOURS_HRRR", "24"))
MAX_HOURS_NAM = int(os.environ.get("TILE_BUILD_MAX_HOURS_NAM", "60"))
MAX_HOURS_GFS = int(os.environ.get("TILE_BUILD_MAX_HOURS_GFS", "168"))

# Models to build tiles for (in priority order)
MODELS_CONFIG = [
    {"id": "hrrr", "max_hours": MAX_HOURS_HRRR, "check_hours": 6},
    {"id": "nam_nest", "max_hours": MAX_HOURS_NAM, "check_hours": 12},
    {"id": "gfs", "max_hours": MAX_HOURS_GFS, "check_hours": 12},
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


def get_latest_run(model_id: str, check_hours: int = 12) -> Optional[str]:
    """Find the latest available run for a model."""
    model_config = repomap["MODELS"].get(model_id)
    if not model_config:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    update_freq = model_config.get("update_frequency_hours", 1)

    for hours_ago in range(check_hours):
        check_time = now - datetime.timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")

        # Skip non-synoptic hours for models with 6-hourly updates
        if update_freq >= 6 and int(init_hour) % 6 != 0:
            continue

        if check_run_available(model_id, date_str, init_hour):
            return f"run_{date_str}_{init_hour}"

    return None


def tiles_exist(region_id: str, model_id: str, run_id: str) -> bool:
    """Check if tiles already exist for a run."""
    # Check for at least t2m tiles as a proxy
    res = repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    tile_path = os.path.join(repomap["TILES_DIR"], region_id, res_dir, model_id, run_id, "t2m.npz")
    return os.path.exists(tile_path)


def build_tiles_for_run(region_id: str, model_id: str, run_id: str, max_hours: int) -> bool:
    """Build tiles for a specific run. Returns True on success."""
    logger.info(f"Building tiles for {model_id}/{run_id} in region {region_id}")

    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build_tiles.py"),
        "--region", region_id,
        "--model", model_id,
        "--run", run_id,
        "--max-hours", str(max_hours),
        "--clean-gribs",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            logger.info(f"Successfully built tiles for {model_id}/{run_id}")
            return True
        else:
            logger.error(f"Failed to build tiles for {model_id}/{run_id}: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout building tiles for {model_id}/{run_id}")
        return False
    except Exception as e:
        logger.error(f"Error building tiles for {model_id}/{run_id}: {e}")
        return False


def build_cycle():
    """Run one build cycle for all models and regions."""
    logger.info("Starting build cycle")
    builds_attempted = 0
    builds_succeeded = 0

    for model_cfg in MODELS_CONFIG:
        model_id = model_cfg["id"]
        max_hours = model_cfg["max_hours"]
        check_hours = model_cfg["check_hours"]

        # Find latest available run
        run_id = get_latest_run(model_id, check_hours)
        if not run_id:
            logger.warning(f"No available run found for {model_id}")
            continue

        logger.info(f"Latest run for {model_id}: {run_id}")

        for region_id in REGIONS:
            # Skip if tiles already exist
            if tiles_exist(region_id, model_id, run_id):
                logger.info(f"Tiles already exist for {model_id}/{run_id} in {region_id}, skipping")
                continue

            builds_attempted += 1
            if build_tiles_for_run(region_id, model_id, run_id, max_hours):
                builds_succeeded += 1

            # Rate limit between builds
            time.sleep(5)

    logger.info(f"Build cycle complete: {builds_succeeded}/{builds_attempted} builds succeeded")
    return builds_attempted, builds_succeeded


def cleanup_old_runs(max_runs_per_model: int = 3):
    """Clean up old tile runs to save disk space."""
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

            runs = sorted(
                [r for r in os.listdir(model_dir) if r.startswith("run_") and os.path.isdir(os.path.join(model_dir, r))],
                reverse=True
            )

            # Keep only the most recent runs
            for old_run in runs[max_runs_per_model:]:
                old_run_dir = os.path.join(model_dir, old_run)
                logger.info(f"Cleaning up old run: {old_run_dir}")
                try:
                    import shutil
                    shutil.rmtree(old_run_dir)
                except Exception as e:
                    logger.error(f"Failed to remove {old_run_dir}: {e}")


def main():
    """Main entry point for scheduled tile building."""
    logger.info("=" * 60)
    logger.info("Scheduled Tile Builder Starting")
    logger.info(f"Build interval: {BUILD_INTERVAL_MINUTES} minutes")
    logger.info(f"Models: {[m['id'] for m in MODELS_CONFIG]}")
    logger.info(f"Regions: {REGIONS}")
    logger.info("=" * 60)

    while True:
        try:
            # Run build cycle
            build_cycle()

            # Cleanup old runs periodically
            cleanup_old_runs(max_runs_per_model=3)

        except Exception as e:
            logger.exception(f"Error in build cycle: {e}")

        # Sleep until next cycle
        logger.info(f"Sleeping {BUILD_INTERVAL_MINUTES} minutes until next cycle...")
        time.sleep(BUILD_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    # Support single-run mode via command line arg
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        logger.info("Running single build cycle (--once mode)")
        build_cycle()
        cleanup_old_runs()
    else:
        main()
