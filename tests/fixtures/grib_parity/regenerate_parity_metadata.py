#!/usr/bin/env python3
"""Regenerate parity fixture metadata (.json) from GRIB fixture files.

Re-decodes each .grib2 with cfgrib and writes accurate metadata including
decode checkpoints, mean, nonzero count, etc. This ensures the parity
test reference values match what cfgrib actually produces from these bytes.
"""

import json
import glob
import os
import sys
import numpy as np


def generate_metadata(grib_path: str) -> dict:
    import cfgrib

    name = os.path.basename(grib_path).replace('.grib2', '')

    # Load existing JSON to preserve non-decode fields
    json_path = grib_path.replace('.grib2', '.json')
    with open(json_path) as f:
        existing = json.load(f)

    # Decode with cfgrib
    datasets = cfgrib.open_datasets(grib_path)
    ds = datasets[0]
    vname = list(ds.data_vars)[0]
    da = ds[vname]
    vals = np.array(da.values, dtype=np.float32)

    lat = np.array(da.latitude)
    lon = np.array(da.longitude)

    # Determine lat/lon dimensionality
    lat_ndim = lat.ndim
    if lat_ndim == 1:
        lat2d, lon2d = np.meshgrid(lat, lon, indexing='ij')
    else:
        lat2d, lon2d = lat, lon

    ny, nx = vals.shape

    # Generate checkpoints: evenly spaced through flat array
    total = ny * nx
    n_checkpoints = 20
    indices = np.linspace(0, total - 1, n_checkpoints, dtype=int)

    checkpoints = []
    for flat_idx in indices:
        iy = int(flat_idx // nx)
        ix = int(flat_idx % nx)
        val = float(vals[iy, ix])
        cp_lat = float(lat2d[iy, ix])
        cp_lon = float(lon2d[iy, ix])

        checkpoints.append({
            "flat_index": int(flat_idx),
            "iy": iy,
            "ix": ix,
            "value": round(val, 6) if not np.isnan(val) else None,
            "lat": round(cp_lat, 4),
            "lon": round(cp_lon, 4),
        })

    # Filter out NaN-valued checkpoints (replace with nearby non-NaN if possible)
    checkpoints = [cp for cp in checkpoints if cp["value"] is not None]

    # Aggregate stats
    finite_vals = vals[~np.isnan(vals)]
    mean_value = float(np.mean(finite_vals)) if len(finite_vals) > 0 else 0.0
    total_nonzero = int(np.count_nonzero(finite_vals))
    total_nan = int(np.isnan(vals).sum())

    meta = {
        "model_id": existing.get("model_id", ""),
        "variable_id": existing.get("variable_id", ""),
        "date": existing.get("date", ""),
        "init_hour": existing.get("init_hour", ""),
        "forecast_hour": existing.get("forecast_hour", 0),
        "grib_url": existing.get("grib_url", ""),
        "idx_url": existing.get("idx_url", ""),
        "search_string": existing.get("search_string", ""),
        "herbie_model": existing.get("herbie_model", ""),
        "herbie_product": existing.get("herbie_product", ""),
        "byte_start": existing.get("byte_start", 0),
        "byte_end": existing.get("byte_end", 0),
        "grib_message_size_kb": existing.get("grib_message_size_kb", 0),
        "idx_search_this": existing.get("idx_search_this", ""),
        "output_shape": [ny, nx],
        "output_dtype": "float32",
        "lat_range": [round(float(np.nanmin(lat)), 4), round(float(np.nanmax(lat)), 4)],
        "lon_range": [round(float(np.nanmin(lon)), 4), round(float(np.nanmax(lon)), 4)],
        "lat_ndim": lat_ndim,
        "value_range": [round(float(np.nanmin(vals)), 4), round(float(np.nanmax(vals)), 4)] if len(finite_vals) > 0 else [0, 0],
        "units": str(da.attrs.get("units", "")),
        "var_name_in_dataset": vname,
        "has_nans": bool(total_nan > 0),
        "grib_fixture_bytes": existing.get("grib_fixture_bytes", os.path.getsize(grib_path)),
        "decode_checkpoints": checkpoints,
        "total_nonzero": total_nonzero,
        "total_nan": total_nan,
        "mean_value": round(mean_value, 6),
    }

    return meta


def main():
    fixture_dir = os.path.dirname(os.path.abspath(__file__))
    grib_files = sorted(glob.glob(os.path.join(fixture_dir, '*.grib2')))

    print(f"Regenerating metadata for {len(grib_files)} fixtures...")
    for grib_path in grib_files:
        name = os.path.basename(grib_path).replace('.grib2', '')
        try:
            meta = generate_metadata(grib_path)
            json_path = grib_path.replace('.grib2', '.json')
            with open(json_path, 'w') as f:
                json.dump(meta, f, indent=2)
            print(f"  OK {name}: mean={meta['mean_value']:.6f} var={meta['var_name_in_dataset']}")
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()

    # Clean up cfgrib index files
    for idx_file in glob.glob(os.path.join(fixture_dir, '*.5b7b6.idx')):
        os.remove(idx_file)

    print("\nDone.")


if __name__ == "__main__":
    main()
