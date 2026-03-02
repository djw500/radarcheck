from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from config import repomap
from jobs import init_db as init_jobs_db

DEFAULT_DB_PATH = repomap.get("DB_PATH", "cache/jobs.db")


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    conn = init_jobs_db(path)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_runs (
            region_id TEXT NOT NULL,
            resolution_deg REAL NOT NULL,
            model_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            init_time_utc TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (region_id, resolution_deg, model_id, run_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_variables (
            region_id TEXT NOT NULL,
            resolution_deg REAL NOT NULL,
            model_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            variable_id TEXT NOT NULL,
            job_id INTEGER,
            npz_path TEXT NOT NULL,
            meta_path TEXT NOT NULL,
            hours_json TEXT,
            size_bytes INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (region_id, resolution_deg, model_id, run_id, variable_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_hours (
            region_id TEXT NOT NULL,
            resolution_deg REAL NOT NULL,
            model_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            variable_id TEXT NOT NULL,
            forecast_hour INTEGER NOT NULL,
            job_id INTEGER,
            npz_path TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                region_id, resolution_deg, model_id, run_id, variable_id, forecast_hour
            )
        )
        """
    )
    _ensure_column(conn, "tile_variables", "job_id", "INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tile_runs_model
        ON tile_runs (region_id, resolution_deg, model_id, run_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tile_vars_run
        ON tile_variables (region_id, resolution_deg, model_id, run_id, variable_id)
        """
    )
    # Drop legacy unique indexes that conflict with v2 worker (job_id=0 for finalize)
    conn.execute("DROP INDEX IF EXISTS idx_tile_vars_job_id")
    conn.execute("DROP INDEX IF EXISTS idx_tile_hours_job_id")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def record_tile_run(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    init_time_utc: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO tile_runs (region_id, resolution_deg, model_id, run_id, init_time_utc)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(region_id, resolution_deg, model_id, run_id)
        DO UPDATE SET init_time_utc=excluded.init_time_utc
        """,
        (region_id, resolution_deg, model_id, run_id, init_time_utc),
    )


def record_tile_variable(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    npz_path: str,
    meta_path: str,
    hours: List[int],
    size_bytes: Optional[int],
    job_id: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tile_variables (
            region_id, resolution_deg, model_id, run_id, variable_id,
            job_id, npz_path, meta_path, hours_json, size_bytes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(region_id, resolution_deg, model_id, run_id, variable_id)
        DO UPDATE SET
            job_id=excluded.job_id,
            npz_path=excluded.npz_path,
            meta_path=excluded.meta_path,
            hours_json=excluded.hours_json,
            size_bytes=excluded.size_bytes,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            region_id,
            resolution_deg,
            model_id,
            run_id,
            variable_id,
            job_id,
            npz_path,
            meta_path,
            json.dumps(hours),
            size_bytes,
        ),
    )


def record_tile_hour(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    forecast_hour: int,
    npz_path: str,
    job_id: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tile_hours (
            region_id, resolution_deg, model_id, run_id, variable_id,
            forecast_hour, job_id, npz_path, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(region_id, resolution_deg, model_id, run_id, variable_id, forecast_hour)
        DO UPDATE SET
            job_id=excluded.job_id,
            npz_path=excluded.npz_path,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            region_id,
            resolution_deg,
            model_id,
            run_id,
            variable_id,
            int(forecast_hour),
            job_id,
            npz_path,
        ),
    )


def delete_tile_run(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
) -> None:
    """Delete all database records for a specific tile run."""
    conn.execute(
        """
        DELETE FROM tile_hours
        WHERE region_id=? AND resolution_deg=? AND model_id=? AND run_id=?
        """,
        (region_id, resolution_deg, model_id, run_id),
    )
    conn.execute(
        """
        DELETE FROM tile_variables
        WHERE region_id=? AND resolution_deg=? AND model_id=? AND run_id=?
        """,
        (region_id, resolution_deg, model_id, run_id),
    )
    conn.execute(
        """
        DELETE FROM tile_runs
        WHERE region_id=? AND resolution_deg=? AND model_id=? AND run_id=?
        """,
        (region_id, resolution_deg, model_id, run_id),
    )


def delete_region_tiles(    conn: sqlite3.Connection,
    region_id: str,
) -> None:
    """Delete all database records for a specific region."""
    conn.execute(
        """
        DELETE FROM tile_hours
        WHERE region_id=?
        """,
        (region_id,),
    )
    conn.execute(
        """
        DELETE FROM tile_variables
        WHERE region_id=?
        """,
        (region_id,),
    )
    conn.execute(
        """
        DELETE FROM tile_runs
        WHERE region_id=?
        """,
        (region_id,),
    )


def list_tile_runs_db(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
    model_id: str,
) -> List[str]:
    rows = conn.execute(
        """
        SELECT run_id
        FROM tile_runs
        WHERE region_id=? AND resolution_deg=? AND model_id=?
        ORDER BY run_id DESC
        """,
        (region_id, resolution_deg, model_id),
    ).fetchall()
    return [row["run_id"] for row in rows]


def list_tile_models_db(    conn: sqlite3.Connection,
    region_id: str,
    resolution_deg: float,
) -> Dict[str, List[str]]:
    rows = conn.execute(
        """
        SELECT model_id, run_id
        FROM tile_runs
        WHERE region_id=? AND resolution_deg=?
        ORDER BY model_id, run_id DESC
        """,
        (region_id, resolution_deg),
    ).fetchall()
    result: Dict[str, List[str]] = {}
    for row in rows:
        result.setdefault(row["model_id"], []).append(row["run_id"])
    return result
