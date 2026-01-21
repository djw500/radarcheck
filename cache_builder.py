from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import random

import pytz
import requests
import xarray as xr
from filelock import FileLock, Timeout

import geopandas as gpd
import numpy as np
import psutil

from config import repomap
from plotting import create_plot, select_variable_from_dataset
from utils import (
    GribDownloadError,
    GribValidationError,
    PlotGenerationError,
    convert_units,
    download_file,
    format_forecast_hour,
    fetch_county_shapefile,
)

# Set up logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add file handler and attach to both module logger and root so submodules log here too
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler('logs/cache_builder.log', maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s'
))
logger.addHandler(file_handler)
# Ensure other module loggers (e.g., plotting, utils) also write to this file
root_logger = logging.getLogger()
root_logger.addHandler(file_handler)

def log_memory_usage(context: str = "") -> None:
    """Log current memory usage."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    # Convert to MB
    rss_mb = mem_info.rss / 1024 / 1024
    logger.info(f"Memory Usage [{context}]: {rss_mb:.2f} MB")

def build_variable_query(variable_config: dict[str, Any]) -> str:
    params = [f"{param}=on" for param in variable_config.get("nomads_params", [])]
    levels = variable_config.get("level_params", [])
    query = "&".join(params + levels)
    if query:
        return f"{query}&"
    return ""


def build_model_url(
    model_config: dict[str, Any],
    date_str: str,
    init_hour: str,
    forecast_hour: str,
    variable_query: str,
    location_config: dict[str, Any],
) -> str:
    file_name = model_config["file_pattern"].format(
        init_hour=init_hour,
        forecast_hour=forecast_hour,
    )
    dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
    return (
        f"{model_config['nomads_url']}?"
        f"file={file_name}&"
        f"dir={dir_path}&"
        f"{variable_query}"
        f"leftlon={location_config['lon_min']}&rightlon={location_config['lon_max']}&"
        f"toplat={location_config['lat_max']}&bottomlat={location_config['lat_min']}&"
    )


def get_available_model_runs(model_id: str, max_runs: int = 5) -> list[dict[str, str]]:
    """Find multiple recent model runs available, from newest to oldest."""
    model_config = repomap["MODELS"][model_id]
    now = datetime.now(timezone.utc)
    available_runs = []
    
    # Check the last 24 hours of potential runs
    for hours_ago in range(0, repomap["HOURS_TO_CHECK_FOR_RUNS"]):
        # Stop once we have enough runs
        if len(available_runs) >= max_runs:
            break
            
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        
        # Check if the file exists using the filter URL
        forecast_hour = format_forecast_hour(1, model_id)
        file_name = model_config["file_pattern"].format(
            init_hour=init_hour,
            forecast_hour=forecast_hour,
        )
        dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
        url = (
            f"{model_config['nomads_url']}?"
            f"file={file_name}&"
            f"dir={dir_path}&"
            f"{model_config['availability_check_var']}=on"
        )
        
        try:
            response = requests.head(url, timeout=repomap["HEAD_REQUEST_TIMEOUT_SECONDS"])
            if response.status_code == 200:
                model_time = datetime(
                    year=check_time.year,
                    month=check_time.month,
                    day=check_time.day,
                    hour=int(init_hour),
                    minute=0,
                    second=0,
                    tzinfo=pytz.UTC
                )
                
                run_info = {
                    'date_str': date_str,
                    'init_hour': init_hour,
                    'init_time': model_time.strftime("%Y-%m-%d %H:%M:%S"),
                    'run_id': f"run_{date_str}_{init_hour}"
                }
                
                available_runs.append(run_info)
                logger.info(f"Found available {model_id} run: {run_info['run_id']}")
        except (requests.RequestException, requests.Timeout) as exc:
            logger.warning(f"Error checking run from {hours_ago} hours ago: {str(exc)}")
    
    if not available_runs:
        raise GribDownloadError(f"Could not find any recent {model_id} runs")
        
    return available_runs

def get_latest_model_run(model_id: str) -> tuple[str, str, str]:
    """Find the most recent model run available."""
    runs = get_available_model_runs(model_id, max_runs=1)
    if runs:
        run = runs[0]
        return run['date_str'], run['init_hour'], run['init_time']
    raise GribDownloadError(f"Could not find a recent {model_id} run")


def get_available_hrrr_runs(max_runs: int = 5) -> list[dict[str, str]]:
    """Backward-compatible wrapper for HRRR runs."""
    return get_available_model_runs("hrrr", max_runs=max_runs)


def get_latest_hrrr_run() -> tuple[str, str, str]:
    """Backward-compatible wrapper for the latest HRRR run."""
    return get_latest_model_run("hrrr")

def fetch_grib(*args: Any, **kwargs: Any) -> str:
    """Download and cache the GRIB file for a specific forecast hour and location."""
    if len(args) == 5 and not kwargs:
        date_str, init_hour, forecast_hour, location_config, run_id = args
        model_id = repomap["DEFAULT_MODEL"]
        variable_id = repomap["DEFAULT_VARIABLE"]
    else:
        model_id, variable_id, date_str, init_hour, forecast_hour, location_config, run_id = args

    model_config = repomap["MODELS"][model_id]
    variable_config = repomap["WEATHER_VARIABLES"][variable_id]
    variable_query = build_variable_query(variable_config)
    url = build_model_url(
        model_config,
        date_str,
        init_hour,
        forecast_hour,
        variable_query,
        location_config,
    )

    location_id = location_config['id']
    run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id, variable_id)
    os.makedirs(run_cache_dir, exist_ok=True)

    filename = os.path.join(run_cache_dir, f"grib_{forecast_hour}.grib2")
    
    def try_load_grib(filename: str) -> bool:
        """Try to load and validate a GRIB file"""
        if not os.path.exists(filename) or os.path.getsize(filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
            return False
        try:
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                # Try to open the file without chunks first
                ds = xr.open_dataset(filename, engine="cfgrib")
                data_to_plot = select_variable_from_dataset(ds, variable_config)
                data_to_plot.values
                ds.close()
                return True
        except Timeout as exc:
            logger.error(f"Timeout acquiring lock for {filename}: {exc}")
            raise GribValidationError(f"Lock timeout for {filename}") from exc
        except Exception as exc:
            if "End of resource reached when reading message" in str(exc):
                logger.error(f"GRIB file corrupted (premature EOF): {filename}")
            else:
                logger.warning(f"GRIB file invalid: {filename}, Error: {str(exc)}")
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                        logger.info(f"Deleted invalid file: {filename}")
                    # Also clean up any partial downloads
                    if os.path.exists(f"{filename}.tmp"):
                        os.remove(f"{filename}.tmp")
                except OSError as exc:
                    logger.error(f"Error cleaning up invalid files: {str(exc)}")
            return False

    # Try to use cached file
    if try_load_grib(filename):
        logger.info(f"Using cached valid GRIB file: {filename}")
        return filename

    # Try downloading up to 3 times
    for attempt in range(repomap["MAX_DOWNLOAD_RETRIES"]):
        logger.info(
            "Downloading GRIB file from: %s (attempt %s/%s)",
            url,
            attempt + 1,
            repomap["MAX_DOWNLOAD_RETRIES"],
        )
        try:
            temp_filename = f"{filename}.tmp"
            download_file(url, temp_filename)
            
            # Verify the temporary file
            if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
                raise ValueError(f"Downloaded file is missing or too small: {temp_filename}")
            
            # Try to open with xarray to verify it's valid
            ds = xr.open_dataset(temp_filename, engine="cfgrib")
            data_to_plot = select_variable_from_dataset(ds, variable_config)
            data_to_plot.load()
            ds.close()
            
            # If verification passed, move the file into place atomically
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                os.replace(temp_filename, filename)
                logger.info(f"Successfully downloaded and verified GRIB file: {filename}")
                return filename
                
        except Timeout as exc:
            logger.error(f"Download attempt {attempt + 1} failed: {str(exc)}", exc_info=True)
            raise GribValidationError(f"Lock timeout for {filename}") from exc
        except Exception as exc:
            logger.error(f"Download attempt {attempt + 1} failed: {str(exc)}", exc_info=True)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except OSError:
                    pass
            if attempt < repomap["MAX_DOWNLOAD_RETRIES"] - 1:
                # Exponential backoff with jitter
                base = repomap["RETRY_DELAY_SECONDS"]
                delay = base * (2 ** attempt) + random.uniform(0, 1)
                logger.info(f"Retrying in {delay:.2f}s (attempt {attempt + 2}/{repomap['MAX_DOWNLOAD_RETRIES']})")
                time.sleep(delay)
    
    raise GribDownloadError("Failed to obtain valid GRIB file after retries")


def download_all_hours_parallel(
    model_id: str,
    variable_id: str,
    date_str: str,
    init_hour: str,
    location_config: dict[str, Any],
    run_id: str,
    max_hours: int,
) -> dict[int, str]:
    """Download GRIB files in parallel using a thread pool."""
    results: dict[int, str] = {}
    max_workers = repomap["PARALLEL_DOWNLOAD_WORKERS"]
    model_config = repomap["MODELS"][model_id]
    digits = model_config.get("forecast_hour_digits", 2)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_grib,
                model_id,
                variable_id,
                date_str,
                init_hour,
                f"{hour:0{digits}d}",
                location_config,
                run_id,
            ): hour
            for hour in range(1, max_hours + 1)
        }
        for future in as_completed(futures):
            hour = futures[future]
            try:
                results[hour] = future.result()
            except (GribDownloadError, GribValidationError, requests.RequestException) as exc:
                logger.error(f"Failed to download hour {hour}: {exc}")
    return results

def extract_center_value(
    grib_path: str,
    center_lat: float,
    center_lon: float,
    variable_config: dict[str, Any],
) -> tuple[Optional[float], Optional[str]]:
    """Extract the forecast value at the center point from a GRIB file.

    Handles both 1D indexed coordinates and 2D coordinate arrays (e.g., Lambert
    conformal projection used by HRRR).
    """
    ds = None
    try:
        ds = xr.open_dataset(grib_path, engine="cfgrib")
        data = select_variable_from_dataset(ds, variable_config)
        conversion = variable_config.get("conversion")
        if conversion:
            data = convert_units(data, conversion)

        target_lon = center_lon
        lon_min = float(data.longitude.min())
        lon_max = float(data.longitude.max())
        if lon_min >= 0 and center_lon < 0:
            target_lon = center_lon + 360
        elif lon_max > 180 and center_lon < 0:
            target_lon = center_lon + 360

        # Check if coordinates are 2D (projected data like HRRR Lambert conformal)
        if data.latitude.ndim == 2:
            # Find nearest point using distance calculation on 2D arrays
            lat_diff = data.latitude.values - center_lat
            lon_diff = data.longitude.values - target_lon
            distance = np.sqrt(lat_diff**2 + lon_diff**2)
            min_idx = np.unravel_index(np.argmin(distance), distance.shape)
            center_value = data.values[min_idx]
        else:
            # Standard 1D indexed coordinates
            center_value = data.sel(
                latitude=center_lat,
                longitude=target_lon,
                method="nearest"
            ).values

        value = float(center_value)
        if np.isnan(value):
            return None, data.attrs.get("units")
        return value, variable_config.get("units") or data.attrs.get("units")
    finally:
        if ds is not None:
            ds.close()

def generate_forecast_images(
    location_config: dict[str, Any],
    counties: gpd.GeoDataFrame,
    model_id: str,
    run_info: Optional[dict[str, str]] = None,
    variable_ids: Optional[list[str]] = None,
) -> bool:
    """Generate forecast images for a specific location and model run."""
    try:
        location_id = location_config['id']
        model_config = repomap["MODELS"][model_id]
        variable_ids = variable_ids or list(repomap["WEATHER_VARIABLES"].keys())
        tile_helpers = None
        if repomap["GENERATE_MAP_TILES"] or repomap["GENERATE_VECTOR_CONTOURS"]:
            from tile_generator import generate_tiles, generate_vector_contours, grib_to_geotiff, save_geojson

            tile_helpers = {
                "generate_tiles": generate_tiles,
                "generate_vector_contours": generate_vector_contours,
                "grib_to_geotiff": grib_to_geotiff,
                "save_geojson": save_geojson,
            }
        
        # If no specific run provided, get the latest
        if run_info is None:
            date_str, init_hour, init_time = get_latest_model_run(model_id)
            run_id = f"run_{date_str}_{init_hour}"
        else:
            date_str = run_info['date_str']
            init_hour = run_info['init_hour']
            init_time = run_info['init_time']
            run_id = run_info['run_id']
            
        logger.info(f"Processing {model_id} run {run_id} for {location_config['name']}")
        
        # Create run-specific cache directory
        run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
        os.makedirs(run_cache_dir, exist_ok=True)
        
        metadata_payload = {
            "version": 1,
            "date_str": date_str,
            "init_hour": init_hour,
            "init_time": init_time,
            "run_id": run_id,
            "model_id": model_id,
            "model_name": model_config["name"],
            "location": {
                "name": location_config["name"],
                "center_lat": location_config["center_lat"],
                "center_lon": location_config["center_lon"],
                "zoom": location_config["zoom"],
            },
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        metadata_json_path = os.path.join(run_cache_dir, "metadata.json")
        with open(metadata_json_path, "w") as f:
            json.dump(metadata_payload, f, indent=2)

        # Legacy metadata for backward compatibility
        metadata_path = os.path.join(run_cache_dir, "metadata.txt")
        with open(metadata_path, "w") as f:
            f.write(f"date_str={date_str}\n")
            f.write(f"init_hour={init_hour}\n")
            f.write(f"init_time={init_time}\n")
            f.write(f"run_id={run_id}\n")
            f.write(f"model_id={model_id}\n")
            f.write(f"model_name={model_config['name']}\n")
            f.write(f"location_name={location_config['name']}\n")
            f.write(f"center_lat={location_config['center_lat']}\n")
            f.write(f"center_lon={location_config['center_lon']}\n")
            f.write(f"zoom={location_config['zoom']}\n")

        # Download and process each forecast hour
        max_hours = model_config["max_forecast_hours"]
        for variable_id in variable_ids:
            variable_config = repomap["WEATHER_VARIABLES"][variable_id]
            valid_times = []
            center_values = []
            grib_paths = download_all_hours_parallel(
                model_id,
                variable_id,
                date_str,
                init_hour,
                location_config,
                run_id,
                max_hours,
            )
            for hour in range(1, max_hours + 1):
                hour_str = format_forecast_hour(hour, model_id)
                logger.info(
                    f"Processing {variable_id} hour {hour_str} for {location_config['name']} (run {run_id})"
                )

                try:
                    # Fetch GRIB file
                    grib_path = grib_paths.get(hour)
                    if not grib_path:
                        raise GribDownloadError(f"Missing GRIB for hour {hour_str}")

                    # Calculate valid time
                    init_dt = datetime.strptime(init_time, "%Y-%m-%d %H:%M:%S")
                    if not init_dt.tzinfo:
                        init_dt = pytz.UTC.localize(init_dt)
                    valid_time = init_dt + timedelta(hours=hour)
                    valid_time_str = valid_time.strftime("%Y-%m-%d %H:%M:%S")

                    variable_cache_dir = os.path.join(run_cache_dir, variable_id)
                    os.makedirs(variable_cache_dir, exist_ok=True)
                    image_path = os.path.join(variable_cache_dir, f"frame_{hour_str}.png")

                    # Check if image already exists and is valid
                    if (
                        os.path.exists(image_path)
                        and os.path.getsize(image_path) > repomap["MIN_PNG_FILE_SIZE_BYTES"]
                    ):
                        logger.info(f"Skipping existing frame: {image_path}")
                    else:
                        if repomap["GENERATE_STATIC_IMAGES"]:
                            # Generate plot
                            image_buffer = create_plot(
                                grib_path,
                                init_time,
                                hour_str,
                                repomap["CACHE_DIR"],
                                variable_config=variable_config,
                                model_name=model_config["name"],
                                center_lat=location_config["center_lat"],
                                center_lon=location_config["center_lon"],
                                zoom=location_config["zoom"],
                                counties=counties,
                            )

                            with open(image_path, "wb") as f:
                                f.write(image_buffer.getvalue())

                            logger.info(f"Saved forecast image for hour {hour_str} to {image_path}")

                        if repomap["GENERATE_MAP_TILES"] and tile_helpers:
                            temp_geotiff = os.path.join(variable_cache_dir, f"temp_{hour_str}.tif")
                            geotiff_path = tile_helpers["grib_to_geotiff"](grib_path, temp_geotiff, variable_config)
                            tile_dir = os.path.join(variable_cache_dir, "tiles", f"{hour_str}")
                            tile_helpers["generate_tiles"](
                                geotiff_path,
                                tile_dir,
                                variable_config,
                                min_zoom=repomap["TILE_MIN_ZOOM"],
                                max_zoom=repomap["TILE_MAX_ZOOM"],
                            )
                            if os.path.exists(temp_geotiff):
                                os.remove(temp_geotiff)

                        if repomap["GENERATE_VECTOR_CONTOURS"] and tile_helpers:
                            contours = tile_helpers["generate_vector_contours"](grib_path, variable_config)
                            contour_path = os.path.join(variable_cache_dir, f"contours_{hour_str}.geojson")
                            tile_helpers["save_geojson"](contours, contour_path)

                    center_value, units = extract_center_value(
                        grib_path,
                        location_config['center_lat'],
                        location_config['center_lon'],
                        variable_config,
                    )

                    # Record valid time mapping
                    valid_times.append({
                        "forecast_hour": hour,
                        "valid_time": valid_time_str,
                        "frame_path": f"{variable_id}/frame_{hour_str}.png",
                    })
                    center_values.append({
                        "forecast_hour": hour,
                        "valid_time": valid_time_str,
                        "value": center_value,
                    })

                except (GribDownloadError, GribValidationError, PlotGenerationError, ValueError, RuntimeError) as exc:
                    logger.error(f"Error processing hour {hour_str}: {str(exc)}")
                    # Continue with next hour

            # Save valid time mapping
            valid_times_path = os.path.join(run_cache_dir, variable_id, "valid_times.txt")
            with open(valid_times_path, "w") as f:
                for vt in valid_times:
                    f.write(f"{vt['forecast_hour']}={vt['valid_time']}={vt['frame_path']}\n")

            center_values_path = os.path.join(run_cache_dir, variable_id, "center_values.json")
            with open(center_values_path, "w") as f:
                json.dump({
                    "location_id": location_id,
                    "run_id": run_id,
                    "model_id": model_id,
                    "variable_id": variable_id,
                    "init_time": init_time,
                    "center_lat": location_config['center_lat'],
                    "center_lon": location_config['center_lon'],
                    "units": units,
                    "values": center_values,
                }, f, indent=2)
        
        # Create a symlink to the latest run (atomic replacement)
        latest_link = os.path.join(repomap["CACHE_DIR"], location_id, model_id, "latest")
        # Create temp symlink and atomically rename to avoid race conditions
        temp_link = os.path.join(repomap["CACHE_DIR"], location_id, model_id, f".latest_tmp_{os.getpid()}")
        try:
            os.symlink(run_id, temp_link)
            os.replace(temp_link, latest_link)
        except OSError:
            # Fallback for systems that don't support atomic replace of symlinks
            if os.path.exists(temp_link):
                os.unlink(temp_link)
            if os.path.exists(latest_link) or os.path.islink(latest_link):
                os.unlink(latest_link)
            os.symlink(run_id, latest_link)
        
        logger.info(f"Completed forecast image generation for {location_config['name']} (run {run_id})")
        return True
        
    except (OSError, ValueError, RuntimeError, GribDownloadError, GribValidationError, PlotGenerationError) as exc:
        logger.error(
            f"Error generating forecast images for {location_config['name']}: {str(exc)}",
            exc_info=True,
        )
        return False

def cleanup_old_runs(location_id: str, model_id: str) -> None:
    """Remove old runs to save disk space, keeping only the most recent N runs."""
    try:
        location_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id)
        if not os.path.exists(location_dir):
            return
            
        # Get all run directories
        run_dirs = []
        for item in os.listdir(location_dir):
            if item.startswith("run_") and os.path.isdir(os.path.join(location_dir, item)):
                run_dirs.append(item)
        
        # Sort by run ID (which includes date and hour)
        run_dirs.sort(reverse=True)
        
        # Remove older runs beyond the limit
        max_runs = repomap.get("MAX_RUNS_TO_KEEP", 5)
        if len(run_dirs) > max_runs:
            for old_run in run_dirs[max_runs:]:
                old_run_path = os.path.join(location_dir, old_run)
                logger.info(f"Removing old run: {old_run_path}")
                shutil.rmtree(old_run_path)
    
    except (OSError, ValueError, RuntimeError) as exc:
        logger.error(f"Error cleaning up old runs for {location_id}: {str(exc)}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Build forecast cache for configured locations")
    parser.add_argument("--location", help="Specific location ID to process (default: all)")
    parser.add_argument("--runs", type=int, default=5, help="Number of model runs to process (default: 5)")
    parser.add_argument("--latest-only", action="store_true", help="Process only the latest run")
    parser.add_argument("--model", default=repomap["DEFAULT_MODEL"], help="Model ID to process")
    parser.add_argument("--variables", nargs="*", help="Variable IDs to process (default: all)")
    args = parser.parse_args()
    
    # Ensure cache directory exists
    os.makedirs(repomap["CACHE_DIR"], exist_ok=True)
    
    # Get county shapefile (shared resource)
    shp_path = fetch_county_shapefile(repomap["CACHE_DIR"])
    logger.info("Loading county shapefile into memory...")
    counties = gpd.read_file(shp_path)
    log_memory_usage("after_shapefile_load")
    
    # Get available model runs
    if args.latest_only:
        available_runs = get_available_model_runs(args.model, max_runs=1)
    else:
        available_runs = get_available_model_runs(args.model, max_runs=args.runs)
    
    logger.info(f"Found {len(available_runs)} available {args.model} runs")
    variable_ids = args.variables or list(repomap["WEATHER_VARIABLES"].keys())
    
    # Process locations
    if args.location:
        # Process single location
        if args.location in repomap["LOCATIONS"]:
            # Create a copy to avoid mutating the global config
            location_config = repomap["LOCATIONS"][args.location].copy()
            location_config['id'] = args.location
            logger.info(f"Processing single location: {location_config['name']}")

            for run_info in available_runs:
                generate_forecast_images(
                    location_config,
                    counties,
                    args.model,
                    run_info,
                    variable_ids=variable_ids,
                )
                log_memory_usage(f"after_run_{run_info['run_id']}")
                gc.collect()

            # Clean up old runs
            cleanup_old_runs(args.location, args.model)
        else:
            logger.error(f"Location ID '{args.location}' not found in configuration")
    else:
        # Process all locations
        logger.info(f"Processing all {len(repomap['LOCATIONS'])} configured locations")
        for location_id, location_config_orig in repomap["LOCATIONS"].items():
            # Create a copy to avoid mutating the global config
            location_config = location_config_orig.copy()
            location_config['id'] = location_id

            for run_info in available_runs:
                generate_forecast_images(
                    location_config,
                    counties,
                    args.model,
                    run_info,
                    variable_ids=variable_ids,
                )
                log_memory_usage(f"after_run_{run_info['run_id']}")
                gc.collect()

            # Clean up old runs
            cleanup_old_runs(location_id, args.model)
    
    logger.info("Cache building complete")
    
    # If running in latest-only mode (e.g. testing or manual run), exit immediately
    if args.latest_only:
        return

    # In production (supervisord), sleep before exiting so we don't hammer NOAA
    # HRRR updates hourly, so checking every 15 minutes is sufficient
    import time
    sleep_minutes = int(os.environ.get("CACHE_REFRESH_INTERVAL", repomap["CACHE_REFRESH_INTERVAL_MINUTES"]))
    logger.info(f"Sleeping for {sleep_minutes} minutes before next refresh...")
    time.sleep(sleep_minutes * 60)

if __name__ == "__main__":
    main()
