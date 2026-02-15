"""Integration tests for drain_queue() from the scheduler."""

from unittest.mock import patch, MagicMock

from jobs import enqueue, init_db
from scripts.build_tiles_scheduled import drain_queue


def _setup(tmp_path, monkeypatch):
    from config import repomap

    db_path = str(tmp_path / "jobs.db")
    monkeypatch.setitem(repomap, "DB_PATH", db_path)
    conn = init_db(db_path)
    return conn


def _enqueue_n(conn, n):
    """Enqueue n distinct build_tile_hour jobs."""
    ids = []
    for i in range(n):
        job_id = enqueue(
            conn,
            "build_tile_hour",
            {
                "region_id": "ne",
                "model_id": "hrrr",
                "run_id": "run_20240101_00",
                "variable_id": "t2m",
                "forecast_hour": i + 1,
                "resolution_deg": 1.0,
            },
        )
        ids.append(job_id)
    return ids


def test_drain_processes_all_jobs(tmp_path, monkeypatch):
    """drain_queue should process all jobs and return (3, 0)."""
    conn = _setup(tmp_path, monkeypatch)
    ids = _enqueue_n(conn, 3)

    with patch("scripts.build_tiles_scheduled.process_build_tile_hour"):
        processed, failed = drain_queue(conn)

    assert processed == 3
    assert failed == 0

    for job_id in ids:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "completed"
    conn.close()


def test_drain_counts_failures(tmp_path, monkeypatch):
    """drain_queue: first job fails, siblings are cancelled, nothing left to process."""
    conn = _setup(tmp_path, monkeypatch)
    _enqueue_n(conn, 2)

    call_count = 0

    def fail_first(conn, job):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")

    with patch("scripts.build_tiles_scheduled.process_build_tile_hour", side_effect=fail_first):
        processed, failed = drain_queue(conn)

    # Job 1 fails, cancel_siblings cancels Job 2 (same model/run), queue is empty
    assert failed == 1
    assert processed == 0  # sibling was cancelled, not processed
    conn.close()
