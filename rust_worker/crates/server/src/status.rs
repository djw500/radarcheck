//! Status and job management API handlers.
//!
//! Ports Python status_utils.py, routes/status.py, and job management from jobs.py.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use axum::extract::{Query, State};
use axum::response::{IntoResponse, Json, Response};
use axum::http::StatusCode;
use rusqlite::{Connection, params};
use serde::Deserialize;

use radarcheck_core::config;
use super::AppState;

// ── Helpers ──────────────────────────────────────────────────────────────────

fn open_db(db_path: &Path) -> Result<Connection, rusqlite::Error> {
    let conn = Connection::open(db_path)?;
    conn.execute_batch(
        "PRAGMA journal_mode=WAL;
         PRAGMA busy_timeout=2000;
         PRAGMA foreign_keys=ON;"
    )?;
    Ok(conn)
}

fn error_json(status: u16, msg: &str) -> Response {
    let code = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (code, Json(serde_json::json!({"error": msg}))).into_response()
}

// ── Status summary ──────────────────────────────────────────────────────────

pub async fn api_status_summary(State(state): State<Arc<AppState>>) -> Response {
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        let tiles_dir = state.tiles_dir.clone();
        let cache_dir = state.cache_dir.clone();
        move || status_summary_blocking(&db_path, &tiles_dir, &cache_dir)
    })
    .await;

    match result {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

fn status_summary_blocking(
    db_path: &Path,
    tiles_dir: &Path,
    cache_dir: &Path,
) -> anyhow::Result<serde_json::Value> {
    let disk_usage = get_disk_usage(tiles_dir, cache_dir);
    let memory = get_memory_info();
    let scheduler_status = read_scheduler_status(cache_dir);
    let job_queue = get_job_queue_status(db_path)?;
    let rebuild_eta = get_rebuild_eta(db_path);
    let now = chrono::Utc::now().to_rfc3339();

    Ok(serde_json::json!({
        "disk_usage": disk_usage,
        "memory": memory,
        "scheduler_status": scheduler_status,
        "job_queue": job_queue,
        "rebuild_eta": rebuild_eta,
        "timestamp": now,
    }))
}

fn get_disk_usage(tiles_dir: &Path, cache_dir: &Path) -> serde_json::Value {
    let herbie_dir = cache_dir.join("herbie");

    let mut gribs_total: u64 = 0;
    let mut gribs_by_model = serde_json::Map::new();
    if herbie_dir.exists() {
        gribs_total = dir_size(&herbie_dir);
        if let Ok(entries) = std::fs::read_dir(&herbie_dir) {
            for entry in entries.flatten() {
                if entry.path().is_dir() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    let size = dir_size(&entry.path());
                    gribs_by_model.insert(name, serde_json::json!(size));
                }
            }
        }
    }

    let mut tiles_total: u64 = 0;
    let mut tiles_by_model = serde_json::Map::new();
    if tiles_dir.exists() {
        tiles_total = dir_size(tiles_dir);
        // Walk region/res/model to aggregate by model
        if let Ok(regions) = std::fs::read_dir(tiles_dir) {
            for region_entry in regions.flatten() {
                if !region_entry.path().is_dir() { continue; }
                if let Ok(res_dirs) = std::fs::read_dir(region_entry.path()) {
                    for res_entry in res_dirs.flatten() {
                        if !res_entry.path().is_dir() { continue; }
                        if let Ok(model_dirs) = std::fs::read_dir(res_entry.path()) {
                            for model_entry in model_dirs.flatten() {
                                if !model_entry.path().is_dir() { continue; }
                                let model_name = model_entry.file_name().to_string_lossy().to_string();
                                let size = dir_size(&model_entry.path());
                                let current = tiles_by_model
                                    .get(&model_name)
                                    .and_then(|v| v.as_u64())
                                    .unwrap_or(0);
                                tiles_by_model.insert(model_name, serde_json::json!(current + size));
                            }
                        }
                    }
                }
            }
        }
    }

    let mut gribs_map = serde_json::Map::new();
    gribs_map.insert("total".to_string(), serde_json::json!(gribs_total));
    for (k, v) in gribs_by_model {
        gribs_map.insert(k, v);
    }

    serde_json::json!({
        "total": gribs_total + tiles_total,
        "gribs": gribs_map,
        "tiles": {
            "total": tiles_total,
            "models": tiles_by_model,
        },
    })
}

