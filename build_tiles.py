from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from config import repomap
from tiles import build_tiles_for_variable, save_tiles_npz, open_dataset_robust
from utils import download_file, format_forecast_hour
import requests
import xarray as xr
from filelock import FileLock, Timeout
import logging

# Configure debug logging for requests to trace connections
logging.basicConfig(level=logging.INFO)
logging.getLogger("urllib3").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)


def _build_variable_query(variable_config: dict[str, Any]) -> str:
    params = [f"{param}=on" for param in variable_config.get("nomads_params", [])]
    levels = variable_config.get("level_params", [])
    query = "&".join(params + levels)
    return f"{query}&" if query else ""


def _build_model_url(model_config: dict[str, Any], date_str: str, init_hour: str, forecast_hour: str, variable_query: str, location_config: dict[str, Any]) -> str:
    file_name = model_config["file_pattern"].format(init_hour=init_hour, forecast_hour=forecast_hour)
    dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
    return (
        f"{model_config['nomads_url']}?"
        f"file={file_name}&"
        f"dir={dir_path}&"
        f"{variable_query}"
        f"leftlon={location_config['lon_min']}&rightlon={location_config['lon_max']}&"
        f"toplat={location_config['lat_max']}&bottomlat={location_config['lat_min']}&"
    )


def _get_available_model_runs(model_id: str, max_runs: int = 1) -> list[dict[str, str]]:
    model_config = repomap["MODELS"][model_id]
    from datetime import datetime, timedelta, timezone
    import pytz
    now = datetime.now(timezone.utc)
    found: list[dict[str, str]] = []
    for hours_ago in range(0, repomap["HOURS_TO_CHECK_FOR_RUNS"]):
        if len(found) >= max_runs:
            break
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        forecast_hour = format_forecast_hour(1, model_id)
        file_name = model_config["file_pattern"].format(init_hour=init_hour, forecast_hour=forecast_hour)
        dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
        url = (
            f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&{model_config['availability_check_var']}=on"
        )
        try:
            r = requests.head(url, timeout=repomap["HEAD_REQUEST_TIMEOUT_SECONDS"])
            if r.status_code == 200:
                model_time = datetime(check_time.year, check_time.month, check_time.day, int(init_hour), tzinfo=pytz.UTC)
                found.append({
                    'date_str': date_str,
                    'init_hour': init_hour,
                    'init_time': model_time.strftime("%Y-%m-%d %H:%M:%S"),
                    'run_id': f"run_{date_str}_{init_hour}",
                })
        except requests.RequestException:
            continue
    return found


def _fetch_grib(model_id: str, variable_id: str, date_str: str, init_hour: str, forecast_hour: str, location_config: dict[str, Any], run_id: str) -> str:
    model_config = repomap["MODELS"][model_id]
    variable_config = repomap["WEATHER_VARIABLES"][variable_id]
    
    # Use centralized GRIB cache
    run_cache_dir = os.path.join(repomap["GRIB_CACHE_DIR"], model_id, run_id, variable_id)
    os.makedirs(run_cache_dir, exist_ok=True)
    filename = os.path.join(run_cache_dir, f"grib_{forecast_hour}.grib2")

    # Use NOMADS with CONUS region to ensure we match the cache_builder's files
    # location_config is ignored for the fetch region
    download_region = repomap["DOWNLOAD_REGIONS"]["conus"]
    variable_query = _build_variable_query(variable_config)
    url = _build_model_url(model_config, date_str, init_hour, forecast_hour, variable_query, download_region)

    # Cached valid?
    if os.path.exists(filename) and os.path.getsize(filename) >= repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
        try:
            ds = open_dataset_robust(filename)
            ds.close()
            return filename
        except Exception:
            try:
                os.remove(filename)
            except OSError:
                pass
    # Download with temp then verify basic open
    temp_filename = f"{filename}.tmp"
    download_file(url, temp_filename)
    if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
        raise RuntimeError(f"Downloaded file is missing or too small: {temp_filename}")
    ds = open_dataset_robust(temp_filename)
    ds.close()
    with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
        os.replace(temp_filename, filename)
    return filename


def _download_all_hours_parallel(model_id: str, variable_id: str, date_str: str, init_hour: str, run_id: str, max_hours: int) -> dict[int, str]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: dict[int, str] = {}
    digits = repomap["MODELS"][model_id].get("forecast_hour_digits", 2)
    max_workers = repomap["PARALLEL_DOWNLOAD_WORKERS"]
    # Pass a dummy location config since _fetch_grib ignores it now
    dummy_config = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _fetch_grib,
                model_id,
                variable_id,
                date_str,
                init_hour,
                f"{hour:0{digits}d}",
                dummy_config,
                run_id,
            ): hour
            for hour in range(1, max_hours + 1)
        }
        for future in as_completed(futures):
            hour = futures[future]
            try:
                path = future.result()
                results[hour] = path
            except Exception:
                continue
    return results


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
        runs = _get_available_model_runs(model_id, max_runs=1)
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

    # Pseudo location config for regional subset - used for tile bounds, NOT for download
    # (download now uses CONUS)
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
        
        # Check for model-specific exclusions
        if model_id in variable_config.get("model_exclusions", []):
            print(f"Skipping variable {variable_id} for model {model_id} (excluded in config)")
            continue

        # Download GRIBs for all hours for the region (actually CONUS now)
        grib_paths = _download_all_hours_parallel(
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
