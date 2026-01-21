from __future__ import annotations

import json
import os
from pathlib import Path
import numpy as np

from config import repomap
import tiles as tiles_module
import build_tiles as build_tiles_module


def main() -> int:
    # Point tiles to a temp dir under repo
    out_base = Path("cache/tiles_test_run").resolve()
    out_base.mkdir(parents=True, exist_ok=True)
    repomap["TILES_DIR"] = str(out_base)

    # Small region for quick run
    repomap["TILING_REGIONS"] = {
        "ne": {
            "name": "Test Region",
            "lat_min": 38.0,
            "lat_max": 39.0,
            "lon_min": -75.0,
            "lon_max": -74.0,
            "default_resolution_deg": 0.1,
            "stats": ["min", "max", "mean"],
        }
    }

    # Synthetic lat/lon
    lat_vals = np.linspace(38.0, 39.0, 40)
    lon_vals = np.linspace(-75.0, -74.0, 50)
    lat2d, lon2d = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    class FakeDataArray:
        def __init__(self, values):
            self.values = values
            self.latitude = lat2d
            self.longitude = (lon2d + 360.0)
            self.attrs = {"units": "dBZ"}

    class FakeDataset:
        def __init__(self, da):
            self.data_vars = {"refc": da}
        def __getitem__(self, k):
            return self.data_vars[k]
        def close(self):
            pass

    def fake_open_dataset(path, engine=None, **kwargs):
        # hour from filename
        h = 1
        for tok in str(path).split("_"):
            if tok.isdigit():
                try:
                    h = int(tok)
                    break
                except Exception:
                    pass
        values = np.sin(lat2d * np.pi) * np.cos(lon2d * np.pi) + h
        return FakeDataset(FakeDataArray(values.astype(np.float32)))

    tiles_module.xr.open_dataset = fake_open_dataset  # type: ignore

    def fake_download_all_hours_parallel(model_id, variable_id, date_str, init_hour, location_config, run_id, max_hours):
        return {h: f"/fake/grib_{h:02d}.grib2" for h in range(1, min(max_hours, 4) + 1)}

    build_tiles_module.download_all_hours_parallel = fake_download_all_hours_parallel  # type: ignore

    # Build tiles
    build_tiles_module.build_region_tiles(
        region_id="ne",
        model_id="hrrr",
        run_id="run_20240101_00",
        variables=["refc"],
        resolution_deg=0.1,
        max_hours=4,
    )

    # Validate
    out_dir = out_base / "ne" / "0.1deg" / "hrrr" / "run_20240101_00"
    npz = out_dir / "refc.npz"
    meta = out_dir / "refc.meta.json"
    assert npz.exists(), f"Missing tiles: {npz}"
    assert meta.exists(), f"Missing meta: {meta}"
    data = np.load(str(npz))
    hours = data["hours"].tolist()
    print("E2E OK: hours:", hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

