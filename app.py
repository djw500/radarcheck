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

from flask import Flask, render_template, request, jsonify
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
from tiles import (
    load_timeseries_for_point,
    list_tile_runs,
    list_tile_variables,
    list_tile_models,
)
from status_utils import (
    scan_cache_status,
    get_disk_usage,
    read_scheduler_logs,
    read_scheduler_status,
    get_scheduled_runs_status,
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


# --- Snow derivation helpers ---
def _temp_to_slr(t_f: np.ndarray) -> np.ndarray:
    """Conservative temperature-based snow-to-liquid ratio (SLR)."""
    slr = np.full_like(t_f, 10.0, dtype=float)
    slr = np.where(t_f >= 33.0, 0.0, slr)
    slr = np.where((t_f >= 31.0) & (t_f < 33.0), 6.0, slr)
    slr = np.where((t_f >= 28.0) & (t_f < 31.0), 8.0, slr)
    slr = np.where((t_f >= 22.0) & (t_f < 28.0), 10.0, slr)
    slr = np.where(t_f < 22.0, 12.0, slr)
    return slr


def _forward_fill_nan(arr: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs in a 1D numpy array."""
    if arr.size == 0:
        return arr
    mask = np.isnan(arr)
    if not np.any(mask):
        return arr
    
    idx = np.where(~mask, np.arange(len(arr)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = arr[idx]
    
    if mask[0]:
        first_valid = np.where(~mask)[0]
        if first_valid.size > 0:
            out[:first_valid[0]] = 0.0
        else:
            out[:] = 0.0
    return out


def _accumulate_timeseries(values: np.ndarray) -> np.ndarray:
    """Convert potentially incremental/resetting cumulative series to strictly monotonic total accumulation."""
    vals = _forward_fill_nan(np.array(values, dtype=float))
    
    diffs = np.diff(vals)
    inc = np.where(diffs >= 0, diffs, vals[1:])
    
    total_inc = np.concatenate(([vals[0]], inc))
    
    total_inc = np.where(total_inc < 1e-3, 0.0, total_inc)
    
    return np.cumsum(total_inc)


def _derive_asnow_timeseries_from_tiles(
    region_id: str,
    res: float,
    model_id: str,
    run_id: str,
    lat: float,
    lon: float,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Derive accumulated snowfall series from APCP/CSNOW/T2M tiles."""
    try:
        h_apcp, v_apcp = load_timeseries_for_point(
            repomap["TILES_DIR"], region_id, res, model_id, run_id, "apcp", lat, lon
        )
    except FileNotFoundError:
        return None, None

    try:
        h_csnow, v_csnow = load_timeseries_for_point(
            repomap["TILES_DIR"], region_id, res, model_id, run_id, "csnow", lat, lon
        )
    except FileNotFoundError:
        return None, None

    try:
        h_t2m, v_t2m = load_timeseries_for_point(
            repomap["TILES_DIR"], region_id, res, model_id, run_id, "t2m", lat, lon
        )
    except FileNotFoundError:
        h_t2m, v_t2m = None, None

    all_hour_sets = [h_apcp]
    if h_csnow is not None:
        all_hour_sets.append(h_csnow)
    if h_t2m is not None:
        all_hour_sets.append(h_t2m)

    common_hours = np.unique(np.concatenate(all_hour_sets))
    common_hours = np.sort(common_hours)

    apcp_min, apcp_max = h_apcp.min(), h_apcp.max()
    common_hours = common_hours[(common_hours >= apcp_min) & (common_hours <= apcp_max)]

    if common_hours.size == 0:
        return None, None

    def align_and_interpolate(h, v, target_h, fill_value=0.0):
        if h is None or v is None:
            return np.full_like(target_h, fill_value, dtype=float)

        h_arr = np.array(h, dtype=float)
        v_arr = np.array(v, dtype=float)
        mask = ~np.isnan(v_arr)
        if not np.any(mask):
            return np.full_like(target_h, fill_value, dtype=float)

        return np.interp(target_h, h_arr[mask], v_arr[mask], left=fill_value, right=v_arr[mask][-1])

    apcp_aligned = align_and_interpolate(h_apcp, v_apcp, common_hours, fill_value=0.0)
    
    csnow_aligned = None
    if h_csnow is not None:
        csnow_aligned = align_and_interpolate(h_csnow, v_csnow, common_hours, fill_value=0.0)

    t2m_aligned = None
    if h_t2m is not None:
        t2m_aligned = align_and_interpolate(h_t2m, v_t2m, common_hours, fill_value=32.0)

    diffs = np.diff(apcp_aligned)
    inc_apcp_steps = np.where(diffs >= 0, diffs, apcp_aligned[1:])
    inc_apcp = np.concatenate(([apcp_aligned[0]], inc_apcp_steps))
    
    inc_apcp = np.where(inc_apcp < 1e-3, 0.0, inc_apcp)

    snow_frac = np.ones_like(inc_apcp, dtype=float)
    if csnow_aligned is not None:
        frac = np.clip(csnow_aligned, 0.0, 1.0)
        frac = np.where((frac > 0.0) & (frac < 1.0), frac, (csnow_aligned > 0.5).astype(float))
        snow_frac *= frac

    if t2m_aligned is not None:
        slr = _temp_to_slr(t2m_aligned)
        warm_mask = (slr <= 0.0).astype(float)
        snow_frac *= (1.0 - warm_mask)
    else:
        slr = np.full_like(inc_apcp, 8.0, dtype=float)

    snow_step = inc_apcp * snow_frac * slr
    snow_cum = np.cumsum(snow_step)

    return common_hours, snow_cum

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
    default_limits=["20000 per day", "5000 per hour"],
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
    return None


def parse_run_id_to_init_dt(run_id: str) -> Optional[datetime]:
    try:
        _, d, h = run_id.split("_")
        return datetime.strptime(f"{d}{h}", "%Y%m%d%H").replace(tzinfo=pytz.UTC)
    except Exception:
        return None

@app.route("/api/infer_region")
@require_api_key
def api_infer_region():
    """Infer the tiling region for a given lat/lon coordinate."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400

    region_id = infer_region_for_latlon(lat, lon)
    if not region_id:
        return jsonify({
            "error": "Point outside configured regions",
            "lat": lat,
            "lon": lon,
            "available_regions": list(repomap.get("TILING_REGIONS", {}).keys()),
        }), 404

    region = repomap["TILING_REGIONS"][region_id]
    return jsonify({
        "region_id": region_id,
        "region_name": region.get("name", region_id),
        "lat": lat,
        "lon": lon,
        "bounds": {
            "lat_min": region["lat_min"],
            "lat_max": region["lat_max"],
            "lon_min": region["lon_min"],
            "lon_max": region["lon_max"],
        },
        "default_resolution_deg": region.get("default_resolution_deg", 0.1),
    })


@app.route("/api/timeseries/multirun")
@require_api_key
def api_timeseries_multirun():
    """Return timeseries for multiple runs of a model at a lat/lon point."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400

    requested_model = request.args.get("model", "all")
    variable_id = request.args.get("variable", "asnow")
    region_id = request.args.get("region")
    # Default to last 24 hours of runs to keep the chart readable when showing multiple models
    days_back = float(request.args.get("days", 1.0))

    # Infer region if not provided
    if not region_id:
        region_id = infer_region_for_latlon(lat, lon)
        if not region_id:
            return jsonify({"error": "Point outside configured regions"}), 400

    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400

    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))

    # Determine which models to query
    models_to_query = []
    if requested_model == "all":
        # Get all models that have tiles in this region
        tile_models = list_tile_models(repomap["TILES_DIR"], region_id, res)
        models_to_query = list(tile_models.keys())
    elif requested_model in repomap["MODELS"]:
        models_to_query = [requested_model]
    else:
        return jsonify({"error": "Invalid model"}), 400

    results = {}
    cutoff = datetime.now(pytz.UTC) - timedelta(days=days_back)

    for model_id in models_to_query:
        all_runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
        
        # Select recent runs
        selected_runs = []
        for run_id in all_runs:
            init_dt = parse_run_id_to_init_dt(run_id)
            if init_dt and init_dt >= cutoff:
                selected_runs.append(run_id)
        
        for run_id in selected_runs:
            init_dt = parse_run_id_to_init_dt(run_id)
            if not init_dt:
                continue

            hours = None
            values = None
            
            # Try loading variable directly
            try:
                hours, values = load_timeseries_for_point(
                    repomap["TILES_DIR"], region_id, res, model_id, run_id, variable_id, lat, lon
                )
                # If variable is accumulation type (e.g. asnow, apcp), ensure strictly monotonic
                # accumulation to handle resets in source data (e.g. NBM 6h buckets).
                var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
                if var_cfg.get("is_accumulation") and values is not None:
                    values = _accumulate_timeseries(values)

            except FileNotFoundError:
                if variable_id == "asnow":
                    # Temperature-aware derived snowfall
                    hours, values = _derive_asnow_timeseries_from_tiles(
                        region_id, res, model_id, run_id, lat, lon
                    )
            
            if hours is not None and values is not None:
                series = []
                for h, v in zip(hours.tolist(), values.tolist()):
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        continue
                    valid_time = init_dt + timedelta(hours=int(h))
                    series.append({
                        "valid_time": valid_time.isoformat(),
                        "forecast_hour": int(h),
                        "value": v
                    })
                
                if series:
                    # Key structure: model_id/run_id to ensure uniqueness
                    key = f"{model_id}/{run_id}"
                    results[key] = {
                        "model_id": model_id,
                        "run_id": run_id,
                        "init_time": init_dt.isoformat(),
                        "series": series
                    }

    return jsonify({
        "lat": lat,
        "lon": lon,
        "variable": variable_id,
        "region": region_id,
        "runs": results
    })


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
        "locations_count": len(repomap.get("LOCATIONS", {})),
        "tile_runs": tile_runs,
    })


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain'}

