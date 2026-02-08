import os
import numpy as np


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
    monkeypatch.setitem(
        repomap,
        "TILING_REGIONS",
        {"ne": {"default_resolution_deg": 0.1, "lat_min": 0.0, "lat_max": 1.0, "lon_min": 0.0, "lon_max": 1.0}},
    )
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path.parent))
    monkeypatch.setitem(repomap, "TILES_DB_PATH", str(tmp_path / "tiles.db"))

    from tiles_db import record_tile_run, record_tile_variable
    meta = {
        "lat_min": 0.0,
        "lat_max": 1.0,
        "lon_min": 0.0,
        "lon_max": 1.0,
        "resolution_deg": 0.1,
    }
    record_tile_run(str(tmp_path / "tiles.db"), "ne", 0.1, "hrrr", "run_20260101_12", None)
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        0.1,
        "hrrr",
        "run_20260101_12",
        "t2m",
        expected_hours,
        None,
        str(npz_path),
        str(npz_path.with_suffix(".meta.json")),
        meta,
    )

    ok = tiles_exist("ne", "hrrr", "run_20260101_12", expected_max_hours=4)
    assert ok is True

    # Now write an incomplete hour set and expect failure
    write_npz(npz_path, [1, 2, 4])
    record_tile_variable(
        str(tmp_path / "tiles.db"),
        "ne",
        0.1,
        "hrrr",
        "run_20260101_12",
        "t2m",
        [1, 2, 4],
        None,
        str(npz_path),
        str(npz_path.with_suffix(".meta.json")),
        meta,
    )
    ok2 = tiles_exist("ne", "hrrr", "run_20260101_12", expected_max_hours=4)
    assert ok2 is False
