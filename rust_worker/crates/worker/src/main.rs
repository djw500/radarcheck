//! Radarcheck tile worker — fetches GRIB data and builds statistical tiles.
//!
//! Uses v2 rctile format: accumulates hours in memory, then finalizes
//! a compressed multi-run file per (region, model, variable).

use std::collections::HashMap;

use radarcheck_core::bucket_mapping::BucketMapping;
use radarcheck_core::worker::{self, RunAccumulator};
use radarcheck_core::{config, db};

use anyhow::Result;
use clap::Parser;
use log::{error, info, warn};
use rusqlite::Connection;
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Parser, Debug)]
#[command(version, about = "Radarcheck tile worker")]
struct Args {
    /// Only process jobs for this model
    #[arg(long)]
    model: Option<String>,

    /// Poll interval in seconds
    #[arg(long, default_value_t = 5.0)]
    poll_interval: f64,

    /// Exit after N jobs for memory cleanup (0 = unlimited)
    #[arg(long, default_value_t = 0)]
    max_jobs: u32,

    /// Process a single job and exit
    #[arg(long)]
    once: bool,

    /// Path to jobs.db
    #[arg(long, default_value = "cache/jobs.db")]
    db_path: String,

    /// Path to tiles output directory
    #[arg(long, default_value = "cache/tiles")]
    tiles_dir: String,

    /// Project root (for forecast trigger script)
    #[arg(long, default_value = "/app")]
    project_root: String,
}

/// Models that must all be loaded before triggering auto-forecast
const SYNOPTIC_MODELS: &[&str] = &["gfs", "nam_nest", "ecmwf_hres"];

/// Check if all synoptic models are loaded for a cycle and trigger forecast.
///
/// Called after a synoptic model's job completes. Checks:
/// 1. Was this the last job for this model+run?
/// 2. Do all 3 synoptic models have complete runs at the same init hour?
/// 3. Haven't we already triggered for this cycle?
fn check_and_trigger_forecast(
    conn: &Connection,
    completed_model: &str,
    completed_run_id: &str,
    project_root: &Path,
) {
    if !SYNOPTIC_MODELS.contains(&completed_model) {
        return;
    }

    // 1. Check if this model+run is fully loaded
    let remaining = match db::remaining_jobs_for_run(conn, completed_model, completed_run_id) {
        Ok(n) => n,
        Err(e) => {
            warn!("Failed to check remaining jobs: {}", e);
            return;
        }
    };
    if remaining > 0 {
        return;
    }

    // Extract init hour from run_id (e.g., "run_20260224_12" → "12")
    let parts: Vec<&str> = completed_run_id.split('_').collect();
    let init_hour = match parts.get(2) {
        Some(h) => *h,
        None => return,
    };

    info!(
        "Synoptic run complete: {}/{} -- checking other models",
        completed_model, completed_run_id
    );

    // 2. Check all 3 synoptic models have complete runs at this init hour
    let mut cycle_runs: Vec<String> = Vec::new();
    for model in SYNOPTIC_MODELS {
        match db::latest_complete_run_at_hour(conn, model, init_hour) {
            Ok(Some(run)) => {
                cycle_runs.push(run);
            }
            Ok(None) => {
                info!("  {} has no complete {}Z run yet -- not triggering", model, init_hour);
                return;
            }
            Err(e) => {
                warn!("  Failed to check {}: {}", model, e);
                return;
            }
        }
    }

    // 3. Dedup: build a cycle ID from the run IDs
    let mut sorted_runs = cycle_runs.clone();
    sorted_runs.sort();
    let cycle_id = format!("{}Z_{}", init_hour, sorted_runs.join("_"));

    let trigger_file = project_root.join("cache/last_forecast_trigger.txt");
    if let Ok(last) = std::fs::read_to_string(&trigger_file) {
        if last.trim() == cycle_id {
            info!("  Already triggered forecast for cycle {}", cycle_id);
            return;
        }
    }

    // All conditions met — trigger forecast
    info!("All synoptic models loaded for {}Z cycle: {:?}", init_hour, cycle_runs);
    info!("Triggering auto-forecast...");

    // Write trigger file BEFORE spawning to prevent double-trigger
    if let Some(parent) = trigger_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(e) = std::fs::write(&trigger_file, &cycle_id) {
        error!("Failed to write trigger file: {}", e);
    }

    // Spawn forecast in background (fire and forget)
    let script = project_root.join("scripts/run-forecast.sh");
    let log_path = project_root.join("cache/forecast_run.log");
    match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
    {
        Ok(log_file) => {
            // Strip CLAUDECODE env var so nested claude -p works
            let env: Vec<(std::ffi::OsString, std::ffi::OsString)> = std::env::vars_os()
                .filter(|(k, _)| k != "CLAUDECODE")
                .collect();
            match std::process::Command::new("bash")
                .arg(&script)
                .stdout(log_file.try_clone().unwrap_or_else(|_| log_file))
                .stderr(std::process::Stdio::from(
                    std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(&log_path)
                        .expect("reopen log"),
                ))
                .env_clear()
                .envs(env)
                .current_dir(project_root)
                .spawn()
            {
                Ok(_) => info!("Forecast script spawned in background"),
                Err(e) => error!("Failed to spawn forecast script: {}", e),
            }
        }
        Err(e) => error!("Failed to open forecast log file: {}", e),
    }
}