fn dir_size(path: &Path) -> u64 {
    let mut total: u64 = 0;
    if let Ok(entries) = std::fs::read_dir(path) {
        for entry in entries.flatten() {
            let ft = entry.file_type().unwrap_or_else(|_| {
                // Fallback: assume file
                std::fs::metadata(entry.path())
                    .map(|m| m.file_type())
                    .unwrap_or_else(|_| entry.file_type().unwrap())
            });
            if ft.is_file() {
                total += entry.metadata().map(|m| m.len()).unwrap_or(0);
            } else if ft.is_dir() {
                total += dir_size(&entry.path());
            }
        }
    }
    total
}

fn get_memory_info() -> Option<serde_json::Value> {
    let content = std::fs::read_to_string("/proc/meminfo").ok()?;
    let mut total: u64 = 0;
    let mut available: u64 = 0;

    for line in content.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 2 {
            match parts[0].trim_end_matches(':') {
                "MemTotal" => total = parts[1].parse::<u64>().unwrap_or(0) * 1024,
                "MemAvailable" | "MemFree" => {
                    if available == 0 {
                        available = parts[1].parse::<u64>().unwrap_or(0) * 1024;
                    }
                }
                _ => {}
            }
        }
    }

    let used = total.saturating_sub(available);
    let pct = if total > 0 {
        (used as f64 / total as f64 * 1000.0).round() / 10.0
    } else {
        0.0
    };

    Some(serde_json::json!({
        "total": total,
        "available": available,
        "used": used,
        "percent_used": pct,
    }))
}

fn read_scheduler_status(cache_dir: &Path) -> serde_json::Value {
    let path = cache_dir.join("scheduler_status.json");
    match std::fs::read_to_string(&path) {
        Ok(content) => serde_json::from_str(&content).unwrap_or(serde_json::json!({})),
        Err(_) => serde_json::json!({}),
    }
}

fn get_job_queue_status(db_path: &Path) -> anyhow::Result<serde_json::Value> {
    let conn = open_db(db_path)?;
    let mut stmt = conn.prepare("SELECT status, COUNT(*) as count FROM jobs GROUP BY status")?;
    let rows = stmt.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    })?;

    // Always include all 4 statuses so JS doesn't get undefined
    let mut counts = HashMap::from([
        ("pending".to_string(), 0i64),
        ("processing".to_string(), 0i64),
        ("completed".to_string(), 0i64),
        ("failed".to_string(), 0i64),
    ]);
    for row in rows {
        if let Ok((status, count)) = row {
            counts.insert(status, count);
        }
    }
    Ok(serde_json::json!(counts))
}

