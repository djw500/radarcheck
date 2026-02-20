from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict

logger = logging.getLogger("job_worker")

from cache_builder import fetch_grib
from config import repomap
from jobs import cancel_siblings, claim, complete, fail, init_db
from tile_db import init_db as init_tile_db
from tile_db import record_tile_hour, record_tile_run, record_tile_variable
from tiles import build_tiles_for_variable, upsert_tiles_npz
from utils import format_forecast_hour


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
            repomap["TILING_REGIONS"][region_id].get("default_resolution_deg", 0.1),
        )
    )

    region = repomap["TILING_REGIONS"][region_id]
    lat_min = float(region["lat_min"])
    lat_max = float(region["lat_max"])
    lon_min = float(region["lon_min"])
    lon_max = float(region["lon_max"])

    date_str, init_hour = _parse_run_id(run_id)
    forecast_hour_str = format_forecast_hour(forecast_hour, model_id)
    logger.debug(f"  fetching GRIB: {model_id}/{run_id}/{variable_id} f{forecast_hour}")
    grib_path = fetch_grib(
        model_id,
        variable_id,
        date_str,
        init_hour,
        forecast_hour_str,
        run_id,
        use_hourly=True,
    )

    variable_config = repomap["WEATHER_VARIABLES"][variable_id]
    mins, maxs, means, hours, index_meta = build_tiles_for_variable(
        {forecast_hour: grib_path},
        variable_config,
        lat_min,
        lat_max,
        lon_min,
        lon_max,
        resolution_deg,
    )

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


def run_worker(worker_id: str | None = None, poll_interval_s: float = 5.0, once: bool = False) -> None:
    """Poll the job queue and process jobs until empty (or forever if not once)."""
    if worker_id is None:
        worker_id = f"worker-{os.getpid()}"

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
    wlog.info(f"Worker {worker_id} starting (poll_interval={poll_interval_s}s)")

    conn = init_tile_db(repomap["DB_PATH"])
    processed = 0
    try:
        while True:
            job = claim(conn, worker_id)
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
                else:
                    fail(conn, job["id"], f"Unsupported job type: {job['type']}")
                    wlog.warning(f"Job {job['id']} unsupported type: {job['type']}")
            except Exception as exc:
                elapsed = time.monotonic() - t0
                wlog.error(f"Job {job['id']} FAILED after {elapsed:.1f}s ({job_label}): {exc}")
                fail(conn, job["id"], str(exc))
                cancelled = cancel_siblings(conn, job)
                if cancelled:
                    wlog.info(f"Cancelled {cancelled} sibling jobs for same run")

            if once:
                break
    finally:
        conn.close()
        wlog.info(f"Worker {worker_id} shut down. Processed {processed} jobs.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tile job worker")
    parser.add_argument("--once", action="store_true", help="Process a single job and exit")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel worker processes")
    parser.add_argument("--log-file", type=str, help="Log file path (default: stdout only)")
    args = parser.parse_args()

    if args.log_file:
        import logging as _logging
        fh = _logging.FileHandler(args.log_file)
        fh.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        _logging.getLogger().addHandler(fh)

    if args.workers > 1 and not args.once:
        import multiprocessing
        procs = []
        for i in range(args.workers):
            wid = f"worker-{os.getpid()}-{i}"
            p = multiprocessing.Process(
                target=run_worker,
                kwargs={"worker_id": wid, "poll_interval_s": args.poll_interval},
                name=wid,
                daemon=True,
            )
            p.start()
            procs.append(p)
        print(f"Started {args.workers} worker processes: {[p.pid for p in procs]}")
        try:
            for p in procs:
                p.join()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
    else:
        run_worker(poll_interval_s=args.poll_interval, once=args.once)
