import numpy as np

from jobs import claim, enqueue
from tile_db import init_db
from job_worker import process_build_tile_hour


def test_process_build_tile_hour_marks_job_complete(tmp_path, monkeypatch):
    from config import repomap

    db_path = tmp_path / "jobs.db"
    tiles_dir = tmp_path / "tiles"
    monkeypatch.setitem(repomap, "JOBS_DB_PATH", str(db_path))
    monkeypatch.setitem(repomap, "TILES_DB_PATH", str(db_path))
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

    def fake_fetch_grib(*args, **kwargs):
        return str(tmp_path / "fake.grib2")

    def fake_build_tiles_for_variable(*args, **kwargs):
        mins = np.array([[[1.0]]], dtype=np.float32)
        maxs = np.array([[[2.0]]], dtype=np.float32)
        means = np.array([[[1.5]]], dtype=np.float32)
        return mins, maxs, means, [1], {}

    monkeypatch.setattr("job_worker.fetch_grib", fake_fetch_grib)
    monkeypatch.setattr("job_worker.build_tiles_for_variable", fake_build_tiles_for_variable)

    conn = init_db(str(db_path))
    job_id = enqueue(
        conn,
        "build_tile_hour",
        {
            "region_id": "ne",
            "model_id": "hrrr",
            "run_id": "run_20240101_00",
            "variable_id": "t2m",
            "forecast_hour": 1,
            "resolution_deg": 1.0,
        },
    )
    conn.commit()

    job = claim(conn, "worker-test")
    assert job is not None
    process_build_tile_hour(conn, job)
    conn.commit()

    status_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert status_row["status"] == "completed"

    tile_row = conn.execute(
        "SELECT forecast_hour FROM tile_hours WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert tile_row["forecast_hour"] == 1
    conn.close()
