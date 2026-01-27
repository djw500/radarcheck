"""
ECMWF fetcher using Herbie for IFS Open Data.

Usage notes:
- Uses 'herbie-data' package to fetch from ECMWF Open Data (Google Cloud/AWS/Azure).
- Supports partial downloads (byte ranges) for efficiency.
- No API keys required for operational data.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Optional

import requests
from herbie import Herbie

logger = logging.getLogger(__name__)

VAR_MAP = {
    "t2m": ":2t:",
    "dpt": ":2d:",
    "apcp": ":tp:",  # Total precipitation
    "asnow": ":sf:",  # Snowfall (water equivalent)
    "cape": ":mucape:",  # Most unstable CAPE
    "msl": ":msl:",  # Mean sea level pressure
    "wind_10m": ":10[uv]:",  # u and v components
    "gust": ":10fg:",  # 10m wind gust
    "gh": ":gh:",  # Geopotential height
}


def get_herbie_search_string(variable_id: str) -> str:
    if variable_id not in VAR_MAP:
        raise RuntimeError(f"Variable '{variable_id}' not mapped for ECMWF Herbie fetch.")
    return VAR_MAP[variable_id]


def _resolve_herbie_model(model_id: str) -> dict[str, str]:
    # Map internal model ID to Herbie model name
    # 'ecmwf_eps' might map to 'aifs' or other products, but use IFS until updated.
    return {"model": "ifs", "product": "oper"}


def _url_exists(url: str, timeout: int) -> bool:
    try:
        response = requests.head(url, timeout=timeout)
        if response.status_code == 200:
            return True
        if response.status_code in {403, 405}:
            response = requests.get(url, timeout=timeout)
            return response.status_code == 200
        return False
    except requests.RequestException:
        return False


def herbie_run_available(
    model_id: str,
    variable_id: Optional[str],
    date_str: str,
    init_hour: str,
    forecast_hour: str,
    timeout: int = 10,
) -> bool:
    """Check availability for an ECMWF Herbie run and optional variable inventory."""
    herbie_config = _resolve_herbie_model(model_id)
    dt_str = f"{date_str} {init_hour}:00"
    H = Herbie(
        dt_str,
        model=herbie_config["model"],
        product=herbie_config["product"],
        fxx=int(forecast_hour),
        verbose=False,
    )
    idx_url = getattr(H, "idx", None)
    if not idx_url or not _url_exists(idx_url, timeout):
        logger.info(
            "Herbie index unavailable for %s %s %s f%s (idx=%s)",
            model_id,
            date_str,
            init_hour,
            forecast_hour,
            idx_url,
        )
        return False

    if not variable_id:
        return True

    try:
        search_string = get_herbie_search_string(variable_id)
    except RuntimeError as exc:
        logger.warning("Herbie variable mapping missing: %s", exc)
        return False

    try:
        inventory = H.inventory(search_string, verbose=False)
    except Exception as exc:
        logger.warning("Herbie inventory check failed for %s: %s", search_string, exc)
        return False

    if inventory.empty:
        logger.info("Herbie inventory empty for %s (%s)", model_id, search_string)
        return False
    return True


def fetch_grib_herbie(
    model_id: str,
    variable_id: str,
    date_str: str,
    init_hour: str,
    forecast_hour: str,
    target_path: str,
) -> str:
    """Fetch GRIB via Herbie for ECMWF (IFS) models.

    Args:
        model_id: 'ecmwf_hres' (mapped to 'ifs') or 'ecmwf_eps' (mapped to 'aifs' or similar if available)
        variable_id: Internal variable ID (e.g., 't2m', 'apcp')
        date_str: YYYYMMDD string
        init_hour: "00", "06", "12", "18"
        forecast_hour: Lead time in hours (e.g., "003")
        target_path: Destination path for the downloaded GRIB file.

    Returns:
        The local path to the downloaded GRIB file (should match target_path).
    """
    # Variable mapping: internal_id -> Herbie search string (regex)
    search_string = get_herbie_search_string(variable_id)

    # Map internal model ID to Herbie model name
    herbie_config = _resolve_herbie_model(model_id)

    # Format date for Herbie
    dt_str = f"{date_str} {init_hour}:00"

    try:
        H = Herbie(
            dt_str,
            model=herbie_config["model"],
            product=herbie_config["product"],
            fxx=int(forecast_hour),
            save_dir=os.path.dirname(target_path), # Save near target to minimize move cost? 
            # Actually, Herbie has its own structure. We'll let it save there and move/copy.
            # Or we can try to force it. Let's use default Herbie cache and move.
            verbose=False,
        )
        
        # Download the specific variable(s)
        # verbose=False to reduce noise in logs
        H.download(search_string, verbose=False)
        
        # Herbie saves files with its own naming convention.
        # We need to find the file it just downloaded.
        # get_localFilePath returns the path to the file in the cache.
        source_path = str(H.get_localFilePath(search_string))
        
        if not os.path.exists(source_path):
             raise RuntimeError(f"Herbie reported success but file not found at {source_path}")

        # Ensure target directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # Move or copy to target_path
        # We copy to preserve Herbie's cache if needed, or move to save space. 
        # Given we have a dedicated cache structure, moving is better to avoid duplication.
        shutil.move(source_path, target_path)
        
        return target_path

    except Exception as e:
        raise RuntimeError(f"Herbie fetch failed for {model_id}/{variable_id}: {e}")
