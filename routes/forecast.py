from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from flask import Blueprint, request, jsonify
import numpy as np
import pytz

from config import repomap
from tiles import load_timeseries_for_point, list_tile_runs, list_tile_models

forecast_bp = Blueprint("forecast", __name__)


# --- Accumulation helpers ---

def _is_bucket_data(vals: np.ndarray) -> bool:
    """Detect if accumulation data is per-step buckets vs cumulative/resetting.

    Per-step (NBM): values go up and down freely (e.g., 0.8, 1.6, 1.9, 1.8, 1.5).
    Cumulative (HRRR/GFS): values mostly increase, with occasional resets to near-zero.
    Resetting (NAM): values increase within windows then drop, sawtooth pattern.

    Key insight: in bucket data, values after decreases remain a large fraction of the
    running maximum. In resetting data, values drop to small fractions of the running max
    (start of a new accumulation window).
    """
    diffs = np.diff(vals)
    decreases = diffs < -1e-3
    if not np.any(decreases):
        return False

    decrease_indices = np.where(decreases)[0]
    running_max = 0.0
    bucket_like_count = 0

    for i in decrease_indices:
        running_max = max(running_max, vals[i])
        new_val = vals[i + 1]
        if running_max > 1e-3 and new_val / running_max > 0.5:
            bucket_like_count += 1

    return bucket_like_count > len(decrease_indices) * 0.5


def _forward_fill_nan(values: np.ndarray) -> np.ndarray:
    """Forward-fill NaN values in a 1D array."""
    out = values.copy()
    mask = np.isnan(out)
    if mask.all():
        return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


def _accumulate_timeseries(values: np.ndarray) -> np.ndarray:
    """Convert potentially incremental/resetting cumulative series to strictly monotonic total accumulation."""
    vals = _forward_fill_nan(np.array(values, dtype=float))

    if _is_bucket_data(vals):
        increments = np.where(vals < 1e-3, 0.0, vals)
        return np.cumsum(increments)

    diffs = np.diff(vals)
    inc = np.where(diffs >= 0, diffs, vals[1:])
    total_inc = np.concatenate(([vals[0]], inc))
    total_inc = np.where(total_inc < 1e-3, 0.0, total_inc)
    return np.cumsum(total_inc)




# --- Region helpers ---

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


# --- Route ---

@forecast_bp.route("/api/timeseries/multirun")
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
    days_back = float(request.args.get("days", 1.0))

    if not region_id:
        region_id = infer_region_for_latlon(lat, lon)
        if not region_id:
            return jsonify({"error": "Point outside configured regions"}), 400

    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400

    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))

    models_to_query = []
    if requested_model == "all":
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

            try:
                hours, values = load_timeseries_for_point(
                    repomap["TILES_DIR"], region_id, res, model_id, run_id, variable_id, lat, lon
                )
                var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
                if var_cfg.get("is_accumulation") and values is not None:
                    values = _accumulate_timeseries(values)
            except FileNotFoundError:
                pass

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
