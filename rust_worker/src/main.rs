mod config;
mod fetch;
mod grib;
mod idx;
mod npz;
mod tiles;

use anyhow::Result;
use clap::Parser;
use log::info;

/// Radarcheck tile worker — fetches GRIB data and builds statistical tiles
#[derive(Parser, Debug)]
#[command(version, about)]
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

    info!(
        "radarcheck-worker starting (model={}, poll={}s, max_jobs={})",
        args.model.as_deref().unwrap_or("all"),
        args.poll_interval,
        args.max_jobs
    );

    // TODO: implement job loop (claim from SQLite, process, complete/fail)
    // For now, this is a skeleton that demonstrates the GRIB pipeline works.

    info!("Worker ready. Job loop not yet implemented — use Python worker for now.");
    info!("Run `cargo test` to verify GRIB decode parity with Python.");

    Ok(())
}
