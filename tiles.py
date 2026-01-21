from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

import numpy as np
import xarray as xr

from config import repomap
from plotting import select_variable_from_dataset
from utils import convert_units


def _grid_shape(lat_min: float, lat_max: float, lon_min: float, lon_max: float, res_deg: float) -> Tuple[int, int]:
    ny = int(np.ceil((lat_max - lat_min) / res_deg))
    nx = int(np.ceil((lon_max - lon_min) / res_deg))
    return ny, nx


def _prep_cell_index(
    lat2d: np.ndarray,
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


def _reduce_stats(values2d: np.ndarray, order: np.ndarray, starts: np.ndarray, unique_ids: np.ndarray, n_cells: int, ny: int, nx: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    v = values2d.ravel()
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


def build_tiles_for_variable(
    grib_paths_by_hour: Dict[int, str],
    variable_config: Dict[str, Any],
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    res_deg: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[int]]:
    """
    Build (min, max, mean) tiles for all hours for a single variable.

    Returns arrays shaped (time, ny, nx) and the sorted hours list.
    """
    hours_sorted = sorted(grib_paths_by_hour.keys())
    if not hours_sorted:
        raise ValueError("No GRIB paths provided")

    # Open first hour to get grid and precompute mapping
    first_path = grib_paths_by_hour[hours_sorted[0]]
    ds0 = xr.open_dataset(first_path, engine="cfgrib")
    da0 = select_variable_from_dataset(ds0, variable_config)

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
    order, starts, unique_ids, valid_mask_flat, n_cells, ny, nx = _prep_cell_index(
        lat2d, lon2d, lat_min, lat_max, lon_min, lon_max, res_deg
    )
    # Close dataset to free resources
    ds0.close()

    t = len(hours_sorted)
    mins = np.full((t, ny, nx), np.nan, dtype=np.float32)
    maxs = np.full((t, ny, nx), np.nan, dtype=np.float32)
    means = np.full((t, ny, nx), np.nan, dtype=np.float32)

    for ti, hour in enumerate(hours_sorted):
        path = grib_paths_by_hour[hour]
        ds = xr.open_dataset(path, engine="cfgrib")
        da = select_variable_from_dataset(ds, variable_config)
        if conversion:
            da = convert_units(da, conversion)
        # Reduce stats
        v2d = np.array(da.values)
        # Use same mapping as first hour
        mn, mx, mu = _reduce_stats(v2d, order, starts, unique_ids, n_cells, ny, nx)
        mins[ti] = mn
        maxs[ti] = mx
        means[ti] = mu
        ds.close()

    return mins, maxs, means, hours_sorted


def save_tiles_npz(
    base_dir: str,
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
) -> str:
    res_dir = f"{resolution_deg:.3f}deg".rstrip("0").rstrip(".")
    out_dir = os.path.join(base_dir, region_id, res_dir, model_id, run_id)
    os.makedirs(out_dir, exist_ok=True)
    npz_path = os.path.join(out_dir, f"{variable_id}.npz")
    np.savez_compressed(npz_path, mins=mins, maxs=maxs, means=means, hours=np.array(hours, dtype=np.int32))
    with open(os.path.join(out_dir, f"{variable_id}.meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return npz_path


def load_timeseries_for_point(
    base_dir: str,
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
    lon_min = meta["lon_min"]
    d = np.load(npz_path)
    hours = d["hours"]
    arr = d["means" if stat == "mean" else ("mins" if stat == "min" else "maxs")]

    ny, nx = arr.shape[1], arr.shape[2]
    iy = int(np.floor((lat - lat_min) / meta["resolution_deg"]))
    ix = int(np.floor((lon - lon_min) / meta["resolution_deg"]))
    iy = max(0, min(ny - 1, iy))
    ix = max(0, min(nx - 1, ix))
    values = arr[:, iy, ix]
    return hours, values


def load_grid_slice(
    base_dir: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    hour: int,
    stat: str = "mean",
) -> Tuple[np.ndarray, Dict[str, float]]:
    """
    Load a 2D grid slice for a given hour from tiles. Returns (array, meta_bounds).
    meta_bounds includes lat_min, lat_max, lon_min, lon_max and resolution_deg.
    """
    res_dir = f"{resolution_deg:.3f}deg".rstrip("0").rstrip(".")
    npz_path = os.path.join(base_dir, region_id, res_dir, model_id, run_id, f"{variable_id}.npz")
    meta_path = os.path.join(base_dir, region_id, res_dir, model_id, run_id, f"{variable_id}.meta.json")
    if not os.path.exists(npz_path) or not os.path.exists(meta_path):
        raise FileNotFoundError(f"Tiles not found for {variable_id} at {npz_path}")
    with open(meta_path, "r") as f:
        meta = json.load(f)
    d = np.load(npz_path)
    hours = d["hours"]
    # Map requested hour to index
    try:
        idx = int(np.where(hours == hour)[0][0])
    except Exception:
        raise IndexError(f"Hour {hour} not found in tiles; available: {hours.tolist()}")
    arr3d = d["means" if stat == "mean" else ("mins" if stat == "min" else "maxs")]
    slice2d = arr3d[idx]
    bounds = {
        "lat_min": meta["lat_min"],
        "lat_max": meta["lat_max"],
        "lon_min": meta["lon_min"],
        "lon_max": meta["lon_max"],
        "resolution_deg": meta["resolution_deg"],
    }
    return slice2d, bounds


def list_tile_runs(base_dir: str, region_id: str, resolution_deg: float, model_id: str) -> List[str]:
    res_dir = f"{resolution_deg:.3f}deg".rstrip("0").rstrip(".")
    model_dir = os.path.join(base_dir, region_id, res_dir, model_id)
    if not os.path.isdir(model_dir):
        return []
    runs = [name for name in os.listdir(model_dir) if name.startswith("run_") and os.path.isdir(os.path.join(model_dir, name))]
    runs.sort(reverse=True)
    return runs