fn main() -> Result<()> {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let args = Args::parse();

    let worker_id = format!(
        "rust-worker-{}{}",
        std::process::id(),
        args.model
            .as_ref()
            .map(|m| format!("-{}", m))
            .unwrap_or_default()
    );

    info!(
        "{} starting (model={}, poll={}s, max_jobs={})",
        worker_id,
        args.model.as_deref().unwrap_or("all"),
        args.poll_interval,
        args.max_jobs,
    );

    let db_path = Path::new(&args.db_path);
    let tiles_dir = Path::new(&args.tiles_dir);
    let project_root = PathBuf::from(&args.project_root);
    let conn = db::open_db(db_path)?;

    let poll_duration = std::time::Duration::from_secs_f64(args.poll_interval);
    let mut processed: u32 = 0;

    // Throttle NOMADS-backed models to avoid rate limiting (302 "Over Rate Limit")
    let nomads_throttle = args
        .model
        .as_deref()
        .and_then(config::get_model)
        .map(|m| m.grib_url_template.contains("nomads.ncep.noaa.gov"))
        .unwrap_or(false);

    // v2 state: mapping cache and run accumulators
    let mut mapping_cache: HashMap<String, BucketMapping> = HashMap::new();
    let mut accumulators: HashMap<(String, String), RunAccumulator> = HashMap::new();
    let mut current_run_id: Option<String> = None;

    loop {
        let job = db::claim(&conn, &worker_id, args.model.as_deref())?;

        let job = match job {
            Some(j) => j,
            None => {
                if args.once {
                    info!(
                        "No jobs available, exiting (--once). Processed {} total.",
                        processed
                    );
                    break;
                }
                // No jobs — finalize any pending accumulators before sleeping
                if !accumulators.is_empty() {
                    info!("No pending jobs, finalizing {} accumulators", accumulators.len());
                    worker::finalize_all(&mut accumulators, tiles_dir, &conn);
                    current_run_id = None;
                }
                std::thread::sleep(poll_duration);
                continue;
            }
        };

        // Build a human-readable label for logging
        let job_label = match serde_json::from_str::<db::BuildTileHourArgs>(&job.args_json) {
            Ok(a) => format!(
                "{}/{}/{} f{}",
                a.model_id, a.run_id, a.variable_id, a.forecast_hour
            ),
            Err(_) => job.job_type.clone(),
        };

        info!("Job {}: {}", job.id, job_label);
        let t0 = Instant::now();
        let mut job_failed_rate_limit = false;

        match job.job_type.as_str() {
            "build_tile_hour" => {
                match worker::process_hour_v2(&job, &mut mapping_cache) {
                    Ok(hour_result) => {
                        // Check if we're starting a new run — finalize old accumulators
                        if let Some(ref cur_run) = current_run_id {
                            if *cur_run != hour_result.run_id && !accumulators.is_empty() {
                                info!(
                                    "Run changed {} → {}, finalizing {} accumulators",
                                    cur_run,
                                    hour_result.run_id,
                                    accumulators.len()
                                );
                                worker::finalize_all(&mut accumulators, tiles_dir, &conn);
                            }
                        }
                        current_run_id = Some(hour_result.run_id.clone());

                        // Record tile run (idempotent, shows run in status dashboard early)
                        let _ = db::record_tile_run(
                            &conn,
                            &hour_result.region_id,
                            hour_result.resolution_deg,
                            &hour_result.model_id,
                            &hour_result.run_id,
                            Some(&hour_result.init_time_utc),
                        );

                        // Get or create accumulator for this (run, variable)
                        let key = (
                            hour_result.run_id.clone(),
                            hour_result.variable_id.clone(),
                        );
                        let acc = accumulators.entry(key).or_insert_with(|| {
                            RunAccumulator::new(&hour_result)
                        });

                        // Record individual hour for progress tracking
                        let rctile_path = acc.rctile_path(tiles_dir);
                        let tile_str = rctile_path.to_string_lossy().to_string();
                        let _ = db::record_tile_hour(
                            &conn,
                            &hour_result.region_id,
                            hour_result.resolution_deg,
                            &hour_result.model_id,
                            &hour_result.run_id,
                            &hour_result.variable_id,
                            hour_result.forecast_hour as u32,
                            &tile_str,
                            job.id,
                        );

                        // Accumulate
                        acc.add_hour(hour_result.forecast_hour, hour_result.cell_values);

                        db::complete(&conn, job.id)?;
                        processed += 1;
                        let elapsed = t0.elapsed().as_secs_f64();
                        info!(
                            "Job {} done in {:.1}s ({} total)",
                            job.id, elapsed, processed
                        );

                        // Check if this completes a synoptic model's run
                        check_and_trigger_forecast(
                            &conn,
                            &hour_result.model_id,
                            &hour_result.run_id,
                            &project_root,
                        );
                    }
                    Err(e) => {
                        let elapsed = t0.elapsed().as_secs_f64();
                        let error_str = format!("{:#}", e);
                        error!(
                            "Job {} FAILED after {:.1}s ({}): {}",
                            job.id, elapsed, job_label, error_str
                        );
                        db::fail(&conn, job.id, &error_str)?;

                        // Cancel siblings when run data is truly unavailable (404),
                        // but NOT for rate limits (302) which are temporary
                        job_failed_rate_limit = error_str.contains("302");
                        let unavailable = !job_failed_rate_limit
                            && (error_str.contains("GRIB2 file not found")
                                || error_str.to_lowercase().contains("not found"));
                        if unavailable {
                            match db::cancel_siblings(&conn, &job) {
                                Ok(n) if n > 0 => {
                                    info!(
                                        "Cancelled {} sibling jobs -- run data not available",
                                        n
                                    );
                                }
                                Ok(_) => {}
                                Err(e) => warn!("Failed to cancel siblings: {}", e),
                            }
                        }
                    }
                }
            }
            other => {
                let msg = format!("Unsupported job type: {}", other);
                warn!("Job {}: {}", job.id, msg);
                db::fail(&conn, job.id, &msg)?;
            }
        }

        // Throttle NOMADS requests to avoid rate limiting.
        if nomads_throttle {
            let delay_ms = if job_failed_rate_limit { 5000 } else { 500 };
            std::thread::sleep(std::time::Duration::from_millis(delay_ms));
        }

        if args.once {
            break;
        }
        if args.max_jobs > 0 && processed >= args.max_jobs {
            info!(
                "Reached max_jobs={}, exiting for memory cleanup",
                args.max_jobs
            );
            break;
        }
    }

    // Finalize any remaining accumulators before exit
    if !accumulators.is_empty() {
        info!(
            "Shutdown: finalizing {} remaining accumulators",
            accumulators.len()
        );
        worker::finalize_all(&mut accumulators, tiles_dir, &conn);
    }

    info!("{} shut down. Processed {} jobs.", worker_id, processed);
    Ok(())
}
