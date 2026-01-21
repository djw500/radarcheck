from __future__ import annotations

import json
import os
from pathlib import Path
import numpy as np

import sys
import types

# Stub heavy optional deps before importing project modules
class _Dummy:
    def __getattr__(self, name):
        return _Dummy()
    def __call__(self, *a, **k):
        return None

class _DummyColors:
    class LinearSegmentedColormap:
        @staticmethod
        def from_list(name, colors):
            return None

mpl = types.ModuleType('matplotlib')
mpl.use = lambda *a, **k: None
mpl.colors = _DummyColors
sys.modules['matplotlib'] = mpl
mpl_colors = types.ModuleType('matplotlib.colors')
mpl_colors.LinearSegmentedColormap = _DummyColors.LinearSegmentedColormap
sys.modules['matplotlib.colors'] = mpl_colors
sys.modules['matplotlib.pyplot'] = types.ModuleType('matplotlib.pyplot')

cartopy = types.ModuleType('cartopy')
cartopy_crs = types.ModuleType('cartopy.crs')
cartopy_crs.PlateCarree = object
cartopy.crs = cartopy_crs
sys.modules['cartopy'] = cartopy
sys.modules['cartopy.crs'] = cartopy_crs

geopandas = types.ModuleType('geopandas')
geopandas.read_file = lambda *a, **k: None
sys.modules['geopandas'] = geopandas

shapely = types.ModuleType('shapely')
shapely_geometry = types.ModuleType('shapely.geometry')
shapely_geometry.box = lambda *a, **k: None
shapely.geometry = shapely_geometry
sys.modules['shapely'] = shapely
sys.modules['shapely.geometry'] = shapely_geometry

pil = types.ModuleType('PIL')
pil_image = types.ModuleType('PIL.Image')
sys.modules['PIL'] = pil
sys.modules['PIL.Image'] = pil_image

requests_mod = types.ModuleType('requests')
requests_mod.head = lambda *a, **k: types.SimpleNamespace(status_code=200)
requests_mod.get = lambda *a, **k: types.SimpleNamespace(status_code=200, content=b"", headers={})
sys.modules['requests'] = requests_mod

filelock_mod = types.ModuleType('filelock')
class DummyLock:
    def __init__(self, *a, **k):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
filelock_mod.FileLock = DummyLock
class DummyTimeout(Exception):
    pass
filelock_mod.Timeout = DummyTimeout
sys.modules['filelock'] = filelock_mod

psutil_mod = types.ModuleType('psutil')
class DummyProc:
    def __init__(self, pid):
        pass
    def memory_info(self):
        return types.SimpleNamespace(rss=0)
psutil_mod.Process = DummyProc
sys.modules['psutil'] = psutil_mod

from config import repomap
import tiles as tiles_module
import build_tiles as build_tiles_module


def main() -> int:
    # Point tiles to a temp dir under repo
    # Use default TILES_DIR so the server can read these tiles directly
    out_base = Path(repomap["TILES_DIR"]).resolve()
    out_base.mkdir(parents=True, exist_ok=True)

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

    # Synthetic lat/lon (larger than region to test subsetting)
    lat_vals = np.linspace(30.0, 45.0, 150)
    lon_vals = np.linspace(-80.0, -70.0, 100)
    lat2d, lon2d = np.meshgrid(lat_vals, lon_vals, indexing="ij")

    class FakeDataArray:
        def __init__(self, values):
            self.values = values
            self.latitude = lat2d
            self.longitude = (lon2d + 360.0)
            self.attrs = {"units": "dBZ"}

    class FakeDataset:
        def __init__(self, da):
            # Expose t2m to match variable short_name expected
            self.data_vars = {"t2m": da}
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
        # Value depends on lat/lon
        values = lat2d + h 
        return FakeDataset(FakeDataArray(values.astype(np.float32)))

    tiles_module.xr.open_dataset = fake_open_dataset  # type: ignore

    def fake_download_all_hours_parallel(model_id, variable_id, date_str, init_hour, location_config, run_id, max_hours):
        return {h: f"/fake/grib_{h:02d}.grib2" for h in range(1, min(max_hours, 4) + 1)}

    build_tiles_module._download_all_hours_parallel = fake_download_all_hours_parallel  # type: ignore

    # Disable unit conversion for t2m to avoid arithmetic on fake arrays
    repomap["WEATHER_VARIABLES"]["t2m"]["conversion"] = None

    # Build tiles
    build_tiles_module.build_region_tiles(
        region_id="ne",
        model_id="hrrr",
        run_id="run_20240101_00",
        variables=["t2m"],
        resolution_deg=0.1,
        max_hours=4,
    )

    # Validate
    out_dir = out_base / "ne" / f"{0.1:.3f}deg" / "hrrr" / "run_20240101_00"
    npz = out_dir / "t2m.npz"
    meta = out_dir / "t2m.meta.json"
    assert npz.exists(), f"Missing tiles: {npz}"
    assert meta.exists(), f"Missing meta: {meta}"
    data = np.load(str(npz))
    hours = data["hours"].tolist()
    means = data["means"]
    
    # Check value at a specific point
    # Region 38-39. Middle is 38.5.
    # Tile grid size for 1 deg at 0.1 res is 10x10.
    # Middle index approx 5.
    
    # Expected value at lat 38.5, hour 1: 38.5 + 1 = 39.5
    # If bug exists, it might pick value from lat 30 (first pixels): 30 + 1 = 31.0
    
    # Find cell closest to 38.5
    # Grid starts at 38.0. (38.5 - 38.0) / 0.1 = 5.
    
    val_check = means[0, 5, 5] # Hour 1 (idx 0), iy 5, ix 5
    print(f"Check Value: {val_check}")
    assert np.abs(val_check - 39.5) < 0.2, f"Expected ~39.5, got {val_check}"

    print("E2E OK: hours:", hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
