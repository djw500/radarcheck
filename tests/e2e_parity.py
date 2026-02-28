#!/usr/bin/env python3
"""Live e2e parity test: fetch real GRIBs from NOMADS, decode with both
Python (cfgrib) and Rust (grib crate), build tiles, compare results.

This test verifies the full pipeline against live data with non-trivial
values — not just fixture bytes. It downloads individual GRIB messages
via byte-range requests using the IDX file.

Usage:
    python tests/e2e_parity.py [--model hrrr] [--run-id run_20260228_12] [--fhour 3]
"""

import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WEATHER_VARIABLES, MODELS
from tiles import _grid_shape, _prep_cell_index, _reduce_stats
from utils import convert_units


# NE region
LAT_MIN, LAT_MAX = 33.0, 47.0
LON_MIN, LON_MAX = -88.0, -66.0

# Test cases: model, variable, forecast_hour, expected minimum nonzero fraction
TEST_CASES = [
    # HRRR — Lambert conformal, JPEG2000
    {"model": "hrrr", "var": "t2m", "fhour": 3, "min_nonzero_frac": 0.9},
    {"model": "hrrr", "var": "apcp", "fhour": 3, "min_nonzero_frac": 0.05},
    {"model": "hrrr", "var": "snod", "fhour": 3, "min_nonzero_frac": 0.05},
    # GFS — regular lat/lon, Complex Packing
    {"model": "gfs", "var": "t2m", "fhour": 3, "min_nonzero_frac": 0.9},
    {"model": "gfs", "var": "apcp", "fhour": 3, "min_nonzero_frac": 0.01},
    # NAM Nest — Lambert conformal, Complex Packing + Spatial Diff
    {"model": "nam_nest", "var": "t2m", "fhour": 3, "min_nonzero_frac": 0.9},
    # NBM — Lambert conformal, DRT3
    {"model": "nbm", "var": "t2m", "fhour": 1, "min_nonzero_frac": 0.9},
]


def find_latest_run(model_id: str) -> tuple:
    """Find the latest available run by checking IDX availability."""
    from datetime import datetime, timedelta, timezone
    model_cfg = MODELS[model_id]
    now = datetime.now(timezone.utc)

    for hours_ago in range(0, 24):
        t = now - timedelta(hours=hours_ago)
        date_str = t.strftime("%Y%m%d")
        # Try common init hours
        for hh in ["12", "06", "00", "18"]:
            idx_url = build_idx_url(model_cfg, date_str, hh, 1)
            try:
                r = requests.head(idx_url, timeout=5)
                if r.status_code == 200:
                    return date_str, hh
            except Exception:
                continue
    return None, None


def build_grib_url(model_cfg: dict, date: str, hh: str, fhour: int) -> str:
    """Build GRIB URL from model config."""
    herbie_model = model_cfg.get("herbie_model", "")
    fxx_digits = 2 if herbie_model in ("hrrr", "nam") else 3
    fxx = str(fhour).zfill(fxx_digits)

    url_templates = {
        "hrrr": f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus/hrrr.t{hh}z.wrfsfcf{fxx}.grib2",
        "gfs": f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{date}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fxx}",
        "nam": f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam/prod/nam.{date}/nam.t{hh}z.conusnest.hiresf{fxx}.tm00.grib2",
        "nbm": f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{date}/{hh}/core/blend.t{hh}z.core.f{fxx.zfill(3)}.co.grib2",
    }
    return url_templates.get(herbie_model, "")


def build_idx_url(model_cfg: dict, date: str, hh: str, fhour: int) -> str:
    return build_grib_url(model_cfg, date, hh, fhour) + ".idx"


