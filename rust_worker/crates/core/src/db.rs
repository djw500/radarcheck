//! SQLite job queue and tile metadata database operations.
//!
//! Mirrors Python jobs.py + tile_db.py. Shared database at cache/jobs.db.

use anyhow::{Context, Result};
use rusqlite::{Connection, params};
use serde::Deserialize;
use std::path::Path;

/// A job row from the jobs table
#[derive(Debug, Clone)]
pub struct Job {
    pub id: i64,
    pub job_type: String,
    pub args_json: String,
}

/// Parsed job arguments for build_tile_hour jobs
#[derive(Debug, Clone, Deserialize)]
pub struct BuildTileHourArgs {
    pub region_id: String,
    pub model_id: String,
    pub run_id: String,
    pub variable_id: String,
    pub forecast_hour: u32,
    pub resolution_deg: Option<f64>,
}

/// Open SQLite connection with WAL mode and busy timeout.
/// Creates tile tables if they don't exist (jobs table is created by Python scheduler).
pub fn open_db(db_path: &Path) -> Result<Connection> {
    let conn = Connection::open(db_path)
        .context(format!("Failed to open database: {:?}", db_path))?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA busy_timeout=30000;
         PRAGMA foreign_keys=ON;
         PRAGMA synchronous=NORMAL;",
    )?;
    // Create tile tables (idempotent, matches tile_db.py)
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS tile_runs (
            region_id TEXT NOT NULL,
            resolution_deg REAL NOT NULL,
            model_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            init_time_utc TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (region_id, resolution_deg, model_id, run_id)
        );
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
        );
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
        );
        CREATE INDEX IF NOT EXISTS idx_tile_runs_model
            ON tile_runs (region_id, resolution_deg, model_id, run_id);
        CREATE INDEX IF NOT EXISTS idx_tile_vars_run
            ON tile_variables (region_id, resolution_deg, model_id, run_id, variable_id);",
    )?;
    Ok(conn)
}

