//! Job processing pipeline v2: fetch GRIB → decode → gather mapping → accumulate → finalize.
//!
//! Replaces v1 scatter-based tile building with gather-based bucket mapping.
//! Data accumulates in memory during a run, then finalizes to a single-run
//! .rctile v2 file (one file per run, no merge needed).

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

    /// V2 rctile file path for this accumulator (one file per run).
    pub fn rctile_path(&self, tiles_dir: &Path) -> PathBuf {
        let res_dir = config::format_res_dir(self.resolution_deg);
        tiles_dir
            .join(&self.region_id)
            .join(&res_dir)
            .join(&self.model_id)
            .join(&self.variable_id)
            .join(format!("{}.rctile", self.run_id))
    }

    /// Directory containing per-run rctile files for this variable.
    pub fn variable_dir(&self, tiles_dir: &Path) -> PathBuf {
        let res_dir = config::format_res_dir(self.resolution_deg);
        tiles_dir
            .join(&self.region_id)
            .join(&res_dir)
            .join(&self.model_id)
            .join(&self.variable_id)
    }

    /// Finalize: write single-run v2 file, apply retention to old files.
    /// Returns the path to the written rctile file.
    pub fn finalize(
        self,
        tiles_dir: &Path,
        conn: &rusqlite::Connection,
    ) -> Result<PathBuf> {
        let rctile_path = self.rctile_path(tiles_dir);
        let variable_dir = self.variable_dir(tiles_dir);

        // Ensure variable subdirectory exists
        std::fs::create_dir_all(&variable_dir)
            .context("Failed to create v2 tile variable directory")?;

        // Build RunData from accumulator, merging with existing file if present
        // (handles partial finalization when --max-jobs restarts worker mid-run)
        let run_data = if rctile_path.exists() {
            match std::fs::read(&rctile_path) {
                Ok(data) if data.len() >= 4 && &data[0..4] == b"RCT2" => {
                    match rctile_v2::load_all_runs(&data) {
                        Ok(mut existing) if existing.len() == 1 => {
                            let ex = &mut existing[0];
                            // Merge new hours into existing
                            for (hi, &hour) in self.hours.iter().enumerate() {
                                if !ex.hours.contains(&hour) {
                                    ex.hours.push(hour);
                                    for cell_idx in 0..ex.cell_values.len() {
                                        let val = self.cell_values.get(cell_idx)
                                            .and_then(|cv| cv.get(hi).copied())
                                            .unwrap_or(f32::NAN);
                                        ex.cell_values[cell_idx].push(val);
                                    }
                                }
                            }
                            // Sort hours and reorder cell values to match
                            let mut hour_order: Vec<usize> = (0..ex.hours.len()).collect();
                            hour_order.sort_by_key(|&i| ex.hours[i]);
                            let sorted_hours: Vec<i32> = hour_order.iter().map(|&i| ex.hours[i]).collect();
                            for cell in &mut ex.cell_values {
                                let sorted: Vec<f32> = hour_order.iter().map(|&i| cell[i]).collect();
                                *cell = sorted;
                            }
                            ex.hours = sorted_hours;
                            log::info!(
                                "Merged {} new hours into existing {} ({} total hours)",
                                self.hours.len(), self.run_id, ex.hours.len()
                            );
                            existing.remove(0)
                        }
                        _ => RunData {
                            run_id: self.run_id.clone(),
                            init_unix: self.init_unix,
                            hours: self.hours.clone(),
                            cell_values: self.cell_values,
                        },
                    }
                }
                _ => RunData {
                    run_id: self.run_id.clone(),
                    init_unix: self.init_unix,
                    hours: self.hours.clone(),
                    cell_values: self.cell_values,
                },
            }
        } else {
            RunData {
                run_id: self.run_id.clone(),
                init_unix: self.init_unix,
                hours: self.hours.clone(),
                cell_values: self.cell_values,
            }
        };

        let all_hours: Vec<i32> = run_data.hours.clone();

        // Write single-run v2 file atomically
        rctile_v2::write_v2(
            &rctile_path,
            &[run_data],
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
            &all_hours,
            size_bytes,
            0, // no single job_id for finalize
        )?;

        // Apply tiered retention: delete old run files + DB records
        if let Err(e) = apply_retention(
            &variable_dir,
            conn,
            &self.region_id,
            self.resolution_deg,
            &self.model_id,
        ) {
            log::warn!("Retention cleanup failed for {}/{}: {:#}", self.model_id, self.variable_id, e);
        }

        // Clean up legacy multi-run file if it exists at the old path
        let legacy_path = tiles_dir
            .join(&self.region_id)
            .join(&config::format_res_dir(self.resolution_deg))
            .join(&self.model_id)
            .join(format!("{}.rctile", self.variable_id));
        if legacy_path.is_file() {
            log::info!("Removing legacy multi-run rctile: {:?}", legacy_path);
            let _ = std::fs::remove_file(&legacy_path);
        }

        log::info!(
            "Finalized v2: {}/{}/{} ({} hours, {:.1} MB)",
            self.model_id,
            self.run_id,
            self.variable_id,
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

    let var_config = config::get_variable(&args.variable_id)
        .context(format!("Unknown variable: {}", args.variable_id))?;

    // Use variable resolution override if set, then job args, then model default
    let resolution_deg = var_config
        .variable_resolution_override
        .or(args.resolution_deg)
        .unwrap_or_else(|| config::get_tile_resolution(region, &args.model_id));

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

    // Build or reuse BucketMapping (cached per model+resolution)
    let cache_key = format!("{}@{:.3}", args.model_id, resolution_deg);
    if !mapping_cache.contains_key(&cache_key) {
        let mapping = BucketMapping::build(
            &decoded.latitudes,
            &decoded.longitudes,
            region,
            resolution_deg,
        );
        let empty_cells = mapping.cells.iter().filter(|c| c.sources.is_empty()).count();
        log::info!(
            "Built bucket mapping for {} at {:.3}°: {}x{} ({} cells, {} empty)",
            args.model_id,
            resolution_deg,
            mapping.ny,
            mapping.nx,
            mapping.cells.len(),
            empty_cells,
        );
        mapping_cache.insert(cache_key.clone(), mapping);
    }
    let mapping = mapping_cache.get(&cache_key).unwrap();

    // Apply mapping: gather + convert + snap
    let src_units = if decoded.units != "unknown" {
        Some(decoded.units.as_str())
    } else {
        None
    };
    let conversion = var_config.conversion_for_model(model.herbie_model, src_units);
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

/// Apply tiered retention to a variable directory containing per-run rctile files.
/// Keeps MAX_SYNOPTIC_RUNS synoptic runs (init_hour % 6 == 0) and MAX_HOURLY_RUNS hourly runs.
/// Deletes excess files and their DB records.
fn apply_retention(
    variable_dir: &Path,
    conn: &rusqlite::Connection,
    region_id: &str,
    resolution_deg: f64,
    model_id: &str,
) -> Result<()> {
    const MAX_SYNOPTIC_RUNS: usize = 8;
    const MAX_HOURLY_RUNS: usize = 12;

    // List all run_*.rctile files in the directory
    let entries: Vec<_> = std::fs::read_dir(variable_dir)
        .context("Failed to read variable directory")?
        .filter_map(|e| e.ok())
        .filter(|e| {
            let name = e.file_name();
            let name = name.to_string_lossy();
            name.starts_with("run_") && name.ends_with(".rctile")
        })
        .collect();

    // Parse run_id from filename (e.g. "run_20260304_12.rctile" -> "run_20260304_12")
    let mut synoptic: Vec<(String, PathBuf)> = Vec::new();
    let mut hourly: Vec<(String, PathBuf)> = Vec::new();

    for entry in &entries {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let run_id = name_str.trim_end_matches(".rctile");

        let is_synoptic = run_id
            .split('_')
            .nth(2)
            .and_then(|h| h.parse::<u32>().ok())
            .map(|h| h % 6 == 0)
            .unwrap_or(false);

        if is_synoptic {
            synoptic.push((run_id.to_string(), entry.path()));
        } else {
            hourly.push((run_id.to_string(), entry.path()));
        }
    }

    // Sort newest first (lexicographic on run_id works: run_YYYYMMDD_HH)
    synoptic.sort_by(|a, b| b.0.cmp(&a.0));
    hourly.sort_by(|a, b| b.0.cmp(&a.0));

    // Delete excess synoptic runs
    for (run_id, path) in synoptic.iter().skip(MAX_SYNOPTIC_RUNS) {
        log::info!("Retention: removing synoptic {}/{}", model_id, run_id);
        if let Err(e) = std::fs::remove_file(path) {
            log::warn!("Failed to remove {}: {}", path.display(), e);
        }
        let _ = db::delete_tile_run_records(conn, region_id, resolution_deg, model_id, run_id);
    }

    // Delete excess hourly runs
    for (run_id, path) in hourly.iter().skip(MAX_HOURLY_RUNS) {
        log::info!("Retention: removing hourly {}/{}", model_id, run_id);
        if let Err(e) = std::fs::remove_file(path) {
            log::warn!("Failed to remove {}: {}", path.display(), e);
        }
        let _ = db::delete_tile_run_records(conn, region_id, resolution_deg, model_id, run_id);
    }

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

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    /// Create a RunAccumulator with fake cell data (no GRIB needed).
    fn make_accumulator(run_id: &str, init_unix: i64, hours: &[i32]) -> RunAccumulator {
        let ny = 3u16;
        let nx = 4u16;
        let n_cells = ny as usize * nx as usize;
        let mut acc = RunAccumulator {
            model_id: "hrrr".to_string(),
            run_id: run_id.to_string(),
            variable_id: "t2m".to_string(),
            region_id: "ne".to_string(),
            resolution_deg: 0.03,
            init_time_utc: "2026-03-04T12:00:00Z".to_string(),
            init_unix,
            hours: Vec::new(),
            cell_values: vec![Vec::new(); n_cells],
            ny,
            nx,
            lat_min: 38.0,
            lat_max: 46.0,
            lon_min: -82.0,
            lon_max: -66.0,
        };
        for &h in hours {
            let values: Vec<f32> = (0..n_cells).map(|c| 270.0 + c as f32 * 0.1 + h as f32 * 0.01).collect();
            acc.add_hour(h, values);
        }
        acc
    }

    fn open_test_db() -> rusqlite::Connection {
        let conn = rusqlite::Connection::open_in_memory().unwrap();
        // Run the same schema setup as db::open_db
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA busy_timeout=30000;
             CREATE TABLE IF NOT EXISTS tile_runs (
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
             );",
        ).unwrap();
        conn
    }

    #[test]
    fn test_rctile_path_is_per_run() {
        let acc = make_accumulator("run_20260304_12", 0, &[0]);
        let tiles_dir = Path::new("/tmp/tiles");
        let path = acc.rctile_path(tiles_dir);
        assert_eq!(
            path,
            PathBuf::from("/tmp/tiles/ne/0.030deg/hrrr/t2m/run_20260304_12.rctile")
        );
    }

    #[test]
    fn test_finalize_writes_single_run_file() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        let acc = make_accumulator("run_20260304_12", 1709553600, &[0, 1, 2, 3]);
        let path = acc.finalize(tiles_dir, &conn).unwrap();

        // File should exist at per-run path
        assert!(path.exists());
        assert!(path.to_string_lossy().contains("t2m/run_20260304_12.rctile"));

        // Read back and verify it has exactly 1 run
        let data = std::fs::read(&path).unwrap();
        let result = rctile_v2::query_point_v2(&data, 42.0, -74.0).unwrap();
        assert_eq!(result.runs.len(), 1);
        assert_eq!(result.runs[0].run_id, "run_20260304_12");
        assert_eq!(result.runs[0].hours.len(), 4);

        // DB should have records
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tile_runs WHERE run_id='run_20260304_12'",
            [],
            |r| r.get(0),
        ).unwrap();
        assert_eq!(count, 1);

        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tile_variables WHERE run_id='run_20260304_12'",
            [],
            |r| r.get(0),
        ).unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn test_multiple_runs_create_separate_files() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        let acc1 = make_accumulator("run_20260304_06", 1709532000, &[0, 1, 2]);
        let acc2 = make_accumulator("run_20260304_12", 1709553600, &[0, 1, 2, 3]);

        let path1 = acc1.finalize(tiles_dir, &conn).unwrap();
        let path2 = acc2.finalize(tiles_dir, &conn).unwrap();

        assert!(path1.exists());
        assert!(path2.exists());
        assert_ne!(path1, path2);

        // Each file has exactly 1 run
        let data1 = std::fs::read(&path1).unwrap();
        let r1 = rctile_v2::query_point_v2(&data1, 42.0, -74.0).unwrap();
        assert_eq!(r1.runs.len(), 1);
        assert_eq!(r1.runs[0].run_id, "run_20260304_06");

        let data2 = std::fs::read(&path2).unwrap();
        let r2 = rctile_v2::query_point_v2(&data2, 42.0, -74.0).unwrap();
        assert_eq!(r2.runs.len(), 1);
        assert_eq!(r2.runs[0].run_id, "run_20260304_12");
    }

    #[test]
    fn test_retention_deletes_excess_synoptic() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        // Create 10 synoptic runs (00Z each day) — retention keeps 8
        for day in 1..=10 {
            let run_id = format!("run_202603{:02}_00", day);
            let init_unix = 1709251200 + (day as i64 - 1) * 86400;
            let acc = make_accumulator(&run_id, init_unix, &[0, 1, 2]);
            acc.finalize(tiles_dir, &conn).unwrap();
        }

        // Variable dir should have 8 files (retention deleted 2 oldest)
        let var_dir = tiles_dir.join("ne/0.030deg/hrrr/t2m");
        let files: Vec<_> = std::fs::read_dir(&var_dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().ends_with(".rctile"))
            .collect();
        assert_eq!(files.len(), 8, "Should keep 8 synoptic runs, got {}", files.len());

        // Oldest 2 (day 1,2) should be gone
        assert!(!var_dir.join("run_20260301_00.rctile").exists());
        assert!(!var_dir.join("run_20260302_00.rctile").exists());
        // Newest should remain
        assert!(var_dir.join("run_20260310_00.rctile").exists());

        // DB records for deleted runs should also be gone
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tile_runs WHERE run_id='run_20260301_00'",
            [],
            |r| r.get(0),
        ).unwrap();
        assert_eq!(count, 0, "DB record for deleted run should be gone");
    }

    #[test]
    fn test_retention_keeps_synoptic_and_hourly_separately() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        // Create 10 synoptic (00Z) + 14 hourly (01Z) runs
        for day in 1..=10 {
            let run_id = format!("run_202603{:02}_00", day);
            let init_unix = 1709251200 + (day as i64 - 1) * 86400;
            let acc = make_accumulator(&run_id, init_unix, &[0, 1]);
            acc.finalize(tiles_dir, &conn).unwrap();
        }
        for day in 1..=14 {
            let run_id = format!("run_202603{:02}_01", day);
            let init_unix = 1709254800 + (day as i64 - 1) * 86400;
            let acc = make_accumulator(&run_id, init_unix, &[0, 1]);
            acc.finalize(tiles_dir, &conn).unwrap();
        }

        let var_dir = tiles_dir.join("ne/0.030deg/hrrr/t2m");
        let files: Vec<_> = std::fs::read_dir(&var_dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().ends_with(".rctile"))
            .collect();
        // Should keep 8 synoptic + 12 hourly = 20
        assert_eq!(files.len(), 20, "Should keep 8 syn + 12 hourly = 20, got {}", files.len());
    }

    #[test]
    fn test_partial_finalize_merges_hours() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        // First finalize: hours 0-4
        let acc1 = make_accumulator("run_20260304_12", 1709553600, &[0, 1, 2, 3, 4]);
        let path = acc1.finalize(tiles_dir, &conn).unwrap();

        let data = std::fs::read(&path).unwrap();
        let r = rctile_v2::query_point_v2(&data, 42.0, -74.0).unwrap();
        assert_eq!(r.runs[0].hours.len(), 5);

        // Second finalize: hours 3-7 (overlap on 3,4 — should dedup)
        let acc2 = make_accumulator("run_20260304_12", 1709553600, &[3, 4, 5, 6, 7]);
        let path2 = acc2.finalize(tiles_dir, &conn).unwrap();
        assert_eq!(path, path2);

        let data2 = std::fs::read(&path2).unwrap();
        let r2 = rctile_v2::query_point_v2(&data2, 42.0, -74.0).unwrap();
        assert_eq!(r2.runs.len(), 1);
        assert_eq!(r2.runs[0].hours, vec![0, 1, 2, 3, 4, 5, 6, 7]);
        assert_eq!(r2.runs[0].values.len(), 8);

        // Verify original hour 0 value survived (not overwritten)
        // ny=3, nx=4, lat_min=38, lon_min=-82, res=0.03
        // iy=clamp(floor((42-38)/0.03),0,2)=2, ix=clamp(floor((-74-(-82))/0.03),0,3)=3
        // cell_idx = 2*4+3 = 11
        let cell_idx = 11;
        let expected_h0 = 270.0 + cell_idx as f32 * 0.1 + 0.0 * 0.01;
        assert!(
            (r2.runs[0].values[0] - expected_h0).abs() < 1e-3,
            "Hour 0 value should be preserved from first finalize: got {} expected {}",
            r2.runs[0].values[0], expected_h0
        );
    }

    #[test]
    fn test_legacy_file_cleaned_up() {
        let dir = TempDir::new().unwrap();
        let tiles_dir = dir.path();
        let conn = open_test_db();

        // Create a legacy multi-run file at the old path
        let legacy_path = tiles_dir.join("ne/0.030deg/hrrr/t2m.rctile");
        std::fs::create_dir_all(legacy_path.parent().unwrap()).unwrap();
        std::fs::write(&legacy_path, b"RCT2fake").unwrap();
        assert!(legacy_path.exists());

        // Finalize a new run — should delete the legacy file
        let acc = make_accumulator("run_20260304_12", 1709553600, &[0, 1]);
        acc.finalize(tiles_dir, &conn).unwrap();

        assert!(!legacy_path.exists(), "Legacy file should be deleted");
    }
}
