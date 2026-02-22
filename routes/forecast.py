from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from flask import Blueprint, request, jsonify
import numpy as np
import pytz

from config import repomap
from tiles import load_timeseries_for_point, list_tile_runs, list_tile_models

forecast_bp = Blueprint("forecast", __name__)


# --- Snow derivation helpers ---

def _temp_to_slr(t_f: np.ndarray) -> np.ndarray:  # USED
    """Conservative temperature-based snow-to-liquid ratio (SLR).
    Rough guidance from operational rules of thumb:
      - >=33 F: rain (0:1)
      - 31-33 F: 6:1
      - 28-31 F: 8:1
      - 22-28 F: 10:1
      - <22 F: 12:1 (cap)
    """
    slr = np.full_like(t_f, 10.0, dtype=float)
    slr = np.where(t_f >= 33.0, 0.0, slr)
    slr = np.where((t_f >= 31.0) & (t_f < 33.0), 6.0, slr)
    slr = np.where((t_f >= 28.0) & (t_f < 31.0), 8.0, slr)
    slr = np.where((t_f >= 22.0) & (t_f < 28.0), 10.0, slr)
    slr = np.where(t_f < 22.0, 12.0, slr)
    return slr


def _forward_fill_nan(arr: np.ndarray) -> np.ndarray:  # USED
    """Forward-fill NaNs in a 1D numpy array.
    Leading NaNs are converted to 0.0.
    """
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


def _is_bucket_data(vals: np.ndarray) -> bool:  # USED
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


def _accumulate_timeseries(values: np.ndarray) -> np.ndarray:  # USED
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


def _derive_asnow_timeseries_from_tiles(  # USED
    region_id: str,
    res: float,
    model_id: str,
    run_id: str,
    lat: float,
    lon: float,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Derive accumulated snowfall series from APCP/CSNOW/T2M tiles.

    - Detects whether APCP is cumulative or stepwise and converts to per-step deltas.
    - Gates by CSNOW when available.
    - Uses T2M to veto warm periods and modulate SLR.
    - Returns (hours, cumulative_snow_inches) or (None, None) if inputs missing.
    """
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
        h_csnow, v_csnow = None, None

    try:
        h_t2m, v_t2m = load_timeseries_for_point(
            repomap["TILES_DIR"], region_id, res, model_id, run_id, "t2m", lat, lon
        )
    except FileNotFoundError:
        h_t2m, v_t2m = None, None

    try:
        h_snod, v_snod = load_timeseries_for_point(
            repomap["TILES_DIR"], region_id, res, model_id, run_id, "snod", lat, lon
        )
    except FileNotFoundError:
        h_snod, v_snod = None, None

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

    if _is_bucket_data(apcp_aligned):
        inc_apcp = np.where(apcp_aligned < 1e-3, 0.0, apcp_aligned)
    else:
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


# --- Region helpers ---

def infer_region_for_latlon(lat: float, lon: float) -> Optional[str]:  # USED
    regions = repomap.get("TILING_REGIONS", {})
    for region_id, r in regions.items():
        if (
            r.get("lat_min") <= lat <= r.get("lat_max")
            and r.get("lon_min") <= lon <= r.get("lon_max")
        ):
            return region_id
    return None


def parse_run_id_to_init_dt(run_id: str) -> Optional[datetime]:  # USED
    try:
        _, d, h = run_id.split("_")
        return datetime.strptime(f"{d}{h}", "%Y%m%d%H").replace(tzinfo=pytz.UTC)
    except Exception:
        return None


# --- Route ---

@forecast_bp.route("/api/timeseries/multirun")
def api_timeseries_multirun():  # USED
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
                if variable_id == "asnow":
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
