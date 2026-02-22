from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import random

import requests
import xarray as xr
from filelock import FileLock, Timeout

import numpy as np

from config import repomap
from ecmwf import fetch_grib_herbie, herbie_run_available  # using Herbie for ECMWF
from tiles import open_dataset_robust, _select_variable_from_dataset as select_variable_from_dataset
from utils import (
    GribDownloadError,
    GribValidationError,
    download_file,
    format_forecast_hour,
)


# Set up logging
os.makedirs('logs', exist_ok=True)
logger = logging.getLogger(__name__)

# Add file handler
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler('logs/grib_fetcher.log', maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s'
))
logger.addHandler(file_handler)
# We don't call logging.basicConfig here because it would add a console handler.
# The entry point scripts (build_tiles.py, etc) handle the root config.

# Suppress noisy external libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.WARNING)


def get_valid_forecast_hours(model_id: str, max_hours: int) -> list[int]:
    """Get list of valid forecast hours for a model, respecting its schedule.

    Some models (like NBM) have non-hourly data after a certain point:
    - NBM: hourly 1-36, 3-hourly 39-192, 6-hourly 198-264

    Returns list of valid forecast hours up to max_hours.
    """
    model_config = repomap["MODELS"].get(model_id, {})
    schedule = model_config.get("forecast_hour_schedule")

    if not schedule:
        # Default: hourly
        return list(range(1, max_hours + 1))

    hours = []
    for segment in schedule:
        start = segment["start"]
        end = min(segment["end"], max_hours)
        step = segment["step"]
        if start <= max_hours:
            hours.extend(range(start, end + 1, step))

    return sorted(set(h for h in hours if h <= max_hours))


from functools import lru_cache

def _nomads_head(url: str) -> bool:
    try:
        response = requests.head(url, timeout=repomap["HEAD_REQUEST_TIMEOUT_SECONDS"])
        return response.status_code == 200
    except requests.RequestException:
        return False


@lru_cache(maxsize=128)
def detect_hourly_support(model_id: str, date_str: str, init_hour: str) -> bool:
    """Detect if this run supports hourly files for the first hours.

    - For NOMADS models with a configured hourly file pattern (e.g., GFS pgrb2b), probe hour 001.
    - For Herbie (ECMWF), assume hourly available if configured via hourly_override_first_hours.
    """
    model_config = repomap["MODELS"].get(model_id, {})
    hourly_first = int(model_config.get("hourly_override_first_hours", 0) or 0)
    if hourly_first <= 0:
        return False

    if model_config.get("source") == "herbie":
        return True

    hourly_pattern = model_config.get("file_pattern_hourly")
    if not hourly_pattern:
        return False
    fhour = format_forecast_hour(1, model_id)
    file_name = hourly_pattern.format(init_hour=init_hour, forecast_hour=fhour)
    dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
    url = (
        f"{model_config['nomads_url']}?"
        f"file={file_name}&"
        f"dir={dir_path}&"
        f"{model_config['availability_check_var']}=on"
    )
    return _nomads_head(url)


def get_run_forecast_hours(model_id: str, date_str: str, init_hour: str, max_hours: int) -> list[int]:
    """Return expected hours for this run, applying hourly override if supported.

    Base schedule is get_valid_forecast_hours; if hourly_override_first_hours is set and
    detect_hourly_support() returns True, use hourly 1..N and then resume base schedule > N.
    """
    base = get_valid_forecast_hours(model_id, max_hours)
    model_config = repomap["MODELS"].get(model_id, {})
    hourly_first = int(model_config.get("hourly_override_first_hours", 0) or 0)
    if hourly_first <= 0:
        return base
    if not detect_hourly_support(model_id, date_str, init_hour):
        return base
    n = min(hourly_first, max_hours)
    hourly = list(range(1, n + 1))
    rest = [h for h in base if h > n]
    return hourly + rest


def build_variable_query(variable_config: dict[str, Any]) -> str:
    params = [f"{param}=on" for param in variable_config.get("nomads_params", [])]
    levels = variable_config.get("level_params", [])
    query = "&".join(params + levels)
    if query:
        return f"{query}&"
    return ""


