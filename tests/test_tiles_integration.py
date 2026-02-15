import os
import shutil
import sqlite3
import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from tile_db import init_db
from tiles import upsert_tiles_npz, save_tiles_npz, load_timeseries_for_point
import build_tiles
from config import repomap

TEST_DIR = "tests/temp_e2e"
DB_PATH = os.path.join(TEST_DIR, "jobs.db")
TILES_DIR = os.path.join(TEST_DIR, "tiles")

@pytest.fixture
def setup_env():
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)
    os.makedirs(TILES_DIR)

    # Patch repomap to use test paths
    with patch.dict(repomap, {
        "DB_PATH": DB_PATH,
        "TILES_DIR": TILES_DIR,
        "TILING_REGIONS": {
            "test_region": {
                "lat_min": 30.0,
                "lat_max": 32.0,
                "lon_min": -80.0,
                "lon_max": -78.0,
                "default_resolution_deg": 1.0,
                "name": "Test Region"
            }
        },
        "MODELS": {
            "test_model": {
                "max_forecast_hours": 5,
                "name": "Test Model"
            }
        },
        "WEATHER_VARIABLES": {
            "t2m": {
                "short_name": "t2m",
                "units": "K"
            }
        }
    }):
        yield

    # Cleanup
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)

def test_build_region_tiles_populates_db(setup_env):
    # Mock download_all_hours_parallel and build_tiles_for_variable
    with patch("build_tiles.download_all_hours_parallel") as mock_download, \
         patch("build_tiles.build_tiles_for_variable") as mock_build, \
         patch("build_tiles.get_available_model_runs") as mock_get_runs:

        # Setup mocks
        mock_get_runs.return_value = [{
            "date_str": "20230101",
            "init_hour": "00",
            "init_time": "2023-01-01 00:00:00",
            "run_id": "run_20230101_00"
        }]

        mock_download.return_value = {1: "dummy_path"}

        # Return dummy arrays: 1 hour, 2x2 grid (since 1.0 deg res, 30-32 lat, -80 to -78 lon)
        ny, nx = 2, 2
        mins = np.zeros((1, ny, nx), dtype=np.float32)
        maxs = np.zeros((1, ny, nx), dtype=np.float32)
        means = np.zeros((1, ny, nx), dtype=np.float32)
        hours = [1]
        meta = {"lon_0_360": False, "index_lon_min": -80.0}

        mock_build.return_value = (mins, maxs, means, hours, meta)

        # Run build_region_tiles
        build_tiles.build_region_tiles(
            region_id="test_region",
            model_id="test_model",
            run_id="run_20230101_00",
            audit_only=False
        )

        # Verify DB
        conn = init_db(DB_PATH)

        # Check tile_runs
        runs = conn.execute("SELECT * FROM tile_runs").fetchall()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "run_20230101_00"

        # Check tile_variables
        vars = conn.execute("SELECT * FROM tile_variables").fetchall()
        assert len(vars) == 1
        assert vars[0]["variable_id"] == "t2m"

        # Check tile_hours (THIS IS WHAT WE ADDED)
        hours_rows = conn.execute("SELECT * FROM tile_hours").fetchall()
        assert len(hours_rows) == 1
        assert hours_rows[0]["forecast_hour"] == 1
        assert hours_rows[0]["variable_id"] == "t2m"

        conn.close()

def test_upsert_tiles_concurrency(setup_env):
    """Test that concurrent upserts do not corrupt data or lose updates."""
    from concurrent.futures import ThreadPoolExecutor

    region_id = "test_region"
    res_deg = 1.0
    model_id = "test_model"
    run_id = "run_20230101_00"
    variable_id = "t2m"

    # Init meta
    meta = {
        "region_id": region_id,
        "lat_min": 30.0,
        "lat_max": 32.0,
        "lon_min": -80.0,
        "lon_max": -78.0,
        "resolution_deg": res_deg,
    }

    ny, nx = 2, 2

    # Function to upsert a specific hour
    def do_upsert(hour):
        mins = np.full((1, ny, nx), hour, dtype=np.float32)
        maxs = np.full((1, ny, nx), hour, dtype=np.float32)
        means = np.full((1, ny, nx), hour, dtype=np.float32)
        hours = [hour]

        upsert_tiles_npz(
            TILES_DIR, region_id, res_deg, model_id, run_id, variable_id,
            mins, maxs, means, hours, meta
        )
        return hour

    # Run concurrently for hours 1 to 10
    hours_to_process = list(range(1, 11))

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(do_upsert, hours_to_process))

    # Verify final NPZ file contains all hours
    res_dir = f"{res_deg:.3f}deg".rstrip("0").rstrip(".")
    npz_path = os.path.join(TILES_DIR, region_id, res_dir, model_id, run_id, f"{variable_id}.npz")

    assert os.path.exists(npz_path)

    with np.load(npz_path) as data:
        saved_hours = data["hours"]
        saved_means = data["means"]

    assert len(saved_hours) == 10
    assert sorted(saved_hours.tolist()) == hours_to_process

    # Verify values for each hour (we stored 'hour' as the value)
    for h in hours_to_process:
        idx = np.where(saved_hours == h)[0][0]
        # Check mean of the grid for this hour
        assert np.allclose(saved_means[idx], h)

def test_save_tiles_npz_locking(setup_env):
    """Test that save_tiles_npz respects locking (conceptually).
    Actually, since we refactored it to use FileLock, we assume it works.
    But we can verify it doesn't crash."""

    region_id = "test_region"
    res_deg = 1.0
    model_id = "test_model"
    run_id = "run_20230101_00"
    variable_id = "t2m"

    meta = {
        "region_id": region_id,
        "lat_min": 30.0,
        "lat_max": 32.0,
        "lon_min": -80.0,
        "lon_max": -78.0,
        "resolution_deg": res_deg,
    }

    ny, nx = 2, 2
    mins = np.zeros((1, ny, nx), dtype=np.float32)
    maxs = np.zeros((1, ny, nx), dtype=np.float32)
    means = np.zeros((1, ny, nx), dtype=np.float32)
    hours = [1]

    path = save_tiles_npz(
        TILES_DIR, region_id, res_deg, model_id, run_id, variable_id,
        mins, maxs, means, hours, meta
    )

    assert os.path.exists(path)
    assert os.path.exists(f"{path}.lock") # Lock file should be created (and possibly left empty or removed depending on impl, but FileLock usually leaves .lock file)
    # FileLock default implementation creates .lock file.
