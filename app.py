from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

from flask import Flask, send_file, render_template, redirect, url_for, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
try:
    from flasgger import Swagger
    FLASGGER_AVAILABLE = True
except ImportError:
    FLASGGER_AVAILABLE = False
    Swagger = None
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
import pytz
import numpy as np

from config import repomap
from alerts import get_alerts_for_location
try:
    from plotting import select_variable_from_dataset, get_colormap  # type: ignore
    PLOTTING_AVAILABLE = True
except Exception:
    PLOTTING_AVAILABLE = False
    def select_variable_from_dataset(*args, **kwargs):  # type: ignore
        raise RuntimeError("Plotting stack not available; install optional deps to enable.")
    def get_colormap(variable_config):  # type: ignore
        try:
            import matplotlib.pyplot as plt  # local import to avoid hard dep at startup
            return plt.get_cmap(variable_config.get("colormap", "viridis"))
        except Exception:
            # Fallback: no matplotlib available
            raise RuntimeError("Matplotlib not available to build colormap.")
from summary import summarize_run
from utils import convert_units, format_forecast_hour
from forecast_table import (
    load_all_center_values,
    build_forecast_table,
    format_table_html,
    format_table_json,
    get_variable_display_order,
)
from tiles import (
    load_timeseries_for_point,
    load_grid_slice,
    list_tile_runs,
    list_tile_variables,
    list_tile_models,
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API Key authentication for production
# In production: set via `fly secrets set RADARCHECK_API_KEY=...`
# In development: defaults to allowing all requests
API_KEY = os.environ.get("RADARCHECK_API_KEY")

def require_api_key(f):
    """Decorator to require API key for endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Skip auth in development (when no key is configured)
        if API_KEY is None:
            return f(*args, **kwargs)
        
        # Check header first, then query parameter (for browser testing)
        provided_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if provided_key != API_KEY:
            logger.warning(f"Invalid or missing API key attempt from {request.remote_addr}")
            return jsonify({"error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated

def parse_metadata_file(filepath: str) -> dict[str, str]:
    """Safely parse a metadata file with key=value format."""
    metadata = {}
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    key, value = line.split("=", 1)
                    metadata[key] = value
    except (IOError, OSError) as e:
        logger.warning(f"Error reading metadata file {filepath}: {e}")
    return metadata

def is_safe_path(base_dir: str, user_path: str) -> bool:
    """Check if the user-provided path is within the base directory (prevent path traversal)."""
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_dir, user_path))
    return target.startswith(base + os.sep) or target == base

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Add file handler
file_handler = RotatingFileHandler('logs/app.log', maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
logger.addHandler(file_handler)

app = Flask(__name__, static_folder='static', template_folder='templates')
logger.info('Application startup')

if FLASGGER_AVAILABLE:
    swagger = Swagger(app)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
)

def get_or_create_counter(name: str, description: str, labels: list[str]) -> Counter:
    try:
        return Counter(name, description, labels)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        raise


def get_or_create_histogram(name: str, description: str, labels: list[str]) -> Histogram:
    try:
        return Histogram(name, description, labels)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        raise


REQUEST_COUNT = get_or_create_counter(
    "radarcheck_requests_total",
    "Total requests",
    ["endpoint", "status"],
)
REQUEST_LATENCY = get_or_create_histogram(
    "radarcheck_request_latency_seconds",
    "Request latency",
    ["endpoint"],
)

def read_metadata(run_dir: str) -> dict[str, str]:
    metadata_json = os.path.join(run_dir, "metadata.json")
    if os.path.exists(metadata_json):
        try:
            with open(metadata_json, "r") as f:
                payload = json.load(f)
            location = payload.get("location", {})
            return {
                "date_str": payload.get("date_str", ""),
                "init_hour": payload.get("init_hour", ""),
                "init_time": payload.get("init_time", "Unknown"),
                "run_id": payload.get("run_id", "Unknown"),
                "model_id": payload.get("model_id", ""),
                "model_name": payload.get("model_name", ""),
                "location_name": location.get("name", ""),
                "center_lat": str(location.get("center_lat", "")),
                "center_lon": str(location.get("center_lon", "")),
                "zoom": str(location.get("zoom", "")),
            }
        except (IOError, json.JSONDecodeError) as exc:
            logger.warning(f"Error reading metadata JSON {metadata_json}: {exc}")

    metadata_txt = os.path.join(run_dir, "metadata.txt")
    return parse_metadata_file(metadata_txt)


def get_available_locations(model_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Get list of locations with available forecast data"""
    model_id = model_id or repomap["DEFAULT_MODEL"]
    locations = []
    for location_id, location_config in repomap["LOCATIONS"].items():
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id)
        
        if not os.path.exists(location_cache_dir):
            legacy_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
            if not os.path.exists(legacy_cache_dir):
                continue
            location_cache_dir = legacy_cache_dir
            
        # Look for the latest run
        latest_run_link = os.path.join(location_cache_dir, "latest")
        if os.path.islink(latest_run_link):
            latest_run = os.readlink(latest_run_link)
            run_dir = os.path.join(location_cache_dir, latest_run)
            
            if os.path.exists(run_dir):
                metadata = read_metadata(run_dir)

                # Check if any variable has data (frames, valid_times, or center_values)
                has_any_data = False
                try:
                    for var_id in repomap["WEATHER_VARIABLES"].keys():
                        vdir = os.path.join(run_dir, var_id)
                        if not os.path.isdir(vdir):
                            continue
                        # any valid PNG frame
                        for name in os.listdir(vdir):
                            if name.startswith("frame_") and name.endswith(".png"):
                                fpath = os.path.join(vdir, name)
                                if os.path.getsize(fpath) >= repomap["MIN_PNG_FILE_SIZE_BYTES"]:
                                    has_any_data = True
                                    break
                        if has_any_data:
                            break
                        # valid_times.txt with content
                        vt = os.path.join(vdir, "valid_times.txt")
                        if os.path.exists(vt) and os.path.getsize(vt) > 0:
                            has_any_data = True
                            break
                        # center_values.json with any values
                        cv = os.path.join(vdir, "center_values.json")
                        if os.path.exists(cv):
                            try:
                                with open(cv, "r") as f:
                                    payload = json.load(f)
                                vals = payload.get("values", [])
                                if isinstance(vals, list) and len(vals) > 0:
                                    has_any_data = True
                                    break
                            except Exception:
                                pass
                except Exception:
                    has_any_data = False

                if has_any_data:
                    locations.append({
                        "id": location_id,
                        "name": location_config["name"],
                        "init_time": metadata.get("init_time", "Unknown"),
                        "run_id": metadata.get("run_id", "Unknown"),
                        "model_id": model_id,
                        "model_name": repomap["MODELS"].get(model_id, {}).get("name", "Model"),
                    })
    
    return locations


def find_nearest_location(lat: float, lon: float) -> str:
    nearest_id = next(iter(repomap["LOCATIONS"]))
    nearest_distance = float("inf")
    for location_id, location_config in repomap["LOCATIONS"].items():
        lat_diff = lat - location_config["center_lat"]
        lon_diff = lon - location_config["center_lon"]
        distance = (lat_diff ** 2 + lon_diff ** 2) ** 0.5
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_id = location_id
    return nearest_id