fn get_rebuild_eta(db_path: &Path) -> Option<serde_json::Value> {
    let conn = open_db(db_path).ok()?;
    let default_workers: i64 = std::env::var("TILE_BUILD_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(2);

    let pending: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('pending','processing')",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);

    let avg_duration: Option<f64> = conn
        .query_row(
            "SELECT AVG((julianday(completed_at) - julianday(started_at)) * 86400)
             FROM jobs WHERE status='completed'
             AND started_at IS NOT NULL AND completed_at IS NOT NULL",
            [],
            |row| row.get(0),
        )
        .ok()?;

    // Active workers: completed in last 5 min
    let mut workers: i64 = conn
        .query_row(
            "SELECT COUNT(DISTINCT worker_id) FROM jobs
             WHERE status='completed'
             AND completed_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-5 minutes')",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);

    // Fallback: processing in last 2 min
    if workers == 0 {
        workers = conn
            .query_row(
                "SELECT COUNT(DISTINCT worker_id) FROM jobs
                 WHERE status='processing'
                 AND started_at > strftime('%Y-%m-%dT%H:%M:%SZ','now','-2 minutes')",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);
    }

    if workers == 0 {
        workers = default_workers;
    }

    let eta_seconds = avg_duration.map(|avg| {
        if pending > 0 {
            Some((pending as f64 * avg / workers.max(1) as f64) as i64)
        } else {
            None
        }
    }).flatten();

    Some(serde_json::json!({
        "pending_total": pending,
        "avg_job_seconds": avg_duration.map(|a| (a * 10.0).round() / 10.0),
        "workers": workers,
        "eta_seconds": eta_seconds,
    }))
}

// ── Run grid ─────────────────────────────────────────────────────────────────

pub async fn api_status_run_grid(State(state): State<Arc<AppState>>) -> Response {
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || get_run_grid(&db_path)
    })
    .await;

    match result {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

fn get_run_grid(db_path: &Path) -> anyhow::Result<serde_json::Value> {
    let conn = open_db(db_path)?;

    let mut stmt = conn.prepare(
        "SELECT
            json_extract(args_json, '$.model_id') as model_id,
            json_extract(args_json, '$.run_id') as run_id,
            json_extract(args_json, '$.variable_id') as variable_id,
            status,
            COUNT(*) as cnt
         FROM jobs
         WHERE type = 'build_tile_hour'
         GROUP BY 1, 2, 3, 4
         ORDER BY model_id, run_id DESC, variable_id, status"
    )?;

    // Build nested: model -> run -> variable -> {status: count}
    let mut raw: HashMap<String, HashMap<String, HashMap<String, HashMap<String, i64>>>> = HashMap::new();

    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, i64>(4)?,
        ))
    })?;

    for row in rows {
        let (model_id, run_id, var_id, status, cnt) = row?;
        raw.entry(model_id)
            .or_default()
            .entry(run_id)
            .or_default()
            .entry(var_id)
            .or_default()
            .insert(status, cnt);
    }

    let preferred_order = ["t2m", "apcp", "asnow", "snod"];
    let mut grid = serde_json::Map::new();

    for &model_id in config::ALL_MODEL_IDS {
        let model_config = config::get_model(model_id);
        let model_name = model_config.as_ref().map(|m| m.name).unwrap_or(model_id);
        let model_raw = match raw.get(model_id) {
            Some(r) => r,
            None => {
                grid.insert(model_id.to_string(), serde_json::json!({
                    "name": model_name,
                    "variables": [],
                    "runs": [],
                    "available_runs": get_expected_runs(model_id, 24),
                }));
                continue;
            }
        };

        // Collect all variables present in DB
        let mut all_vars: std::collections::HashSet<String> = std::collections::HashSet::new();
        for run_data in model_raw.values() {
            for var_id in run_data.keys() {
                all_vars.insert(var_id.clone());
            }
        }

        let mut ordered_vars: Vec<String> = preferred_order
            .iter()
            .filter(|v| all_vars.contains(**v))
            .map(|v| v.to_string())
            .collect();
        let mut extras: Vec<String> = all_vars
            .iter()
            .filter(|v| !preferred_order.contains(&v.as_str()))
            .cloned()
            .collect();
        extras.sort();
        ordered_vars.extend(extras);

        // Build run list (newest first)
        let mut sorted_runs: Vec<&String> = model_raw.keys().collect();
        sorted_runs.sort_by(|a, b| b.cmp(a));

        let mut run_list = Vec::new();
        for run_id in sorted_runs {
            // Display format: MM/DD HHZ
            let display = if run_id.starts_with("run_") && run_id.len() >= 15 {
                let d = &run_id[4..12]; // YYYYMMDD
                let h = &run_id[13..15]; // HH
                format!("{}/{} {}Z", &d[4..6], &d[6..8], h)
            } else {
                run_id.clone()
            };

            let run_vars = &model_raw[run_id];
            let mut var_summaries = serde_json::Map::new();
            let mut totals = HashMap::from([
                ("completed", 0i64),
                ("pending", 0i64),
                ("failed", 0i64),
                ("processing", 0i64),
                ("total", 0i64),
            ]);

            for var_id in &ordered_vars {
                let status_counts = run_vars.get(var_id);
                let completed = status_counts.and_then(|s| s.get("completed")).copied().unwrap_or(0);
                let pending = status_counts.and_then(|s| s.get("pending")).copied().unwrap_or(0);
                let failed = status_counts.and_then(|s| s.get("failed")).copied().unwrap_or(0);
                let processing = status_counts.and_then(|s| s.get("processing")).copied().unwrap_or(0);
                let total = completed + pending + failed + processing;

                var_summaries.insert(var_id.clone(), serde_json::json!({
                    "completed": completed,
                    "pending": pending,
                    "failed": failed,
                    "processing": processing,
                    "total": total,
                }));

                *totals.get_mut("completed").unwrap() += completed;
                *totals.get_mut("pending").unwrap() += pending;
                *totals.get_mut("failed").unwrap() += failed;
                *totals.get_mut("processing").unwrap() += processing;
                *totals.get_mut("total").unwrap() += total;
            }

            // Skip runs with zero useful work
            if totals["completed"] == 0 && totals["pending"] == 0 && totals["processing"] == 0 {
                continue;
            }

            run_list.push(serde_json::json!({
                "run_id": run_id,
                "display": display,
                "variables": var_summaries,
                "totals": {
                    "completed": totals["completed"],
                    "pending": totals["pending"],
                    "failed": totals["failed"],
                    "processing": totals["processing"],
                    "total": totals["total"],
                },
            }));
        }

        grid.insert(model_id.to_string(), serde_json::json!({
            "name": model_name,
            "variables": ordered_vars,
            "runs": run_list,
            "available_runs": get_expected_runs(model_id, 24),
        }));
    }

    Ok(serde_json::Value::Object(grid))
}

