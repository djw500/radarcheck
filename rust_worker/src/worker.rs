//! Job processing pipeline: fetch GRIB → decode → build tiles → save NPZ → record in DB.
//!
//! Mirrors Python job_worker.py process_build_tile_hour().

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use ndarray::Array3;
use serde::Serialize;

use crate::config;
use crate::db::{self, BuildTileHourArgs, Job};
use crate::fetch;
use crate::grib;
use crate::npz::{self, TileNpz};
use crate::tiles;

#[derive(Debug, Serialize)]
struct TileMeta {
    region_id: String,
    model_id: String,
    run_id: String,
    variable_id: String,
    lat_min: f64,
    lat_max: f64,
    lon_min: f64,
    lon_max: f64,
    resolution_deg: f64,
    units: String,
    lon_0_360: bool,
    index_lon_min: f64,
    init_time_utc: Option<String>,
}

/// Process a single build_tile_hour job.
pub fn process_build_tile_hour(
    conn: &rusqlite::Connection,
    job: &Job,
    tiles_dir: &Path,
) -> Result<()> {
    let args: BuildTileHourArgs = serde_json::from_str(&job.args_json)
        .context("Failed to parse job args_json")?;

    let model = config::get_model(&args.model_id)
        .context(format!("Unknown model: {}", args.model_id))?;

    if model.grib_url_template.is_empty() {
        bail!(
            "Model {} not supported by Rust worker (no URL template — use Python worker)",
            args.model_id
        );
    }

    let region = config::get_region(&args.region_id)
        .context(format!("Unknown region: {}", args.region_id))?;

    let resolution_deg = args
        .resolution_deg
        .unwrap_or_else(|| config::get_tile_resolution(region, &args.model_id));

    let var_config = config::get_variable(&args.variable_id)
        .context(format!("Unknown variable: {}", args.variable_id))?;

    let (date_str, init_hour) = parse_run_id(&args.run_id)?;

    // Build URLs and fetch GRIB
    let grib_url = config::build_grib_url(&model, &date_str, &init_hour, args.forecast_hour);
    let idx_url = config::build_idx_url(&model, &date_str, &init_hour, args.forecast_hour);
    let search = var_config.search.get_search(model.herbie_model);

    let grib_bytes = fetch::fetch_variable_grib(&grib_url, &idx_url, search).context(format!(
        "GRIB2 file not found: {}/{} f{}",
        args.model_id, args.variable_id, args.forecast_hour
    ))?;

    // Decode GRIB
    let decoded = grib::decode_grib_message(&grib_bytes)
        .context("Failed to decode GRIB message")?;

    // Determine unit conversion
    let src_units = if decoded.units != "unknown" {
        Some(decoded.units.as_str())
    } else {
        None
    };
    let conversion = var_config.conversion_for_units(src_units);

    // Build tile statistics
    let tile_stats = tiles::build_tile_stats(&decoded, region, resolution_deg, conversion)
        .context("Failed to build tile stats")?;

    // Build init_time_utc
    let init_time_utc = format!(
        "{}-{}-{}T{}:00:00Z",
        &date_str[..4],
        &date_str[4..6],
        &date_str[6..8],
        &init_hour
    );

    let meta = TileMeta {
        region_id: args.region_id.clone(),
        model_id: args.model_id.clone(),
        run_id: args.run_id.clone(),
        variable_id: args.variable_id.clone(),
        lat_min: region.lat_min,
        lat_max: region.lat_max,
        lon_min: region.lon_min,
        lon_max: region.lon_max,
        resolution_deg,
        units: var_config.units.to_string(),
        lon_0_360: tile_stats.lon_0_360,
        index_lon_min: tile_stats.index_lon_min,
        init_time_utc: Some(init_time_utc.clone()),
    };

    // Record tile run
    db::record_tile_run(
        conn,
        &args.region_id,
        resolution_deg,
        &args.model_id,
        &args.run_id,
        Some(&init_time_utc),
    )?;

    // Upsert NPZ (merge with existing hours)
    let (npz_path, merged_hours) = upsert_tiles_npz(
        tiles_dir,
        region,
        resolution_deg,
        &args.model_id,
        &args.run_id,
        &args.variable_id,
        args.forecast_hour as i32,
        &tile_stats,
    )?;

    // Write meta.json
    let meta_path = npz_path.with_extension("meta.json");
    let meta_json = serde_json::to_string_pretty(&meta)?;
    std::fs::write(&meta_path, meta_json).context("Failed to write meta.json")?;

    // Get file size
    let size_bytes = std::fs::metadata(&npz_path).map(|m| m.len()).ok();

    // Record in tile DB
    let npz_str = npz_path.to_string_lossy().to_string();
    let meta_str = meta_path.to_string_lossy().to_string();

    db::record_tile_variable(
        conn,
        &args.region_id,
        resolution_deg,
        &args.model_id,
        &args.run_id,
        &args.variable_id,
        &npz_str,
        &meta_str,
        &merged_hours,
        size_bytes,
        job.id,
    )?;

    db::record_tile_hour(
        conn,
        &args.region_id,
        resolution_deg,
        &args.model_id,
        &args.run_id,
        &args.variable_id,
        args.forecast_hour,
        &npz_str,
        job.id,
    )?;

    Ok(())
}

