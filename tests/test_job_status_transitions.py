"""Contract tests for job status transitions: the full lifecycle."""

from jobs import claim, complete, enqueue, fail, init_db, recover_stale


def test_full_lifecycle_pending_to_completed(tmp_path):
    """enqueue -> claim -> complete: verify status at each step."""
    conn = init_db(str(tmp_path / "jobs.db"))

    # Step 1: enqueue
    job_id = enqueue(conn, "build_tile_hour", {"a": 1})
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "pending"

    # Step 2: claim
    job = claim(conn, "w1")
    assert job is not None
    assert job["id"] == job_id
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "processing"

    # Step 3: complete
    complete(conn, job_id)
    row = conn.execute("SELECT status, completed_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


def test_full_lifecycle_pending_to_failed_to_retry(tmp_path):
    """enqueue -> claim -> fail(max_retries=3): should go back to pending with retry_count=1."""
    conn = init_db(str(tmp_path / "jobs.db"))

    job_id = enqueue(conn, "build_tile_hour", {"a": 1})
    claim(conn, "w1")
    fail(conn, job_id, "network error", max_retries=3)

    row = conn.execute(
        "SELECT status, retry_count, retry_after, error_message FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert row["retry_after"] is not None
    assert "network error" in row["error_message"]


def test_full_lifecycle_exhaust_retries(tmp_path):
    """Repeated fail() until max_retries exhausted -> final status is 'failed'."""
    conn = init_db(str(tmp_path / "jobs.db"))
    max_retries = 2

    job_id = enqueue(conn, "build_tile_hour", {"a": 1})

    for i in range(max_retries + 1):
        # Each iteration: claim then fail
        # After fail with retries remaining, status goes to pending with future retry_after
        # We need to clear retry_after so we can claim again immediately
        conn.execute(
            "UPDATE jobs SET retry_after = NULL WHERE id = ?", (job_id,)
        )
        conn.commit()

        job = claim(conn, "w1")
        assert job is not None, f"Should be claimable on iteration {i}"
        fail(conn, job_id, f"error #{i}", max_retries=max_retries)

    row = conn.execute("SELECT status, retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["retry_count"] == max_retries + 1


def test_stale_recovery_then_reprocess(tmp_path):
    """enqueue -> claim -> recover_stale -> claim again -> complete."""
    conn = init_db(str(tmp_path / "jobs.db"))

    job_id = enqueue(conn, "build_tile_hour", {"a": 1})
    job = claim(conn, "w1")
    assert job is not None

    # Simulate crash: job stuck in processing for 20 minutes
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "processing"
    conn.execute(
        "UPDATE jobs SET started_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-20 minutes') WHERE id = ?",
        (job_id,),
    )
    conn.commit()

    # Recover stale jobs
    recovered = recover_stale(conn)
    assert recovered == 1
    row = conn.execute("SELECT status, worker_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["worker_id"] is None

    # Re-claim and complete
    job2 = claim(conn, "w2")
    assert job2 is not None
    assert job2["id"] == job_id

    complete(conn, job_id)
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "completed"
