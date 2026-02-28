#!/usr/bin/env python3
"""Generate tile reference fixtures for Rust worker integration tests.

For each GRIB parity fixture, decode the GRIB with cfgrib, build tiles
using the same logic as tiles.py (NE region, model-appropriate resolution,
correct unit conversion), and save as <name>_tiles.npz + <name>_tiles.json.
"""

import json
import sys
import os
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from tiles import _grid_shape, _prep_cell_index, _reduce_stats
from utils import convert_units

# NE region (matches Rust NE_REGION)
LAT_MIN, LAT_MAX = 33.0, 47.0
LON_MIN, LON_MAX = -88.0, -66.0

# Model → tile resolution (matches Rust config)
MODEL_RESOLUTION = {
    "hrrr": 0.03,
    "nam_nest": 0.1,
    "gfs": 0.1,
    "nbm": 0.1,
    "ecmwf_hres": 0.1,
}

# Variable → conversion config (matches Python config.py)
VARIABLE_CONFIG = {
    "t2m": {
        "conversion": "k_to_f",
        "unit_conversions_by_units": {"K": "k_to_f", "degC": "c_to_f", "°C": "c_to_f"},
    },
    "apcp": {
        "conversion": "kg_m2_to_in",
        "unit_conversions_by_units": {"m": "m_to_in", "kg m-2": "kg_m2_to_in", "kg m**-2": "kg_m2_to_in"},
    },
    "asnow": {
        "conversion": "m_to_in",
        "unit_conversions_by_units": {},
    },
    "snod": {
        "conversion": "m_to_in",
        "unit_conversions_by_units": {},
    },
}

FIXTURES = [
    ("hrrr_apcp_f1", "hrrr", "apcp"),
    ("hrrr_asnow_f1", "hrrr", "asnow"),
    ("hrrr_snod_f1", "hrrr", "snod"),
    ("hrrr_t2m_f1", "hrrr", "t2m"),
    ("nam_nest_apcp_f3", "nam_nest", "apcp"),
    ("nam_nest_snod_f3", "nam_nest", "snod"),
    ("nam_nest_t2m_f3", "nam_nest", "t2m"),
    ("gfs_apcp_f3", "gfs", "apcp"),
    ("gfs_snod_f3", "gfs", "snod"),
    ("gfs_t2m_f3", "gfs", "t2m"),
    ("nbm_apcp_f1", "nbm", "apcp"),
    ("nbm_asnow_f1", "nbm", "asnow"),
    ("nbm_t2m_f1", "nbm", "t2m"),
    ("ecmwf_hres_apcp_f3", "ecmwf_hres", "apcp"),
    ("ecmwf_hres_t2m_f3", "ecmwf_hres", "t2m"),
    ("ecmwf_hres_snod_f3", "ecmwf_hres", "snod"),
]


def generate_fixture(name: str, model_id: str, variable_id: str):
    """Generate tile reference for a single GRIB fixture."""
    import cfgrib

    fixture_dir = os.path.dirname(os.path.abspath(__file__))
    grib_path = os.path.join(fixture_dir, f"{name}.grib2")
    json_path = os.path.join(fixture_dir, f"{name}.json")

    if not os.path.exists(grib_path):
        print(f"  SKIP {name}: missing {grib_path}")
        return False

    # Load fixture metadata for source units
    with open(json_path) as f:
        fixture_meta = json.load(f)
    src_units = fixture_meta.get("units")

    # Determine conversion
    var_cfg = VARIABLE_CONFIG[variable_id]
    conversion = var_cfg["conversion"]
    by_units = var_cfg.get("unit_conversions_by_units", {})
    if src_units and src_units in by_units:
        conversion = by_units[src_units]

    # Decode GRIB
    datasets = cfgrib.open_datasets(grib_path)
    ds = datasets[0]
    # Get first data variable
    var_names = list(ds.data_vars)
    da = ds[var_names[0]]

    # Apply conversion
    data = np.array(da.values)
    data_converted = np.array(convert_units(data, conversion))

    # Get coordinates
    lat = np.array(da.latitude)
    lon = np.array(da.longitude)
    if lat.ndim == 1 and lon.ndim == 1:
        lat2d, lon2d = np.meshgrid(lat, lon, indexing='ij')
    else:
        lat2d, lon2d = lat, lon

    # Get resolution
    res_deg = MODEL_RESOLUTION[model_id]

    # Build tiles
    order, starts, unique_ids, valid_mask_flat, n_cells, ny, nx = _prep_cell_index(
        lat2d, lon2d, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, res_deg
    )

    mn, mx, mu = _reduce_stats(
        data_converted, valid_mask_flat, order, starts, unique_ids, n_cells, ny, nx
    )

    # Detect lon convention
    lon_0_360 = bool(np.nanmin(lon2d) >= 0)
    index_lon_min = (360.0 + LON_MIN) if (lon_0_360 and LON_MIN < 0) else LON_MIN

    # Stack to 3D (1 hour)
    hours = np.array([fixture_meta.get("forecast_hour", 1)], dtype=np.int32)
    mins_3d = mn.reshape(1, ny, nx)
    maxs_3d = mx.reshape(1, ny, nx)
    means_3d = mu.reshape(1, ny, nx)

    # Save NPZ
    npz_path = os.path.join(fixture_dir, f"{name}_tiles.npz")
    np.savez_compressed(npz_path, hours=hours, mins=mins_3d, maxs=maxs_3d, means=means_3d)

    # Save metadata JSON
    meta = {
        "model_id": model_id,
        "variable_id": variable_id,
        "resolution_deg": res_deg,
        "tile_shape": [1, ny, nx],
        "conversion": conversion,
        "src_units": src_units,
        "lon_0_360": lon_0_360,
        "index_lon_min": index_lon_min,
        "lat_min": LAT_MIN,
        "lat_max": LAT_MAX,
        "lon_min": LON_MIN,
        "lon_max": LON_MAX,
        "hours": [int(h) for h in hours],
        "means_finite_count": int(np.isfinite(means_3d).sum()),
        "means_nan_count": int(np.isnan(means_3d).sum()),
        "means_range": [
            float(np.nanmin(means_3d)) if np.any(np.isfinite(means_3d)) else None,
            float(np.nanmax(means_3d)) if np.any(np.isfinite(means_3d)) else None,
        ],
    }
    meta_path = os.path.join(fixture_dir, f"{name}_tiles.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    npz_size = os.path.getsize(npz_path)
    print(f"  OK {name}: {ny}x{nx} tiles @ {res_deg}° ({conversion}), {npz_size/1024:.1f}KB")
    return True


def main():
    print("Generating tile reference fixtures...")
    ok, skip, fail = 0, 0, 0
    for name, model_id, variable_id in FIXTURES:
        try:
            if generate_fixture(name, model_id, variable_id):
                ok += 1
            else:
                skip += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            fail += 1

    print(f"\nDone: {ok} generated, {skip} skipped, {fail} failed")
    return 1 if fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
