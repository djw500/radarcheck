import sqlite3
import json
import hashlib
import time
import os
import threading
from enum import Enum
from datetime import datetime, timedelta, timezone

class JobStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

def init_db(db_path: str = "cache/jobs.db") -> sqlite3.Connection:
    """Initialize the jobs database and return a connection."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            type            TEXT    NOT NULL,
            args_json       TEXT    NOT NULL,
            args_hash       TEXT    NOT NULL,
            priority        INTEGER NOT NULL DEFAULT 0,
            status          TEXT    NOT NULL DEFAULT 'pending',
            worker_id       TEXT,
            created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            started_at      TEXT,
            completed_at    TEXT,
            retry_after     TEXT,
            error_message   TEXT,
            retry_count     INTEGER NOT NULL DEFAULT 0,

            UNIQUE(type, args_hash)
        );
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_claimable
        ON jobs(status, retry_after, priority DESC, created_at ASC)
        WHERE status = 'pending';
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_by_type_status
        ON jobs(type, status);
    """)

    conn.commit()
    return conn

def enqueue(conn: sqlite3.Connection, job_type: str, args: dict, priority: int = 0) -> int:
    """Enqueue a job if it doesn't already exist. Returns job ID."""
    args_json = json.dumps(args, sort_keys=True)
    args_hash = hashlib.sha256(f"{job_type}{args_json}".encode()).hexdigest()

    try:
        cursor = conn.execute("""
            INSERT INTO jobs (type, args_json, args_hash, priority)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(type, args_hash) DO UPDATE SET
                priority = MAX(priority, excluded.priority),
                status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END,
                retry_count = CASE WHEN status = 'failed' THEN 0 ELSE retry_count END,
                error_message = CASE WHEN status = 'failed' THEN NULL ELSE error_message END
            RETURNING id;
        """, (job_type, args_json, args_hash, priority))

        row = cursor.fetchone()
        conn.commit()
        return row['id']
    except sqlite3.Error as e:
        conn.rollback()
        raise e

def claim(conn: sqlite3.Connection, worker_id: str) -> dict | None:
    """Claim the next available job."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # Using a transaction to ensure atomic claim
        with conn:
            cursor = conn.execute("""
                UPDATE jobs
                SET    status     = 'processing',
                       worker_id  = ?,
                       started_at = ?
                WHERE  id = (
                    SELECT id FROM jobs
                    WHERE  status = 'pending'
                    AND    (retry_after IS NULL OR retry_after <= ?)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                )
                RETURNING *;
            """, (worker_id, now, now))

            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    except sqlite3.Error:
        return None

def complete(conn: sqlite3.Connection, job_id: int) -> None:
    """Mark a job as completed."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("""
        UPDATE jobs
        SET    status = 'completed',
               completed_at = ?,
               worker_id = NULL
        WHERE  id = ?;
    """, (now, job_id))
    conn.commit()

def fail(conn: sqlite3.Connection, job_id: int, error_message: str, max_retries: int = 3) -> None:
    """Mark a job as failed, potentially requeuing it."""
    # Check current retry count
    cursor = conn.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    if not row:
        return

    retry_count = row['retry_count']

    if retry_count < max_retries:
        # Requeue with backoff
        backoff_seconds = 60 * (2 ** retry_count)
        retry_after = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

        conn.execute("""
            UPDATE jobs
            SET    status = 'pending',
                   worker_id = NULL,
                   retry_count = retry_count + 1,
                   retry_after = ?,
                   error_message = ?
            WHERE  id = ?;
        """, (retry_after, error_message, job_id))
    else:
        # Hard fail
        conn.execute("""
            UPDATE jobs
            SET    status = 'failed',
                   worker_id = NULL,
                   error_message = ?
            WHERE  id = ?;
        """, (error_message, job_id))

    conn.commit()

def recover_stale(conn: sqlite3.Connection, timeout_seconds: int = 3600) -> int:
    """Reset jobs that have been processing for too long."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    cursor = conn.execute("""
        UPDATE jobs
        SET    status = 'pending',
               worker_id = NULL,
               started_at = NULL
        WHERE  status = 'processing'
        AND    started_at < ?;
    """, (cutoff,))
    conn.commit()
    return cursor.rowcount

def prune_completed(conn: sqlite3.Connection, max_age_hours: int = 72) -> int:
    """Delete completed jobs older than max_age_hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    cursor = conn.execute("""
        DELETE FROM jobs
        WHERE  status = 'completed'
        AND    completed_at < ?;
    """, (cutoff,))
    conn.commit()
    return cursor.rowcount

def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Return count of jobs by status."""
    cursor = conn.execute("SELECT status, COUNT(*) as count FROM jobs GROUP BY status;")
    counts = {s.value: 0 for s in JobStatus}
    for row in cursor:
        counts[row['status']] = row['count']
    return counts

def get_jobs(conn: sqlite3.Connection, job_type: str = None, status: str = None, limit: int = 100) -> list[dict]:
    """Get list of jobs with optional filtering."""
    query = "SELECT * FROM jobs"
    params = []
    conditions = []

    if job_type:
        conditions.append("type = ?")
        params.append(job_type)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    jobs = []
    for row in cursor:
        job = dict(row)
        job['args'] = json.loads(job['args_json'])
        jobs.append(job)
    return jobs
