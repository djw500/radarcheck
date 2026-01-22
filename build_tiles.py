from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from config import repomap
from tiles import build_tiles_for_variable, save_tiles_npz, open_dataset_robust
from utils import download_file, format_forecast_hour
from cache_builder import get_available_model_runs, download_all_hours_parallel
import requests
import xarray as xr
from filelock import FileLock, Timeout
import logging

# Configure logging - internals go to file (via parent)
logger = logging.getLogger(__name__)

# Suppress noisy external libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("matplotlib").setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.WARNING)


def build_region_tiles(
    region_id: str,
    model_id: str,
    run_id: Optional[str],
    variables: Optional[List[str]] = None,
    resolution_deg: Optional[float] = None,
    max_hours: Optional[int] = None,
    clean_gribs: bool = False,
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
    if max_hours is None:
        max_hours = model_config.get("max_forecast_hours", 24)

    var_ids = variables or list(repomap["WEATHER_VARIABLES"].keys())
    for variable_id in var_ids:
        variable_config = repomap["WEATHER_VARIABLES"][variable_id]
        
        # Check for model-specific exclusions
        if model_id in variable_config.get("model_exclusions", []):
            print(f"Skipping variable {variable_id} for model {model_id} (excluded in config)")
            continue

        # Download GRIBs for all hours using cache_builder logic
        grib_paths = download_all_hours_parallel(
            model_id,
            variable_id,
            run_info["date_str"],
            run_info["init_hour"],
            run_info["run_id"],
            max_hours,
        )
        if not grib_paths:
            continue

        try:
            mins, maxs, means, hours, index_meta = build_tiles_for_variable(
                grib_paths,
                variable_config,
                lat_min,
                lat_max,
                lon_min,
                lon_max,
                res_deg,
            )

            # Compute init_time_utc from run_info
            init_time_utc = None
            if run_info.get("init_time"):
                init_time_utc = run_info["init_time"]
            elif run_info.get("date_str") and run_info.get("init_hour"):
                from datetime import datetime
                try:
                    dt = datetime.strptime(f"{run_info['date_str']}{run_info['init_hour']}", "%Y%m%d%H")
                    init_time_utc = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    pass

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
                "lon_0_360": bool(index_meta.get("lon_0_360", False)),
                "index_lon_min": float(index_meta.get("index_lon_min", lon_min)),
                "init_time_utc": init_time_utc,
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
        except Exception as e:
            print(f"Error building tiles for {variable_id} in {run_info['run_id']}: {e}")
            continue

        if clean_gribs:
            for path in grib_paths.values():
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError as e:
                    print(f"Warning: Failed to clean GRIB {path}: {e}")
            # Try to clean up empty variable directory if possible
            try:
                var_dir = os.path.dirname(list(grib_paths.values())[0])
                os.rmdir(var_dir)
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 0.1° tiles (min/max/mean) for a region")
    parser.add_argument("--region", default="ne", help="Region ID (default: ne)")
    parser.add_argument("--model", default=repomap["DEFAULT_MODEL"], help="Model ID (e.g., hrrr)")
    parser.add_argument("--run", help="Run ID (run_YYYYMMDD_HH). Defaults to latest")
    parser.add_argument("--variables", nargs="*", help="Variable IDs (default: all)")
    parser.add_argument("--resolution", type=float, default=None, help="Grid resolution in degrees (default per region)")
    parser.add_argument("--max-hours", type=int, default=None, help="Optional cap on hours")
    parser.add_argument("--clean-gribs", action="store_true", help="Delete GRIB files after processing to save space")
    args = parser.parse_args()

    os.makedirs(repomap["TILES_DIR"], exist_ok=True)
    build_region_tiles(
        region_id=args.region,
        model_id=args.model,
        run_id=args.run,
        variables=args.variables,
        resolution_deg=args.resolution,
        max_hours=args.max_hours,
        clean_gribs=args.clean_gribs,
    )


if __name__ == "__main__":
    main()