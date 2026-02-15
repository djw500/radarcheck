"""Integration tests for run_worker() — the actual worker loop."""

from unittest.mock import patch

from jobs import enqueue, init_db
from job_worker import run_worker
from tile_db import init_db as init_tile_db


def _setup(tmp_path, monkeypatch):
    from config import repomap

    db_path = str(tmp_path / "jobs.db")
    monkeypatch.setitem(repomap, "DB_PATH", db_path)
    monkeypatch.setitem(repomap, "TILES_DIR", str(tmp_path / "tiles"))
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
    conn = init_db(db_path)
    return conn, db_path


def test_run_worker_completes_job(tmp_path, monkeypatch):
    """run_worker(once=True) should claim a job, process it, and mark it completed."""
    conn, db_path = _setup(tmp_path, monkeypatch)
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
    assert job_id is not None

    # Mock process_build_tile_hour to be a no-op (don't hit NOMADS or build real tiles)
    with patch("job_worker.process_build_tile_hour") as mock_process:
        run_worker(once=True)

    mock_process.assert_called_once()

    # Verify job is completed (worker loop called complete(), not process_build_tile_hour)
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "completed"
    conn.close()


def test_run_worker_fails_job_on_exception(tmp_path, monkeypatch):
    """If process_build_tile_hour raises, job should be retried (pending with retry_count=1)."""
    conn, db_path = _setup(tmp_path, monkeypatch)
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

    with patch("job_worker.process_build_tile_hour", side_effect=RuntimeError("NOMADS down")):
        run_worker(once=True)

    row = conn.execute(
        "SELECT status, retry_count, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "failed"  # no retries (max_retries=0)
    assert row["retry_count"] == 1
    assert "NOMADS down" in row["error_message"]
    conn.close()


def test_run_worker_rejects_unknown_job_type(tmp_path, monkeypatch):
    """Jobs with unknown type should be marked failed."""
    conn, db_path = _setup(tmp_path, monkeypatch)
    # Insert a job with a bogus type directly
    job_id = enqueue(conn, "bogus_type", {"x": 1})
    assert job_id is not None

    run_worker(once=True)

    row = conn.execute(
        "SELECT status, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    # fail() defaults to max_retries=0, so first failure goes straight to failed
    assert row["status"] == "failed"
    assert "Unsupported job type" in row["error_message"]
    conn.close()


def test_run_worker_cancels_siblings_on_failure(tmp_path, monkeypatch):
    """When a job fails, all pending sibling jobs (same model/run) should be cancelled."""
    conn, db_path = _setup(tmp_path, monkeypatch)
    base_args = {
        "region_id": "ne",
        "model_id": "hrrr",
        "run_id": "run_20240101_00",
        "variable_id": "t2m",
        "resolution_deg": 1.0,
    }
    # Enqueue 3 jobs for the same model/run
    ids = []
    for h in [1, 2, 3]:
        job_id = enqueue(conn, "build_tile_hour", {**base_args, "forecast_hour": h})
        ids.append(job_id)

    # Worker processes first job (H1) and it fails — should cancel H2, H3
    with patch("job_worker.process_build_tile_hour", side_effect=RuntimeError("404")):
        run_worker(once=True)

    statuses = []
    for jid in ids:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
        statuses.append(row["status"])

    assert statuses[0] == "failed"  # the one that ran
    assert statuses[1] == "failed"  # cancelled sibling
    assert statuses[2] == "failed"  # cancelled sibling
    conn.close()


def test_run_worker_exits_on_empty_queue(tmp_path, monkeypatch):
    """run_worker(once=True) with empty queue should return without hanging."""
    conn, db_path = _setup(tmp_path, monkeypatch)
    # Empty queue — just ensure it returns
    run_worker(once=True)
    conn.close()