/// Get expected runs for backfill dropdown (last N hours).
fn get_expected_runs(model_id: &str, lookback_hours: i64) -> Vec<String> {
    if config::get_model(model_id).is_none() {
        return vec![];
    }

    let now = chrono::Utc::now();
    let mut runs = Vec::new();

    // Determine update frequency from model (approximate)
    let update_freq: i64 = match model_id {
        "hrrr" | "nbm" => 1,
        _ => 6,
    };

    for hours_ago in 0..lookback_hours {
        let check_time = now - chrono::Duration::hours(hours_ago);
        let date_str = check_time.format("%Y%m%d").to_string();
        let init_hour: i64 = check_time.format("%H").to_string().parse().unwrap_or(0);

        if update_freq > 1 && init_hour % update_freq != 0 {
            continue;
        }

        let is_recent = hours_ago <= 12;
        let is_synoptic = init_hour % 6 == 0;

        if is_recent || is_synoptic {
            runs.push(format!("run_{}_{:02}", date_str, init_hour));
        }
    }

    runs
}

// ── Logs ─────────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct LogsParams {
    lines: Option<usize>,
}

pub async fn api_status_logs(Query(params): Query<LogsParams>) -> Json<serde_json::Value> {
    let n = params.lines.unwrap_or(100);
    let log_path = "logs/scheduler_detailed.log";

    let lines = match std::fs::read_to_string(log_path) {
        Ok(content) => {
            let all_lines: Vec<&str> = content.lines().collect();
            let start = all_lines.len().saturating_sub(n);
            all_lines[start..].iter().map(|s| s.to_string()).collect::<Vec<_>>()
        }
        Err(_) => vec![],
    };

    Json(serde_json::json!({ "lines": lines }))
}

// ── Jobs ─────────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
pub struct JobsListParams {
    status: Option<String>,
    #[serde(rename = "type")]
    job_type: Option<String>,
    limit: Option<i64>,
}