def get_location_runs(location_id: str, model_id: Optional[str] = None) -> list[dict[str, str]]:
    """Get all available runs for a location"""
    if location_id not in repomap["LOCATIONS"]:
        return []

    model_id = model_id or repomap["DEFAULT_MODEL"]
    location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id)
    if not os.path.exists(location_cache_dir):
        legacy_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        if not os.path.exists(legacy_cache_dir):
            return []
        location_cache_dir = legacy_cache_dir
        
    runs = []
    for item in os.listdir(location_cache_dir):
        if item.startswith("run_") and os.path.isdir(os.path.join(location_cache_dir, item)):
            run_dir = os.path.join(location_cache_dir, item)
            metadata = read_metadata(run_dir)

            # Check if this run has frames
            default_variable = repomap["DEFAULT_VARIABLE"]
            frame_dir = os.path.join(run_dir, default_variable)
            if not os.path.exists(frame_dir):
                frame_dir = run_dir

            max_hours = repomap["MODELS"].get(model_id, {}).get("max_forecast_hours", 24)
            has_frames = any(
                os.path.exists(
                    os.path.join(frame_dir, f"frame_{format_forecast_hour(hour, model_id)}.png")
                )
                for hour in range(1, max_hours + 1)
            )
            
            if has_frames:
                runs.append({
                    "run_id": item,
                    "init_time": metadata.get("init_time", "Unknown"),
                    "date_str": metadata.get("date_str", ""),
                    "init_hour": metadata.get("init_hour", ""),
                    "model_id": metadata.get("model_id", model_id),
                })
    
    # Sort runs by init_time (newest first)
    runs.sort(key=lambda x: x["init_time"], reverse=True)
    return runs

def get_run_metadata(location_id: str, run_id: str, model_id: Optional[str] = None) -> Optional[dict[str, str]]:
    """Get metadata for a specific run"""
    if location_id not in repomap["LOCATIONS"]:
        return None

    model_id = model_id or repomap["DEFAULT_MODEL"]
    # Validate run_id to prevent path traversal
    if not is_safe_path(os.path.join(repomap["CACHE_DIR"], location_id, model_id), run_id):
        logger.warning(f"Potential path traversal attempt with run_id: {run_id}")
        return None

    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not os.path.exists(run_dir):
        legacy_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
        if not os.path.exists(legacy_dir):
            return None
        run_dir = legacy_dir

    return read_metadata(run_dir)

