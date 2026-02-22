import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from jobs import (
    claim,
    complete,
    count_by_status,
    enqueue,
    fail,
    get_jobs,
    init_db,
    prune_completed,
    recover_stale,
)


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = init_db(str(db_path))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs';"
    ).fetchone()
    assert tables is not None


def test_init_db_enables_wal_mode(tmp_path):
    db_path = tmp_path / "jobs.db"
    conn = init_db(str(db_path))
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "jobs.db"
    init_db(str(db_path))
    conn = init_db(str(db_path))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs';"
    ).fetchone()
    assert tables is not None


def test_enqueue_returns_job_id(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    assert isinstance(job_id, int)


def test_enqueue_sets_pending_status(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "pending"


def test_enqueue_duplicate_is_noop(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    duplicate_id = enqueue(conn, "ingest_grib", {"a": 1})
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert duplicate_id is None
    assert count == 1
    assert job_id is not None


def test_enqueue_different_args_creates_separate_jobs(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1})
    enqueue(conn, "ingest_grib", {"a": 2})
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert count == 2


def test_enqueue_with_priority(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1}, priority=5)
    priority = conn.execute("SELECT priority FROM jobs").fetchone()[0]
    assert priority == 5


def test_claim_returns_highest_priority_first(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1}, priority=1)
    enqueue(conn, "ingest_grib", {"a": 2}, priority=5)
    job = claim(conn, "worker-1")
    assert job["args_json"].endswith("2}")


def test_claim_returns_oldest_first_at_same_priority(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1}, priority=1)
    enqueue(conn, "ingest_grib", {"a": 2}, priority=1)
    job = claim(conn, "worker-1")
    assert job["args_json"].endswith("1}")


def test_claim_sets_processing_status(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    claim(conn, "worker-1")
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "processing"


def test_claim_sets_worker_id_and_started_at(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    claim(conn, "worker-1")
    row = conn.execute(
        "SELECT worker_id, started_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["worker_id"] == "worker-1"
    assert row["started_at"] is not None


def test_claim_returns_none_when_empty(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    assert claim(conn, "worker-1") is None


def test_claim_skips_processing_jobs(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute("UPDATE jobs SET status = 'processing' WHERE id = ?", (job_id,))
    conn.commit()
    assert claim(conn, "worker-1") is None


def test_claim_skips_jobs_before_retry_after(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE jobs SET retry_after = ? WHERE id = ?", (future, job_id))
    conn.commit()
    assert claim(conn, "worker-1") is None


def test_claim_returns_jobs_past_retry_after(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE jobs SET retry_after = ? WHERE id = ?", (past, job_id))
    conn.commit()
    job = claim(conn, "worker-1")
    assert job["id"] == job_id


def test_complete_sets_completed_status(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    complete(conn, job_id)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "completed"


def test_complete_sets_completed_at_timestamp(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    complete(conn, job_id)
    completed_at = conn.execute(
        "SELECT completed_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    assert completed_at is not None


def test_fail_sets_failed_status(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    fail(conn, job_id, "boom", max_retries=0)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "failed"


def test_fail_stores_error_message(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    fail(conn, job_id, "boom", max_retries=0)
    error = conn.execute(
        "SELECT error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    assert error == "boom"


def test_fail_requeues_if_under_max_retries(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    fail(conn, job_id, "boom", max_retries=3)
    row = conn.execute(
        "SELECT status, retry_after FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["retry_after"] is not None


def test_fail_stays_failed_if_at_max_retries(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute("UPDATE jobs SET retry_count = 1 WHERE id = ?", (job_id,))
    conn.commit()
    fail(conn, job_id, "boom", max_retries=1)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "failed"


def test_fail_increments_retry_count(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    fail(conn, job_id, "boom", max_retries=3)
    retry_count = conn.execute(
        "SELECT retry_count FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    assert retry_count == 1


def test_fail_requeue_sets_retry_after_with_backoff(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    fail(conn, job_id, "boom", max_retries=3)
    retry_after = conn.execute(
        "SELECT retry_after FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    assert retry_after is not None


def test_recover_stale_resets_processing_to_pending(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute("UPDATE jobs SET status = 'processing' WHERE id = ?", (job_id,))
    conn.commit()
    recover_stale(conn)
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "pending"


def test_recover_stale_clears_worker_id(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute(
        "UPDATE jobs SET status = 'processing', worker_id = 'w' WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    recover_stale(conn)
    worker_id = conn.execute(
        "SELECT worker_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()[0]
    assert worker_id is None


def test_prune_completed_deletes_old_jobs(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute(
        "UPDATE jobs SET status = 'completed', completed_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%SZ"), job_id),
    )
    conn.commit()
    pruned = prune_completed(conn, older_than_hours=72)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert pruned == 1
    assert count == 0


def test_prune_completed_keeps_recent_jobs(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    job_id = enqueue(conn, "ingest_grib", {"a": 1})
    conn.execute(
        "UPDATE jobs SET status = 'completed', completed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), job_id),
    )
    conn.commit()
    pruned = prune_completed(conn, older_than_hours=72)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert pruned == 0
    assert count == 1


def test_count_by_status_returns_correct_counts(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1})
    enqueue(conn, "ingest_grib", {"a": 2})
    job_id = enqueue(conn, "ingest_grib", {"a": 3})
    complete(conn, job_id)
    counts = count_by_status(conn)
    assert counts["pending"] == 2
    assert counts["completed"] == 1


def test_concurrent_claims_no_duplicates(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1})
    enqueue(conn, "ingest_grib", {"a": 2})

    results = []

    def claim_job():
        local_conn = sqlite3.connect(str(tmp_path / "jobs.db"))
        local_conn.row_factory = sqlite3.Row
        job = claim(local_conn, threading.current_thread().name)
        results.append(job["id"] if job else None)
        local_conn.close()

    threads = [threading.Thread(target=claim_job, name=f"worker-{i}") for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 2


def test_get_jobs_filters_by_type_and_status(tmp_path):
    conn = init_db(str(tmp_path / "jobs.db"))
    enqueue(conn, "ingest_grib", {"a": 1})
    job_id = enqueue(conn, "build_tile", {"a": 2})
    complete(conn, job_id)
    jobs = get_jobs(conn, job_type="build_tile", status="completed", limit=10)
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["type"] == "build_tile"
