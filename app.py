from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
import pytz

from config import repomap, get_tile_resolution
from tiles import list_tile_runs

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.makedirs('logs', exist_ok=True)
file_handler = RotatingFileHandler('logs/app.log', maxBytes=1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
logger.addHandler(file_handler)

# --- App setup ---

app = Flask(__name__, static_folder='static', template_folder='templates')
logger.info('Application startup')

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["20000 per day", "5000 per hour"],
)

# --- Auth ---

API_KEY = os.environ.get("RADARCHECK_API_KEY")




# --- Prometheus metrics ---

def _get_or_create_counter(name, description, labels):
    try:
        return Counter(name, description, labels)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        raise


def _get_or_create_histogram(name, description, labels):
    try:
        return Histogram(name, description, labels)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        raise


REQUEST_COUNT = _get_or_create_counter(
    "radarcheck_requests_total", "Total requests", ["endpoint", "status"],
)
REQUEST_LATENCY = _get_or_create_histogram(
    "radarcheck_request_latency_seconds", "Request latency", ["endpoint"],
)


@app.before_request
def start_timer():
    request.start_time = time.perf_counter()


@app.after_request
def track_metrics(response):
    if hasattr(request, "start_time"):
        latency = time.perf_counter() - request.start_time
        endpoint = request.path
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(latency)
        REQUEST_COUNT.labels(endpoint=endpoint, status=response.status_code).inc()
    return response


# --- Register blueprints ---

from routes.forecast import forecast_bp
from routes.status import status_bp
from routes.writeup import writeup_bp

app.register_blueprint(forecast_bp)
app.register_blueprint(status_bp)
app.register_blueprint(writeup_bp)


# Apply auth globally (skip /health and /metrics which are unauthenticated)
@app.before_request
def check_api_key():
    if request.path in ("/health", "/metrics") or request.path.startswith("/static/"):
        return None
    if API_KEY is None:
        return None
    provided_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if provided_key != API_KEY:
        logger.warning(f"Invalid or missing API key attempt from {request.remote_addr}")
        return jsonify({"error": "Invalid or missing API key"}), 401


# --- Core routes ---

@app.route("/")
def index():
    return render_template('index.html')


@app.route("/health")
def health_check():
    regions = repomap.get("TILING_REGIONS", {})
    default_region = next(iter(regions)) if regions else None
    tile_runs = []
    if default_region:
        res = get_tile_resolution(default_region, repomap["DEFAULT_MODEL"])
        tile_runs = list_tile_runs(repomap["TILES_DIR"], default_region, res, repomap["DEFAULT_MODEL"])
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tile_runs": tile_runs,
    })


@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain'}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", 5001)))
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=True)