/// Claim the next pending job, atomically setting it to 'processing'.
pub fn claim(
    conn: &Connection,
    worker_id: &str,
    model_filter: Option<&str>,
) -> Result<Option<Job>> {
    let result = if let Some(model_id) = model_filter {
        let like_pattern = format!("%\"model_id\":%\"{}\"%" , model_id);
        conn.query_row(
            "UPDATE jobs
             SET status = 'processing',
                 worker_id = ?1,
                 started_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = (
                 SELECT id FROM jobs
                 WHERE status = 'pending'
                   AND (retry_after IS NULL OR retry_after <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                   AND args_json LIKE ?2
                 ORDER BY priority DESC, created_at ASC, id ASC
                 LIMIT 1
             )
             RETURNING id, type, args_json",
            params![worker_id, like_pattern],
            |row| {
                Ok(Job {
                    id: row.get(0)?,
                    job_type: row.get(1)?,
                    args_json: row.get(2)?,
                })
            },
        )
    } else {
        conn.query_row(
            "UPDATE jobs
             SET status = 'processing',
                 worker_id = ?1,
                 started_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
             WHERE id = (
                 SELECT id FROM jobs
                 WHERE status = 'pending'
                   AND (retry_after IS NULL OR retry_after <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                 ORDER BY priority DESC, created_at ASC, id ASC
                 LIMIT 1
             )
             RETURNING id, type, args_json",
            params![worker_id],
            |row| {
                Ok(Job {
                    id: row.get(0)?,
                    job_type: row.get(1)?,
                    args_json: row.get(2)?,
                })
            },
        )
    };

    match result {
        Ok(job) => Ok(Some(job)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

/// Mark a job as completed.
pub fn complete(conn: &Connection, job_id: i64) -> Result<()> {
    conn.execute(
        "UPDATE jobs SET status = 'completed',
            completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ?1",
        params![job_id],
    )?;
    Ok(())
}

/// Mark a job as failed (no retries per user preference).
pub fn fail(conn: &Connection, job_id: i64, error: &str) -> Result<()> {
    conn.execute(
        "UPDATE jobs SET status = 'failed',
            completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
            error_message = ?1,
            retry_count = retry_count + 1
         WHERE id = ?2",
        params![error, job_id],
    )?;
    Ok(())
}

/// Cancel all pending sibling jobs for the same model+run.
pub fn cancel_siblings(conn: &Connection, job: &Job) -> Result<usize> {
    let args: BuildTileHourArgs = serde_json::from_str(&job.args_json)?;
    let model_like = format!("%\"model_id\":%\"{}\"%" , args.model_id);
    let run_like = format!("%\"run_id\":%\"{}\"%" , args.run_id);
    let count = conn.execute(
        "UPDATE jobs
         SET status = 'failed',
             error_message = 'cancelled: sibling job failed',
             completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE status = 'pending'
           AND type = ?1
           AND args_json LIKE ?2
           AND args_json LIKE ?3",
        params![job.job_type, model_like, run_like],
    )?;
    Ok(count)
}

// ── Tile metadata recording ─────────────────────────────────────────────────

pub fn record_tile_run(
    conn: &Connection,
    region_id: &str,
    resolution_deg: f64,
    model_id: &str,
    run_id: &str,
    init_time_utc: Option<&str>,
) -> Result<()> {
    conn.execute(
        "INSERT INTO tile_runs (region_id, resolution_deg, model_id, run_id, init_time_utc)
         VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(region_id, resolution_deg, model_id, run_id)
         DO UPDATE SET init_time_utc=excluded.init_time_utc",
        params![region_id, resolution_deg, model_id, run_id, init_time_utc],
    )?;
    Ok(())
}

pub fn record_tile_variable(
    conn: &Connection,
    region_id: &str,
    resolution_deg: f64,
    model_id: &str,
    run_id: &str,
    variable_id: &str,
    npz_path: &str,
    meta_path: &str,
    hours: &[i32],
    size_bytes: Option<u64>,
    job_id: i64,
) -> Result<()> {
    let hours_json = serde_json::to_string(hours)?;
    conn.execute(
        "INSERT INTO tile_variables (
            region_id, resolution_deg, model_id, run_id, variable_id,
            job_id, npz_path, meta_path, hours_json, size_bytes, updated_at
         )
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, CURRENT_TIMESTAMP)
         ON CONFLICT(region_id, resolution_deg, model_id, run_id, variable_id)
         DO UPDATE SET
            job_id=excluded.job_id,
            npz_path=excluded.npz_path,
            meta_path=excluded.meta_path,
            hours_json=excluded.hours_json,
            size_bytes=excluded.size_bytes,
            updated_at=CURRENT_TIMESTAMP",
        params![
            region_id,
            resolution_deg,
            model_id,
            run_id,
            variable_id,
            job_id,
            npz_path,
            meta_path,
            hours_json,
            size_bytes.map(|s| s as i64),
        ],
    )?;
    Ok(())
}

pub fn record_tile_hour(
    conn: &Connection,
    region_id: &str,
    resolution_deg: f64,
    model_id: &str,
    run_id: &str,
    variable_id: &str,
    forecast_hour: u32,
    npz_path: &str,
    job_id: i64,
) -> Result<()> {
    conn.execute(
        "INSERT INTO tile_hours (
            region_id, resolution_deg, model_id, run_id, variable_id,
            forecast_hour, job_id, npz_path, updated_at
         )
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, CURRENT_TIMESTAMP)
         ON CONFLICT(region_id, resolution_deg, model_id, run_id, variable_id, forecast_hour)
         DO UPDATE SET
            job_id=excluded.job_id,
            npz_path=excluded.npz_path,
            updated_at=CURRENT_TIMESTAMP",
        params![
            region_id,
            resolution_deg,
            model_id,
            run_id,
            variable_id,
            forecast_hour as i32,
            job_id,
            npz_path,
        ],
    )?;
    Ok(())
}
