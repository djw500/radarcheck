import os
import sqlite3
import pytest
from jobs import (
    init_db,
    enqueue,
    claim,
    complete,
    fail,
    recover_stale,
    prune_completed,
    count_by_status,
    get_jobs,
    JobStatus
)

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "jobs.db")

@pytest.fixture
def conn(db_path):
    conn = init_db(db_path)
    yield conn
    conn.close()

def test_init_db_creates_tables(db_path):
    conn = init_db(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    assert cursor.fetchone() is not None
    conn.close()

def test_enqueue_returns_job_id(conn):
    job_id = enqueue(conn, "test_job", {"foo": "bar"})
    assert isinstance(job_id, int)
    assert job_id > 0

def test_enqueue_sets_pending_status(conn):
    job_id = enqueue(conn, "test_job", {"foo": "bar"})
    job = get_jobs(conn, limit=1)[0]
    assert job["status"] == JobStatus.PENDING.value
    assert job["type"] == "test_job"
    assert job["args"] == {"foo": "bar"}

def test_enqueue_duplicate_is_noop(conn):
    job_id1 = enqueue(conn, "test_job", {"foo": "bar"})
    job_id2 = enqueue(conn, "test_job", {"foo": "bar"})
    assert job_id1 == job_id2
    assert count_by_status(conn)[JobStatus.PENDING.value] == 1

def test_enqueue_different_args_creates_separate_jobs(conn):
    job_id1 = enqueue(conn, "test_job", {"foo": "bar"})
    job_id2 = enqueue(conn, "test_job", {"foo": "baz"})
    assert job_id1 != job_id2
    assert count_by_status(conn)[JobStatus.PENDING.value] == 2

def test_claim_returns_highest_priority_first(conn):
    enqueue(conn, "low_prio", {}, priority=0)
    enqueue(conn, "high_prio", {}, priority=10)

    job = claim(conn, "worker-1")
    assert job["type"] == "high_prio"

def test_claim_returns_oldest_first_at_same_priority(conn):
    enqueue(conn, "first", {}, priority=0)
    enqueue(conn, "second", {}, priority=0)

    job = claim(conn, "worker-1")
    assert job["type"] == "first"

def test_claim_sets_processing_status(conn):
    enqueue(conn, "test_job", {})
    job = claim(conn, "worker-1")
    assert job["status"] == JobStatus.PROCESSING.value
    assert job["worker_id"] == "worker-1"
    assert job["started_at"] is not None

def test_claim_returns_none_when_empty(conn):
    job = claim(conn, "worker-1")
    assert job is None

def test_complete_sets_completed_status(conn):
    job_id = enqueue(conn, "test_job", {})
    claim(conn, "worker-1")
    complete(conn, job_id)

    job = get_jobs(conn, status=JobStatus.COMPLETED.value)[0]
    assert job["status"] == JobStatus.COMPLETED.value
    assert job["completed_at"] is not None

def test_fail_sets_failed_status_if_max_retries_exceeded(conn):
    job_id = enqueue(conn, "test_job", {})
    claim(conn, "worker-1")
    fail(conn, job_id, "error", max_retries=0)

    job = get_jobs(conn, status=JobStatus.FAILED.value)[0]
    assert job["status"] == JobStatus.FAILED.value
    assert job["error_message"] == "error"

def test_fail_requeues_if_under_max_retries(conn):
    job_id = enqueue(conn, "test_job", {})
    claim(conn, "worker-1")
    fail(conn, job_id, "error", max_retries=1)

    job = get_jobs(conn, status=JobStatus.PENDING.value)[0]
    assert job["status"] == JobStatus.PENDING.value
    assert job["retry_count"] == 1
    assert job["retry_after"] is not None

def test_recover_stale_resets_processing_to_pending(conn):
    enqueue(conn, "test_job", {})
    claim(conn, "worker-1")

    # Simulate stale job by manually updating started_at (not possible via public API easily without sleep)
    # But recover_stale logic usually checks time. For test, we can force update
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET started_at = datetime('now', '-2 hours') WHERE status = 'processing'")
    conn.commit()

    recovered = recover_stale(conn, timeout_seconds=3600)
    assert recovered == 1

    job = get_jobs(conn)[0]
    assert job["status"] == JobStatus.PENDING.value
    assert job["worker_id"] is None

def test_prune_completed_deletes_old_jobs(conn):
    enqueue(conn, "test_job", {})
    claim(conn, "worker-1")
    complete(conn, 1)

    # Manually backdate completed_at
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET completed_at = datetime('now', '-4 days') WHERE status = 'completed'")
    conn.commit()

    deleted = prune_completed(conn, max_age_hours=72)
    assert deleted == 1
    assert len(get_jobs(conn)) == 0
