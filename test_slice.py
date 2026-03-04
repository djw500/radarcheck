#!/usr/bin/env python3
"""Analyze sparsity of rctile tile data."""

import struct
import numpy as np
from pathlib import Path
from collections import defaultdict

TILE_DIR = Path("/app/cache/tiles")

# Accumulators: key = (model, variable)
stats = defaultdict(lambda: {
    "files": 0,
    "total": 0,
    "nan": 0,
    "zero": 0,       # |val| < 0.001
    "near_zero": 0,  # |val| < 0.01
    "total_bytes": 0,
    "data_bytes": 0,
})

for rctile in sorted(TILE_DIR.rglob("*.rctile")):
    parts = rctile.parts
    var = rctile.stem
    run_dir = rctile.parent.name
    model = rctile.parent.parent.name

    with open(rctile, "rb") as f:
        header = f.read(64)

    magic = header[0:4]
    if magic != b"RCT1":
        print(f"SKIP (bad magic): {rctile}")
        continue

    version = struct.unpack_from("<H", header, 4)[0]
    ny = struct.unpack_from("<H", header, 6)[0]
    nx = struct.unpack_from("<H", header, 8)[0]
    max_hours = struct.unpack_from("<H", header, 10)[0]
    n_hours_written = struct.unpack_from("<H", header, 12)[0]
    n_cells = struct.unpack_from("<I", header, 16)[0]
    hours_offset = struct.unpack_from("<I", header, 36)[0]
    data_offset = struct.unpack_from("<I", header, 40)[0]

    if n_hours_written == 0 or n_cells == 0:
        continue

    file_size = rctile.stat().st_size

    with open(rctile, "rb") as f:
        f.seek(data_offset)
        raw = f.read(n_cells * max_hours * 4)

    all_data = np.frombuffer(raw, dtype=np.float32).reshape(n_cells, max_hours)
    meaningful = all_data[:, :n_hours_written].ravel()

    total = meaningful.size
    nan_count = int(np.isnan(meaningful).sum())
    non_nan = meaningful[~np.isnan(meaningful)]
    zero_count = int((np.abs(non_nan) < 0.001).sum())
    near_zero_count = int((np.abs(non_nan) < 0.01).sum())

    key = (model, var)
    s = stats[key]
    s["files"] += 1
    s["total"] += total
    s["nan"] += nan_count
    s["zero"] += zero_count
    s["near_zero"] += near_zero_count
    s["total_bytes"] += file_size
    s["data_bytes"] += n_cells * max_hours * 4

print(f"{'Model':<12} {'Var':<8} {'Files':>5} {'Total Values':>14} {'NaN %':>8} {'Zero %':>8} {'NearZero %':>10} {'DataMB':>8}")
print("-" * 80)

for (model, var) in sorted(stats.keys()):
    s = stats[(model, var)]
    total = s["total"]
    if total == 0:
        continue
    nan_pct = 100.0 * s["nan"] / total
    non_nan = total - s["nan"]
    zero_pct = 100.0 * s["zero"] / non_nan if non_nan > 0 else 0
    near_zero_pct = 100.0 * s["near_zero"] / non_nan if non_nan > 0 else 0
    data_mb = s["data_bytes"] / (1024 * 1024)
    print(f"{model:<12} {var:<8} {s['files']:>5} {total:>14,} {nan_pct:>7.1f}% {zero_pct:>7.1f}% {near_zero_pct:>9.1f}% {data_mb:>7.1f}")

print("\n\n=== Aggregated by Variable ===")
print(f"{'Var':<8} {'Files':>5} {'Total Values':>14} {'NaN %':>8} {'Zero %':>8} {'NearZero %':>10} {'DataMB':>8}")
print("-" * 70)

var_stats = defaultdict(lambda: {"files": 0, "total": 0, "nan": 0, "zero": 0, "near_zero": 0, "data_bytes": 0})
for (model, var), s in stats.items():
    v = var_stats[var]
    for k in ["files", "total", "nan", "zero", "near_zero", "data_bytes"]:
        v[k] += s[k]

for var in sorted(var_stats.keys()):
    v = var_stats[var]
    total = v["total"]
    if total == 0:
        continue
    nan_pct = 100.0 * v["nan"] / total
    non_nan = total - v["nan"]
    zero_pct = 100.0 * v["zero"] / non_nan if non_nan > 0 else 0
    near_zero_pct = 100.0 * v["near_zero"] / non_nan if non_nan > 0 else 0
    data_mb = v["data_bytes"] / (1024 * 1024)
    print(f"{var:<8} {v['files']:>5} {total:>14,} {nan_pct:>7.1f}% {zero_pct:>7.1f}% {near_zero_pct:>9.1f}% {data_mb:>7.1f}")

print("\n\n=== Aggregated by Model ===")
print(f"{'Model':<12} {'Files':>5} {'Total Values':>14} {'NaN %':>8} {'Zero %':>8} {'NearZero %':>10} {'DataMB':>8}")
print("-" * 70)

model_stats = defaultdict(lambda: {"files": 0, "total": 0, "nan": 0, "zero": 0, "near_zero": 0, "data_bytes": 0})
for (model, var), s in stats.items():
    m = model_stats[model]
    for k in ["files", "total", "nan", "zero", "near_zero", "data_bytes"]:
        m[k] += s[k]

for model in sorted(model_stats.keys()):
    m = model_stats[model]
    total = m["total"]
    if total == 0:
        continue
    nan_pct = 100.0 * m["nan"] / total
    non_nan = total - m["nan"]
    zero_pct = 100.0 * m["zero"] / non_nan if non_nan > 0 else 0
    near_zero_pct = 100.0 * m["near_zero"] / non_nan if non_nan > 0 else 0
    data_mb = m["data_bytes"] / (1024 * 1024)
    print(f"{model:<12} {m['files']:>5} {total:>14,} {nan_pct:>7.1f}% {zero_pct:>7.1f}% {near_zero_pct:>9.1f}% {data_mb:>7.1f}")

print("\n\n=== Grand Total ===")
grand = {"files": 0, "total": 0, "nan": 0, "zero": 0, "near_zero": 0, "total_bytes": 0, "data_bytes": 0}
for s in stats.values():
    for k in grand:
        grand[k] += s[k]

total = grand["total"]
non_nan = total - grand["nan"]
print(f"Files:          {grand['files']:>10,}")
print(f"Total values:   {total:>10,}")
print(f"NaN:            {grand['nan']:>10,} ({100*grand['nan']/total:.1f}%)")
print(f"Zero (<.001):   {grand['zero']:>10,} ({100*grand['zero']/non_nan:.1f}% of non-NaN)")
print(f"NearZero(<.01): {grand['near_zero']:>10,} ({100*grand['near_zero']/non_nan:.1f}% of non-NaN)")
print(f"File bytes:     {grand['total_bytes']/1024/1024:.1f} MB")
print(f"Data bytes:     {grand['data_bytes']/1024/1024:.1f} MB")