def build_model_url(    model_config: dict[str, Any],
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


def fetch_grib(    model_id: str,
    variable_id: str,
    date_str: str,
    init_hour: str,
    forecast_hour: str,
    run_id: str,
    location_config: Optional[dict[str, Any]] = None, # Kept for signature compatibility but ignored for fetch region
    use_hourly: bool = False,  # Use hourly file pattern if available (e.g., GFS pgrb2b)
) -> str:
    """Download and cache the GRIB file for a specific forecast hour in the central GRIB cache."""
    model_config = repomap["MODELS"][model_id]
    variable_config = repomap["WEATHER_VARIABLES"][variable_id]

    # Always use central GRIB cache
    run_cache_dir = os.path.join(repomap["GRIB_CACHE_DIR"], model_id, run_id, variable_id)
    os.makedirs(run_cache_dir, exist_ok=True)

    filename = os.path.join(run_cache_dir, f"grib_{forecast_hour}.grib2")

    # Determine preferred stepType filter
    preferred = None
    if variable_config.get("is_accumulation"):
        preferred = {'stepType': 'accum'}
    if variable_config.get("preferred_step_type"):
        preferred = {'stepType': variable_config.get("preferred_step_type")}
    if variable_config.get("short_name") == "prate" and not preferred:
        preferred = {'stepType': 'instant'}

    def try_load_grib(filename: str) -> bool:
        """Try to load and validate a GRIB file"""
        if not os.path.exists(filename) or os.path.getsize(filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
            return False
        try:
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                # Try to open the file without chunks first
                ds = open_dataset_robust(filename, preferred)
                data_to_plot = select_variable_from_dataset(ds, variable_config)
                # Check for magnitude/components if vector
                if variable_config.get("vector_components"):
                    # select_variable_from_dataset for vectors already doeshypot
                    pass
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

    # Audit Check: skip if GRIB already exists and is valid
    if try_load_grib(filename):
        logger.info(f"[CACHE HIT] {model_id} {variable_id} {forecast_hour}")
        return filename

    # Use CONUS region for all downloads to ensure full coverage and deduplication
    download_region = repomap["DOWNLOAD_REGIONS"]["conus"]
    
    # ECMWF download path (Herbie)
    if model_config.get("source") == "herbie":
        temp_filename = f"{filename}.tmp"
        try:
            fetch_grib_herbie(
                model_id,
                variable_id,
                date_str,
                init_hour,
                forecast_hour,
                temp_filename,
            )
        except Exception as exc:
            logger.error(f"Herbie fetch failed for {model_id} {variable_id} hour {forecast_hour}: {exc}")
            raise GribDownloadError(str(exc)) from exc

        # Verify file via xarray open and move into place
        try:
            if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
                raise ValueError(f"Downloaded file is missing or too small: {temp_filename}")

            ds = xr.open_dataset(temp_filename, engine="cfgrib")
            data_to_plot = select_variable_from_dataset(ds, variable_config)
            # Basic validation
            if data_to_plot.size == 0:
                 raise ValueError("Empty variable")
            ds.close()
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                os.replace(temp_filename, filename)
                logger.info(f"Successfully downloaded and verified GRIB file (Herbie): {filename}")
                return filename
        except Exception as exc:
            logger.error(f"Herbie verification failed: {exc}")
            try:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
            except OSError:
                pass
            raise GribValidationError(str(exc)) from exc

    # DWD Open Data download path
    if model_config.get("source") == "dwd":
        dwd_var = variable_config.get("dwd_var")
        if not dwd_var:
            raise GribDownloadError(f"Variable {variable_id} not configured for DWD (missing dwd_var)")
        
        # Pattern: https://opendata.dwd.de/weather/nwp/icon/grib/HH/var/icon_global_icosahedral_single-level_YYYYMMDDHH_FH_VAR_UPPER.grib2.bz2
        # dir_pattern should be: "weather/nwp/icon/grib/{init_hour}/{dwd_var}"
        # file_pattern should be: "icon_global_icosahedral_single-level_{date_str}{init_hour}_{forecast_hour}_{dwd_var_upper}.grib2.bz2"
        
        base_url = model_config["nomads_url"] # Reuse field for base URL
        dir_path = model_config["dir_pattern"].format(init_hour=init_hour, dwd_var=dwd_var)
        file_name = model_config["file_pattern"].format(
            date_str=date_str, 
            init_hour=init_hour, 
            forecast_hour=forecast_hour, 
            dwd_var_upper=dwd_var.upper()
        )
        url = f"{base_url}/{dir_path}/{file_name}"
        
        # Download and decompress
        import bz2
        
        temp_bz2 = f"{filename}.bz2.tmp"
        temp_grib = f"{filename}.tmp"
        
        try:
            download_file(url, temp_bz2)
            
            # Decompress
            if not os.path.exists(temp_bz2):
                 raise GribDownloadError(f"Failed to download DWD file: {url}")
                 
            with open(temp_bz2, 'rb') as f_in, open(temp_grib, 'wb') as f_out:
                decompressor = bz2.BZ2Decompressor()
                for data in iter(lambda: f_in.read(100 * 1024), b''):
                    f_out.write(decompressor.decompress(data))
            
            os.remove(temp_bz2) # Cleanup compressed file
            
            # Verify
            if not os.path.exists(temp_grib) or os.path.getsize(temp_grib) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
                raise ValueError("Decompressed file is too small")
                
            # DWD GRIBs are unstructured. cfgrib needs to open them.
            # Warning: This verification might be slow or memory intensive for global unstructured grids.
            # But we must verify.
            # Note: We rely on tiles.py/prep_cell_index to handle the 1D coords later.
            
            ds = open_dataset_robust(temp_grib, preferred) # use preferred filter from config
            # Check variable existence (fast load)
            # DWD variable names might differ from config short_name?
            # select_variable_from_dataset handles candidates.
            # For ICON, T_2M -> t2m usually works or we add candidates.
            data_to_plot = select_variable_from_dataset(ds, variable_config)
            # data_to_plot.load() # Skip full load for global unstructured to save RAM?
            # Just check shape/coords
            if data_to_plot.size == 0:
                 raise ValueError("Empty variable")
            ds.close()
            
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                os.replace(temp_grib, filename)
                logger.info(f"Successfully downloaded and verified GRIB file (DWD): {filename}")
                return filename

        except Exception as exc:
            logger.error(f"DWD fetch failed: {exc}")
            if os.path.exists(temp_bz2): os.remove(temp_bz2)
            if os.path.exists(temp_grib): os.remove(temp_grib)
            raise GribDownloadError(str(exc)) from exc

    # NOMADS download path
    variable_query = build_variable_query(variable_config)
    # If hourly is requested and a special hourly pattern is configured, use it
    if use_hourly and model_config.get("file_pattern_hourly"):
        file_name = model_config["file_pattern_hourly"].format(
            init_hour=init_hour,
            forecast_hour=forecast_hour,
        )
        dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
        url = (
            f"{model_config['nomads_url']}?"
            f"file={file_name}&"
            f"dir={dir_path}&"
            f"{variable_query}"
        )
    else:
        url = build_model_url(
            model_config,
            date_str,
            init_hour,
            forecast_hour,
            variable_query,
            download_region,
        )
    
    # Try downloading up to 3 times
    for attempt in range(repomap["MAX_DOWNLOAD_RETRIES"]):
        try:
            temp_filename = f"{filename}.tmp"
            download_file(url, temp_filename)
            
            # Verify the temporary file
            if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
                raise ValueError(f"Downloaded file is missing or too small: {temp_filename}")
            
            # Try to open with xarray to verify it's valid
            ds = open_dataset_robust(temp_filename, preferred)
            data_to_plot = select_variable_from_dataset(ds, variable_config)
            data_to_plot.load()
            ds.close()
            
            # If verification passed, move the file into place atomically
            with FileLock(f"{filename}.lock", timeout=repomap["FILELOCK_TIMEOUT_SECONDS"]):
                os.replace(temp_filename, filename)
                return filename
                
        except Timeout as exc:
            # logger.error(f"Download attempt {attempt + 1} failed: {str(exc)}", exc_info=True)
            raise GribValidationError(f"Lock timeout for {filename}") from exc
        except requests.exceptions.HTTPError as exc:
            # Concise one-liner for HTTP errors (like 404)
            if exc.response.status_code == 404:
                # If it's a 404, it's not there yet. Don't retry, just fail this hour.
                raise GribDownloadError(f"Hour {forecast_hour} not available (404)")
            else:
                logger.warning(f"Download attempt {attempt + 1} failed: HTTP {exc.response.status_code}")
        except Exception as exc:
            logger.debug(f"Download attempt {attempt + 1} failed: {str(exc)}", exc_info=True)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except OSError:
                    pass
            if attempt < repomap["MAX_DOWNLOAD_RETRIES"] - 1:
                # Exponential backoff with jitter
                base = repomap["RETRY_DELAY_SECONDS"]
                delay = base * (2 ** attempt) + random.uniform(0, 1)
                # logger.info(f"Retrying in {delay:.2f}s (attempt {attempt + 2}/{repomap['MAX_DOWNLOAD_RETRIES']})")
                time.sleep(delay)
    
    raise GribDownloadError("Failed to obtain valid GRIB file after retries")

