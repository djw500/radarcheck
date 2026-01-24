from __future__ import annotations

import logging
import os
import zipfile
from typing import Any, Optional

import numpy as np
import requests

from config import repomap

import time
from functools import wraps

logger = logging.getLogger(__name__)

# Audit stats for build monitoring
audit_stats = {
    "tiles_skipped": 0,
    "tiles_processed": 0,
    "grib_hits": 0,
    "grib_misses": 0
}

def time_function(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = f(*args, **kwargs)
        end = time.perf_counter()
        logger.info(f"PERF: {f.__name__} took {end - start:.4f}s")
        return result
    return wrapper

class GribDownloadError(Exception):
    """Failed to download GRIB file from NOMADS."""


class GribValidationError(Exception):
    """GRIB file is corrupted or invalid."""


class PlotGenerationError(Exception):
    """Failed to generate forecast plot."""


def download_file(url: str, local_path: str, timeout: Optional[int] = None) -> None:
    """Download a file if it doesn't exist in cache.

    Args:
        url: The URL to download from
        local_path: The local path to save the file
        timeout: Request timeout in seconds (default from config)
    """
    timeout = timeout or repomap["DOWNLOAD_TIMEOUT_SECONDS"]
    if not os.path.exists(local_path):
        response = requests.get(url, stream=True, timeout=timeout)
        # Raise for obvious HTTP errors early
        try:
            response.raise_for_status()
        except requests.HTTPError:
            # Silence body preview logging to reduce noise
            raise

        ctype = response.headers.get("Content-Type", "")
        clen = response.headers.get("Content-Length")

        # Heuristic guard: reject textual/error responses
        if "text/html" in ctype or ctype.startswith("text/"):
            try:
                preview = next(response.iter_content(chunk_size=512))
            except Exception:
                preview = b""
            logger.error("Unexpected textual response for GRIB request; rejecting download.")
            if preview:
                logger.error(f"Body preview: {preview[:200]!r}")
            raise ValueError(f"Unexpected content-type '{ctype}' from server")

        # Heuristic guard: reject very small responses when CL is present
        if clen is not None:
            try:
                if int(clen) < repomap["MIN_GRIB_FILE_SIZE_BYTES"]:
                    logger.error(
                        f"Content-Length too small ({clen} < {repomap['MIN_GRIB_FILE_SIZE_BYTES']}); rejecting download."
                    )
                    raise ValueError("Response too small for GRIB")
            except ValueError:
                # If header is malformed, ignore and continue
                pass

        # Create directory if it doesn't exist
        dir_path = os.path.dirname(local_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        total = 0
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                f.write(chunk)
        # logger.info(f"Downloaded: {local_path} ({total} bytes)")
    else:
        # logger.info(f"Using cached file: {local_path}")
        pass


def fetch_county_shapefile(cache_dir: str) -> str:
    """Download and extract the county shapefile if needed."""
    county_zip = os.path.join(cache_dir, "cb_2018_us_county_20m.zip")
    county_dir = os.path.join(cache_dir, "county_shapefile")
    county_shp = os.path.join(county_dir, "cb_2018_us_county_20m.shp")
    
    url_county = "https://www2.census.gov/geo/tiger/GENZ2018/shp/cb_2018_us_county_20m.zip"
    download_file(url_county, county_zip)
    if not os.path.exists(county_dir):
        with zipfile.ZipFile(county_zip, "r") as zip_ref:
            zip_ref.extractall(county_dir)
        logger.info("Extracted county shapefile.")
    else:
        logger.info("County shapefile already extracted.")
    return county_shp


def convert_units(data: Any, conversion: Optional[str]) -> Any:
    """Convert data arrays to display units."""
    if conversion is None:
        return data
    if conversion == "k_to_f":
        return (data - 273.15) * 9 / 5 + 32
    if conversion == "m_s_to_mph":
        return data * 2.23694
    if conversion == "kg_m2_to_in":
        return data * 0.0393701
    if conversion == "kg_m2_s_to_in_hr":
        return data * 0.0393701 * 3600
    if conversion == "m_to_in":
        return data * 39.3701
    if conversion == "m_water_to_in_snow":
        return data * 393.701
    if conversion == "m_to_mi":
        return data * 0.000621371
    if conversion == "c_to_f":
        return data * 9 / 5 + 32
    if conversion == "pa_to_mb":
        return data / 100.0
    return data


def compute_wind_speed(u_component: Any, v_component: Any) -> Any:
    """Compute wind speed magnitude from u/v components."""
    return np.sqrt(u_component ** 2 + v_component ** 2)


def format_forecast_hour(hour: int, model_id: Optional[str] = None) -> str:
    """Format forecast hour string based on model requirements."""
    digits = 2
    if model_id:
        digits = repomap["MODELS"].get(model_id, {}).get("forecast_hour_digits", 2)
    return f"{int(hour):0{digits}d}"
