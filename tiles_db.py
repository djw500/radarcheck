import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def init_tiles_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id       TEXT NOT NULL,
            resolution_deg  REAL NOT NULL,
            model_id        TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            init_time_utc   TEXT,
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            UNIQUE(region_id, resolution_deg, model_id, run_id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tile_variables (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            region_id       TEXT NOT NULL,
            resolution_deg  REAL NOT NULL,
            model_id        TEXT NOT NULL,
            run_id          TEXT NOT NULL,
            variable_id     TEXT NOT NULL,
            hours_json      TEXT NOT NULL,
            size_bytes      INTEGER,
            npz_path        TEXT NOT NULL,
            meta_path       TEXT NOT NULL,
            lat_min         REAL,
            lat_max         REAL,
            lon_min         REAL,
            lon_max         REAL,
            index_lon_min   REAL,
            lon_0_360       INTEGER,
            updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            UNIQUE(region_id, resolution_deg, model_id, run_id, variable_id)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tile_runs_lookup
            ON tile_runs(region_id, resolution_deg, model_id, init_time_utc DESC, run_id DESC);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tile_variables_lookup
            ON tile_variables(region_id, resolution_deg, model_id, run_id);
        """
    )
    conn.commit()
    return conn


def record_tile_run(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    init_time_utc: Optional[str],
) -> None:
    conn = init_tiles_db(db_path)
    conn.execute(
        """
        INSERT INTO tile_runs (region_id, resolution_deg, model_id, run_id, init_time_utc, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(region_id, resolution_deg, model_id, run_id)
        DO UPDATE SET init_time_utc = excluded.init_time_utc,
                      updated_at = excluded.updated_at;
        """,
        (region_id, resolution_deg, model_id, run_id, init_time_utc, _utc_now_iso()),
    )
    conn.commit()
    conn.close()


def record_tile_variable(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
    hours: Iterable[int],
    size_bytes: Optional[int],
    npz_path: str,
    meta_path: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    hours_json = json.dumps(list(hours))
    meta = meta or {}
    conn = init_tiles_db(db_path)
    conn.execute(
        """
        INSERT INTO tile_variables (
            region_id, resolution_deg, model_id, run_id, variable_id,
            hours_json, size_bytes, npz_path, meta_path,
            lat_min, lat_max, lon_min, lon_max, index_lon_min, lon_0_360,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(region_id, resolution_deg, model_id, run_id, variable_id)
        DO UPDATE SET hours_json = excluded.hours_json,
                      size_bytes = excluded.size_bytes,
                      npz_path = excluded.npz_path,
                      meta_path = excluded.meta_path,
                      lat_min = excluded.lat_min,
                      lat_max = excluded.lat_max,
                      lon_min = excluded.lon_min,
                      lon_max = excluded.lon_max,
                      index_lon_min = excluded.index_lon_min,
                      lon_0_360 = excluded.lon_0_360,
                      updated_at = excluded.updated_at;
        """,
        (
            region_id,
            resolution_deg,
            model_id,
            run_id,
            variable_id,
            hours_json,
            size_bytes,
            npz_path,
            meta_path,
            meta.get("lat_min"),
            meta.get("lat_max"),
            meta.get("lon_min"),
            meta.get("lon_max"),
            meta.get("index_lon_min"),
            int(bool(meta.get("lon_0_360", False))) if meta.get("lon_0_360") is not None else None,
            _utc_now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def fetch_tile_runs(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
) -> List[str]:
    conn = init_tiles_db(db_path)
    rows = conn.execute(
        """
        SELECT run_id
        FROM tile_runs
        WHERE region_id = ? AND resolution_deg = ? AND model_id = ?
        ORDER BY init_time_utc DESC, run_id DESC;
        """,
        (region_id, resolution_deg, model_id),
    ).fetchall()
    conn.close()
    return [row["run_id"] for row in rows]


def fetch_tile_models(
    db_path: str,
    region_id: str,
    resolution_deg: float,
) -> Dict[str, List[str]]:
    conn = init_tiles_db(db_path)
    rows = conn.execute(
        """
        SELECT model_id, run_id, init_time_utc
        FROM tile_runs
        WHERE region_id = ? AND resolution_deg = ?
        ORDER BY init_time_utc DESC, run_id DESC;
        """,
        (region_id, resolution_deg),
    ).fetchall()
    conn.close()
    result: Dict[str, List[str]] = {}
    for row in rows:
        result.setdefault(row["model_id"], []).append(row["run_id"])
    return result


def fetch_tile_variables(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
) -> Dict[str, Dict[str, Any]]:
    conn = init_tiles_db(db_path)
    rows = conn.execute(
        """
        SELECT variable_id, hours_json, size_bytes, npz_path, meta_path
        FROM tile_variables
        WHERE region_id = ? AND resolution_deg = ? AND model_id = ? AND run_id = ?
        ORDER BY variable_id ASC;
        """,
        (region_id, resolution_deg, model_id, run_id),
    ).fetchall()
    conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            hours = json.loads(row["hours_json"])
        except json.JSONDecodeError:
            hours = []
        out[row["variable_id"]] = {
            "hours": hours,
            "file": row["npz_path"],
            "size_bytes": row["size_bytes"],
            "meta": row["meta_path"],
        }
    return out


def fetch_tile_variable_hours(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
) -> List[int]:
    conn = init_tiles_db(db_path)
    row = conn.execute(
        """
        SELECT hours_json
        FROM tile_variables
        WHERE region_id = ? AND resolution_deg = ? AND model_id = ? AND run_id = ? AND variable_id = ?;
        """,
        (region_id, resolution_deg, model_id, run_id, variable_id),
    ).fetchone()
    conn.close()
    if not row:
        return []
    try:
        return json.loads(row["hours_json"])
    except json.JSONDecodeError:
        return []


def fetch_tile_variable_metadata(
    db_path: str,
    region_id: str,
    resolution_deg: float,
    model_id: str,
    run_id: str,
    variable_id: str,
) -> Optional[Dict[str, Any]]:
    conn = init_tiles_db(db_path)
    row = conn.execute(
        """
        SELECT resolution_deg, lat_min, lat_max, lon_min, lon_max, index_lon_min, lon_0_360, updated_at
        FROM tile_variables
        WHERE region_id = ? AND resolution_deg = ? AND model_id = ? AND run_id = ? AND variable_id = ?;
        """,
        (region_id, resolution_deg, model_id, run_id, variable_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "resolution_deg": row["resolution_deg"],
        "lat_min": row["lat_min"],
        "lat_max": row["lat_max"],
        "lon_min": row["lon_min"],
        "lon_max": row["lon_max"],
        "index_lon_min": row["index_lon_min"],
        "lon_0_360": bool(row["lon_0_360"]) if row["lon_0_360"] is not None else None,
        "updated_at": row["updated_at"],
    }
