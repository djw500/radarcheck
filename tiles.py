from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple

import numpy as np
import xarray as xr
from filelock import FileLock

from config import repomap
from tile_db import init_db, list_tile_models_db, list_tile_runs_db
from utils import convert_units, time_function

logger = logging.getLogger(__name__)


def _extract_data_var(ds: xr.Dataset) -> xr.DataArray:
    """Extract the primary data variable from a Herbie xarray Dataset.

    Handles:
    - Wind speed: prefers si10 (computed by Herbie with_wind) over raw components
    - Unknown var names: NBM ASNOW/SNOWLR decode as 'unknown', just take first var
    """
    if "si10" in ds.data_vars:
        return ds["si10"]
    if "ws" in ds.data_vars:
        return ds["ws"]
    if not ds.data_vars:
        raise ValueError("No variables found in dataset")
    return ds[list(ds.data_vars)[0]]


def _grid_shape(lat_min: float, lat_max: float, lon_min: float, lon_max: float, res_deg: float) -> Tuple[int, int]:
    ny = int(np.ceil((lat_max - lat_min) / res_deg))
    nx = int(np.ceil((lon_max - lon_min) / res_deg))
    return ny, nx


def _prep_cell_index(    lat2d: np.ndarray,
    lon2d: np.ndarray,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    res_deg: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, int, int]:
    """
    Precompute mapping from native grid points to (iy, ix) tile indices.

    Returns:
      - order: indices to sort by cell_id
      - starts: segment start indices for reduceat
      - unique_ids: unique cell ids (iy*nx + ix)
      - valid_mask_flat: boolean mask on flattened native grid
      - n_cells, ny, nx: counts
    """
    ny, nx = _grid_shape(lat_min, lat_max, lon_min, lon_max, res_deg)

    lat_flat = lat2d.ravel()
    lon_flat = lon2d.ravel()

    # Normalize longitudes if dataset is 0..360
    if np.nanmin(lon_flat) >= 0 and lon_min < 0:
        lon_min_adj = 360.0 + lon_min
        lon_max_adj = 360.0 + lon_max
    else:
        lon_min_adj = lon_min
        lon_max_adj = lon_max

    valid = (
        (lat_flat >= lat_min) & (lat_flat < lat_max) &
        (lon_flat >= lon_min_adj) & (lon_flat < lon_max_adj)
    )
    iy = np.floor((lat_flat - lat_min) / res_deg).astype(np.int64)
    ix = np.floor((lon_flat - lon_min_adj) / res_deg).astype(np.int64)

    # Clamp indices, mask invalid
    iy = np.clip(iy, 0, max(ny - 1, 0))
    ix = np.clip(ix, 0, max(nx - 1, 0))

    # Consider only valid points
    valid_idx = np.where(valid)[0]
    if valid_idx.size == 0:
        # No points in region
        order = np.array([], dtype=np.int64)
        starts = np.array([], dtype=np.int64)
        unique_ids = np.array([], dtype=np.int64)
        return order, starts, unique_ids, valid, ny * nx, ny, nx

    iyv = iy[valid_idx]
    ixv = ix[valid_idx]
    cell_id = iyv * nx + ixv
    order = np.argsort(cell_id)
    sorted_ids = cell_id[order]
    # Segment starts for each unique id
    unique_ids, starts = np.unique(sorted_ids, return_index=True)
    return order, starts, unique_ids, valid, ny * nx, ny, nx