def get_run_valid_times(
    location_id: str,
    run_id: str,
    model_id: Optional[str] = None,
    variable_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get valid times for a specific run"""
    if location_id not in repomap["LOCATIONS"]:
        return []

    model_id = model_id or repomap["DEFAULT_MODEL"]
    variable_id = variable_id or repomap["DEFAULT_VARIABLE"]

    # Validate run_id to prevent path traversal
    if not is_safe_path(os.path.join(repomap["CACHE_DIR"], location_id, model_id), run_id):
        logger.warning(f"Potential path traversal attempt with run_id: {run_id}")
        return []

    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    valid_times_path = os.path.join(run_dir, variable_id, "valid_times.txt")
    
    if not os.path.exists(valid_times_path):
        legacy_path = os.path.join(repomap["CACHE_DIR"], location_id, run_id, "valid_times.txt")
        if not os.path.exists(legacy_path):
            return []
        valid_times_path = legacy_path
        
    valid_times = []
    with open(valid_times_path, "r") as f:
        for line in f:
            parts = line.strip().split("=")
            if len(parts) >= 3:
                forecast_hour = int(parts[0])
                valid_time = parts[1]
                frame_path = parts[2]
                
                valid_times.append({
                    "forecast_hour": forecast_hour,
                    "valid_time": valid_time,
                    "frame_path": frame_path
                })
    
    # Sort by forecast hour
    valid_times.sort(key=lambda x: x["forecast_hour"])
    return valid_times

def get_run_center_values(
    location_id: str,
    run_id: str,
    model_id: Optional[str] = None,
    variable_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Get center-point forecast values for a specific run."""
    if location_id not in repomap["LOCATIONS"]:
        return None

    model_id = model_id or repomap["DEFAULT_MODEL"]
    variable_id = variable_id or repomap["DEFAULT_VARIABLE"]

    if not is_safe_path(os.path.join(repomap["CACHE_DIR"], location_id, model_id), run_id):
        logger.warning(f"Potential path traversal attempt with run_id: {run_id}")
        return None

    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    values_path = os.path.join(run_dir, variable_id, "center_values.json")
    if not os.path.exists(values_path):
        legacy_path = os.path.join(repomap["CACHE_DIR"], location_id, run_id, "center_values.json")
        if not os.path.exists(legacy_path):
            return None
        values_path = legacy_path

    try:
        with open(values_path, "r") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.warning(f"Error reading center values file {values_path}: {e}")
        return None

def get_local_time_text(utc_time_str: str) -> str:
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

def handle_error(error_message: str, status_code: int = 500):
    """Standardized error handling function"""
    logger.error(error_message)
    if request.headers.get('Accept') == 'application/json':
        return jsonify({
            "error": error_message,
            "status": status_code
        }), status_code
    return f"Error: {error_message}", status_code

def get_variable_categories() -> dict[str, Any]:
    """Build a categorized list of available weather variables."""
    categories_config = repomap.get("WEATHER_CATEGORIES", {})
    variables_config = repomap.get("WEATHER_VARIABLES", {})

    categories = {
        category_id: {"name": name, "variables": []}
        for category_id, name in categories_config.items()
    }

    for variable_id, variable in variables_config.items():
        category_id = variable.get("category", "other")
        if category_id not in categories:
            categories[category_id] = {
                "name": category_id.replace("_", " ").title(),
                "variables": [],
            }
        categories[category_id]["variables"].append(variable_id)

    for category in categories.values():
        category["variables"].sort()

    variables_payload = {}
    for variable_id, variable in variables_config.items():
        variables_payload[variable_id] = {
            "display_name": variable.get("display_name", variable_id),
            "units": variable.get("units", ""),
            "category": variable.get("category", "other"),
        }

    return {"categories": categories, "variables": variables_payload}


def get_model_payload() -> dict[str, Any]:
    return {
        "models": {
            model_id: {
                "name": model["name"],
                "max_forecast_hours": model["max_forecast_hours"],
                "update_frequency_hours": model["update_frequency_hours"],
            }
            for model_id, model in repomap["MODELS"].items()
        }
    }


def get_available_models_for_location(location_id: str) -> dict[str, Any]:
    """Return model metadata filtered to models that have runs for this location."""
    all_models = get_model_payload()["models"]
    available: dict[str, Any] = {}
    for model_id in repomap["MODELS"].keys():
        runs = get_location_runs(location_id, model_id)
        if runs:
            available[model_id] = all_models.get(model_id, {
                "name": repomap["MODELS"][model_id]["name"],
                "max_forecast_hours": repomap["MODELS"][model_id]["max_forecast_hours"],
                "update_frequency_hours": repomap["MODELS"][model_id]["update_frequency_hours"],
            })
    return available

def get_available_variables_for_run(location_id: str, model_id: str, run_id: str) -> list[str]:
    """Detect which variables have data for a given run by inspecting cache."""
    available: list[str] = []
    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not os.path.isdir(run_dir):
        legacy_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
        if os.path.isdir(legacy_dir):
            run_dir = legacy_dir
        else:
            return available

    for var_id in repomap["WEATHER_VARIABLES"].keys():
        vdir = os.path.join(run_dir, var_id)
        if not os.path.isdir(vdir):
            continue
        # valid_times.txt with any lines
        vt = os.path.join(vdir, "valid_times.txt")
        has_valid_times = os.path.exists(vt) and os.path.getsize(vt) > 0
        # center_values.json with any values
        has_values = False
        cv = os.path.join(vdir, "center_values.json")
        if os.path.exists(cv):
            try:
                with open(cv, "r") as f:
                    payload = json.load(f)
                vals = payload.get("values", [])
                has_values = isinstance(vals, list) and len(vals) > 0
            except Exception:
                has_values = False
        # any frame PNG
        has_frames = False
        try:
            for name in os.listdir(vdir):
                if name.startswith("frame_") and name.endswith(".png"):
                    fpath = os.path.join(vdir, name)
                    if os.path.getsize(fpath) >= repomap["MIN_PNG_FILE_SIZE_BYTES"]:
                        has_frames = True
                        break
        except Exception:
            pass
        if has_valid_times or has_values or has_frames:
            available.append(var_id)
    return sorted(available)

def get_variable_categories_filtered(allowed_vars: list[str]) -> dict[str, Any]:
    """Build variable categories limited to allowed variable IDs."""
    base = get_variable_categories()
    allowed = set(allowed_vars)
    categories = {}
    for cat_id, cat in base["categories"].items():
        filtered = [vid for vid in cat.get("variables", []) if vid in allowed]
        if filtered:
            categories[cat_id] = {"name": cat.get("name", cat_id), "variables": filtered}
    variables_payload = {vid: base["variables"][vid] for vid in allowed if vid in base["variables"]}
    return {"categories": categories, "variables": variables_payload}

def get_layer_payload() -> dict[str, Any]:
    layers = repomap.get("MAP_LAYERS", {})
    return {
        "layers": {
            layer_id: {
                "name": layer["name"],
                "type": layer.get("type", "tile"),
                "url": layer.get("url"),
                "attribution": layer.get("attribution"),
                "max_zoom": layer.get("max_zoom"),
                "min_zoom": layer.get("min_zoom"),
                "opacity": layer.get("opacity", 0.7),
            }
            for layer_id, layer in layers.items()
        }
    }


def summary_has_data(payload: dict[str, Any]) -> bool:
    summary = payload.get("summary", {})
    temp_range = summary.get("temperature_range_f", {})
    values = [
        summary.get("total_snowfall_inches"),
        summary.get("total_precipitation_inches"),
        summary.get("max_wind_gust_mph"),
        temp_range.get("min"),
        temp_range.get("max"),
    ]
    return any(value is not None for value in values)


def get_grib_path(
    location_id: str,
    model_id: str,
    run_id: str,
    variable_id: str,
    hour: int,
) -> Optional[str]:
    run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    forecast_hour = format_forecast_hour(hour, model_id)
    grib_path = os.path.join(run_cache_dir, variable_id, f"grib_{forecast_hour}.grib2")
    if os.path.exists(grib_path):
        return grib_path
    legacy_path = os.path.join(repomap["CACHE_DIR"], location_id, run_id, f"grib_{forecast_hour}.grib2")
    if os.path.exists(legacy_path):
        return legacy_path
    return None


def extract_point_value(
    grib_path: str,
    lat: float,
    lon: float,
    variable_config: dict[str, Any],
) -> tuple[Optional[float], Optional[str]]:
    """Extract forecast value at a lat/lon point.

    Handles both 1D indexed coordinates and 2D coordinate arrays (e.g., Lambert
    conformal projection used by HRRR).
    """
    try:
        import xarray as xr  # defer heavy import until needed
    except Exception as e:
        raise RuntimeError("xarray/cfgrib not available; install scientific deps to use GRIB extraction") from e
    ds = xr.open_dataset(grib_path, engine="cfgrib")
    try:
        data = select_variable_from_dataset(ds, variable_config)
        conversion = variable_config.get("conversion")
        if conversion:
            data = convert_units(data, conversion)

        target_lon = lon
        lon_min = float(data.longitude.min())
        lon_max = float(data.longitude.max())
        if lon_min >= 0 and lon < 0:
            target_lon = lon + 360
        elif lon_max > 180 and lon < 0:
            target_lon = lon + 360

        # Check if coordinates are 2D (projected data like HRRR Lambert conformal)
        if data.latitude.ndim == 2:
            # Find nearest point using distance calculation on 2D arrays
            lat_diff = data.latitude.values - lat
            lon_diff = data.longitude.values - target_lon
            distance = np.sqrt(lat_diff**2 + lon_diff**2)
            min_idx = np.unravel_index(np.argmin(distance), distance.shape)
            point_value = data.values[min_idx]
        else:
            # Standard 1D indexed coordinates
            point_value = data.sel(latitude=lat, longitude=target_lon, method="nearest").values

        value = float(point_value)
        if np.isnan(value):
            return None, data.attrs.get("units")
        return value, variable_config.get("units") or data.attrs.get("units")
    finally:
        ds.close()


@app.before_request
def start_timer() -> None:
    request.start_time = time.perf_counter()


@app.after_request
def add_cache_headers(response):
    if response.mimetype in {"image/png", "application/geo+json"}:
        response.headers["Cache-Control"] = "public, max-age=3600"
        if response.mimetype == "image/png" and not response.direct_passthrough:
            response.headers["ETag"] = hashlib.md5(response.get_data()).hexdigest()

    if hasattr(request, "start_time"):
        latency = time.perf_counter() - request.start_time
        endpoint = request.path
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(latency)
        REQUEST_COUNT.labels(endpoint=endpoint, status=response.status_code).inc()

    return response

# --- Flask Endpoints ---

# --- Helpers for tile-backed rendering ---

def infer_region_for_latlon(lat: float, lon: float) -> Optional[str]:
    regions = repomap.get("TILING_REGIONS", {})
    for region_id, r in regions.items():
        if (
            r.get("lat_min") <= lat <= r.get("lat_max")
            and r.get("lon_min") <= lon <= r.get("lon_max")
        ):
            return region_id
    return next(iter(regions.keys()), None)


def match_region_for_latlon(lat: float, lon: float) -> Optional[str]:
    regions = repomap.get("TILING_REGIONS", {})
    for region_id, r in regions.items():
        if (
            r.get("lat_min") <= lat <= r.get("lat_max")
            and r.get("lon_min") <= lon <= r.get("lon_max")
        ):
            return region_id
    return None


def load_tile_run_metadata(
    base_dir: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
) -> dict[str, Any]:
    vars_info = list_tile_variables(base_dir, region_id, resolution_deg, model_id, run_id)
    for info in vars_info.values():
        meta_path = info.get("meta")
        if meta_path and os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
    return {}


def parse_run_id_to_init_dt(run_id: str) -> Optional[datetime]:
    try:
        _, d, h = run_id.split("_")
        return datetime.strptime(f"{d}{h}", "%Y%m%d%H").replace(tzinfo=pytz.UTC)
    except Exception:
        return None


def parse_init_time_utc(init_time_utc: Optional[str], run_id: str) -> Optional[datetime]:
    if init_time_utc:
        try:
            return datetime.strptime(init_time_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=pytz.UTC)
        except ValueError:
            pass
    return parse_run_id_to_init_dt(run_id)


def normalize_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

@app.route("/api/tile_runs/<model_id>")
@require_api_key
def api_tile_runs(model_id: str):
    """List available tile runs for a model (using default region/resolution)."""
    region_id = request.args.get("region", "ne")
    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
    return jsonify({"region": region_id, "resolution_deg": res, "runs": runs})

@app.route("/api/infer_region")
@require_api_key
def api_infer_region():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    regions = repomap.get("TILING_REGIONS", {})
    matched = match_region_for_latlon(lat, lon)
    region_id = matched or next(iter(regions.keys()), None)
    if not region_id:
        return jsonify({"error": "No regions configured"}), 400
    return jsonify({
        "region": region_id,
        "matched": bool(matched),
        "resolution_deg": regions[region_id].get("default_resolution_deg", 0.1),
    })

@app.route("/api/table/bylatlon")
@require_api_key
def api_table_bylatlon():
    """Return table-like timeseries for all variables at a lat/lon using tiles."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    model_id = request.args.get("model", repomap["DEFAULT_MODEL"])
    region_id = request.args.get("region")
    run_id = request.args.get("run") or None
    stat = request.args.get("stat", "mean")
    regions = repomap.get("TILING_REGIONS", {})
    if not region_id:
        inferred = infer_region_for_latlon(lat, lon)
        if not inferred:
            return jsonify({"error": "No regions configured"}), 400
        region_id = inferred
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    candidate_runs = []
    if run_id is None:
        candidate_runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
        if not candidate_runs:
            return jsonify({
                "error": "No tile runs available",
                "metadata": {"region": region_id, "model_id": model_id, "lat": lat, "lon": lon, "resolution_deg": res},
                "diagnostics": {
                    "region_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg"),
                    "models_with_runs": list_tile_models(repomap["TILES_DIR"], region_id, res),
                }
            }), 404
    else:
        candidate_runs = [run_id]

    chosen_run = None
    chosen_vars_info = {}
    for cand in candidate_runs:
        info = list_tile_variables(repomap["TILES_DIR"], region_id, res, model_id, cand)
        if info:
            chosen_run = cand
            chosen_vars_info = info
            break
    if not chosen_run:
        return jsonify({
            "error": "No variables present in available tile runs",
            "metadata": {"region": region_id, "model_id": model_id, "lat": lat, "lon": lon, "resolution_deg": res},
            "candidate_runs": candidate_runs,
            "diagnostics": {
                "region_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg"),
                "models_with_runs": list_tile_models(repomap["TILES_DIR"], region_id, res),
                "model_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg", model_id),
            }
        }), 404
    run_id = chosen_run

    # Limit to variables present in tiles for this run
    variables = list(chosen_vars_info.keys()) or list(repomap["WEATHER_VARIABLES"].keys())
    rows_by_hour: dict[int, dict[str, Any]] = {}
    first_hours = None
    variables_considered = list(repomap["WEATHER_VARIABLES"].keys())
    variables_found: list[str] = []
    tile_cell_info = None
    init_time_utc = None
    for var_id in variables:
        try:
            hours, values = load_timeseries_for_point(
                repomap["TILES_DIR"], region_id, res, model_id, run_id, var_id, lat, lon, stat=stat
            )
        except FileNotFoundError:
            continue
        # Compute and record tile cell lat/lon center for first found variable
        if tile_cell_info is None:
            # read meta to compute cell center
            res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
            meta_path = os.path.join(
                repomap["TILES_DIR"], region_id, res_dir, model_id, run_id, f"{var_id}.meta.json"
            )
            try:
                with open(meta_path, 'r') as f:
                    meta = json.load(f)
                lat_min_m = float(meta.get('lat_min'))
                lon_min_index = float(meta.get('index_lon_min', meta.get('lon_min')))
                lon_0_360 = bool(meta.get('lon_0_360', False))
                res_deg_m = float(meta.get('resolution_deg', res))
                init_time_utc = meta.get("init_time_utc")
                # recompute iy/ix consistent with tiles
                iy = int((lat - lat_min_m) // res_deg_m)
                target_lon = lon + 360.0 if (lon_0_360 and lon < 0) else lon
                ix = int((target_lon - lon_min_index) // res_deg_m)
                # cell center
                lat_center = lat_min_m + (iy + 0.5) * res_deg_m
                lon_center = (lon_min_index + (ix + 0.5) * res_deg_m)
                if lon_0_360 and lon_center > 180:
                    lon_center = lon_center - 360.0
                tile_cell_info = {
                    "iy": iy,
                    "ix": ix,
                    "lat_center": lat_center,
                    "lon_center": lon_center,
                    "lon_0_360": lon_0_360,
                }
            except Exception:
                tile_cell_info = None
        if first_hours is None:
            first_hours = hours
        for hour, val in zip(hours.tolist(), values.tolist()):
            if hour not in rows_by_hour:
                rows_by_hour[hour] = {"hour": int(hour)}
            rows_by_hour[hour][var_id] = None if (val is None or (isinstance(val, float) and np.isnan(val))) else val
        variables_found.append(var_id)

    if not rows_by_hour:
        return jsonify({
            "error": "No variables available at this point",
            "metadata": {
                "region": region_id,
                "model_id": model_id,
                "run_id": run_id,
                "stat": stat,
                "resolution_deg": res,
                "lat": lat,
                "lon": lon,
                "tiles_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg", model_id, run_id),
            },
            "diagnostics": {
                "candidate_runs": candidate_runs,
                "variables_present_in_run": list(chosen_vars_info.keys()),
            },
        }), 404

    hours_sorted = sorted(rows_by_hour.keys())
    rows = [rows_by_hour[h] for h in hours_sorted]
    missing_variables = [v for v in variables_considered if v not in variables_found]
    if init_time_utc is None and run_id:
        parsed = parse_run_id_to_init_dt(run_id)
        init_time_utc = parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else None
    tiles_dir = os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg", model_id, run_id) if run_id else None
    return jsonify({
        "metadata": {
            "region": region_id,
            "model_id": model_id,
            "run_id": run_id,
            "init_time_utc": init_time_utc,
            "stat": stat,
            "resolution_deg": res,
            "lat": lat,
            "lon": lon,
            "tiles_dir": tiles_dir,
        },
        "diagnostics": {
            "variables_considered": variables_considered,
            "variables_found": variables_found,
            "missing_variables": missing_variables,
            "hours_returned": hours_sorted,
            "variables_present_in_run": list(chosen_vars_info.keys()),
            "models_with_runs": list_tile_models(repomap["TILES_DIR"], region_id, res),
            "region_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg"),
            "model_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg", model_id),
            "tile_cell": tile_cell_info,
        },
        "rows": rows,
    })


@app.route("/api/table/multimodel")
@require_api_key
def api_table_multimodel():
    """Return a multi-model merged table by valid time for a lat/lon."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    stat = request.args.get("stat", "mean")
    region_id = request.args.get("region")
    regions = repomap.get("TILING_REGIONS", {})
    if not region_id:
        region_id = infer_region_for_latlon(lat, lon)
    if not region_id:
        return jsonify({"error": "No regions configured"}), 400
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))

    variable_ids = ["apcp", "snod", "t2m"]
    variables_meta = {
        var_id: {
            "display_name": repomap["WEATHER_VARIABLES"][var_id].get("display_name", var_id),
            "units": repomap["WEATHER_VARIABLES"][var_id].get("units"),
        }
        for var_id in variable_ids
        if var_id in repomap["WEATHER_VARIABLES"]
    }

    models_with_runs = list_tile_models(repomap["TILES_DIR"], region_id, res)
    if not models_with_runs:
        return jsonify({
            "error": "No tile models available for region",
            "metadata": {"region": region_id, "resolution_deg": res},
        }), 404

    rows_by_time: dict[str, dict[str, Any]] = {}
    model_payloads: dict[str, Any] = {}
    variables_found: set[str] = set()
    models_skipped: dict[str, str] = {}
    for model_id in sorted(models_with_runs.keys()):
        runs = models_with_runs[model_id]
        chosen_run = None
        chosen_vars_info: dict[str, Any] = {}
        for candidate in runs:
            info = list_tile_variables(repomap["TILES_DIR"], region_id, res, model_id, candidate)
            if any(var_id in info for var_id in variable_ids):
                chosen_run = candidate
                chosen_vars_info = info
                break
        if not chosen_run:
            models_skipped[model_id] = "no_matching_variables"
            continue
        variables_found.update([var_id for var_id in variable_ids if var_id in chosen_vars_info])

        run_meta = load_tile_run_metadata(repomap["TILES_DIR"], region_id, res, model_id, chosen_run)
        init_time_utc = run_meta.get("init_time_utc")
        init_dt = parse_init_time_utc(init_time_utc, chosen_run)
        if not init_dt:
            models_skipped[model_id] = "missing_init_time"
            continue
        if not init_time_utc:
            init_time_utc = init_dt.strftime("%Y-%m-%d %H:%M:%S")

        model_hours: set[int] = set()
        for var_id in variable_ids:
            if var_id not in chosen_vars_info:
                continue
            try:
                hours, values = load_timeseries_for_point(
                    repomap["TILES_DIR"],
                    region_id,
                    res,
                    model_id,
                    chosen_run,
                    var_id,
                    lat,
                    lon,
                    stat=stat,
                )
            except FileNotFoundError:
                continue
            for hour, value in zip(hours.tolist(), values.tolist()):
                model_hours.add(int(hour))
                valid_dt = init_dt + timedelta(hours=int(hour))
                valid_time = valid_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                row = rows_by_time.setdefault(valid_time, {"valid_time_utc": valid_time, "models": {}})
                row["models"].setdefault(model_id, {})[var_id] = normalize_value(value)

        model_payloads[model_id] = {
            "name": repomap["MODELS"].get(model_id, {}).get("name", model_id),
            "run_id": chosen_run,
            "init_time_utc": init_time_utc,
            "hours": sorted(model_hours),
            "available_variables": [var_id for var_id in variable_ids if var_id in chosen_vars_info],
        }

    if not model_payloads:
        return jsonify({
            "error": "No tile data available for requested models",
            "metadata": {"region": region_id, "resolution_deg": res},
        }), 404

    for row in rows_by_time.values():
        for model_id in model_payloads.keys():
            model_entry = row["models"].setdefault(model_id, {})
            for var_id in variable_ids:
                model_entry.setdefault(var_id, None)

    rows = sorted(rows_by_time.values(), key=lambda r: r["valid_time_utc"])
    return jsonify({
        "metadata": {
            "region": region_id,
            "resolution_deg": res,
            "stat": stat,
            "lat": lat,
            "lon": lon,
        },
        "diagnostics": {
            "models_with_runs": models_with_runs,
            "models_skipped": models_skipped,
            "variables_considered": variable_ids,
            "variables_found": sorted(variables_found),
            "rows_returned": len(rows),
            "region_dir": os.path.join(repomap["TILES_DIR"], region_id, f"{res:.3f}deg"),
        },
        "variables": variables_meta,
        "models": model_payloads,
        "rows": rows,
    })

