from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict

logger = logging.getLogger("job_worker")

from grib_fetcher import open_as_xarray
from config import repomap, get_tile_resolution
from jobs import cancel_siblings, claim, complete, fail, init_db
from tile_db import init_db as init_tile_db
from tile_db import record_tile_hour, record_tile_run, record_tile_variable
from tiles import build_tiles_for_variable, upsert_tiles_npz

# Synoptic models that must all be loaded before triggering auto-forecast
SYNOPTIC_MODELS = {"gfs", "nam_nest", "ecmwf_hres"}
FORECAST_TRIGGER_FILE = os.path.join(repomap["CACHE_DIR"], "last_forecast_trigger.txt")


def _parse_run_id(run_id: str) -> tuple[str, str]:
    parts = run_id.split("_")
    if len(parts) != 3:
        raise ValueError("run_id must be of the form run_YYYYMMDD_HH")
    return parts[1], parts[2]


def process_build_tile_hour(conn, job: Dict[str, Any]) -> None:
    args = json.loads(job["args_json"])
    region_id = args["region_id"]
    model_id = args["model_id"]
    run_id = args["run_id"]
    variable_id = args["variable_id"]
    forecast_hour = int(args["forecast_hour"])
    resolution_deg = float(
        args.get(
            "resolution_deg",
            get_tile_resolution(region_id, model_id),
        )
    )

    region = repomap["TILING_REGIONS"][region_id]
    lat_min = float(region["lat_min"])
    lat_max = float(region["lat_max"])
    lon_min = float(region["lon_min"])
    lon_max = float(region["lon_max"])

    date_str, init_hour = _parse_run_id(run_id)

    logger.debug(f"  fetching via Herbie: {model_id}/{run_id}/{variable_id} f{forecast_hour}")
    ds = open_as_xarray(model_id, variable_id, date_str, init_hour, forecast_hour)

    variable_config = repomap["WEATHER_VARIABLES"][variable_id]
    try:
        mins, maxs, means, hours, index_meta = build_tiles_for_variable(
            {forecast_hour: ds},
            variable_config,
            lat_min,
            lat_max,
            lon_min,
            lon_max,
            resolution_deg,
        )
    finally:
        ds.close()
        del ds

    init_time_utc = None
    try:
        from datetime import datetime

        dt = datetime.strptime(f"{date_str}{init_hour}", "%Y%m%d%H")
        init_time_utc = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        init_time_utc = None

    meta = {
        "region_id": region_id,
        "model_id": model_id,
        "run_id": run_id,
        "variable_id": variable_id,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
        "resolution_deg": resolution_deg,
        "units": variable_config.get("units"),
        "lon_0_360": bool(index_meta.get("lon_0_360", False)),
        "index_lon_min": float(index_meta.get("index_lon_min", lon_min)),
        "init_time_utc": init_time_utc,
    }

    record_tile_run(conn, region_id, resolution_deg, model_id, run_id, init_time_utc)

    npz_path, merged_hours = upsert_tiles_npz(
        repomap["TILES_DIR"],
        region_id,
        resolution_deg,
        model_id,
        run_id,
        variable_id,
        mins,
        maxs,
        means,
        hours,
        meta,
    )

    try:
        size_bytes = os.path.getsize(npz_path)
    except OSError:
        size_bytes = None

    record_tile_variable(
        conn,
        region_id,
        resolution_deg,
        model_id,
        run_id,
        variable_id,
        npz_path,
        os.path.join(os.path.dirname(npz_path), f"{variable_id}.meta.json"),
        merged_hours,
        size_bytes,
    )

    record_tile_hour(
        conn,
        region_id,
        resolution_deg,
        model_id,
        run_id,
        variable_id,
        forecast_hour,
        npz_path,
        job_id=job["id"],
    )