@app.route("/")
@require_api_key
def index():
    """Home page showing available locations"""
    return render_template('index.html')

@app.route("/api/status/scheduled")
@require_api_key
def api_status_scheduled():
    """Get status of scheduled runs vs cache."""
    region_id = request.args.get("region", "ne")
    results = get_scheduled_runs_status(region=region_id)
    return jsonify({"runs": results})


@app.route("/api/status/summary")
@require_api_key
def api_status_summary():
    """Get system status summary (cache, disk, scheduler)."""
    region_id = request.args.get("region", "ne")
    
    cache_status = scan_cache_status(region=region_id)
    disk_usage = get_disk_usage()
    scheduler_status = read_scheduler_status()
    
    return jsonify({
        "cache_status": cache_status,
        "disk_usage": disk_usage,
        "scheduler_status": scheduler_status,
        "timestamp": datetime.now(pytz.UTC).isoformat()
    })

@app.route("/status")
@require_api_key
def status_page():
    """Render system status dashboard."""
    return render_template("status.html")

@app.route("/api/status/logs")
@require_api_key
def api_status_logs():
    """Get recent scheduler logs."""
    try:
        lines = int(request.args.get("lines", 100))
    except ValueError:
        lines = 100
        
    log_data = read_scheduler_logs(lines=lines)
    return jsonify({"lines": log_data})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
