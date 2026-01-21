"""
ECMWF fetcher scaffolding for HRES and EPS via CDS.

Usage notes:
- Requires 'cdsapi' package and valid credentials (typically in ~/.cdsapirc).
- This module exposes a fetch_grib_cds() function with a NOMADS-like signature
  to integrate with cache_builder's flow. It currently raises a clear error
  if cdsapi is not available or credentials are missing.
"""
from __future__ import annotations

import os
from typing import Any, Dict


def fetch_grib_cds(
    model_id: str,
    variable_id: str,
    date_str: str,
    init_hour: str,
    forecast_hour: str,
    location_config: Dict[str, Any],
    run_id: str,
    target_path: str,
) -> str:
    """Fetch GRIB via CDS for ECMWF models.

    Returns the local path to the downloaded GRIB file.
    """
    try:
        import cdsapi  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "cdsapi is required for ECMWF downloads. Install with 'pip install cdsapi' and configure ~/.cdsapirc."
        ) from exc

    # Basic environment checks
    cds_url = os.environ.get("CDSAPI_URL")
    cds_key = os.environ.get("CDSAPI_KEY")
    if not (os.path.exists(os.path.expanduser("~/.cdsapirc")) or (cds_url and cds_key)):
        raise RuntimeError(
            "CDS credentials not found. Create ~/.cdsapirc or set CDSAPI_URL and CDSAPI_KEY."
        )

    # Minimal variable mapping (extend as needed)
    var_map = {
        "t2m": {"cds_var": "2m_temperature"},
        "dpt": {"cds_var": "2m_dewpoint_temperature"},
        "apcp": {"cds_var": "total_precipitation"},  # 'tp' shortName
        "prate": {"cds_var": "total_precipitation"},  # derive rate downstream if needed
    }
    if variable_id not in var_map:
        raise RuntimeError(f"Variable '{variable_id}' not mapped for ECMWF CDS fetch.")

    dataset = {
        "ecmwf_hres": "ecmwf-high-resolution-forecast",
        "ecmwf_eps": "ecmwf-ensemble-forecast",
    }.get(model_id)
    if not dataset:
        raise RuntimeError(f"Model '{model_id}' not supported by ECMWF CDS fetch.")

    # Build request parameters (simplified; refine for production)
    # ECMWF expects times/steps rather than 'forecast_hour' directly; map accordingly.
    step = int(forecast_hour)  # hours since init
    request = {
        "product_type": "forecast",
        "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        "time": f"{init_hour}:00",
        "variable": var_map[variable_id]["cds_var"],
        "step": [step],
        "format": "grib",
        # Area: North, West, South, East
        "area": [
            location_config["lat_max"],
            location_config["lon_min"],
            location_config["lat_min"],
            location_config["lon_max"],
        ],
    }

    c = cdsapi.Client()
    c.retrieve(dataset, request, target_path)
    return target_path