@app.route("/table/geo")
@require_api_key
def table_geo_view():
    """Landing page that uses browser geolocation to render a table from tiles."""
    return render_template("table_geo.html")

@app.route("/explainer")
@require_api_key
def explainer_view():
    """Page explaining available models and variables."""
    # Get all configured locations to populate the nav
    locations = get_available_locations()
    
    # Get model info
    models = repomap.get("MODELS", {})
    
    # Get variable info categorized
    var_data = get_variable_categories()
    
    return render_template(
        "explainer.html",
        models=models,
        categories=var_data["categories"],
        variables=var_data["variables"],
        locations=locations
    )

@app.route("/api/tile_run_detail/<model_id>/<run_id>")
@require_api_key
def api_tile_run_detail(model_id: str, run_id: str):
    region_id = request.args.get("region", "ne")
    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    vars_info = list_tile_variables(repomap["TILES_DIR"], region_id, res, model_id, run_id)
    return jsonify({
        "region": region_id,
        "resolution_deg": res,
        "model_id": model_id,
        "run_id": run_id,
        "variables": vars_info,
    })

@app.route("/frame/tiles/<region_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@require_api_key
def frame_from_tiles(region_id: str, model_id: str, run_id: str, variable_id: str, hour: int):
    """Render a PNG for a given tiles slice (stat=mean|min|max)."""
    stat = request.args.get("stat", "mean")
    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return handle_error("Invalid region", 400)
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    try:
        arr2d, bounds = load_grid_slice(repomap["TILES_DIR"], region_id, res, model_id, run_id, variable_id, hour, stat=stat)
    except (FileNotFoundError, IndexError) as exc:
        return handle_error(str(exc), 404)

    # Render via matplotlib to PNG in-memory
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from io import BytesIO

    var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
    cmap = get_colormap(var_cfg)
    vmin = var_cfg.get("vmin")
    vmax = var_cfg.get("vmax")
    fig, ax = plt.subplots(figsize=(6, 5))
    extent = [bounds["lon_min"], bounds["lon_max"], bounds["lat_min"], bounds["lat_max"]]
    im = ax.imshow(arr2d, origin='lower', extent=extent, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(f"{model_id.upper()} {variable_id} h{hour:02d} ({stat})")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    buf = BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route("/frame/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@require_api_key
@limiter.limit("100 per minute")
def get_frame(location_id: str, model_id: str, run_id: str, variable_id: str, hour: int):
    """Serve a single forecast frame for a specific location and run."""
    try:
        logger.info(
            "Requesting frame for location %s, model %s, run %s, variable %s, hour %s",
            location_id,
            model_id,
            run_id,
            variable_id,
            hour,
        )
        
        if location_id not in repomap["LOCATIONS"]:
            logger.warning(f'Invalid location requested: {location_id}')
            return "Invalid location", 400

        if model_id not in repomap["MODELS"]:
            logger.warning(f'Invalid model requested: {model_id}')
            return "Invalid model", 400

        if variable_id not in repomap["WEATHER_VARIABLES"]:
            logger.warning(f'Invalid variable requested: {variable_id}')
            return "Invalid variable", 400
            
        if not 1 <= hour <= repomap["MODELS"][model_id]["max_forecast_hours"]:
            logger.warning(f'Invalid forecast hour requested: {hour}')
            return "Invalid forecast hour", 400
            
        # Format hour as two digits
        hour_str = format_forecast_hour(hour, model_id)

        # Prefer tile-backed rendering when available
        loc_cfg = repomap["LOCATIONS"][location_id]
        region_id = infer_region_for_latlon(loc_cfg["center_lat"], loc_cfg["center_lon"]) or next(iter(repomap.get("TILING_REGIONS", {}) or {}), None)
        if region_id:
            res = repomap["TILING_REGIONS"][region_id].get("default_resolution_deg", 0.1)
            effective_run_id = run_id
            if effective_run_id == "latest":
                runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
                if runs:
                    effective_run_id = runs[0]
            if effective_run_id and isinstance(effective_run_id, str):
                try:
                    arr2d, bounds = load_grid_slice(
                        repomap["TILES_DIR"], region_id, res, model_id, effective_run_id, variable_id, hour, stat=request.args.get("stat", "mean")
                    )
                    # Render quickly
                    import matplotlib
                    matplotlib.use('Agg')
                    import matplotlib.pyplot as plt
                    from io import BytesIO
                    var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
                    cmap = get_colormap(var_cfg)
                    vmin = var_cfg.get("vmin")
                    vmax = var_cfg.get("vmax")
                    fig, ax = plt.subplots(figsize=(6, 5))
                    extent = [bounds["lon_min"], bounds["lon_max"], bounds["lat_min"], bounds["lat_max"]]
                    im = ax.imshow(arr2d, origin='lower', extent=extent, cmap=cmap, vmin=vmin, vmax=vmax)
                    ax.set_title(f"{model_id.upper()} {variable_id} h{hour:02d} (tiles)")
                    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                    buf = BytesIO()
                    plt.savefig(buf, format='png', bbox_inches='tight')
                    plt.close(fig)
                    buf.seek(0)
                    return send_file(buf, mimetype='image/png')
                except (FileNotFoundError, IndexError):
                    pass  # Fall back to legacy frame files

        # Handle "latest" run_id
        if run_id == "latest":
            latest_link = os.path.join(repomap["CACHE_DIR"], location_id, model_id, "latest")
            if os.path.islink(latest_link):
                run_id = os.readlink(latest_link)
            else:
                logger.warning(f'No latest run available for location: {location_id}')
                return "No latest run available", 404

        # Validate run_id to prevent path traversal
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id)
        if not is_safe_path(location_cache_dir, run_id):
            logger.warning(f'Potential path traversal attempt with run_id: {run_id}')
            return "Invalid run ID", 400

        # Check if the frame exists in cache
        run_cache_dir = os.path.join(location_cache_dir, run_id)
        frame_path = os.path.join(run_cache_dir, variable_id, f"frame_{hour_str}.png")
        if not os.path.exists(frame_path):
            legacy_frame = os.path.join(
                repomap["CACHE_DIR"],
                location_id,
                run_id,
                f"frame_{hour_str}.png",
            )
            if os.path.exists(legacy_frame):
                frame_path = legacy_frame
        
        if not os.path.exists(frame_path):
            logger.warning(f'Frame not found in cache: {frame_path}')
            return "Forecast frame not available", 404
            
        return send_file(frame_path, mimetype="image/png")
    except (OSError, IOError, ValueError) as exc:
        logger.error(f'Unexpected error in get_frame: {str(exc)}', exc_info=True)
        return f"Internal server error: {str(exc)}", 500

@app.route("/frame/<location_id>/<run_id>/<variable_id>/<int:hour>")
def get_variable_frame(location_id: str, run_id: str, variable_id: str, hour: int):
    """Serve a frame for the default model."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], run_id, variable_id, hour)

@app.route("/frame/<location_id>/<run_id>/<int:hour>")
def get_latest_frame(location_id: str, run_id: str, hour: int):
    """Backward compatibility: serve a frame from the latest run with default variable."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], run_id, repomap["DEFAULT_VARIABLE"], hour)

@app.route("/frame/<location_id>/<int:hour>")
def get_latest_frame_short(location_id: str, hour: int):
    """Backward compatibility: serve a frame from the latest run."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], "latest", repomap["DEFAULT_VARIABLE"], hour)


@app.route("/tiles/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>/<int:z>/<int:x>/<int:y>.png")
@require_api_key
def get_tile(
    location_id: str,
    model_id: str,
    run_id: str,
    variable_id: str,
    hour: int,
    z: int,
    x: int,
    y: int,
):
    """Serve map tiles for interactive display."""
    tile_path = os.path.join(
        repomap["CACHE_DIR"],
        location_id,
        model_id,
        run_id,
        variable_id,
        "tiles",
        format_forecast_hour(hour, model_id),
        str(z),
        str(x),
        f"{y}.png",
    )
    if not os.path.exists(tile_path):
        return "", 204
    return send_file(tile_path, mimetype="image/png")


@app.route("/api/geojson/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@require_api_key
def get_geojson(
    location_id: str,
    model_id: str,
    run_id: str,
    variable_id: str,
    hour: int,
):
    """Serve GeoJSON contours for vector rendering."""
    geojson_path = os.path.join(
        repomap["CACHE_DIR"],
        location_id,
        model_id,
        run_id,
        variable_id,
        f"contours_{format_forecast_hour(hour, model_id)}.geojson",
    )
    if not os.path.exists(geojson_path):
        return jsonify({"error": "Data not available"}), 404
    return send_file(geojson_path, mimetype="application/geo+json")


@app.route("/api/value/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@require_api_key
def get_point_value(
    location_id: str,
    model_id: str,
    run_id: str,
    variable_id: str,
    hour: int,
):
    """Get forecast value at a specific lat/lon point."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    variable_config = repomap["WEATHER_VARIABLES"].get(variable_id)
    if variable_config is None:
        return jsonify({"error": "Invalid variable"}), 400

    grib_path = get_grib_path(location_id, model_id, run_id, variable_id, hour)
    if grib_path is None:
        return jsonify({"error": "GRIB data not available"}), 404

    value, units = extract_point_value(grib_path, lat, lon, variable_config)
    return jsonify({
        "lat": lat,
        "lon": lon,
        "value": value,
        "units": units,
        "variable": variable_id,
        "forecast_hour": hour,
    })


@app.route("/custom")
@require_api_key
def custom_region():
    """Allow users to specify a custom center point and zoom."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    zoom = request.args.get("zoom", default=1.5, type=float)
    if lat is None or lon is None:
        return handle_error("lat and lon are required", 400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return handle_error("Invalid lat/lon bounds", 400)
    if not (0.5 <= zoom <= 5):
        return handle_error("Zoom must be between 0.5 and 5", 400)

    nearest = find_nearest_location(lat, lon)
    return redirect(url_for("location_view", location_id=nearest, lat=lat, lon=lon, zoom=zoom))

@app.route("/location/<location_id>")
@require_api_key
def location_view(location_id: str):
    """Show forecast for a specific location"""
    if location_id not in repomap["LOCATIONS"]:
        return handle_error(f"Location '{location_id}' not found", 404)

    requested_model = request.args.get('model', repomap["DEFAULT_MODEL"])
    # Filter models to those available for this location
    models_available = get_available_models_for_location(location_id)
    if not models_available:
        return handle_error("Forecast data not available for this location", 404)
    model_id = requested_model if requested_model in models_available else next(iter(models_available))

    variable_id = request.args.get('variable', repomap["DEFAULT_VARIABLE"])
    if variable_id not in repomap["WEATHER_VARIABLES"]:
        variable_id = repomap["DEFAULT_VARIABLE"]

    # Get all runs for this location
    runs = get_location_runs(location_id, model_id)
    if not runs:
        return handle_error("Forecast data not available for this location", 404)
    
    # Default to the latest run
    run_id = request.args.get('run', runs[0]['run_id'])
    
    # Get metadata for the selected run
    metadata = get_run_metadata(location_id, run_id, model_id)
    if not metadata:
        return "Selected forecast run not available", 404
    
    location_config = repomap["LOCATIONS"][location_id]
    location_name = location_config["name"]
    init_time = metadata.get("init_time", "Unknown")

    custom_lat = request.args.get("lat", type=float)
    custom_lon = request.args.get("lon", type=float)
    custom_zoom = request.args.get("zoom", type=float)
    
    # Get valid times for this run
    valid_times = get_run_valid_times(location_id, run_id, model_id, variable_id)
    
    # Pre-fetch all valid times for all runs to avoid API calls
    all_valid_times = {}
    for run in runs:
        all_valid_times[run['run_id']] = get_run_valid_times(
            location_id,
            run['run_id'],
            model_id,
            variable_id,
        )
    
    # Determine available variables for this run and filter categories
    available_vars = get_available_variables_for_run(location_id, model_id, run_id)
    # Ensure selected variable is valid; if not, pick the first available
    if available_vars and variable_id not in available_vars:
        variable_id = available_vars[0]
        valid_times = get_run_valid_times(location_id, run_id, model_id, variable_id)
    variables_filtered = get_variable_categories_filtered(available_vars) if available_vars else get_variable_categories()

    # Get all available locations for the navigation (based on current model)
    locations = get_available_locations(model_id)
    
    return render_template(
        'location.html', 
        location_id=location_id,
        location_name=location_name,
        init_time=init_time,
        run_id=run_id,
        model_id=model_id,
        variable_id=variable_id,
        model_name=repomap["MODELS"][model_id]["name"],
        models=models_available,
        variables=variables_filtered,
        runs=runs,
        locations=locations,
        all_valid_times=all_valid_times,
        overlay_layers=get_layer_payload()["layers"],
        map_center_lat=custom_lat if custom_lat is not None else location_config["center_lat"],
        map_center_lon=custom_lon if custom_lon is not None else location_config["center_lon"],
        map_zoom=custom_zoom if custom_zoom is not None else location_config["zoom"],
    )

@app.route("/forecast")
@require_api_key
def forecast():
    """Multi-model forecast table view."""
    regions = repomap.get("TILING_REGIONS", {})
    default_region = next(iter(regions.keys()), "ne")
    return render_template(
        "forecast.html",
        default_region=default_region,
        regions=regions,
    )


@app.route("/summary/<location_id>")
@require_api_key
def summary_view(location_id: str):
    """Summary dashboard for a location."""
    if location_id not in repomap["LOCATIONS"]:
        return handle_error(f"Location '{location_id}' not found", 404)

    model_id = request.args.get("model", repomap["DEFAULT_MODEL"])
    if model_id not in repomap["MODELS"]:
        return handle_error(f"Model '{model_id}' not found", 404)

    runs = get_location_runs(location_id, model_id)
    if not runs:
        return handle_error("Forecast data not available for this location", 404)

    run_id = request.args.get("run", runs[0]["run_id"])
    summary_payload = summarize_run(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not summary_has_data(summary_payload):
        return handle_error("Summary data not available for this run", 404)

    locations = get_available_locations(model_id)
    location_config = repomap["LOCATIONS"][location_id]

    return render_template(
        "summary.html",
        location_id=location_id,
        location_name=location_config["name"],
        model_id=model_id,
        model_name=repomap["MODELS"][model_id]["name"],
        run_id=run_id,
        runs=runs,
        locations=locations,
        models=get_model_payload()["models"],
        summary=summary_payload["summary"],
        units=summary_payload.get("units", {}),
    )


@app.route("/table/<location_id>")
@app.route("/table/<location_id>/<model_id>")
@app.route("/table/<location_id>/<model_id>/<run_id>")
@require_api_key
def table_view(location_id: str, model_id: Optional[str] = None, run_id: Optional[str] = None):
    """Simple table view showing all forecast values for a location.

    This provides a cleaner, debug-friendly interface to inspect forecast
    data across all variables and forecast hours.
    """
    if location_id not in repomap["LOCATIONS"]:
        return handle_error(f"Location '{location_id}' not found", 404)

    model_id = model_id or repomap["DEFAULT_MODEL"]
    if model_id not in repomap["MODELS"]:
        return handle_error(f"Model '{model_id}' not found", 404)

    # Try tile-backed rows first
    loc = repomap["LOCATIONS"][location_id]
    region_id = infer_region_for_latlon(loc["center_lat"], loc["center_lon"]) or next(iter(repomap.get("TILING_REGIONS", {}) or {}), None)
    rows = []
    variables_present: list[str] = []
    tile_run_list = []
    init_time = "Unknown"
    if region_id:
        res = repomap["TILING_REGIONS"][region_id].get("default_resolution_deg", 0.1)
        tile_runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
        tile_run_list = [
            {"run_id": r, "init_time": parse_run_id_to_init_dt(r).strftime("%Y-%m-%d %H:%M:%S") if parse_run_id_to_init_dt(r) else r}
            for r in tile_runs
        ]
        if not run_id and tile_runs:
            run_id = tile_runs[0]
        if run_id:
            # Accumulate variable timeseries
            vals_by_var: dict[str, dict[int, float]] = {}
            hours_union: set[int] = set()
            for var_id in repomap["WEATHER_VARIABLES"].keys():
                try:
                    hours, vals = load_timeseries_for_point(
                        repomap["TILES_DIR"], region_id, res, model_id, run_id, var_id, loc["center_lat"], loc["center_lon"], stat="mean"
                    )
                except FileNotFoundError:
                    continue
                hlist = hours.tolist()
                vlist = vals.tolist()
                if not hlist:
                    continue
                vals_by_var[var_id] = {int(h): float(v) for h, v in zip(hlist, vlist)}
                hours_union.update(int(h) for h in hlist)
            if hours_union:
                variables_present = list(vals_by_var.keys())
                init_dt = parse_run_id_to_init_dt(run_id)
                if init_dt:
                    init_time = init_dt.strftime("%Y-%m-%d %H:%M:%S")
                for h in sorted(hours_union):
                    row = {"hour": h, "valid_time": None}
                    if init_dt is not None:
                        row["valid_time"] = (init_dt + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
                    for var_id in variables_present:
                        val = vals_by_var[var_id].get(h)
                        cfg = repomap["WEATHER_VARIABLES"][var_id]
                        if val is None or np.isnan(val):
                            row[var_id] = "-"
                        else:
                            if cfg.get("units") in ("in", "in/hr", "mi"):
                                row[var_id] = f"{val:.2f} {cfg.get('units','')}"
                            elif cfg.get("units") in ("°F", "mph"):
                                row[var_id] = f"{val:.1f} {cfg.get('units','')}"
                            elif cfg.get("units") in ("dBZ", "J/kg", "m²/s²", "%"):
                                row[var_id] = f"{val:.0f} {cfg.get('units','')}"
                            else:
                                row[var_id] = f"{val:.1f} {cfg.get('units','')}"
                    rows.append(row)

    # Fallback to legacy center_values
    data = None
    if not rows:
        data = load_all_center_values(repomap["CACHE_DIR"], location_id, model_id, run_id)
        if not data.get("variables"):
            return handle_error("No forecast data available for this location/model", 404)
        rows = build_forecast_table(data)
        variables_present = [v for v in get_variable_display_order() if v in data.get("variables", {})]
        init_time = data["metadata"].get("init_time", "Unknown")

    # Get all runs for navigation
    runs = tile_run_list or get_location_runs(location_id, model_id)
    locations = get_available_locations(model_id)
    models_available = get_available_models_for_location(location_id)

    # Determine output format
    output_format = request.args.get("format", "html")
    if output_format == "json":
        return jsonify({
            "metadata": data.get("metadata", {}),
            "columns": ["hour", "valid_time"] + [
                v for v in get_variable_display_order() if v in data.get("variables", {})
            ],
            "rows": rows,
        })

    # For HTML, use template or raw HTML
    location_config = repomap["LOCATIONS"][location_id]
    return render_template(
        "table.html",
        location_id=location_id,
        location_name=location_config["name"],
        model_id=model_id,
        model_name=repomap["MODELS"][model_id]["name"],
        run_id=run_id or (data["metadata"].get("run_id", "unknown") if data else "unknown"),
        init_time=init_time,
        runs=runs,
        locations=locations,
        models=models_available,
        variables=repomap["WEATHER_VARIABLES"],
        variable_order=variables_present,
        rows=rows,
        weather_variables=repomap["WEATHER_VARIABLES"],
    )


@app.route("/api/table/<location_id>")
@app.route("/api/table/<location_id>/<model_id>")
@app.route("/api/table/<location_id>/<model_id>/<run_id>")
@require_api_key
def api_table(location_id: str, model_id: Optional[str] = None, run_id: Optional[str] = None):
    """API endpoint to get tabular forecast data for a location."""
    if location_id not in repomap["LOCATIONS"]:
        return handle_error(f"Location '{location_id}' not found", 404)

    model_id = model_id or repomap["DEFAULT_MODEL"]
    if model_id not in repomap["MODELS"]:
        return handle_error(f"Model '{model_id}' not found", 404)

    data = load_all_center_values(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not data.get("variables"):
        return handle_error("No forecast data available", 404)

    rows = build_forecast_table(data)
    return jsonify({
        "metadata": data.get("metadata", {}),
        "columns": ["hour", "valid_time"] + [
            v for v in get_variable_display_order() if v in data.get("variables", {})
        ],
        "rows": rows,
    })


@app.route("/api/runs/<location_id>")
@app.route("/api/runs/<location_id>/<model_id>")
@require_api_key
def api_runs(location_id: str, model_id: Optional[str] = None):
    """API endpoint to get all runs for a location"""
    runs = get_location_runs(location_id, model_id)
    return jsonify(runs)

@app.route("/api/valid_times/<location_id>/<run_id>")
@app.route("/api/valid_times/<location_id>/<model_id>/<run_id>/<variable_id>")
@require_api_key
def api_valid_times(
    location_id: str,
    run_id: str,
    model_id: Optional[str] = None,
    variable_id: Optional[str] = None,
):
    """API endpoint to get valid times for a specific run"""
    valid_times = get_run_valid_times(location_id, run_id, model_id, variable_id)
    return jsonify(valid_times)

@app.route("/api/center_values/<location_id>")
@app.route("/api/center_values/<location_id>/<model_id>")
@require_api_key
def api_center_values(location_id: str, model_id: Optional[str] = None):
    """API endpoint to get center-point values for all runs for a location."""
    if model_id is not None and model_id not in repomap["MODELS"]:
        center_values = get_run_center_values(location_id, model_id)
        if not center_values:
            return handle_error("Center values not available for this run", 404)
        return jsonify(center_values)

    runs = get_location_runs(location_id, model_id)
    if not runs:
        return jsonify([])

    response = []
    for run in runs:
        run_id = run["run_id"]
        center_values = get_run_center_values(location_id, run_id, model_id)
        if center_values:
            response.append(center_values)
    return jsonify(response)

@app.route("/api/center_values/<location_id>/<run_id>")
@app.route("/api/center_values/<location_id>/<model_id>/<run_id>/<variable_id>")
@require_api_key
def api_center_values_run(
    location_id: str,
    run_id: str,
    model_id: Optional[str] = None,
    variable_id: Optional[str] = None,
):
    """API endpoint to get center-point values for a specific run."""
    center_values = get_run_center_values(location_id, run_id, model_id, variable_id)
    if not center_values:
        return handle_error("Center values not available for this run", 404)
    return jsonify(center_values)

@app.route("/api/locations")
@require_api_key
def api_locations():
    """API endpoint to get all available locations"""
    locations = get_available_locations()
    return jsonify(locations)


@app.route("/api/alerts/<location_id>")
@require_api_key
def api_alerts(location_id: str):
    """API endpoint to get NWS alerts for a location."""
    location = repomap["LOCATIONS"].get(location_id)
    if not location:
        return jsonify({"error": "Invalid location"}), 400
    alerts = get_alerts_for_location(location["center_lat"], location["center_lon"])
    return jsonify(alerts)

@app.route("/api/variables")
@app.route("/api/variables/<location_id>/<run_id>")
@app.route("/api/variables/<location_id>/<model_id>/<run_id>")
@require_api_key
def api_variables(
    location_id: Optional[str] = None,
    run_id: Optional[str] = None,
    model_id: Optional[str] = None,
):
    """API endpoint to get available weather variables."""
    if location_id is not None:
        if location_id not in repomap["LOCATIONS"]:
            return jsonify({"error": "Invalid location"}), 400
        model_id = model_id or repomap["DEFAULT_MODEL"]
        if model_id not in repomap["MODELS"]:
            return jsonify({"error": "Invalid model"}), 400
        if run_id == "latest":
            latest_link = os.path.join(repomap["CACHE_DIR"], location_id, model_id, "latest")
            if not os.path.islink(latest_link):
                return jsonify({"error": "No latest run available"}), 404
        elif run_id is not None:
            location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id)
            if not is_safe_path(location_cache_dir, run_id):
                logger.warning(f"Potential path traversal attempt with run_id: {run_id}")
                return jsonify({"error": "Invalid run ID"}), 400
            run_dir = os.path.join(location_cache_dir, run_id)
            if not os.path.exists(run_dir):
                return jsonify({"error": "Run not found"}), 404

    return jsonify(get_variable_categories())

@app.route("/api/models")
@require_api_key
def api_models():
    """API endpoint to get available model metadata."""
    return jsonify(get_model_payload())


@app.route("/api/layers")
@require_api_key
def api_layers():
    """API endpoint to get available map overlay layers."""
    return jsonify(get_layer_payload())


@app.route("/api/regions")
@require_api_key
def api_regions():
    """Return configured tiling regions and defaults."""
    return jsonify(repomap.get("TILING_REGIONS", {}))


@app.route("/api/tile_models")
@require_api_key
def api_tile_models():
    """Return models that have tile runs under a region/resolution."""
    region_id = request.args.get("region", "ne")
    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400
    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    return jsonify(list_tile_models(repomap["TILES_DIR"], region_id, res))


@app.route("/api/summary/<location_id>")
@app.route("/api/summary/<location_id>/<model_id>/<run_id>")
@require_api_key
def api_summary(location_id: str, model_id: Optional[str] = None, run_id: Optional[str] = None):
    """API endpoint to return summary metrics for a run."""
    if location_id not in repomap["LOCATIONS"]:
        return handle_error(f"Location '{location_id}' not found", 404)

    model_id = model_id or repomap["DEFAULT_MODEL"]
    if model_id not in repomap["MODELS"]:
        return handle_error(f"Model '{model_id}' not found", 404)

    runs = get_location_runs(location_id, model_id)
    if not runs:
        return handle_error("Forecast data not available for this location", 404)

    run_id = run_id or runs[0]["run_id"]
    summary_payload = summarize_run(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not summary_has_data(summary_payload):
        return handle_error("Summary data not available for this run", 404)

    return jsonify(summary_payload)

@app.route("/health")
def health_check():
    """Health check endpoint for monitoring"""
    # Also report tile runs for default model/region
    regions = repomap.get("TILING_REGIONS", {})
    default_region = next(iter(regions)) if regions else None
    tile_runs = []
    if default_region:
        res = regions[default_region].get("default_resolution_deg", 0.1)
        tile_runs = list_tile_runs(repomap["TILES_DIR"], default_region, res, repomap["DEFAULT_MODEL"])
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "locations_count": len(get_available_locations()),
        "tile_runs": tile_runs,
    })


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain'}

@app.route("/")
@require_api_key
def index():
    """Home page showing available locations"""
    locations = get_available_locations()
    return render_template('index.html', locations=locations)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
