//! Query helpers and accumulation logic for the API server.
//!
//! Provides accumulation, run-id parsing, and tile-run listing for the
//! v2 rctile pipeline. Point queries use `rctile_v2::query_point_v2` directly.

use std::path::Path;

use anyhow::{Context, Result};
use rusqlite::Connection;

/// List tile runs from the database for a given region/model.
pub fn list_tile_runs(
    db_path: &Path,
    region_id: &str,
    resolution_deg: f64,
    model_id: &str,
) -> Result<Vec<String>> {
    let conn = Connection::open(db_path)
        .context("Failed to open database")?;
    conn.execute_batch("PRAGMA busy_timeout=2000;")?;

    let mut stmt = conn.prepare(
        "SELECT run_id FROM tile_runs
         WHERE region_id=?1 AND resolution_deg=?2 AND model_id=?3
         ORDER BY run_id DESC"
    )?;

    let runs: Vec<String> = stmt
        .query_map(rusqlite::params![region_id, resolution_deg, model_id], |row| {
            row.get(0)
        })?
        .filter_map(|r| r.ok())
        .collect();

    Ok(runs)
}

// ── Accumulation helpers (ported from Python routes/forecast.py) ─────────────

/// Detect if accumulation data is per-step buckets (NBM) vs cumulative/resetting.
fn is_bucket_data(vals: &[f64]) -> bool {
    let mut decrease_count = 0;
    let mut bucket_like_count = 0;
    let mut running_max: f64 = 0.0;

    for i in 0..vals.len().saturating_sub(1) {
        let diff = vals[i + 1] - vals[i];
        if diff < -1e-3 {
            decrease_count += 1;
            running_max = running_max.max(vals[i]);
            let new_val = vals[i + 1];
            if running_max > 1e-3 && new_val / running_max > 0.5 {
                bucket_like_count += 1;
            }
        }
    }

    if decrease_count == 0 {
        return false;
    }
    bucket_like_count as f64 > decrease_count as f64 * 0.5
}

/// Forward-fill NaN values in a slice.
fn forward_fill_nan(values: &[f64]) -> Vec<f64> {
    let mut out = values.to_vec();
    let mut last_valid = f64::NAN;
    for v in out.iter_mut() {
        if v.is_nan() {
            if !last_valid.is_nan() {
                *v = last_valid;
            }
        } else {
            last_valid = *v;
        }
    }
    out
}

/// Convert potentially incremental/resetting cumulative series to strictly monotonic total.
pub fn accumulate_timeseries(values: &[f32]) -> Vec<f64> {
    let vals_f64: Vec<f64> = values.iter().map(|&v| v as f64).collect();
    let vals = forward_fill_nan(&vals_f64);

    if is_bucket_data(&vals) {
        // Per-step buckets: cumsum of non-tiny values
        let mut total = 0.0;
        return vals.iter().map(|&v| {
            let inc = if v < 1e-3 { 0.0 } else { v };
            total += inc;
            total
        }).collect();
    }

    // Cumulative/resetting
    if vals.is_empty() {
        return vec![];
    }

    let mut result = Vec::with_capacity(vals.len());
    result.push(if vals[0] < 1e-3 { 0.0 } else { vals[0] });

    for i in 1..vals.len() {
        let diff = vals[i] - vals[i - 1];
        let inc = if diff >= 0.0 {
            diff
        } else if diff > -0.01 {
            // Floating-point noise, not a real reset
            0.0
        } else {
            // Real reset — use the new value as the increment
            vals[i]
        };
        let inc = if inc < 1e-3 { 0.0 } else { inc };
        result.push(result[i - 1] + inc);
    }

    result
}

/// Parse "run_YYYYMMDD_HH" → ISO datetime string "YYYY-MM-DDTHH:00:00+00:00"
pub fn parse_run_id_to_init_iso(run_id: &str) -> Option<String> {
    let parts: Vec<&str> = run_id.split('_').collect();
    if parts.len() != 3 || parts[0] != "run" || parts[1].len() != 8 || parts[2].len() != 2 {
        return None;
    }
    let d = parts[1];
    let h = parts[2];
    Some(format!(
        "{}-{}-{}T{}:00:00+00:00",
        &d[..4], &d[4..6], &d[6..8], h
    ))
}

/// Parse "run_YYYYMMDD_HH" → Unix timestamp (seconds since epoch)
pub fn parse_run_id_to_unix(run_id: &str) -> Option<i64> {
    let parts: Vec<&str> = run_id.split('_').collect();
    if parts.len() != 3 || parts[0] != "run" || parts[1].len() != 8 || parts[2].len() != 2 {
        return None;
    }
    let d = parts[1];
    let h = parts[2];
    let year: i64 = d[..4].parse().ok()?;
    let month: i64 = d[4..6].parse().ok()?;
    let day: i64 = d[6..8].parse().ok()?;
    let hour: i64 = h.parse().ok()?;

    // Simple epoch calculation (good enough for 2020-2030 range)
    let mut total_days: i64 = 0;
    for y in 1970..year {
        total_days += if is_leap(y) { 366 } else { 365 };
    }
    let days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    for m in 1..month {
        total_days += days_in_month[m as usize] as i64;
        if m == 2 && is_leap(year) {
            total_days += 1;
        }
    }
    total_days += day - 1;

    Some(total_days * 86400 + hour * 3600)
}

fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}
