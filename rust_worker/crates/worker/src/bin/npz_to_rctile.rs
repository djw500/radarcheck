//! Convert existing NPZ tiles to .rctile format.
//!
//! Reads all NPZ + meta.json files under a tiles directory and writes
//! corresponding .rctile files alongside them. NPZ files are preserved.
//!
//! Usage:
//!   npz-to-rctile --tiles-dir cache/tiles [--verify]

use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};
use clap::Parser;

use radarcheck_core::npz;
use radarcheck_core::rctile;

#[derive(Parser)]
#[command(name = "npz-to-rctile")]
struct Args {
    /// Path to tiles directory (e.g. cache/tiles)
    #[arg(long)]
    tiles_dir: String,

    /// After conversion, verify rctile values match NPZ at sample points
    #[arg(long, default_value = "false")]
    verify: bool,
}

#[derive(Debug, serde::Deserialize)]
struct TileMeta {
    #[serde(default)]
    model_id: String,
    lat_min: f64,
    #[serde(default)]
    lon_min: f64,
    resolution_deg: f64,
    #[serde(default)]
    lon_0_360: bool,
    #[serde(default)]
    index_lon_min: f64,
}

fn find_npz_files(tiles_dir: &Path) -> Vec<PathBuf> {
    let mut results = Vec::new();
    fn walk(dir: &Path, results: &mut Vec<PathBuf>) {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    walk(&path, results);
                } else if path.extension().map_or(false, |e| e == "npz") {
                    results.push(path);
                }
            }
        }
    }
    walk(tiles_dir, &mut results);
    results.sort();
    results
}

/// Extract model_id from path: .../tiles/ne/0.100deg/<model_id>/run_.../var.npz
fn model_from_path(npz_path: &Path) -> String {
    let components: Vec<&str> = npz_path
        .components()
        .filter_map(|c| c.as_os_str().to_str())
        .collect();
    // Find the component before "run_*"
    for (i, c) in components.iter().enumerate() {
        if c.starts_with("run_") && i > 0 {
            return components[i - 1].to_string();
        }
    }
    "unknown".to_string()
}

