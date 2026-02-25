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
    # Tiny negative diffs (< 0.01) are floating-point noise, not real resets.
    # Clamp them to zero.  Real resets (NAM sawtooth) drop by whole inches.
    noise = (diffs < 0) & (diffs > -0.01)
    diffs_clean = diffs.copy()
    diffs_clean[noise] = 0.0
    inc = np.where(diffs_clean >= 0, diffs_clean, vals[1:])
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


@forecast_bp.route("/api/timeseries/stitched")
def api_timeseries_stitched():
    """Stitch accumulation across consecutive runs to compute total event snowfall.

    Each model run resets ASNOW to 0 at init time.  This endpoint chains runs:
    trust each run for 1 hour (its best forecast window), then hand off to the
    next run.  The latest run provides the remaining forecast.  Result: a single
    monotonic curve of total anticipated event snowfall.

    Query params: lat, lon, model, variable, region, resolution, days.
    """
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400

    model_id = request.args.get("model", "hrrr")
    variable_id = request.args.get("variable", "asnow")
    region_id = request.args.get("region")
    days_back = float(request.args.get("days", 2.0))

    if not region_id:
        region_id = infer_region_for_latlon(lat, lon)
        if not region_id:
            return jsonify({"error": "Point outside configured regions"}), 400

    regions = repomap.get("TILING_REGIONS", {})
    if region_id not in regions:
        return jsonify({"error": "Invalid region"}), 400

    res = float(request.args.get("resolution", regions[region_id].get("default_resolution_deg", 0.1)))
    cutoff = datetime.now(pytz.UTC) - timedelta(days=days_back)

    # ---------- Collect all runs ----------
    all_runs = list_tile_runs(repomap["TILES_DIR"], region_id, res, model_id)
    run_data = []  # (init_dt, run_id, n_points, {valid_time: accum_value})

    for run_id in all_runs:
        init_dt = parse_run_id_to_init_dt(run_id)
        if not init_dt or init_dt < cutoff:
            continue
        try:
            hours, values = load_timeseries_for_point(
                repomap["TILES_DIR"], region_id, res, model_id, run_id, variable_id, lat, lon
            )
            var_cfg = repomap["WEATHER_VARIABLES"].get(variable_id, {})
            if var_cfg.get("is_accumulation") and values is not None:
                values = _accumulate_timeseries(values)
        except FileNotFoundError:
            continue

        if hours is None or values is None:
            continue

        point_map = {}
        for h, v in zip(hours.tolist(), values.tolist()):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            vt = init_dt + timedelta(hours=int(h))
            point_map[vt] = v

        if point_map:
            run_data.append((init_dt, run_id, len(point_map), point_map))

    if not run_data:
        return jsonify({"error": "No data available"}), 404

    run_data.sort(key=lambda x: x[0])

    # ---------- Find latest extended run (the forecast) ----------
    # Extended runs are synoptic cycles (00/06/12/18Z).  Use the latest one
    # even if it's still ingesting — it'll have more data next refresh.
    SYNOPTIC_HOURS = {0, 6, 12, 18}
    latest_extended = None
    for init_dt, run_id, npts, point_map in reversed(run_data):
        if init_dt.hour in SYNOPTIC_HOURS:
            latest_extended = (init_dt, run_id, npts, point_map)
            break

    if not latest_extended:
        return jsonify({"error": "No extended run available"}), 404

    ext_init = latest_extended[0]

    # ---------- Build baseline: chain 1-hour verified segments ----------
    # For each run before the latest extended run's init time, trust it
    # for 1 hour (until the next run takes over).  Sum those increments.
    pre_runs = [(i, r, n, m) for i, r, n, m in run_data if i < ext_init]
    baseline = 0.0

    for idx in range(len(pre_runs)):
        curr_init, _, _, curr_map = pre_runs[idx]
        next_init = pre_runs[idx + 1][0] if idx + 1 < len(pre_runs) else ext_init

        # What did this run accumulate in its 1-hour verified window?
        accum_at_handoff = 0.0
        for vt in sorted(curr_map.keys()):
            if vt <= next_init:
                accum_at_handoff = curr_map[vt]
            else:
                break
        baseline += accum_at_handoff

    # ---------- Result: baseline + latest extended run ----------
    series = []
    ext_map = latest_extended[3]
    for vt in sorted(ext_map.keys()):
        series.append({
            "valid_time": vt.isoformat(),
            "value": round(baseline + ext_map[vt], 2),
            "source_run": latest_extended[1],
        })

    event_total = max(baseline + v for v in ext_map.values()) if ext_map else 0.0

    return jsonify({
        "lat": lat,
        "lon": lon,
        "model": model_id,
        "variable": variable_id,
        "event_total": round(event_total, 2),
        "baseline_accumulated": round(baseline, 2),
        "latest_run": latest_extended[1],
        "runs_in_baseline": len(pre_runs),
        "series": series,
    })