pub async fn api_jobs_list(
    State(state): State<Arc<AppState>>,
    Query(params): Query<JobsListParams>,
) -> Response {
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || {
            let conn = open_db(&db_path)?;
            let limit = params.limit.unwrap_or(50).min(200);

            let mut clauses = Vec::new();
            let mut bind_values: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

            if let Some(ref t) = params.job_type {
                clauses.push("type = ?");
                bind_values.push(Box::new(t.clone()));
            }
            if let Some(ref s) = params.status {
                clauses.push("status = ?");
                bind_values.push(Box::new(s.clone()));
            }

            let where_clause = if clauses.is_empty() {
                String::new()
            } else {
                format!("WHERE {}", clauses.join(" AND "))
            };

            let query = format!(
                "SELECT * FROM jobs {} ORDER BY created_at DESC, id DESC LIMIT ?",
                where_clause
            );
            bind_values.push(Box::new(limit));

            let mut stmt = conn.prepare(&query)?;
            let column_names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();

            let refs: Vec<&dyn rusqlite::types::ToSql> = bind_values.iter().map(|b| b.as_ref()).collect();

            let jobs: Vec<serde_json::Value> = stmt
                .query_map(refs.as_slice(), |row| {
                    let mut map = serde_json::Map::new();
                    for (i, name) in column_names.iter().enumerate() {
                        let val: rusqlite::types::Value = row.get(i)?;
                        let json_val = match val {
                            rusqlite::types::Value::Null => serde_json::Value::Null,
                            rusqlite::types::Value::Integer(n) => serde_json::json!(n),
                            rusqlite::types::Value::Real(f) => serde_json::json!(f),
                            rusqlite::types::Value::Text(s) => serde_json::json!(s),
                            rusqlite::types::Value::Blob(b) => serde_json::json!(format!("<blob {} bytes>", b.len())),
                        };
                        map.insert(name.clone(), json_val);
                    }
                    Ok(serde_json::Value::Object(map))
                })?
                .filter_map(|r| r.ok())
                .collect();

            // Count by status
            let mut count_stmt = conn.prepare("SELECT status, COUNT(*) FROM jobs GROUP BY status")?;
            let counts: HashMap<String, i64> = count_stmt
                .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)))?
                .filter_map(|r| r.ok())
                .collect();

            Ok::<_, anyhow::Error>(serde_json::json!({
                "jobs": jobs,
                "counts": counts,
            }))
        }
    })
    .await;

    match result {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

#[derive(Deserialize)]
pub struct RetryBody {
    job_id: Option<i64>,
}

pub async fn api_jobs_retry_failed(
    State(state): State<Arc<AppState>>,
    Json(body): Json<RetryBody>,
) -> Response {
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || {
            let conn = open_db(&db_path)?;
            let count = if let Some(job_id) = body.job_id {
                conn.execute(
                    "UPDATE jobs SET status='pending', error_message=NULL, retry_after=NULL,
                     worker_id=NULL, started_at=NULL WHERE id=?1 AND status='failed'",
                    params![job_id],
                )?
            } else {
                conn.execute(
                    "UPDATE jobs SET status='pending', error_message=NULL, retry_after=NULL,
                     worker_id=NULL, started_at=NULL WHERE status='failed'",
                    [],
                )?
            };
            Ok::<_, anyhow::Error>(count)
        }
    })
    .await;

    match result {
        Ok(Ok(count)) => Json(serde_json::json!({"retried": count})).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

#[derive(Deserialize)]
pub struct CancelBody {
    job_id: Option<i64>,
    status: Option<String>,
}

pub async fn api_jobs_cancel(
    State(state): State<Arc<AppState>>,
    Json(body): Json<CancelBody>,
) -> Response {
    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || {
            let conn = open_db(&db_path)?;
            let count = if let Some(job_id) = body.job_id {
                conn.execute(
                    "UPDATE jobs SET status='failed', error_message='cancelled by user',
                     completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                     WHERE id=?1 AND status IN ('pending','processing')",
                    params![job_id],
                )?
            } else if let Some(ref status_filter) = body.status {
                conn.execute(
                    "UPDATE jobs SET status='failed', error_message='cancelled by user',
                     completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                     WHERE status=?1",
                    params![status_filter],
                )?
            } else {
                0
            };
            Ok::<_, anyhow::Error>(count)
        }
    })
    .await;

    match result {
        Ok(Ok(count)) => Json(serde_json::json!({"cancelled": count})).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

#[derive(Deserialize)]
pub struct EnqueueRunBody {
    model_id: Option<String>,
    run_id: Option<String>,
    region_id: Option<String>,
}

pub async fn api_jobs_enqueue_run(
    State(state): State<Arc<AppState>>,
    Json(body): Json<EnqueueRunBody>,
) -> Response {
    let model_id = match body.model_id {
        Some(ref m) if !m.is_empty() => m.clone(),
        _ => return error_json(400, "model_id and run_id are required"),
    };
    let run_id = match body.run_id {
        Some(ref r) if !r.is_empty() => r.clone(),
        _ => return error_json(400, "model_id and run_id are required"),
    };
    let region_id = body.region_id.unwrap_or_else(|| "ne".to_string());

    let result = tokio::task::spawn_blocking({
        let db_path = state.db_path.clone();
        move || enqueue_run_blocking(&db_path, &region_id, &model_id, &run_id)
    })
    .await;

    match result {
        Ok(Ok(count)) => Json(serde_json::json!({"enqueued": count})).into_response(),
        Ok(Err(e)) => error_json(500, &format!("{:#}", e)),
        Err(e) => error_json(500, &format!("{}", e)),
    }
}

fn enqueue_run_blocking(
    db_path: &Path,
    region_id: &str,
    model_id: &str,
    run_id: &str,
) -> anyhow::Result<usize> {
    let conn = open_db(db_path)?;
    let resolution_deg = config::get_tile_resolution_by_id(region_id, model_id);

    // Get max hours (simplified — use model default)
    let _model_config = config::get_model(model_id)
        .ok_or_else(|| anyhow::anyhow!("Unknown model: {}", model_id))?;

    // Default max hours
    let max_hours: u32 = match model_id {
        "hrrr" => 48,
        "gfs" => 384,
        "nam_nest" => 60,
        "nbm" => 264,
        "ecmwf_hres" => 240,
        _ => 48,
    };

    // Get tile build variables from env
    let var_ids_str = std::env::var("TILE_BUILD_VARIABLES")
        .unwrap_or_else(|_| "apcp,asnow,snod,t2m".to_string());
    let var_ids: Vec<&str> = var_ids_str.split(',').map(|s| s.trim()).collect();

    // Compute priority
    let now_unix = chrono::Utc::now().timestamp();
    let init_unix = radarcheck_core::tile_query::parse_run_id_to_unix(run_id).unwrap_or(0);
    let minutes_old = ((now_unix - init_unix) / 60).max(0);
    let priority = (100000i64 - minutes_old).max(0);

    let mut enqueued = 0usize;

    for var_id in &var_ids {
        // Check model exclusions
        if let Some(var_config) = config::get_variable(var_id) {
            if var_config.model_exclusions.contains(&model_id) {
                continue;
            }
        }

        for hour in 1..=max_hours {
            let args = serde_json::json!({
                "forecast_hour": hour,
                "model_id": model_id,
                "region_id": region_id,
                "resolution_deg": resolution_deg,
                "run_id": run_id,
                "variable_id": var_id,
            });
            let args_json = serde_json::to_string(&args)?;

            let hash_input = format!("build_tile_hour:{}", args_json);
            let hash = sha256_hex(&hash_input);

            let result = conn.execute(
                "INSERT OR IGNORE INTO jobs (type, args_json, args_hash, priority)
                 VALUES ('build_tile_hour', ?1, ?2, ?3)",
                params![args_json, hash, priority],
            );

            match result {
                Ok(1) => enqueued += 1,
                Ok(_) => {
                    // Already exists — try to reset if failed/cancelled
                    conn.execute(
                        "UPDATE jobs SET status='pending', error_message=NULL, retry_after=NULL,
                         retry_count=0, worker_id=NULL, started_at=NULL, completed_at=NULL
                         WHERE type='build_tile_hour' AND args_hash=?1 AND status IN ('failed','cancelled')",
                        params![hash],
                    )?;
                }
                Err(e) => return Err(e.into()),
            }
        }
    }

    Ok(enqueued)
}

fn sha256_hex(input: &str) -> String {
    // Simple SHA256-like hash for job deduplication.
    // Uses DefaultHasher for consistency within the Rust server.
    // The Python scheduler creates jobs with proper SHA256 hashes,
    // so this is only used for the enqueue-run endpoint.
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let mut hasher = DefaultHasher::new();
    input.hash(&mut hasher);
    let h1 = hasher.finish();
    let mut hasher2 = DefaultHasher::new();
    (input, h1).hash(&mut hasher2);
    let h2 = hasher2.finish();
    format!("{:016x}{:016x}{:016x}{:016x}", h1, h2, h1 ^ h2, h2.wrapping_add(h1))
}
