from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
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
import xarray as xr
import numpy as np

from config import repomap
from alerts import get_alerts_for_location
from plotting import select_variable_from_dataset
from summary import summarize_run
from utils import convert_units, format_forecast_hour
from forecast_table import (
    load_all_center_values,
    build_forecast_table,
    format_table_html,
    format_table_json,
    get_variable_display_order,
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

                # Check if forecast frames exist
                if model_id in repomap["MODELS"]:
                    default_variable = repomap["DEFAULT_VARIABLE"]
                    frame_dir = os.path.join(run_dir, default_variable)
                else:
                    frame_dir = run_dir

                max_hours = repomap["MODELS"].get(model_id, {}).get("max_forecast_hours", 24)
                has_frames = any(
                    os.path.exists(
                        os.path.join(frame_dir, f"frame_{format_forecast_hour(hour, model_id)}.png")
                    )
                    for hour in range(1, max_hours + 1)
                )
                
                if has_frames:
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

    model_id = request.args.get('model', repomap["DEFAULT_MODEL"])
    if model_id not in repomap["MODELS"]:
        return handle_error(f"Model '{model_id}' not found", 404)

    variable_id = request.args.get('variable', repomap["DEFAULT_VARIABLE"])
    if variable_id not in repomap["WEATHER_VARIABLES"]:
        return handle_error(f"Variable '{variable_id}' not found", 404)

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
    
    # Get all available locations for the navigation
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
        models=get_model_payload()["models"],
        variables=get_variable_categories(),
        runs=runs,
        locations=locations,
        all_valid_times=all_valid_times,
        overlay_layers=get_layer_payload()["layers"],
        map_center_lat=custom_lat if custom_lat is not None else location_config["center_lat"],
        map_center_lon=custom_lon if custom_lon is not None else location_config["center_lon"],
        map_zoom=custom_zoom if custom_zoom is not None else location_config["zoom"],
    )

@app.route("/forecast")
def forecast():
    """Legacy endpoint for GIF - redirect to main page"""
    return redirect(url_for('index'))


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

    # Load forecast data
    data = load_all_center_values(repomap["CACHE_DIR"], location_id, model_id, run_id)
    if not data.get("variables"):
        return handle_error("No forecast data available for this location/model", 404)

    # Build table rows
    rows = build_forecast_table(data)

    # Get all runs for navigation
    runs = get_location_runs(location_id, model_id)
    locations = get_available_locations(model_id)

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
        run_id=data["metadata"].get("run_id", "unknown"),
        init_time=data["metadata"].get("init_time", "Unknown"),
        runs=runs,
        locations=locations,
        models=get_model_payload()["models"],
        variables=data.get("variables", {}),
        variable_order=[v for v in get_variable_display_order() if v in data.get("variables", {})],
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
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "locations_count": len(get_available_locations())
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
