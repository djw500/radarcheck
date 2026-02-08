import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

DEFAULT_DB_PATH = "cache/jobs.db"


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _args_json(args: Dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"))


def _args_hash(job_type: str, args_json: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"{job_type}:{args_json}".encode("utf-8"))
    return digest.hexdigest()


def _dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
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
            parent_job_id   INTEGER,
            UNIQUE(type, args_hash)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_claimable
            ON jobs(status, retry_after, priority DESC, created_at ASC)
            WHERE status = 'pending';
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_by_type_status
            ON jobs(type, status);
        """
    )
    conn.commit()
    return conn


def enqueue(
    conn: sqlite3.Connection,
    job_type: str,
    args: Dict[str, Any],
    priority: int = 0,
) -> Optional[int]:
    args_json = _args_json(args)
    args_hash = _args_hash(job_type, args_json)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO jobs (type, args_json, args_hash, priority)
        VALUES (?, ?, ?, ?);
        """,
        (job_type, args_json, args_hash, priority),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid


def claim(conn: sqlite3.Connection, worker_id: str) -> Optional[Dict[str, Any]]:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET    status     = 'processing',
               worker_id  = ?,
               started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        WHERE  id = (
            SELECT id FROM jobs
            WHERE  status = 'pending'
            AND    (retry_after IS NULL OR retry_after <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            ORDER BY priority DESC, created_at ASC, id ASC
            LIMIT 1
        )
        RETURNING *;
        """,
        (worker_id,),
    )
    row = cursor.fetchone()
    conn.commit()
    if row is None:
        return None
    return _dict_from_row(row)


def complete(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = 'completed',
            completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        WHERE id = ?;
        """,
        (job_id,),
    )
    conn.commit()


def _retry_after_timestamp(retry_count: int) -> str:
    delay_seconds = 60 * (2**retry_count)
    return (datetime.utcnow() + timedelta(seconds=delay_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def fail(
    conn: sqlite3.Connection,
    job_id: int,
    error: str,
    max_retries: int = 3,
) -> None:
    row = conn.execute(
        "SELECT retry_count FROM jobs WHERE id = ?;",
        (job_id,),
    ).fetchone()
    if row is None:
        return
    retry_count = row["retry_count"] + 1
    if retry_count <= max_retries:
        retry_after = _retry_after_timestamp(retry_count)
        conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                retry_after = ?,
                error_message = ?,
                retry_count = ?,
                worker_id = NULL,
                started_at = NULL
            WHERE id = ?;
            """,
            (retry_after, error, retry_count, job_id),
        )
    else:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                error_message = ?,
                retry_count = ?
            WHERE id = ?;
            """,
            (error, retry_count, job_id),
        )
    conn.commit()


def recover_stale(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'pending',
            worker_id = NULL,
            started_at = NULL
        WHERE status = 'processing';
        """
    )
    conn.commit()
    return cursor.rowcount


def prune_completed(conn: sqlite3.Connection, older_than_hours: int = 72) -> int:
    cursor = conn.execute(
        """
        DELETE FROM jobs
        WHERE status = 'completed'
        AND completed_at < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?);
        """,
        (f"-{older_than_hours} hours",),
    )
    conn.commit()
    return cursor.rowcount


def count_by_status(conn: sqlite3.Connection) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM jobs GROUP BY status;"
    ).fetchall()
    return {row["status"]: row["count"] for row in rows}


def count_by_type_and_status(conn: sqlite3.Connection) -> Dict[str, Dict[str, int]]:
    rows = conn.execute(
        """
        SELECT type, status, COUNT(*) as count
        FROM jobs
        GROUP BY type, status;
        """
    ).fetchall()
    results: Dict[str, Dict[str, int]] = {}
    for row in rows:
        results.setdefault(row["type"], {})[row["status"]] = row["count"]
    return results


def get_jobs(
    conn: sqlite3.Connection,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> Iterable[Dict[str, Any]]:
    clauses = []
    params = []
    if job_type is not None:
        clauses.append("type = ?")
        params.append(job_type)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM jobs {where_clause} ORDER BY created_at DESC, id DESC LIMIT ?;"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_dict_from_row(row) for row in rows]
