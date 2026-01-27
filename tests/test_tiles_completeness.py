import os
import numpy as np

from cache_builder import get_valid_forecast_hours


def write_npz(path, hours):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # minimal payload with hours only
    np.savez_compressed(path, hours=np.array(hours, dtype=np.int32), means=np.zeros((len(hours), 1, 1), dtype=np.float32))


def test_strict_tiles_exist_check(tmp_path, monkeypatch):
    # Build a fake tiles directory with t2m.npz
    npz_path = tmp_path / "ne/0.1deg/hrrr/run_20260101_12/t2m.npz"

    # HRRR hourly schedule: first 4 hours
    expected_hours = list(range(1, 5))
    write_npz(npz_path, expected_hours)

    # Verify exact match considered complete
    from scripts.build_tiles_scheduled import tiles_exist
    # monkeypatch the config to look at our tmp path
    from config import repomap
    monkeypatch.setitem(repomap, "TILING_REGIONS", {"ne": {"default_resolution_deg": 0.1}})
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path))

    ok = tiles_exist("ne", "hrrr", "run_20260101_12", expected_max_hours=4)
    assert ok is True

    # Now write an incomplete hour set and expect failure
    write_npz(npz_path, [1, 2, 4])
    ok2 = tiles_exist("ne", "hrrr", "run_20260101_12", expected_max_hours=4)
    assert ok2 is False

