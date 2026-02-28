//! E2E tile worker binary for parity testing.
//!
//! Reads a GRIB file, decodes it, builds tiles for NE_REGION, writes NPZ output.
//! Called by tests/e2e_parity.py to compare Rust output against Python reference.

use std::path::Path;

use anyhow::{Context, Result};
use clap::Parser;

use radarcheck_worker::config::{Conversion, NE_REGION};
use radarcheck_worker::grib;
use radarcheck_worker::npz::{self, TileNpz};
use radarcheck_worker::tiles;

#[derive(Parser)]
#[command(name = "e2e-tile-worker")]
struct Args {
    /// Path to input GRIB2 file
    #[arg(long)]
    grib_path: String,

    /// Path for output NPZ file
    #[arg(long)]
    output_path: String,

    /// Unit conversion name (k_to_f, m_to_in, kg_m2_to_in, etc.)
    #[arg(long, default_value = "none")]
    conversion: String,

    /// Tile resolution in degrees
    #[arg(long, default_value = "0.1")]
    resolution: f64,
}

fn parse_conversion(name: &str) -> Conversion {
    match name {
        "k_to_f" => Conversion::KToF,
        "c_to_f" => Conversion::CToF,
        "m_s_to_mph" => Conversion::MSToMph,
        "kg_m2_to_in" => Conversion::KgM2ToIn,
        "m_to_in" => Conversion::MToIn,
        "kg_m2_s_to_in_hr" => Conversion::KgM2SToInHr,
        "m_to_ft" => Conversion::MToFt,
        "pa_to_mb" => Conversion::PaToMb,
        _ => Conversion::None,
    }
}

fn main() -> Result<()> {
    let args = Args::parse();

    // 1. Read GRIB
    let grib_bytes = std::fs::read(&args.grib_path)
        .context(format!("Failed to read GRIB: {}", args.grib_path))?;

    // 2. Decode
    let decoded = grib::decode_grib_message(&grib_bytes)
        .context("Failed to decode GRIB message")?;

    eprintln!(
        "Decoded: {}x{} grid, {} values",
        decoded.ny, decoded.nx,
        decoded.values.len()
    );

    // 3. Build tiles
    let conversion = parse_conversion(&args.conversion);
    let tile_stats = tiles::build_tile_stats(&decoded, &NE_REGION, args.resolution, conversion)
        .context("Failed to build tile stats")?;

    eprintln!(
        "Tiles: {}x{} grid",
        tile_stats.ny, tile_stats.nx
    );

    // 4. Wrap as 3D arrays (1, ny, nx) for NPZ compatibility
    let means_3d = tile_stats.means.into_shape_with_order((1, tile_stats.ny, tile_stats.nx))?;
    let mins_3d = tile_stats.mins.into_shape_with_order((1, tile_stats.ny, tile_stats.nx))?;
    let maxs_3d = tile_stats.maxs.into_shape_with_order((1, tile_stats.ny, tile_stats.nx))?;

    let tile_npz = TileNpz {
        hours: vec![0],
        means: Some(means_3d),
        mins: Some(mins_3d),
        maxs: Some(maxs_3d),
    };

    // 5. Write NPZ
    npz::write_tile_npz(Path::new(&args.output_path), &tile_npz)
        .context("Failed to write NPZ")?;

    eprintln!("Wrote: {}", args.output_path);
    Ok(())
}
