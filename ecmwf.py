"""
ECMWF fetcher using Herbie for IFS Open Data.

Usage notes:
- Uses 'herbie-data' package to fetch from ECMWF Open Data (Google Cloud/AWS/Azure).
- Supports partial downloads (byte ranges) for efficiency.
- No API keys required for operational data.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict
from datetime import datetime


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
    try:
        from herbie import Herbie
    except ImportError as exc:
        raise RuntimeError(
            "herbie-data is required for ECMWF downloads. Install with 'pip install herbie-data'."
        ) from exc

    # Variable mapping: internal_id -> Herbie search string (regex)
    var_map = {
        "t2m": ":2t:",
        "dpt": ":2d:",
        "apcp": ":tp:",  # Total precipitation
        "asnow": ":sf:", # Snowfall (water equivalent)
        "cape": ":mucape:", # Most unstable CAPE
        "msl": ":msl:",  # Mean sea level pressure
        "wind_10m": ":10[uv]:",  # u and v components
        "gust": ":10fg:",       # 10m wind gust
        "gh": ":gh:",           # Geopotential height
    }

    if variable_id not in var_map:
        raise RuntimeError(f"Variable '{variable_id}' not mapped for ECMWF Herbie fetch.")

    search_string = var_map[variable_id]

    # Map internal model ID to Herbie model name
    herbie_model = "ifs"  # Default to IFS (HRES)
    # Note: 'ecmwf_eps' might map to 'aifs' or specific product types in Herbie, 
    # but 'ifs' is the main deterministic one.

    # Format date for Herbie
    dt_str = f"{date_str} {init_hour}:00"

    try:
        H = Herbie(
            dt_str,
            model=herbie_model,
            product="oper",  # Operational high-resolution
            fxx=int(forecast_hour),
            save_dir=os.path.dirname(target_path), # Save near target to minimize move cost? 
            # Actually, Herbie has its own structure. We'll let it save there and move/copy.
            # Or we can try to force it. Let's use default Herbie cache and move.
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