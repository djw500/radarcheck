from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


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
    """Failed to fetch GRIB data."""


class GribValidationError(Exception):
    """GRIB file is corrupted or invalid."""


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
    if conversion == "m_to_ft":
        return data * 3.28084
    if conversion == "c_to_f":
        return data * 9 / 5 + 32
    if conversion == "pa_to_mb":
        return data / 100.0
    return data


def format_forecast_hour(hour: int, model_id: Optional[str] = None) -> str:
    """Format forecast hour string based on model requirements."""
    from config import repomap
    digits = 2
    if model_id:
        digits = repomap["MODELS"].get(model_id, {}).get("forecast_hour_digits", 2)
    return f"{int(hour):0{digits}d}"
