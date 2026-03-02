//! Job processing pipeline v2: fetch GRIB → decode → gather mapping → accumulate → finalize.
//!
//! Replaces v1 scatter-based tile building with gather-based bucket mapping.
//! Data accumulates in memory during a run, then finalizes to a compressed
//! multi-run .rctile v2 file.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

use crate::bucket_mapping::{self, BucketMapping};
use crate::config;
use crate::db::{BuildTileHourArgs, Job};
use crate::fetch;
use crate::grib;
use crate::rctile_v2::{self, RunData};
use crate::tile_query;
use crate::db;

/// Result of processing a single forecast hour (before accumulation).
pub struct HourResult {
    pub model_id: String,
    pub run_id: String,
    pub variable_id: String,
    pub region_id: String,
    pub forecast_hour: i32,
    pub cell_values: Vec<f32>,
    pub init_time_utc: String,
    pub init_unix: i64,
    pub ny: u16,
    pub nx: u16,
    pub resolution_deg: f64,
    pub lat_min: f32,
    pub lat_max: f32,
    pub lon_min: f32,
    pub lon_max: f32,
}

/// Accumulates hourly data for one (run, variable) combination.
/// Finalize writes a compressed multi-run v2 rctile file.
pub struct RunAccumulator {
    pub model_id: String,
    pub run_id: String,
    pub variable_id: String,
    pub region_id: String,
    pub resolution_deg: f64,
    pub init_time_utc: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    /// cell_values[cell_idx] holds all accumulated hour values for that cell
    pub cell_values: Vec<Vec<f32>>,
    pub ny: u16,
    pub nx: u16,
    pub lat_min: f32,
    pub lat_max: f32,
    pub lon_min: f32,
    pub lon_max: f32,
}

impl RunAccumulator {
    pub fn new(hr: &HourResult) -> Self {
        let n_cells = hr.ny as usize * hr.nx as usize;
        Self {
            model_id: hr.model_id.clone(),
            run_id: hr.run_id.clone(),
            variable_id: hr.variable_id.clone(),
            region_id: hr.region_id.clone(),
            resolution_deg: hr.resolution_deg,
            init_time_utc: hr.init_time_utc.clone(),
            init_unix: hr.init_unix,
            hours: Vec::new(),
            cell_values: vec![Vec::new(); n_cells],
            ny: hr.ny,
            nx: hr.nx,
            lat_min: hr.lat_min,
            lat_max: hr.lat_max,
            lon_min: hr.lon_min,
            lon_max: hr.lon_max,
        }
    }

    /// Add one forecast hour's mapped cell values.
    pub fn add_hour(&mut self, hour: i32, values: Vec<f32>) {
        self.hours.push(hour);
        for (cell_idx, &val) in values.iter().enumerate() {
            if cell_idx < self.cell_values.len() {
                self.cell_values[cell_idx].push(val);
            }
        }
    }

    /// V2 rctile file path for this accumulator.
    pub fn rctile_path(&self, tiles_dir: &Path) -> PathBuf {
        let res_dir = config::format_res_dir(self.resolution_deg);
        tiles_dir
            .join(&self.region_id)
            .join(&res_dir)
            .join(&self.model_id)
            .join(format!("{}.rctile", self.variable_id))
    }

    /// Finalize: merge with existing v2 file, write atomically.
    /// Returns the path to the written rctile file.
    pub fn finalize(
        self,
        tiles_dir: &Path,
        conn: &rusqlite::Connection,
    ) -> Result<PathBuf> {
        let rctile_path = self.rctile_path(tiles_dir);

        // Ensure output directory exists
        if let Some(parent) = rctile_path.parent() {
            std::fs::create_dir_all(parent)
                .context("Failed to create v2 tile output directory")?;
        }

        // Load existing runs from v2 file
        let mut all_runs: Vec<RunData> = if rctile_path.exists() {
            let data = std::fs::read(&rctile_path)
                .context("Failed to read existing v2 rctile")?;
            if data.len() >= 4 && &data[0..4] == b"RCT2" {
                rctile_v2::load_all_runs(&data).unwrap_or_else(|e| {
                    log::warn!("Failed to load existing v2 runs: {}, starting fresh", e);
                    vec![]
                })
            } else {
                // Old v1 file at this path — ignore, start fresh
                log::info!(
                    "Ignoring v1 rctile at {:?}, starting fresh v2",
                    rctile_path
                );
                vec![]
            }
        } else {
            vec![]
        };

        // Remove any existing entry for this run (in case of re-processing)
        all_runs.retain(|r| r.run_id != self.run_id);

        // Add new run
        all_runs.push(RunData {
            run_id: self.run_id.clone(),
            init_unix: self.init_unix,
            hours: self.hours.clone(),
            cell_values: self.cell_values,
        });

        // Sort by init_unix, keep only newest runs
        all_runs.sort_by_key(|r| r.init_unix);
        const MAX_RETAINED_RUNS: usize = 5;
        if all_runs.len() > MAX_RETAINED_RUNS {
            all_runs = all_runs.split_off(all_runs.len() - MAX_RETAINED_RUNS);
        }

        // Write v2 file atomically
        rctile_v2::write_v2(
            &rctile_path,
            &all_runs,
            self.ny,
            self.nx,
            self.lat_min,
            self.lat_max,
            self.lon_min,
            self.lon_max,
            self.resolution_deg as f32,
        )?;

        // Record in DB
        db::record_tile_run(
            conn,
            &self.region_id,
            self.resolution_deg,
            &self.model_id,
            &self.run_id,
            Some(&self.init_time_utc),
        )?;

        let tile_str = rctile_path.to_string_lossy().to_string();
        let size_bytes = std::fs::metadata(&rctile_path).map(|m| m.len()).ok();

        db::record_tile_variable(
            conn,
            &self.region_id,
            self.resolution_deg,
            &self.model_id,
            &self.run_id,
            &self.variable_id,
            &tile_str,
            &tile_str, // no separate meta file for v2
            &self.hours,
            size_bytes,
            0, // no single job_id for finalize
        )?;

        log::info!(
            "Finalized v2: {}/{}/{} ({} runs, {} hours, {:.1} MB)",
            self.model_id,
            self.run_id,
            self.variable_id,
            all_runs.len(),
            self.hours.len(),
            size_bytes.unwrap_or(0) as f64 / 1_048_576.0,
        );

        Ok(rctile_path)
    }
}

