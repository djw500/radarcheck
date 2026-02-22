import numpy as np

from jobs import claim, complete, enqueue
from tile_db import init_db
from job_worker import process_build_tile_hour


def _setup_worker_test(tmp_path, monkeypatch):
    """Common setup for worker tests: DB, fake GRIB/tile functions."""
    from config import repomap

    db_path = tmp_path / "jobs.db"
    tiles_dir = tmp_path / "tiles"
    monkeypatch.setitem(repomap, "DB_PATH", str(db_path))
    monkeypatch.setitem(repomap, "TILES_DIR", str(tiles_dir))
    monkeypatch.setitem(
        repomap,
        "TILING_REGIONS",
        {
            "ne": {
                "name": "Northeast",
                "lat_min": 0.0,
                "lat_max": 1.0,
                "lon_min": 0.0,
                "lon_max": 1.0,
                "default_resolution_deg": 1.0,
            }
        },
    )

    def fake_open_as_xarray(*args, **kwargs):
        import xarray as xr
        return xr.Dataset({"t2m": (["latitude", "longitude"], np.array([[1.0]]))})

    def fake_build_tiles_for_variable(*args, **kwargs):
        mins = np.array([[[1.0]]], dtype=np.float32)
        maxs = np.array([[[2.0]]], dtype=np.float32)
        means = np.array([[[1.5]]], dtype=np.float32)
        return mins, maxs, means, [1], {}

    monkeypatch.setattr("job_worker.open_as_xarray", fake_open_as_xarray)
    monkeypatch.setattr("job_worker.build_tiles_for_variable", fake_build_tiles_for_variable)

    conn = init_db(str(db_path))
    return conn


def _enqueue_tile_job(conn, **overrides):
    """Enqueue a standard build_tile_hour job, return job_id."""
    args = {
        "region_id": "ne",
        "model_id": "hrrr",
        "run_id": "run_20240101_00",
        "variable_id": "t2m",
        "forecast_hour": 1,
        "resolution_deg": 1.0,
    }
    args.update(overrides)
    return enqueue(conn, "build_tile_hour", args)


def test_process_build_tile_hour_does_not_auto_complete(tmp_path, monkeypatch):
    """After removing double-complete, process_build_tile_hour should NOT complete the job.

    The worker loop is responsible for calling complete().
    """
    conn = _setup_worker_test(tmp_path, monkeypatch)
    job_id = _enqueue_tile_job(conn)
    conn.commit()

    job = claim(conn, "worker-test")
    assert job is not None
    process_build_tile_hour(conn, job)
    conn.commit()

    # Job should still be 'processing' — worker hasn't called complete() yet
    status_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert status_row["status"] == "processing"

    # Tile hour record should still be written
    tile_row = conn.execute(
        "SELECT forecast_hour FROM tile_hours WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert tile_row["forecast_hour"] == 1

    # Now simulate the worker calling complete()
    complete(conn, job_id)
    status_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert status_row["status"] == "completed"
    conn.close()
