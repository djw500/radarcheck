import time
import json
import socket
import os
import threading
import traceback
import logging
import hashlib
from datetime import datetime, timedelta, timezone

from jobs import init_db, claim, complete, fail, recover_stale, prune_completed
from config import repomap
from utils import format_forecast_hour
from cache_builder import (
    fetch_grib,
    get_run_forecast_hours,
    download_all_hours_parallel,
    detect_hourly_support
)
from scripts.build_tiles_scheduled import cleanup_old_gribs, cleanup_old_runs
# We need to import build_region_tiles carefully to avoid circular imports if it imports worker?
# It doesn't.
from build_tiles import build_region_tiles

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("worker")

class RetryLater(Exception):
    pass

def run_worker(db_path: str = None, poll_interval: float = 2.0):
    if db_path is None:
        db_path = repomap.get("JOBS_DB_PATH", "cache/jobs.db")

    conn = init_db(db_path)
    # Recover stale jobs on startup
    recovered = recover_stale(conn)
    if recovered:
        logger.info(f"Recovered {recovered} stale jobs")

    worker_id = f"{socket.gethostname()}-{os.getpid()}-{threading.current_thread().name}"
    logger.info(f"Worker {worker_id} started")

    while True:
        try:
            job = claim(conn, worker_id)
            if not job:
                time.sleep(poll_interval)
                continue

            logger.info(f"Claimed job {job['id']} ({job['type']})")

            try:
                execute_job(job, conn)
                complete(conn, job["id"])
                logger.info(f"Completed job {job['id']}")
            except RetryLater as e:
                logger.info(f"Requeuing job {job['id']}: {e}")
                # Use fail to reschedule
                fail(conn, job["id"], str(e), max_retries=1000)
            except Exception as e:
                logger.error(f"Failed job {job['id']}: {e}", exc_info=True)
                fail(conn, job["id"], str(e) + "\n" + traceback.format_exc())

        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            time.sleep(5)

def execute_job(job: dict, conn):
    args = job["args"]
    if job["type"] == "ingest_grib":
        execute_ingest_grib(args)
    elif job["type"] == "build_tile":
        execute_build_tile(args, conn)
    elif job["type"] == "cleanup":
        execute_cleanup(args, conn)
    else:
        raise ValueError(f"Unknown job type: {job['type']}")

def execute_ingest_grib(args: dict):
    model_id = args["model_id"]
    forecast_hour_int = args["forecast_hour"]
    date_str = args["date_str"]
    init_hour = args["init_hour"]

    forecast_hour_str = format_forecast_hour(forecast_hour_int, model_id)

    # Determine use_hourly
    model_cfg = repomap["MODELS"][model_id]
    max_hours = model_cfg.get("max_forecast_hours", 24) # approx check
    hourly_first = int(model_cfg.get("hourly_override_first_hours", 0) or 0)
    use_hourly = False
    if hourly_first > 0 and forecast_hour_int <= hourly_first:
        if detect_hourly_support(model_id, date_str, init_hour):
            use_hourly = True

    fetch_grib(
        model_id=model_id,
        variable_id=args["variable_id"],
        date_str=date_str,
        init_hour=init_hour,
        forecast_hour=forecast_hour_str,
        run_id=args["run_id"],
        use_hourly=use_hourly
    )

def execute_build_tile(args: dict, conn):
    model_id = args["model_id"]
    run_id = args["run_id"]
    variable_id = args["variable_id"]
    region_id = args["region_id"]

    parts = run_id.split('_')
    date_str = parts[1]
    init_hour = parts[2]

    # Determine max hours
    model_config = repomap["MODELS"][model_id]
    max_hours = model_config.get("max_forecast_hours", 24)
    if "max_hours_by_init" in model_config:
        max_hours = model_config["max_hours_by_init"].get(init_hour, model_config["max_hours_by_init"].get("default", max_hours))

    expected_hours = get_run_forecast_hours(model_id, date_str, init_hour, max_hours)

    # Check dependencies via DB
    completed_count = 0
    for h in expected_hours:
        ingest_args = {
            "model_id": model_id,
            "variable_id": variable_id,
            "date_str": date_str,
            "init_hour": init_hour,
            "forecast_hour": h,
            "run_id": run_id
        }
        args_json = json.dumps(ingest_args, sort_keys=True)
        args_hash = hashlib.sha256(f"ingest_grib{args_json}".encode()).hexdigest()

        row = conn.execute("SELECT status FROM jobs WHERE type='ingest_grib' AND args_hash=?", (args_hash,)).fetchone()
        if row and row['status'] == 'completed':
            completed_count += 1

    if completed_count < len(expected_hours):
        raise RetryLater(f"Waiting for ingest: {completed_count}/{len(expected_hours)} ready")

    # Build tiles
    build_region_tiles(
        region_id=region_id,
        model_id=model_id,
        run_id=run_id,
        variables=[variable_id],
        resolution_deg=None,
        max_hours=max_hours,
        clean_gribs=False,
        audit_only=False
    )

def execute_cleanup(args: dict, conn):
    target = args.get("target")
    if target == "gribs":
        cleanup_old_gribs()
    elif target == "runs":
        cleanup_old_runs()
    elif target == "db":
        prune_completed(conn, max_age_hours=72)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as e:
            logger.warning(f"WAL checkpoint failed: {e}")

if __name__ == "__main__":
    run_worker()
