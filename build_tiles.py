from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from config import repomap
from cache_builder import (
    get_available_model_runs,
    download_all_hours_parallel,
)
from tiles import build_tiles_for_variable, save_tiles_npz


def build_region_tiles(
    region_id: str,
    model_id: str,
    run_id: Optional[str],
    variables: Optional[List[str]] = None,
    resolution_deg: Optional[float] = None,
    max_hours: Optional[int] = None,
) -> None:
    regions = repomap["TILING_REGIONS"]
    if region_id not in regions:
        raise SystemExit(f"Unknown region_id '{region_id}'. Known: {list(regions.keys())}")

    region = regions[region_id]
    res_deg = resolution_deg or region.get("default_resolution_deg", 0.1)
    lat_min = float(region["lat_min"])
    lat_max = float(region["lat_max"])
    lon_min = float(region["lon_min"])
    lon_max = float(region["lon_max"])

    # Determine run
    if run_id is None:
        runs = get_available_model_runs(model_id, max_runs=1)
        if not runs:
            raise SystemExit(f"No recent runs available for {model_id}")
        run_info = runs[0]
    else:
        # Interpret run_id as run_YYYYMMDD_HH
        if not run_id.startswith("run_"):
            raise SystemExit("run_id must be of the form 'run_YYYYMMDD_HH'")
        date_str, init_hour = run_id.split("_")[1:]
        run_info = {
            "date_str": date_str,
            "init_hour": init_hour,
            "init_time": "",
            "run_id": run_id,
        }

    model_config = repomap["MODELS"][model_id]
    digits = model_config.get("forecast_hour_digits", 2)
    if max_hours is None:
        max_hours = model_config.get("max_forecast_hours", 24)

    # Pseudo location config for regional subset
    location_config = {
        "id": f"region_{region_id}",
        "name": f"Region {region_id}",
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
        # Unused by tiling, but present for compatibility
        "center_lat": (lat_min + lat_max) / 2.0,
        "center_lon": (lon_min + lon_max) / 2.0,
        "zoom": max(lat_max - lat_min, (lon_max - lon_min) / 1.3) / 2.0,
    }

    var_ids = variables or list(repomap["WEATHER_VARIABLES"].keys())
    for variable_id in var_ids:
        variable_config = repomap["WEATHER_VARIABLES"][variable_id]
        # Download GRIBs for all hours for the region
        grib_paths = download_all_hours_parallel(
            model_id,
            variable_id,
            run_info["date_str"],
            run_info["init_hour"],
            location_config,
            run_info["run_id"],
            max_hours,
        )
        if not grib_paths:
            continue

        mins, maxs, means, hours = build_tiles_for_variable(
            grib_paths,
            variable_config,
            lat_min,
            lat_max,
            lon_min,
            lon_max,
            res_deg,
        )

        meta = {
            "region_id": region_id,
            "model_id": model_id,
            "run_id": run_info["run_id"],
            "variable_id": variable_id,
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lon_min": lon_min,
            "lon_max": lon_max,
            "resolution_deg": res_deg,
            "units": variable_config.get("units"),
        }

        out_path = save_tiles_npz(
            repomap["TILES_DIR"],
            region_id,
            res_deg,
            model_id,
            run_info["run_id"],
            variable_id,
            mins,
            maxs,
            means,
            hours,
            meta,
        )
        print(f"Saved {variable_id} tiles to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 0.1° tiles (min/max/mean) for a region")
    parser.add_argument("--region", default="ne", help="Region ID (default: ne)")
    parser.add_argument("--model", default=repomap["DEFAULT_MODEL"], help="Model ID (e.g., hrrr)")
    parser.add_argument("--run", help="Run ID (run_YYYYMMDD_HH). Defaults to latest")
    parser.add_argument("--variables", nargs="*", help="Variable IDs (default: all)")
    parser.add_argument("--resolution", type=float, default=None, help="Grid resolution in degrees (default per region)")
    parser.add_argument("--max-hours", type=int, default=None, help="Optional cap on hours")
    args = parser.parse_args()

    os.makedirs(repomap["TILES_DIR"], exist_ok=True)
    build_region_tiles(
        region_id=args.region,
        model_id=args.model,
        run_id=args.run,
        variables=args.variables,
        resolution_deg=args.resolution,
        max_hours=args.max_hours,
    )


if __name__ == "__main__":
    main()