def _reduce_stats(values2d: np.ndarray, valid_mask: np.ndarray, order: np.ndarray, starts: np.ndarray, unique_ids: np.ndarray, n_cells: int, ny: int, nx: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if starts.size == 0:
        nan_grid = np.full((ny, nx), np.nan, dtype=np.float32)
        return nan_grid, nan_grid.copy(), nan_grid.copy()

    v = values2d.ravel()
    v = v[valid_mask]  # Filter to valid points first
    v = v[order]  # reorder to cell-grouped
    # Compute segment ends
    ends = np.empty_like(starts)
    ends[:-1] = starts[1:]
    ends[-1] = v.size

    # Means via sum/count
    sums = np.add.reduceat(v, starts)
    counts = ends - starts
    means = sums / np.maximum(counts, 1)

    # Mins/Maxes via reduceat
    mins = np.minimum.reduceat(v, starts)
    maxs = np.maximum.reduceat(v, starts)

    # Initialize with NaN and scatter
    mean_grid = np.full((ny * nx,), np.nan, dtype=np.float32)
    min_grid = np.full((ny * nx,), np.nan, dtype=np.float32)
    max_grid = np.full((ny * nx,), np.nan, dtype=np.float32)

    mean_grid[unique_ids] = means.astype(np.float32)
    min_grid[unique_ids] = mins.astype(np.float32)
    max_grid[unique_ids] = maxs.astype(np.float32)

    return min_grid.reshape(ny, nx), max_grid.reshape(ny, nx), mean_grid.reshape(ny, nx)


@time_function
def build_tiles_for_variable(
    datasets_by_hour: Dict[int, xr.Dataset],
    variable_config: Dict[str, Any],
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    res_deg: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[int], Dict[str, Any]]:
    """Build (min, max, mean) tiles for all hours for a single variable.

    Accepts pre-opened xarray Datasets (from Herbie) keyed by forecast hour.
    Returns arrays shaped (time, ny, nx) and the sorted hours list.
    """
    hours_sorted = sorted(datasets_by_hour.keys())
    if not hours_sorted:
        raise ValueError("No datasets provided")

    # Use first hour to get grid and precompute mapping
    ds0 = datasets_by_hour[hours_sorted[0]]
    da0 = _extract_data_var(ds0)

    # Determine conversion based on units if specified
    conversion = variable_config.get("conversion")
    by_units = variable_config.get("unit_conversions_by_units", {})
    src_units = da0.attrs.get("units") if hasattr(da0, "attrs") else None
    if src_units and src_units in by_units:
        conversion = by_units[src_units]

    if conversion:
        da0 = convert_units(da0, conversion)

    lat2d = np.array(da0.latitude)
    lon2d = np.array(da0.longitude)

    # Handle 1D coordinates (e.g. GFS) by broadcasting to 2D
    if lat2d.ndim == 1 and lon2d.ndim == 1:
        lat2d, lon2d = np.meshgrid(lat2d, lon2d, indexing='ij')

    order, starts, unique_ids, valid_mask_flat, n_cells, ny, nx = _prep_cell_index(
        lat2d, lon2d, lat_min, lat_max, lon_min, lon_max, res_deg
    )
    lon_0_360 = bool(np.nanmin(lon2d) >= 0)
    used_lon_min = lon_min if not lon_0_360 else (360.0 + lon_min if lon_min < 0 else lon_min)

    t = len(hours_sorted)
    mins = np.full((t, ny, nx), np.nan, dtype=np.float32)
    maxs = np.full((t, ny, nx), np.nan, dtype=np.float32)
    means = np.full((t, ny, nx), np.nan, dtype=np.float32)

    for ti, hour in enumerate(hours_sorted):
        ds = datasets_by_hour[hour]
        da = _extract_data_var(ds)
        if conversion:
            da = convert_units(da, conversion)
        v2d = np.array(da.values)
        mn, mx, mu = _reduce_stats(v2d, valid_mask_flat, order, starts, unique_ids, n_cells, ny, nx)
        mins[ti] = mn
        maxs[ti] = mx
        means[ti] = mu

    index_meta = {"lon_0_360": lon_0_360, "index_lon_min": used_lon_min}
    return mins, maxs, means, hours_sorted, index_meta


def _save_tiles_npz_internal(    npz_path: str,
    meta_path: str,
    region_id: str,
    variable_id: str,
    mins: np.ndarray,
    maxs: np.ndarray,
    means: np.ndarray,
    hours: List[int],
    meta: Dict[str, Any],
) -> None:
    try:
        region_stats = repomap.get("TILING_REGIONS", {}).get(region_id, {}).get("stats", ["min", "max", "mean"])  # type: ignore
    except Exception:
        region_stats = ["min", "max", "mean"]

    payload: Dict[str, Any] = {"hours": np.array(hours, dtype=np.int32)}
    if "mean" in region_stats:
        payload["means"] = means
    if "min" in region_stats:
        payload["mins"] = mins
    if "max" in region_stats:
        payload["maxs"] = maxs
    np.savez_compressed(npz_path, **payload)
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def upsert_tiles_npz(    base_dir: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    mins: np.ndarray,
    maxs: np.ndarray,
    means: np.ndarray,
    hours: List[int],
    meta: Dict[str, Any],
) -> Tuple[str, List[int]]:
    """Merge new hour(s) into an existing tile NPZ, or create it if absent."""
    res_dir = f"{resolution_deg:.3f}deg".rstrip("0").rstrip(".")
    out_dir = os.path.join(base_dir, region_id, res_dir, model_id, run_id)
    os.makedirs(out_dir, exist_ok=True)
    npz_path = os.path.join(out_dir, f"{variable_id}.npz")
    meta_path = os.path.join(out_dir, f"{variable_id}.meta.json")

    with FileLock(f"{npz_path}.lock"):
        new_hours = np.array(hours, dtype=np.int32)
        if not os.path.exists(npz_path):
            _save_tiles_npz_internal(
                npz_path,
                meta_path,
                region_id,
                variable_id,
                mins,
                maxs,
                means,
                hours,
                meta,
            )
            return npz_path, hours

        try:
            with np.load(npz_path) as data:
                existing_hours = data.get("hours", np.array([], dtype=np.int32))
                existing_means = data["means"] if "means" in data.files else None
                existing_mins = data["mins"] if "mins" in data.files else None
                existing_maxs = data["maxs"] if "maxs" in data.files else None
        except Exception:
            # Corrupt NPZ — overwrite with fresh data
            logger.warning(f"Corrupt NPZ at {npz_path}, overwriting with fresh data")
            _save_tiles_npz_internal(
                npz_path, meta_path, region_id, variable_id,
                mins, maxs, means, hours, meta,
            )
            return npz_path, hours

        merged_hours = sorted(set(existing_hours.tolist()) | set(new_hours.tolist()))
        hour_index = {h: i for i, h in enumerate(merged_hours)}
        time_len = len(merged_hours)
        ny, nx = means.shape[1], means.shape[2]

        def _merge(existing: np.ndarray | None, incoming: np.ndarray | None) -> np.ndarray | None:
            if incoming is None and existing is None:
                return None
            if incoming is None:
                return existing
            if existing is None:
                out = np.full((time_len, ny, nx), np.nan, dtype=np.float32)
            else:
                out = np.full((time_len, ny, nx), np.nan, dtype=np.float32)
                for idx, hour in enumerate(existing_hours.tolist()):
                    out[hour_index[hour]] = existing[idx]
            for idx, hour in enumerate(new_hours.tolist()):
                out[hour_index[hour]] = incoming[idx]
            return out

        merged_means = _merge(existing_means, means)
        merged_mins = _merge(existing_mins, mins)
        merged_maxs = _merge(existing_maxs, maxs)

        payload: Dict[str, Any] = {"hours": np.array(merged_hours, dtype=np.int32)}
        if merged_means is not None:
            payload["means"] = merged_means
        if merged_mins is not None:
            payload["mins"] = merged_mins
        if merged_maxs is not None:
            payload["maxs"] = merged_maxs

        np.savez_compressed(npz_path, **payload)
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    return npz_path, merged_hours

def load_timeseries_for_point(    base_dir: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    lat: float,
    lon: float,
    stat: str = "mean",
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Load a timeseries for the cell containing (lat, lon). Returns (hours, values).
    """
    res_dir = f"{resolution_deg:.3f}deg".rstrip("0").rstrip(".")
    npz_path = os.path.join(base_dir, region_id, res_dir, model_id, run_id, f"{variable_id}.npz")
    meta_path = os.path.join(base_dir, region_id, res_dir, model_id, run_id, f"{variable_id}.meta.json")
    if not os.path.exists(npz_path) or not os.path.exists(meta_path):
        raise FileNotFoundError(f"Tiles not found for {variable_id} at {npz_path}")
    with open(meta_path, "r") as f:
        meta = json.load(f)
    lat_min = meta["lat_min"]
    # Use indexing lon_min if present (handles 0-360 indexing)
    lon_min_index = meta.get("index_lon_min", meta.get("lon_min"))
    lon_0_360 = bool(meta.get("lon_0_360", False))
    try:
        npz_data = np.load(npz_path)
    except Exception:
        raise FileNotFoundError(f"Corrupt tile for {variable_id} at {npz_path}")
    with npz_data as d:
        hours = d["hours"]
        # Fallback to means when requested stat is not available
        key = "means"
        if stat == "min" and "mins" in d.files:
            key = "mins"
        elif stat == "max" and "maxs" in d.files:
            key = "maxs"
        arr = d[key]

        ny, nx = arr.shape[1], arr.shape[2]
        iy = int(np.floor((lat - lat_min) / meta["resolution_deg"]))
        # Normalize longitude if tiles were indexed on 0-360
        target_lon = lon + 360.0 if (lon_0_360 and lon < 0) else lon
        ix = int(np.floor((target_lon - lon_min_index) / meta["resolution_deg"]))
        iy = max(0, min(ny - 1, iy))
        ix = max(0, min(nx - 1, ix))
        values = arr[:, iy, ix]

        # If the exact point is missing (NaN) due to sparse grid vs tile resolution mismatch,
        # search for the nearest valid neighbor within a small radius.
        # This handles cases like GFS (0.25 deg) on 0.1 deg tiles where many cells are empty.
        if np.all(np.isnan(values)):
            search_radius = 3
            best_dist_sq = float('inf')

            y_min = max(0, iy - search_radius)
            y_max = min(ny - 1, iy + search_radius)
            x_min = max(0, ix - search_radius)
            x_max = min(nx - 1, ix + search_radius)

            for cy in range(y_min, y_max + 1):
                for cx in range(x_min, x_max + 1):
                    if cy == iy and cx == ix:
                        continue
                    cand_vals = arr[:, cy, cx]
                    if not np.all(np.isnan(cand_vals)):
                        dist_sq = (cy - iy)**2 + (cx - ix)**2
                        if dist_sq < best_dist_sq:
                            best_dist_sq = dist_sq
                            values = cand_vals

    return hours, values


def list_tile_runs(base_dir: str, region_id: str, resolution_deg: float, model_id: str) -> List[str]:
    conn = init_db(repomap.get("DB_PATH"))
    try:
        return list_tile_runs_db(conn, region_id, resolution_deg, model_id)
    finally:
        conn.close()


def list_tile_models(base_dir: str, region_id: str, resolution_deg: float) -> Dict[str, List[str]]:
    """Return models present under a region/resolution with their available runs."""
    conn = init_db(repomap.get("DB_PATH"))
    try:
        return list_tile_models_db(conn, region_id, resolution_deg)
    finally:
        conn.close()