def fetch_variable_grib(grib_url: str, idx_url: str, search: str) -> bytes:
    """Fetch a single variable from a GRIB file via byte-range request."""
    import re

    # Download IDX
    r = requests.get(idx_url, timeout=30)
    r.raise_for_status()
    idx_text = r.text

    # Parse IDX and find matching entry
    entries = []
    for line in idx_text.strip().split("\n"):
        parts = line.split(":")
        if len(parts) >= 6:
            try:
                offset = int(parts[1])
            except ValueError:
                continue
            entries.append({
                "offset": offset,
                "search_this": ":" + ":".join(parts[3:]),
            })

    # Find match
    pattern = re.compile(search)
    match_idx = None
    for i, entry in enumerate(entries):
        if pattern.search(entry["search_this"]):
            match_idx = i
            break

    if match_idx is None:
        raise ValueError(f"No match for '{search}' in IDX ({len(entries)} entries)")

    byte_start = entries[match_idx]["offset"]
    byte_end = entries[match_idx + 1]["offset"] - 1 if match_idx + 1 < len(entries) else None

    # Download byte range
    if byte_end:
        headers = {"Range": f"bytes={byte_start}-{byte_end}"}
    else:
        headers = {"Range": f"bytes={byte_start}-"}

    r = requests.get(grib_url, headers=headers, timeout=60)
    if r.status_code not in (200, 206):
        raise ValueError(f"HTTP {r.status_code} fetching GRIB range")

    return r.content


def python_decode_and_tile(grib_bytes: bytes, var_config: dict, resolution: float) -> dict:
    """Decode GRIB with cfgrib and build tiles — Python reference path."""
    import cfgrib
    import tempfile

    # Write to temp file for cfgrib
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
        f.write(grib_bytes)
        tmp_path = f.name

    try:
        ds_list = cfgrib.open_datasets(tmp_path)
        ds = ds_list[0]
        vname = list(ds.data_vars)[0]
        da = ds[vname]

        # Determine conversion
        conversion = var_config.get("conversion")
        by_units = var_config.get("unit_conversions_by_units", {})
        src_units = da.attrs.get("units", "")
        if src_units and src_units in by_units:
            conversion = by_units[src_units]

        data = np.array(da.values, dtype=np.float32)
        data_converted = np.array(convert_units(data, conversion), dtype=np.float32)

        lat = np.array(da.latitude)
        lon = np.array(da.longitude)
        if lat.ndim == 1 and lon.ndim == 1:
            lat2d, lon2d = np.meshgrid(lat, lon, indexing='ij')
        else:
            lat2d, lon2d = lat, lon

        order, starts, unique_ids, valid_mask, n_cells, ny, nx = _prep_cell_index(
            lat2d, lon2d, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, resolution
        )
        mn, mx, mu = _reduce_stats(
            data_converted, valid_mask, order, starts, unique_ids, n_cells, ny, nx
        )

        return {
            "means": mu,
            "mins": mn,
            "maxs": mx,
            "ny": ny,
            "nx": nx,
            "raw_shape": data.shape,
            "src_units": src_units,
            "conversion": conversion,
            "var_name": vname,
        }
    finally:
        os.unlink(tmp_path)
        # Clean up cfgrib index files
        for f in [tmp_path + ".5b7b6.idx"]:
            if os.path.exists(f):
                os.unlink(f)


