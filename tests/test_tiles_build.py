import os
import json
import numpy as np
import types

import pytest


def test_build_region_tiles_with_mocks(tmp_path, monkeypatch):
    # Configure TILES_DIR to temp
    from config import repomap
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path / "tiles"))

    # Minimal region config to ensure inference is not needed
    monkeypatch.setitem(
        repomap,
        "TILING_REGIONS",
        {
            "ne": {
                "name": "Northeast US",
                "lat_min": 38.0,
                "lat_max": 39.0,
                "lon_min": -75.0,
                "lon_max": -74.0,
                "default_resolution_deg": 0.1,
                "stats": ["min", "max", "mean"],
            }
        },
    )

    # Prepare synthetic 2D lat/lon grids and data
    lat_vals = np.linspace(38.0, 39.0, 50)
    lon_vals = np.linspace(-75.0, -74.0, 60)
    lat2d, lon2d = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    class FakeDataArray:
        def __init__(self, values):
            self.values = values
            self.latitude = lat2d
            # Convert negative lon to 0-360 to exercise normalization
            self.longitude = (lon2d + 360.0)
            self.attrs = {"units": "dBZ"}

    class FakeDataset:
        def __init__(self, data_array):
            self._da = data_array
            self.data_vars = {"refc": data_array}

        def __getitem__(self, key):
            return self.data_vars[key]

        def close(self):
            pass

    # Mock xr.open_dataset used inside tiles.build_tiles_for_variable
    import tiles as tiles_module

    def fake_open_dataset(path, engine=None, **kwargs):
        # Extract hour from path if present
        h = 1
        for tok in str(path).split("_"):
            if tok.isdigit():
                try:
                    h = int(tok)
                    break
                except Exception:
                    pass
        # Synthetic field varies with hour to ensure stacking works
        values = np.sin(lat2d * np.pi) * np.cos(lon2d * np.pi) + h
        return FakeDataset(FakeDataArray(values.astype(np.float32)))

    monkeypatch.setattr(tiles_module.xr, "open_dataset", fake_open_dataset)

    # Mock downloader to avoid network; return predictable fake paths
    import build_tiles as build_tiles_module

    def fake_download_all_hours_parallel(model_id, variable_id, date_str, init_hour, location_config, run_id, max_hours):
        return {h: f"/fake/grib_{h:02d}.grib2" for h in range(1, min(max_hours, 4) + 1)}

    monkeypatch.setattr(build_tiles_module, "download_all_hours_parallel", fake_download_all_hours_parallel)

    # Run builder for one variable
    build_tiles_module.build_region_tiles(
        region_id="ne",
        model_id="hrrr",
        run_id="run_20240101_00",
        variables=["refc"],
        resolution_deg=0.1,
        max_hours=4,
    )

    # Verify outputs
    out_dir = tmp_path / "tiles" / "ne" / "0.1deg" / "hrrr" / "run_20240101_00"
    npz_path = out_dir / "refc.npz"
    meta_path = out_dir / "refc.meta.json"
    assert npz_path.exists(), "NPZ tiles not written"
    assert meta_path.exists(), "Metadata not written"

    data = np.load(str(npz_path))
    mins = data["mins"]
    maxs = data["maxs"]
    means = data["means"]
    hours = data["hours"].tolist()
    assert hours == [1, 2, 3, 4]
    assert mins.shape[0] == 4 and maxs.shape[0] == 4 and means.shape[0] == 4
    # Some basic sanity checks
    assert np.isfinite(means).any()
    with open(meta_path) as f:
        meta = json.load(f)
    assert meta["resolution_deg"] == 0.1
    assert meta["region_id"] == "ne"

