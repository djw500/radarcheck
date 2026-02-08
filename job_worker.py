from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

from cache_builder import fetch_grib
from config import repomap
from jobs import claim, fail, init_db
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


def run_worker(poll_interval_s: float = 5.0, once: bool = False) -> None:
    conn = init_tile_db(repomap["JOBS_DB_PATH"])
    try:
        while True:
            job = claim(conn, "tile-worker")
            if job is None:
                if once:
                    break
                time.sleep(poll_interval_s)
                continue

            try:
                if job["type"] == "build_tile_hour":
                    process_build_tile_hour(conn, job)
                    conn.commit()
                else:
                    fail(conn, job["id"], f"Unsupported job type: {job['type']}")
            except Exception as exc:
                fail(conn, job["id"], str(exc))
            if once:
                break
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tile job worker")
    parser.add_argument("--once", action="store_true", help="Process a single job and exit")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()
    run_worker(poll_interval_s=args.poll_interval, once=args.once)