def rust_decode_and_tile(grib_bytes: bytes, conversion_name: str, resolution: float) -> dict:
    """Decode GRIB with Rust grib crate and build tiles — Rust path.

    Uses a small Rust test binary that reads GRIB bytes from stdin,
    decodes, builds tiles, and writes NPZ to a temp file.
    """
    # Write GRIB to temp file
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
        f.write(grib_bytes)
        grib_path = f.name

    npz_path = grib_path.replace(".grib2", "_tiles.npz")

    try:
        # Use the Rust e2e_tile_worker binary
        env = os.environ.copy()
        result = subprocess.run(
            [
                "cargo", "run", "--quiet", "--bin", "e2e_tile_worker", "--",
                "--grib-path", grib_path,
                "--output-path", npz_path,
                "--conversion", conversion_name,
                "--resolution", str(resolution),
            ],
            cwd="/app/rust_worker",
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Rust tile worker failed:\n{result.stderr}")

        # Read the output NPZ
        data = np.load(npz_path)
        means = data["means"][0]  # (1, ny, nx) → (ny, nx)
        mins = data["mins"][0]
        maxs = data["maxs"][0]
        ny, nx = means.shape
        data.close()

        return {
            "means": means,
            "mins": mins,
            "maxs": maxs,
            "ny": ny,
            "nx": nx,
        }
    finally:
        for p in [grib_path, npz_path]:
            if os.path.exists(p):
                os.unlink(p)


def compare_tiles(name: str, py_result: dict, rust_result: dict) -> dict:
    """Compare Python and Rust tile outputs."""
    py_means = py_result["means"]
    rs_means = rust_result["means"]

    assert py_means.shape == rs_means.shape, \
        f"{name}: shape mismatch py={py_means.shape} rust={rs_means.shape}"

    total = py_means.size
    both_nan = np.isnan(py_means) & np.isnan(rs_means)
    both_finite = np.isfinite(py_means) & np.isfinite(rs_means)
    nan_disagree = (np.isnan(py_means) != np.isnan(rs_means)).sum()

    if both_finite.sum() > 0:
        diffs = np.abs(py_means[both_finite] - rs_means[both_finite])
        mae = float(diffs.mean())
        max_ae = float(diffs.max())
        pct_close = float((diffs < 0.01).sum() / both_finite.sum() * 100)
    else:
        mae, max_ae, pct_close = 0.0, 0.0, 100.0

    nan_agree_pct = float((both_nan.sum() + both_finite.sum()) / total * 100)

    # Nonzero comparison
    py_nonzero = np.count_nonzero(py_means[both_finite])
    rs_nonzero = np.count_nonzero(rs_means[both_finite])

    return {
        "mae": mae,
        "max_ae": max_ae,
        "pct_close_001": pct_close,
        "nan_agree_pct": nan_agree_pct,
        "nan_disagree": int(nan_disagree),
        "finite_cells": int(both_finite.sum()),
        "py_nonzero": int(py_nonzero),
        "rs_nonzero": int(rs_nonzero),
    }


def get_search_string(model_id: str, var_id: str) -> str:
    """Get the IDX search string for a model/variable combo."""
    var_cfg = WEATHER_VARIABLES[var_id]
    herbie_search = var_cfg.get("herbie_search", {})
    model_cfg = MODELS[model_id]
    herbie_model = model_cfg.get("herbie_model", model_id)
    return herbie_search.get(herbie_model, herbie_search.get("default", ""))


def get_conversion_name(var_id: str, src_units: str) -> str:
    """Get the conversion name for the Rust side."""
    var_cfg = WEATHER_VARIABLES[var_id]
    by_units = var_cfg.get("unit_conversions_by_units", {})
    if src_units and src_units in by_units:
        return by_units[src_units]
    return var_cfg.get("conversion", "none")


def run_test(tc: dict, date: str, hh: str) -> bool:
    """Run a single e2e parity test case."""
    model_id = tc["model"]
    var_id = tc["var"]
    fhour = tc["fhour"]
    model_cfg = MODELS[model_id]
    var_cfg = WEATHER_VARIABLES[var_id]
    resolution = model_cfg.get("tile_resolution_deg", 0.1)
    name = f"{model_id}_{var_id}_f{fhour}"

    print(f"\n{'='*60}")
    print(f"  {name} ({date} {hh}Z)")
    print(f"{'='*60}")

    # 1. Fetch GRIB
    search = get_search_string(model_id, var_id)
    grib_url = build_grib_url(model_cfg, date, hh, fhour)
    idx_url = grib_url + ".idx"

    print(f"  Fetching: {search}")
    try:
        grib_bytes = fetch_variable_grib(grib_url, idx_url, search)
    except Exception as e:
        print(f"  SKIP: fetch failed — {e}")
        return None
    print(f"  Got {len(grib_bytes)/1024:.1f} KB")

    # 2. Python decode + tile
    print(f"  Python decode...", end="", flush=True)
    t0 = time.time()
    py_result = python_decode_and_tile(grib_bytes, var_cfg, resolution)
    t_py = time.time() - t0
    print(f" {t_py:.2f}s ({py_result['raw_shape']}, var={py_result['var_name']}, units={py_result['src_units']})")

    # Check non-trivial data
    py_means = py_result["means"]
    finite = np.isfinite(py_means)
    nonzero = np.count_nonzero(py_means[finite])
    nonzero_frac = nonzero / finite.sum() if finite.sum() > 0 else 0
    print(f"  Python tiles: {py_result['ny']}x{py_result['nx']}, {nonzero}/{finite.sum()} nonzero ({nonzero_frac:.1%})")

    if nonzero_frac < tc["min_nonzero_frac"]:
        print(f"  WARN: only {nonzero_frac:.1%} nonzero (expected >= {tc['min_nonzero_frac']:.0%})")

    # 3. Rust decode + tile
    conversion_name = get_conversion_name(var_id, py_result.get("src_units", ""))
    print(f"  Rust decode (conversion={conversion_name})...", end="", flush=True)
    t0 = time.time()
    rust_result = rust_decode_and_tile(grib_bytes, conversion_name, resolution)
    t_rs = time.time() - t0
    print(f" {t_rs:.2f}s")

    # 4. Compare
    stats = compare_tiles(name, py_result, rust_result)

    print(f"  Results:")
    print(f"    MAE:          {stats['mae']:.6f}")
    print(f"    Max AE:       {stats['max_ae']:.4f}")
    print(f"    Close (<0.01): {stats['pct_close_001']:.1f}%")
    print(f"    NaN agree:    {stats['nan_agree_pct']:.1f}%")
    print(f"    Finite cells: {stats['finite_cells']}")
    print(f"    Nonzero: py={stats['py_nonzero']} rust={stats['rs_nonzero']}")

    # Pass/fail
    passed = True
    if stats["mae"] > 0.01:
        print(f"  FAIL: MAE {stats['mae']:.6f} > 0.01")
        passed = False
    if stats["nan_agree_pct"] < 99.0:
        print(f"  FAIL: NaN agreement {stats['nan_agree_pct']:.1f}% < 99%")
        passed = False
    if stats["py_nonzero"] > 0 and stats["rs_nonzero"] == 0:
        print(f"  FAIL: Rust produced all zeros but Python has {stats['py_nonzero']} nonzero")
        passed = False

    if passed:
        print(f"  PASS")
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date YYYYMMDD (default: today)")
    parser.add_argument("--hh", help="Init hour (default: auto-detect)")
    parser.add_argument("--model", help="Only test this model")
    parser.add_argument("--var", help="Only test this variable")
    args = parser.parse_args()

    from datetime import datetime, timezone
    date = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")

    # Find available init hour
    if args.hh:
        hh = args.hh
    else:
        # Try recent init hours
        for try_hh in ["12", "06", "00", "18"]:
            test_model = MODELS.get(args.model or "hrrr", MODELS["hrrr"])
            test_url = build_grib_url(test_model, date, try_hh, 1)
            try:
                r = requests.head(test_url + ".idx", timeout=5)
                if r.status_code == 200:
                    hh = try_hh
                    print(f"Using {date} {hh}Z")
                    break
            except Exception:
                continue
        else:
            print(f"ERROR: No available runs found for {date}")
            return 1

    # Filter test cases
    cases = TEST_CASES
    if args.model:
        cases = [tc for tc in cases if tc["model"] == args.model]
    if args.var:
        cases = [tc for tc in cases if tc["var"] == args.var]

    results = {"pass": 0, "fail": 0, "skip": 0}
    for tc in cases:
        result = run_test(tc, date, hh)
        if result is None:
            results["skip"] += 1
        elif result:
            results["pass"] += 1
        else:
            results["fail"] += 1

    print(f"\n{'='*60}")
    print(f"  E2E PARITY: {results['pass']} pass, {results['fail']} fail, {results['skip']} skip")
    print(f"{'='*60}")

    return 1 if results["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