fn convert_one(npz_path: &Path, verify: bool) -> Result<()> {
    let rctile_path = npz_path.with_extension("rctile");
    let stem = npz_path.file_stem().unwrap().to_str().unwrap();
    let meta_path = npz_path.parent().unwrap().join(format!("{}.meta.json", stem));

    // Read meta
    let meta_str = std::fs::read_to_string(&meta_path)
        .context(format!("Missing meta.json: {:?}", meta_path))?;
    let meta: TileMeta = serde_json::from_str(&meta_str)
        .context(format!("Invalid meta.json: {:?}", meta_path))?;

    // Read NPZ
    let tile = npz::read_tile_npz(npz_path)
        .context(format!("Failed to read NPZ: {:?}", npz_path))?;

    let means = match &tile.means {
        Some(m) => m,
        None => bail!("NPZ has no means array: {:?}", npz_path),
    };

    let shape = means.shape(); // (n_hours, ny, nx)
    let n_hours = shape[0];
    let ny = shape[1];
    let nx = shape[2];

    if tile.hours.len() != n_hours {
        bail!(
            "Hours mismatch: {} hours in array but {} in hours vec: {:?}",
            n_hours, tile.hours.len(), npz_path
        );
    }

    let model_id = if !meta.model_id.is_empty() {
        meta.model_id.clone()
    } else {
        model_from_path(npz_path)
    };
    let max_hours = rctile::max_hours_for_model(&model_id);

    // Determine lon_min for rctile header
    let lon_min = if meta.index_lon_min != 0.0 {
        meta.index_lon_min as f32
    } else if meta.lon_0_360 {
        (meta.lon_min + 360.0) as f32
    } else {
        meta.lon_min as f32
    };

    // Create rctile
    rctile::create_rctile(
        &rctile_path,
        ny as u16,
        nx as u16,
        max_hours,
        meta.lat_min as f32,
        lon_min,
        meta.resolution_deg as f32,
        meta.lon_0_360,
    )
    .context("Failed to create rctile")?;

    // Write each hour
    for (h_idx, &hour) in tile.hours.iter().enumerate() {
        // Extract the (ny, nx) slice for this hour
        let mut hour_vals = Vec::with_capacity(ny * nx);
        for iy in 0..ny {
            for ix in 0..nx {
                hour_vals.push(means[[h_idx, iy, ix]]);
            }
        }
        rctile::write_hour(&rctile_path, hour, &hour_vals)
            .context(format!("Failed to write hour {} to rctile", hour))?;
    }

    // Verify if requested
    if verify && !tile.hours.is_empty() {
        let test_points = [
            (meta.lat_min + 7.0, meta.lon_min + 14.0),  // center-ish
            (meta.lat_min + 0.5, meta.lon_min + 0.5),    // near SW
            (meta.lat_min + 13.0, meta.lon_min + 21.0),  // near NE
        ];

        for (lat, lon) in &test_points {
            let (rc_hours, rc_vals) =
                rctile::read_timeseries(&rctile_path, *lat, *lon)?;

            if rc_hours != tile.hours {
                bail!(
                    "VERIFY FAIL hours: npz={:?} rctile={:?} at ({}, {})",
                    tile.hours, rc_hours, lat, lon
                );
            }

            // Compute expected from NPZ
            let res = meta.resolution_deg;
            let iy = ((*lat - meta.lat_min) / res).floor() as usize;
            let target_lon = if meta.lon_0_360 && *lon < 0.0 {
                *lon + 360.0
            } else {
                *lon
            };
            let ix = ((target_lon - if meta.index_lon_min != 0.0 {
                meta.index_lon_min
            } else if meta.lon_0_360 {
                meta.lon_min + 360.0
            } else {
                meta.lon_min
            }) / res)
                .floor() as usize;

            if iy >= ny || ix >= nx {
                continue; // point out of grid
            }

            for (i, &h_idx) in tile.hours.iter().enumerate() {
                let npz_val = means[[i, iy, ix]];
                let rc_val = rc_vals[i];

                if npz_val.is_nan() && rc_val.is_nan() {
                    continue;
                }
                if (npz_val - rc_val).abs() > 1e-4 {
                    bail!(
                        "VERIFY FAIL value at ({},{}) hour={}: npz={} rctile={} file={:?}",
                        lat, lon, h_idx, npz_val, rc_val, npz_path
                    );
                }
            }
        }
    }

    Ok(())
}

fn main() -> Result<()> {
    let args = Args::parse();
    let tiles_dir = Path::new(&args.tiles_dir);

    if !tiles_dir.exists() {
        bail!("Tiles directory not found: {:?}", tiles_dir);
    }

    let npz_files = find_npz_files(tiles_dir);
    eprintln!("Found {} NPZ files to convert", npz_files.len());

    let mut converted = 0;
    let mut skipped = 0;
    let mut failed = 0;

    for npz_path in &npz_files {
        let rctile_path = npz_path.with_extension("rctile");
        if rctile_path.exists() {
            skipped += 1;
            continue;
        }

        let rel = npz_path.strip_prefix(tiles_dir).unwrap_or(npz_path);
        match convert_one(npz_path, args.verify) {
            Ok(()) => {
                converted += 1;
                eprintln!("  OK  {}", rel.display());
            }
            Err(e) => {
                failed += 1;
                eprintln!("  FAIL  {}: {}", rel.display(), e);
            }
        }
    }

    eprintln!(
        "\nDone: {} converted, {} skipped (already exist), {} failed",
        converted, skipped, failed
    );

    if failed > 0 {
        bail!("{} conversions failed", failed);
    }

    Ok(())
}
