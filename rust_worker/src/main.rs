//! Radarcheck tile worker — fetches GRIB data and builds statistical tiles.
//!
//! Drop-in replacement for Python job_worker.py (for NOAA models).

mod config;
mod db;
mod fetch;
mod grib;
mod idx;
mod npz;
mod tiles;
mod worker;

use anyhow::Result;
use clap::Parser;
use log::{error, info, warn};
use std::path::Path;
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
    let conn = db::open_db(db_path)?;

    let poll_duration = std::time::Duration::from_secs_f64(args.poll_interval);
    let mut processed: u32 = 0;

    // Throttle NOMADS-backed models to avoid rate limiting (302 "Over Rate Limit")
    // S3-backed models (HRRR, GFS) don't need this
    let nomads_throttle = args
        .model
        .as_deref()
        .and_then(config::get_model)
        .map(|m| m.grib_url_template.contains("nomads.ncep.noaa.gov"))
        .unwrap_or(false);

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
                match worker::process_build_tile_hour(&conn, &job, tiles_dir) {
                    Ok(()) => {
                        db::complete(&conn, job.id)?;
                        processed += 1;
                        let elapsed = t0.elapsed().as_secs_f64();
                        info!(
                            "Job {} done in {:.1}s ({} total)",
                            job.id, elapsed, processed
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
        // Back off longer on rate-limit (302) errors.
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

    info!("{} shut down. Processed {} jobs.", worker_id, processed);
    Ok(())
}