def _remaining_jobs_for_run(conn, model_id: str, run_id: str) -> int:
    """Count pending + processing jobs for a specific model+run."""
    row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM jobs
        WHERE status IN ('pending', 'processing')
          AND args_json LIKE ?
          AND args_json LIKE ?
        """,
        (f'%"model_id":"{model_id}"%', f'%"run_id":"{run_id}"%'),
    ).fetchone()
    return row["cnt"] if row else 0


def _latest_complete_synoptic_run(conn, model_id: str, init_hour: str) -> str | None:
    """Find the most recent fully-loaded run for a model at a given init hour.

    A run is "fully loaded" if it has 0 pending/processing jobs.
    Looks back 36 hours to find a match.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    for days_back in range(3):
        dt = now - timedelta(days=days_back)
        date_str = dt.strftime("%Y%m%d")
        run_id = f"run_{date_str}_{init_hour}"

        # Check if tiles exist for this run
        res = get_tile_resolution("ne", model_id)
        res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
        run_dir = os.path.join(repomap["TILES_DIR"], "ne", res_dir, model_id, run_id)
        if not os.path.isdir(run_dir):
            continue

        # Check no remaining jobs
        if _remaining_jobs_for_run(conn, model_id, run_id) == 0:
            return run_id

    return None


def _check_and_trigger_forecast(conn, completed_model: str, completed_run_id: str, wlog) -> None:
    """Check if all synoptic models are loaded for a cycle and trigger forecast.

    Called after a synoptic model's job completes. Checks:
    1. Was this the last job for this model+run?
    2. Do all 3 synoptic models have complete runs at the same init hour?
    3. Haven't we already triggered for this cycle?
    """
    if completed_model not in SYNOPTIC_MODELS:
        return

    # 1. Check if this model+run is fully loaded
    remaining = _remaining_jobs_for_run(conn, completed_model, completed_run_id)
    if remaining > 0:
        return

    # Extract init hour from run_id (e.g., "run_20260224_12" → "12")
    try:
        init_hour = completed_run_id.split("_")[2]
    except (IndexError, ValueError):
        return

    wlog.info(f"Synoptic run complete: {completed_model}/{completed_run_id} — checking other models")

    # 2. Check all 3 synoptic models have complete runs at this init hour
    cycle_runs = {}
    for model in SYNOPTIC_MODELS:
        run = _latest_complete_synoptic_run(conn, model, init_hour)
        if run is None:
            wlog.info(f"  {model} has no complete {init_hour}Z run yet — not triggering")
            return
        cycle_runs[model] = run

    # 3. Dedup: build a cycle ID from the newest run date
    cycle_id = f"{init_hour}Z_" + "_".join(sorted(cycle_runs.values()))
    try:
        if os.path.exists(FORECAST_TRIGGER_FILE):
            with open(FORECAST_TRIGGER_FILE) as f:
                last_trigger = f.read().strip()
            if last_trigger == cycle_id:
                wlog.info(f"  Already triggered forecast for cycle {cycle_id}")
                return
    except Exception:
        pass

    # All conditions met — trigger forecast
    wlog.info(f"All synoptic models loaded for {init_hour}Z cycle: {cycle_runs}")
    wlog.info(f"Triggering auto-forecast...")

    # Write trigger file BEFORE spawning to prevent double-trigger
    try:
        os.makedirs(os.path.dirname(FORECAST_TRIGGER_FILE), exist_ok=True)
        with open(FORECAST_TRIGGER_FILE, "w") as f:
            f.write(cycle_id)
    except Exception as e:
        wlog.error(f"Failed to write trigger file: {e}")

    # Spawn forecast in background (fire and forget)
    try:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "run-forecast.sh")
        # Strip CLAUDECODE from env so nested claude -p works
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        subprocess.Popen(
            ["bash", script],
            stdout=open(os.path.join(repomap["CACHE_DIR"], "forecast_run.log"), "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        wlog.info("Forecast script spawned in background")
    except Exception as e:
        wlog.error(f"Failed to spawn forecast script: {e}")


def run_worker(worker_id: str | None = None, poll_interval_s: float = 5.0, once: bool = False, model_id: str | None = None, max_jobs: int = 0) -> None:
    """Poll the job queue and process jobs until empty (or forever if not once)."""
    if worker_id is None:
        suffix = f"-{model_id}" if model_id else ""
        worker_id = f"worker-{os.getpid()}{suffix}"

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        handlers=[logging.StreamHandler()],
    )
    # Suppress noisy libs
    for _noisy in ["urllib3", "requests", "matplotlib", "cfgrib", "fiona", "rasterio", "herbie"]:
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    logging.getLogger("cfgrib").setLevel(logging.ERROR)

    wlog = logging.getLogger(f"worker.{worker_id}")
    wlog.info(f"Worker {worker_id} starting (poll_interval={poll_interval_s}s, model_filter={model_id or 'all'})")

    conn = init_tile_db(repomap["DB_PATH"])
    processed = 0
    try:
        while True:
            job = claim(conn, worker_id, model_id=model_id)
            if job is None:
                if once:
                    wlog.info(f"No jobs available, exiting (--once). Processed {processed} total.")
                    break
                time.sleep(poll_interval_s)
                continue

            args = json.loads(job["args_json"]) if isinstance(job.get("args_json"), str) else job.get("args_json", {})
            job_label = (
                f"{args.get('model_id')}/{args.get('run_id')}/{args.get('variable_id')} f{args.get('forecast_hour')}"
                if args else job["type"]
            )

            t0 = time.monotonic()
            try:
                if job["type"] == "build_tile_hour":
                    wlog.info(f"Job {job['id']}: {job_label}")
                    process_build_tile_hour(conn, job)
                    complete(conn, job["id"])
                    processed += 1
                    elapsed = time.monotonic() - t0
                    wlog.info(f"Job {job['id']} done in {elapsed:.1f}s ({processed} total)")
                    # Check if all synoptic models are loaded → auto-trigger forecast
                    _check_and_trigger_forecast(
                        conn, args.get("model_id", ""), args.get("run_id", ""), wlog
                    )
                else:
                    fail(conn, job["id"], f"Unsupported job type: {job['type']}")
                    wlog.warning(f"Job {job['id']} unsupported type: {job['type']}")
            except Exception as exc:
                elapsed = time.monotonic() - t0
                error_str = str(exc)
                wlog.error(f"Job {job['id']} FAILED after {elapsed:.1f}s ({job_label}): {error_str}")
                fail(conn, job["id"], error_str)
                # Cancel siblings when the whole model run is unavailable
                # (e.g. run published but hours not yet posted).
                run_unavailable = "GRIB2 file not found" in error_str or "not found" in error_str.lower()
                if run_unavailable:
                    cancelled = cancel_siblings(conn, job)
                    if cancelled:
                        wlog.info(f"Cancelled {cancelled} sibling jobs — run data not available")

            gc.collect()

            if once:
                break
            if max_jobs and processed >= max_jobs:
                wlog.info(f"Reached max_jobs={max_jobs}, exiting for memory cleanup")
                break
    finally:
        conn.close()
        wlog.info(f"Worker {worker_id} shut down. Processed {processed} jobs.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tile job worker")
    parser.add_argument("--once", action="store_true", help="Process a single job and exit")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--model", type=str, default=None, help="Only process jobs for this model_id")
    parser.add_argument("--max-jobs", type=int, default=0, help="Exit after N jobs for memory cleanup (0=unlimited)")
    parser.add_argument("--log-file", type=str, help="Log file path (default: stdout only)")
    args = parser.parse_args()

    if args.log_file:
        import logging as _logging
        fh = _logging.FileHandler(args.log_file)
        fh.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _logging.getLogger().addHandler(fh)

    run_worker(poll_interval_s=args.poll_interval, once=args.once, model_id=args.model, max_jobs=args.max_jobs)
