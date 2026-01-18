import os
import json
import logging
from io import BytesIO
from datetime import datetime
from logging.handlers import RotatingFileHandler
from functools import wraps

from flask import Flask, send_file, render_template, redirect, url_for, request, abort, jsonify
import pytz

from config import repomap

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

def parse_metadata_file(filepath):
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

def is_safe_path(base_dir, user_path):
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

def get_available_locations(model_id=None):
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
                metadata_path = os.path.join(run_dir, "metadata.txt")
                
                if os.path.exists(metadata_path):
                    # Read metadata safely
                    metadata = parse_metadata_file(metadata_path)

                    # Check if forecast frames exist
                    if model_id in repomap["MODELS"]:
                        default_variable = repomap["DEFAULT_VARIABLE"]
                        frame_dir = os.path.join(run_dir, default_variable)
                    else:
                        frame_dir = run_dir

                    max_hours = repomap["MODELS"].get(model_id, {}).get("max_forecast_hours", 24)
                    has_frames = any(
                        os.path.exists(os.path.join(frame_dir, f"frame_{hour:02d}.png"))
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

def get_location_runs(location_id, model_id=None):
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
            metadata_path = os.path.join(run_dir, "metadata.txt")
            
            if os.path.exists(metadata_path):
                metadata = parse_metadata_file(metadata_path)

                # Check if this run has frames
                default_variable = repomap["DEFAULT_VARIABLE"]
                frame_dir = os.path.join(run_dir, default_variable)
                if not os.path.exists(frame_dir):
                    frame_dir = run_dir

                max_hours = repomap["MODELS"].get(model_id, {}).get("max_forecast_hours", 24)
                has_frames = any(
                    os.path.exists(os.path.join(frame_dir, f"frame_{hour:02d}.png"))
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

def get_run_metadata(location_id, run_id, model_id=None):
    """Get metadata for a specific run"""
    if location_id not in repomap["LOCATIONS"]:
        return None

    model_id = model_id or repomap["DEFAULT_MODEL"]
    # Validate run_id to prevent path traversal
    if not is_safe_path(os.path.join(repomap["CACHE_DIR"], location_id, model_id), run_id):
        logger.warning(f"Potential path traversal attempt with run_id: {run_id}")
        return None

    run_dir = os.path.join(repomap["CACHE_DIR"], location_id, model_id, run_id)
    metadata_path = os.path.join(run_dir, "metadata.txt")

    if not os.path.exists(metadata_path):
        legacy_metadata = os.path.join(repomap["CACHE_DIR"], location_id, run_id, "metadata.txt")
        if not os.path.exists(legacy_metadata):
            return None
        metadata_path = legacy_metadata

    return parse_metadata_file(metadata_path)

def get_run_valid_times(location_id, run_id, model_id=None, variable_id=None):
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

def get_run_center_values(location_id, run_id, model_id=None, variable_id=None):
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

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

def handle_error(error_message, status_code=500):
    """Standardized error handling function"""
    logger.error(error_message)
    if request.headers.get('Accept') == 'application/json':
        return jsonify({
            "error": error_message,
            "status": status_code
        }), status_code
    return f"Error: {error_message}", status_code

def get_variable_categories():
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


def get_model_payload():
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

# --- Flask Endpoints ---

@app.route("/frame/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@require_api_key
def get_frame(location_id, model_id, run_id, variable_id, hour):
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
        hour_str = f"{hour:02d}"
        
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
    except Exception as e:
        logger.error(f'Unexpected error in get_frame: {str(e)}', exc_info=True)
        return f"Internal server error: {str(e)}", 500

@app.route("/frame/<location_id>/<run_id>/<variable_id>/<int:hour>")
def get_variable_frame(location_id, run_id, variable_id, hour):
    """Serve a frame for the default model."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], run_id, variable_id, hour)

@app.route("/frame/<location_id>/<run_id>/<int:hour>")
def get_latest_frame(location_id, run_id, hour):
    """Backward compatibility: serve a frame from the latest run with default variable."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], run_id, repomap["DEFAULT_VARIABLE"], hour)

@app.route("/frame/<location_id>/<int:hour>")
def get_latest_frame_short(location_id, hour):
    """Backward compatibility: serve a frame from the latest run."""
    return get_frame(location_id, repomap["DEFAULT_MODEL"], "latest", repomap["DEFAULT_VARIABLE"], hour)

@app.route("/location/<location_id>")
@require_api_key
def location_view(location_id):
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
    
    location_name = repomap["LOCATIONS"][location_id]["name"]
    init_time = metadata.get("init_time", "Unknown")
    
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
    
    return render_template('location.html', 
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
                          all_valid_times=all_valid_times)

@app.route("/forecast")
def forecast():
    """Legacy endpoint for GIF - redirect to main page"""
    return redirect(url_for('index'))


@app.route("/api/runs/<location_id>")
@app.route("/api/runs/<location_id>/<model_id>")
@require_api_key
def api_runs(location_id, model_id=None):
    """API endpoint to get all runs for a location"""
    runs = get_location_runs(location_id, model_id)
    return jsonify(runs)

@app.route("/api/valid_times/<location_id>/<run_id>")
@app.route("/api/valid_times/<location_id>/<model_id>/<run_id>/<variable_id>")
@require_api_key
def api_valid_times(location_id, run_id, model_id=None, variable_id=None):
    """API endpoint to get valid times for a specific run"""
    valid_times = get_run_valid_times(location_id, run_id, model_id, variable_id)
    return jsonify(valid_times)

@app.route("/api/center_values/<location_id>")
@app.route("/api/center_values/<location_id>/<model_id>")
@require_api_key
def api_center_values(location_id, model_id=None):
    """API endpoint to get center-point values for all runs for a location."""
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
def api_center_values_run(location_id, run_id, model_id=None, variable_id=None):
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

@app.route("/api/variables")
@app.route("/api/variables/<location_id>/<run_id>")
@app.route("/api/variables/<location_id>/<model_id>/<run_id>")
@require_api_key
def api_variables(location_id=None, run_id=None, model_id=None):
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

@app.route("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "locations_count": len(get_available_locations())
    })

@app.route("/")
@require_api_key
def index():
    """Home page showing available locations"""
    locations = get_available_locations()
    return render_template('index.html', locations=locations)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
