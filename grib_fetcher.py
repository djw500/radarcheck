"""Unified GRIB fetcher using Herbie for all models.

Herbie handles downloading, caching, and idx-based byte-range subsetting.
We never touch GRIB files directly — H.xarray(search) returns xarray Datasets.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import xarray as xr
from herbie import Herbie

from config import repomap
from utils import GribDownloadError, format_forecast_hour

logger = logging.getLogger(__name__)

# Suppress noisy external libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("cfgrib").setLevel(logging.WARNING)
logging.getLogger("herbie").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Schedule helpers (kept from old code, used by scheduler)
# ---------------------------------------------------------------------------

def get_valid_forecast_hours(model_id: str, max_hours: int) -> list[int]:
    """Get list of valid forecast hours for a model, respecting its schedule."""
    model_config = repomap["MODELS"].get(model_id, {})
    schedule = model_config.get("forecast_hour_schedule")

    if not schedule:
        return list(range(1, max_hours + 1))

    hours = []
    for segment in schedule:
        start = segment["start"]
        end = min(segment["end"], max_hours)
        step = segment["step"]
        if start <= max_hours:
            hours.extend(range(start, end + 1, step))

    return sorted(set(h for h in hours if h <= max_hours))


def get_run_forecast_hours(model_id: str, date_str: str, init_hour: str, max_hours: int) -> list[int]:
    """Return expected hours for this run."""
    return get_valid_forecast_hours(model_id, max_hours)


# ---------------------------------------------------------------------------
# Herbie helpers
# ---------------------------------------------------------------------------

def _get_search_string(variable_id: str, model_id: str) -> str:
    """Look up Herbie search string for a variable+model combination."""
    var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
    herbie_search = var_cfg.get("herbie_search", {})
    model_cfg = repomap["MODELS"].get(model_id, {})
    herbie_model = model_cfg.get("herbie_model", "")
    return herbie_search.get(herbie_model, herbie_search.get("default", ""))


def _build_herbie(model_id: str, date_str: str, init_hour: str, forecast_hour: int) -> Herbie:
    """Create a Herbie object for the given model/date/hour."""
    model_cfg = repomap["MODELS"][model_id]
    dt_str = f"{date_str} {init_hour}:00"
    return Herbie(
        dt_str,
        model=model_cfg["herbie_model"],
        product=model_cfg["herbie_product"],
        fxx=forecast_hour,
        save_dir=repomap["HERBIE_SAVE_DIR"],
        verbose=False,
    )


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def open_as_xarray(
    model_id: str,
    variable_id: str,
    date_str: str,
    init_hour: str,
    forecast_hour: int,
) -> xr.Dataset:
    """Fetch GRIB data via Herbie and return as xarray Dataset.

    Handles special cases:
    - Wind U/V: computes speed via ds.herbie.with_wind()
    - ECMWF snod: computes physical depth from sd (water equiv) and rsn (density)
    """
    model_cfg = repomap["MODELS"][model_id]
    herbie_model = model_cfg["herbie_model"]

    try:
        H = _build_herbie(model_id, date_str, init_hour, forecast_hour)

        # Special case: ECMWF snow depth needs sd + rsn
        if variable_id == "snod" and herbie_model == "ifs":
            return _fetch_ecmwf_snod(H)

        search = _get_search_string(variable_id, model_id)
        if not search:
            raise GribDownloadError(
                f"No Herbie search string for {variable_id}/{model_id}"
            )

        ds = H.xarray(search)

        # Wind: if we fetched U/V components, compute magnitude
        if variable_id == "wind_10m" and herbie_model not in ("nbm",):
            ds = ds.herbie.with_wind(which="speed")

        return ds

    except GribDownloadError:
        raise
    except Exception as exc:
        raise GribDownloadError(
            f"Herbie fetch failed for {model_id}/{variable_id} "
            f"{date_str} {init_hour}z f{forecast_hour}: {exc}"
        ) from exc


def _fetch_ecmwf_snod(H: Herbie) -> xr.Dataset:
    """Fetch ECMWF snow depth: sd (water equiv) / rsn (density) -> physical depth."""
    ds_sd = H.xarray(":sd:")
    ds_rsn = H.xarray(":rsn:")

    sd = ds_sd[list(ds_sd.data_vars)[0]]
    rsn = ds_rsn[list(ds_rsn.data_vars)[0]]

    # physical_depth_m = sd_water_equiv_m * 1000 / density_kg_m3
    rsn_safe = rsn.where(rsn > 10.0, np.nan)
    physical_depth = sd * 1000.0 / rsn_safe
    physical_depth = physical_depth.fillna(0.0)

    result = physical_depth.to_dataset(name="snod")
    result.attrs = ds_sd.attrs
    return result


def check_availability(
    model_id: str,
    date_str: str,
    init_hour: str,
    forecast_hour: int,
) -> bool:
    """Check if data is available for a model run at a specific forecast hour."""
    try:
        H = _build_herbie(model_id, date_str, init_hour, forecast_hour)
        inv = H.inventory(verbose=False)
        return not inv.empty
    except Exception:
        return False