/// Parse "run_YYYYMMDD_HH" → (YYYYMMDD, HH)
fn parse_run_id(run_id: &str) -> Result<(String, String)> {
    let parts: Vec<&str> = run_id.split('_').collect();
    if parts.len() != 3 || parts[0] != "run" {
        bail!(
            "run_id must be of the form run_YYYYMMDD_HH, got: {}",
            run_id
        );
    }
    Ok((parts[1].to_string(), parts[2].to_string()))
}

/// Upsert tile NPZ: merge new hour into existing file, or create new.
fn upsert_tiles_npz(
    base_dir: &Path,
    region: &config::TilingRegion,
    resolution_deg: f64,
    model_id: &str,
    run_id: &str,
    variable_id: &str,
    forecast_hour: i32,
    tile_stats: &tiles::TileStats,
) -> Result<(PathBuf, Vec<i32>)> {
    let res_dir = config::format_res_dir(resolution_deg);
    let out_dir = base_dir
        .join(region.id)
        .join(&res_dir)
        .join(model_id)
        .join(run_id);
    std::fs::create_dir_all(&out_dir).context("Failed to create tile output directory")?;

    let npz_path = out_dir.join(format!("{}.npz", variable_id));
    let lock_path = out_dir.join(format!("{}.npz.lock", variable_id));

    let save_means = region.stats.contains(&"mean");
    let save_mins = region.stats.contains(&"min");
    let save_maxs = region.stats.contains(&"max");

    // Acquire file lock
    use fs2::FileExt;
    let lock_file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&lock_path)?;
    lock_file.lock_exclusive()?;

    let result = do_upsert(
        &npz_path,
        forecast_hour,
        tile_stats,
        save_means,
        save_mins,
        save_maxs,
    );

    lock_file.unlock()?;

    let merged_hours = result?;
    Ok((npz_path, merged_hours))
}

/// Inner upsert logic (called under file lock).
fn do_upsert(
    npz_path: &Path,
    forecast_hour: i32,
    tile_stats: &tiles::TileStats,
    save_means: bool,
    save_mins: bool,
    save_maxs: bool,
) -> Result<Vec<i32>> {
    let ny = tile_stats.ny;
    let nx = tile_stats.nx;

    if npz_path.exists() {
        // Read existing NPZ, merge
        let existing = match npz::read_tile_npz(npz_path) {
            Ok(e) => e,
            Err(e) => {
                log::warn!("Corrupt NPZ at {:?} ({}), overwriting", npz_path, e);
                let hours = vec![forecast_hour];
                write_fresh_npz(npz_path, &hours, tile_stats, ny, nx, save_means, save_mins, save_maxs)?;
                return Ok(hours);
            }
        };

        let mut all_hours: BTreeSet<i32> = existing.hours.iter().cloned().collect();
        all_hours.insert(forecast_hour);
        let merged_hours: Vec<i32> = all_hours.into_iter().collect();
        let time_len = merged_hours.len();

        let hour_to_idx: std::collections::HashMap<i32, usize> = merged_hours
            .iter()
            .enumerate()
            .map(|(i, &h)| (h, i))
            .collect();

        let merge = |existing_arr: Option<&Array3<f32>>,
                     new_2d: &ndarray::Array2<f32>|
                     -> Array3<f32> {
            let mut out = Array3::<f32>::from_elem((time_len, ny, nx), f32::NAN);

            // Copy existing hour slices
            if let Some(ex) = existing_arr {
                for (old_idx, &hour) in existing.hours.iter().enumerate() {
                    if let Some(&new_idx) = hour_to_idx.get(&hour) {
                        if old_idx < ex.shape()[0] {
                            out.slice_mut(ndarray::s![new_idx, .., ..])
                                .assign(&ex.slice(ndarray::s![old_idx, .., ..]));
                        }
                    }
                }
            }

            // Write new hour
            let new_idx = hour_to_idx[&forecast_hour];
            out.slice_mut(ndarray::s![new_idx, .., ..])
                .assign(new_2d);

            out
        };

        let tile_npz = TileNpz {
            hours: merged_hours.clone(),
            means: if save_means {
                Some(merge(existing.means.as_ref(), &tile_stats.means))
            } else {
                None
            },
            mins: if save_mins {
                Some(merge(existing.mins.as_ref(), &tile_stats.mins))
            } else {
                None
            },
            maxs: if save_maxs {
                Some(merge(existing.maxs.as_ref(), &tile_stats.maxs))
            } else {
                None
            },
        };

        npz::write_tile_npz(npz_path, &tile_npz)?;
        Ok(merged_hours)
    } else {
        let hours = vec![forecast_hour];
        write_fresh_npz(npz_path, &hours, tile_stats, ny, nx, save_means, save_mins, save_maxs)?;
        Ok(hours)
    }
}

/// Write a brand-new NPZ with a single forecast hour.
fn write_fresh_npz(
    path: &Path,
    hours: &[i32],
    stats: &tiles::TileStats,
    ny: usize,
    nx: usize,
    save_means: bool,
    save_mins: bool,
    save_maxs: bool,
) -> Result<()> {
    let tile_npz = TileNpz {
        hours: hours.to_vec(),
        means: if save_means {
            Some(stats.means.clone().into_shape_with_order((1, ny, nx))?)
        } else {
            None
        },
        mins: if save_mins {
            Some(stats.mins.clone().into_shape_with_order((1, ny, nx))?)
        } else {
            None
        },
        maxs: if save_maxs {
            Some(stats.maxs.clone().into_shape_with_order((1, ny, nx))?)
        } else {
            None
        },
    };
    npz::write_tile_npz(path, &tile_npz)
}