/// Process a single build_tile_hour job for the v2 pipeline.
/// Fetches GRIB, decodes, applies gather-based mapping with conversion + snap.
/// Returns hour data to be accumulated (does not write to disk).
pub fn process_hour_v2(
    job: &Job,
    mapping_cache: &mut HashMap<String, BucketMapping>,
) -> Result<HourResult> {
    let args: BuildTileHourArgs = serde_json::from_str(&job.args_json)
        .context("Failed to parse job args_json")?;

    let model = config::get_model(&args.model_id)
        .context(format!("Unknown model: {}", args.model_id))?;

    let region = config::get_region(&args.region_id)
        .context(format!("Unknown region: {}", args.region_id))?;

    let resolution_deg = args
        .resolution_deg
        .unwrap_or_else(|| config::get_tile_resolution(region, &args.model_id));

    let var_config = config::get_variable(&args.variable_id)
        .context(format!("Unknown variable: {}", args.variable_id))?;

    let (date_str, init_hour) = parse_run_id(&args.run_id)?;

    // Fetch GRIB
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

    // Build or reuse BucketMapping (cached per model)
    if !mapping_cache.contains_key(&args.model_id) {
        let mapping = BucketMapping::build(
            &decoded.latitudes,
            &decoded.longitudes,
            region,
            resolution_deg,
        );
        let empty_cells = mapping.cells.iter().filter(|c| c.sources.is_empty()).count();
        log::info!(
            "Built bucket mapping for {}: {}x{} ({} cells, {} empty)",
            args.model_id,
            mapping.ny,
            mapping.nx,
            mapping.cells.len(),
            empty_cells,
        );
        mapping_cache.insert(args.model_id.clone(), mapping);
    }
    let mapping = mapping_cache.get(&args.model_id).unwrap();

    // Apply mapping: gather + convert + snap
    let src_units = if decoded.units != "unknown" {
        Some(decoded.units.as_str())
    } else {
        None
    };
    let conversion = var_config.conversion_for_units(src_units);
    let threshold = bucket_mapping::snap_threshold(&args.variable_id);
    let grib_values = decoded.values.as_slice().unwrap();
    let cell_values = mapping.apply(grib_values, conversion, threshold);

    let init_time_utc = format!(
        "{}-{}-{}T{}:00:00Z",
        &date_str[..4],
        &date_str[4..6],
        &date_str[6..8],
        &init_hour
    );

    let init_unix = tile_query::parse_run_id_to_unix(&args.run_id).unwrap_or(0);

    Ok(HourResult {
        model_id: args.model_id,
        run_id: args.run_id,
        variable_id: args.variable_id,
        region_id: args.region_id,
        forecast_hour: args.forecast_hour as i32,
        cell_values,
        init_time_utc,
        init_unix,
        ny: mapping.ny as u16,
        nx: mapping.nx as u16,
        resolution_deg,
        lat_min: region.lat_min as f32,
        lat_max: region.lat_max as f32,
        lon_min: region.lon_min as f32,
        lon_max: region.lon_max as f32,
    })
}

/// Finalize all pending accumulators (called on run change or shutdown).
pub fn finalize_all(
    accumulators: &mut HashMap<(String, String), RunAccumulator>,
    tiles_dir: &Path,
    conn: &rusqlite::Connection,
) {
    for ((run_id, var_id), acc) in accumulators.drain() {
        log::info!("Finalizing accumulator: {}/{}", run_id, var_id);
        if let Err(e) = acc.finalize(tiles_dir, conn) {
            log::error!("Failed to finalize {}/{}: {:#}", run_id, var_id, e);
        }
    }
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
