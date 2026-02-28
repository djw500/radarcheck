//! GRIB2 decoding using the pure Rust `grib` crate.
//!
//! Takes raw GRIB2 bytes (a single message) and decodes to a 2D f32 array
//! with lat/lon coordinate arrays.

use anyhow::{Context, Result, bail};
use grib::{GridDefinitionTemplateValues, LambertGridDefinition, ScanningMode};
use ndarray::Array2;
use std::io::Cursor;

/// Decoded GRIB message
#[derive(Debug)]
pub struct DecodedGrib {
    /// Data values as 2D array (ny, nx)
    pub values: Array2<f32>,
    /// Latitude coordinates — 1D for regular grids, 2D for projected grids
    pub latitudes: GribCoords,
    /// Longitude coordinates — 1D for regular grids, 2D for projected grids
    pub longitudes: GribCoords,
    /// Number of rows (y dimension)
    pub ny: usize,
    /// Number of columns (x dimension)
    pub nx: usize,
    /// Variable short name from GRIB metadata
    pub short_name: String,
    /// Units string from GRIB metadata
    pub units: String,
}

/// Coordinate arrays — either 1D (regular lat/lon grid) or 2D (projected grid)
#[derive(Debug)]
pub enum GribCoords {
    Regular1D(Vec<f64>),
    Projected2D(Array2<f64>),
}

/// Decode a GRIB2 message from raw bytes.
///
/// The bytes should contain exactly one GRIB message (as obtained from
/// a byte-range download using idx offsets).
pub fn decode_grib_message(grib_bytes: &[u8]) -> Result<DecodedGrib> {
    let reader = Cursor::new(grib_bytes);
    let grib2 = grib::from_reader(reader).context("Failed to parse GRIB2 data")?;

    // Get the first (and usually only) submessage
    let (_idx, submsg) = grib2
        .iter()
        .next()
        .context("No submessages in GRIB data")?;

    // Extract grid definition template (owned) before consuming submsg for decoding
    let tmpl = GridDefinitionTemplateValues::try_from(submsg.grid_def())
        .context("Unsupported grid definition template")?;

    // Get grid shape: (ni, nj) where ni=columns, nj=rows
    let (ni, nj) = tmpl.grid_shape();
    let (ny, nx) = (nj, ni);

    // Decode values — this consumes the submessage
    let decoder = grib::Grib2SubmessageDecoder::from(submsg)
        .context("Failed to create decoder")?;
    let decoded_values: Vec<f32> = decoder
        .dispatch()
        .context("Failed to decode GRIB values")?
        .map(|v| v as f32)
        .collect();

    // Some GRIB files (NAM Nest) report a few extra values beyond ni*nj.
    // Truncate if we have slightly more; bail if we have fewer or way too many.
    let expected = ny * nx;
    let mut decoded_values = decoded_values;
    if decoded_values.len() > expected && decoded_values.len() <= expected + 16 {
        log::debug!(
            "Truncating {} extra decoded values (got {}, grid {}x{}={})",
            decoded_values.len() - expected,
            decoded_values.len(),
            ny,
            nx,
            expected
        );
        decoded_values.truncate(expected);
    } else if decoded_values.len() != expected {
        bail!(
            "Value count mismatch: got {} values but grid is {}x{} = {}",
            decoded_values.len(),
            ny,
            nx,
            expected
        );
    }

    let mut values = Array2::from_shape_vec((ny, nx), decoded_values)
        .context("Failed to reshape values into 2D array")?;

    // Fix serpentine scanning: dispatch() returns raw values in scanning order.
    // If adjacent rows scan in opposite directions (boustrophedon), odd rows
    // are reversed compared to row-major order. Reverse them so values align
    // with our row-major coordinate arrays.
    if let Some(sm) = get_scanning_mode(&tmpl) {
        if sm.scans_alternating_rows() {
            log::debug!("Fixing serpentine scanning (alternating rows)");
            for j in (1..ny).step_by(2) {
                let mut row = values.row_mut(j);
                let len = row.len();
                for i in 0..len / 2 {
                    row.swap(i, len - 1 - i);
                }
            }
        }
    }

    // Get coordinates from the template
    let (latitudes, longitudes) = get_coordinates(&tmpl, ny, nx)?;

    Ok(DecodedGrib {
        values,
        latitudes,
        longitudes,
        ny,
        nx,
        short_name: "unknown".to_string(),
        units: "unknown".to_string(),
    })
}

fn get_scanning_mode(tmpl: &GridDefinitionTemplateValues) -> Option<ScanningMode> {
    match tmpl {
        GridDefinitionTemplateValues::Template0(def) => Some(def.scanning_mode),
        GridDefinitionTemplateValues::Template30(def) => Some(def.scanning_mode),
        _ => None,
    }
}

fn get_coordinates(
    tmpl: &GridDefinitionTemplateValues,
    ny: usize,
    nx: usize,
) -> Result<(GribCoords, GribCoords)> {
    match tmpl {
        GridDefinitionTemplateValues::Template0(def) => {
            // Regular lat/lon: use the crate's built-in iterator (no proj needed)
            let latlons: Vec<(f32, f32)> = def
                .latlons()
                .context("Failed to compute regular lat/lon coordinates")?
                .collect();

            if latlons.len() != ny * nx {
                bail!(
                    "Coordinate count mismatch: got {} but expected {}",
                    latlons.len(),
                    ny * nx
                );
            }

            // Extract 1D arrays
            let lats: Vec<f64> = (0..ny).map(|j| latlons[j * nx].0 as f64).collect();
            let lons: Vec<f64> = (0..nx).map(|i| latlons[i].1 as f64).collect();
            Ok((
                GribCoords::Regular1D(lats),
                GribCoords::Regular1D(lons),
            ))
        }
        GridDefinitionTemplateValues::Template30(def) => {
            // Lambert conformal: compute coordinates ourselves (no proj dependency)
            let latlons = lambert_latlons(def)?;

            if latlons.len() != ny * nx {
                bail!(
                    "Lambert coordinate count mismatch: got {} but expected {}",
                    latlons.len(),
                    ny * nx
                );
            }

            let lat_vec: Vec<f64> = latlons.iter().map(|&(lat, _)| lat).collect();
            let lon_vec: Vec<f64> = latlons.iter().map(|&(_, lon)| lon).collect();

            let lats = Array2::from_shape_vec((ny, nx), lat_vec)?;
            let lons = Array2::from_shape_vec((ny, nx), lon_vec)?;

            Ok((
                GribCoords::Projected2D(lats),
                GribCoords::Projected2D(lons),
            ))
        }
        _ => bail!("Unsupported grid template for coordinate computation"),
    }
}

/// Compute lat/lon coordinates for a Lambert Conformal Conic grid.
///
/// Implements the standard LCC inverse projection:
///   (i, j) → (x, y) in projection space → (lat, lon) in geographic coords
fn lambert_latlons(def: &LambertGridDefinition) -> Result<Vec<(f64, f64)>> {
    let earth_radius = def
        .earth_shape
        .radii()
        .map(|(a, _b)| a) // Use semi-major axis
        .unwrap_or(6371200.0);

    let lat1_deg = def.first_point_lat as f64 * 1e-6;
    let lon1_deg = def.first_point_lon as f64 * 1e-6;
    let lov = def.lov as f64 * 1e-6;
    let latin1_deg = def.latin1 as f64 * 1e-6;
    let latin2_deg = def.latin2 as f64 * 1e-6;

    let mut dx = def.dx as f64 * 1e-3; // mm → m
    let mut dy = def.dy as f64 * 1e-3;

    // Adjust sign based on scanning mode
    if !def.scanning_mode.scans_positively_for_i() && dx > 0.0 {
        dx = -dx;
    }
    if !def.scanning_mode.scans_positively_for_j() && dy > 0.0 {
        dy = -dy;
    }

    // Convert to radians
    let phi1 = latin1_deg.to_radians();
    let phi2 = latin2_deg.to_radians();
    let lambda0 = lov.to_radians();
    let phi_first = lat1_deg.to_radians();
    let lambda_first = lon1_deg.to_radians();

    // Cone constant n
    let n = if (phi1 - phi2).abs() < 1e-10 {
        // Tangent case: single standard parallel
        phi1.sin()
    } else {
        // Secant case: two standard parallels
        let t1 = (std::f64::consts::FRAC_PI_4 + phi1 / 2.0).tan();
        let t2 = (std::f64::consts::FRAC_PI_4 + phi2 / 2.0).tan();
        (phi1.cos().ln() - phi2.cos().ln()) / (t2.ln() - t1.ln())
    };

    // F factor
    let f_factor =
        phi1.cos() * (std::f64::consts::FRAC_PI_4 + phi1 / 2.0).tan().powf(n) / n;

    // rho0 at reference latitude (LaD)
    let lad = def.lad as f64 * 1e-6;
    let phi0 = lad.to_radians();
    let rho0 =
        earth_radius * f_factor / (std::f64::consts::FRAC_PI_4 + phi0 / 2.0).tan().powf(n);

    // Forward project first grid point to get (x0, y0) in Lambert space
    let rho_first = earth_radius * f_factor
        / (std::f64::consts::FRAC_PI_4 + phi_first / 2.0)
            .tan()
            .powf(n);
    let theta_first = n * (lambda_first - lambda0);
    let x0 = rho_first * theta_first.sin();
    let y0 = rho0 - rho_first * theta_first.cos();

    // Compute lat/lon for each grid point in row-major order (j, i).
    // The decoded values from dispatcher come in row-major order regardless
    // of scanning mode — the grib crate handles reordering internally.
    let ni = def.ni as usize;
    let nj = def.nj as usize;
    let mut result = Vec::with_capacity(ni * nj);

    for j in 0..nj {
        for i in 0..ni {
            let x = x0 + (i as f64) * dx;
            let y = y0 + (j as f64) * dy;

            // Inverse Lambert projection
            let dy_from_rho0 = rho0 - y;
            let rho = n.signum() * (x * x + dy_from_rho0 * dy_from_rho0).sqrt();

            let lat = if rho.abs() < 1e-10 {
                n.signum() * std::f64::consts::FRAC_PI_2
            } else {
                2.0 * (earth_radius * f_factor / rho).powf(1.0 / n).atan()
                    - std::f64::consts::FRAC_PI_2
            };

            let theta = x.atan2(dy_from_rho0);
            let lon = lambda0 + theta / n;

            result.push((lat.to_degrees(), lon.to_degrees()));
        }
    }

    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_hrrr_apcp() {
        let grib_bytes = std::fs::read("../tests/fixtures/grib_parity/hrrr_apcp_f1.grib2")
            .expect("Missing fixture file");
        let decoded = decode_grib_message(&grib_bytes).expect("Failed to decode");
        assert_eq!(decoded.ny, 1059);
        assert_eq!(decoded.nx, 1799);
        assert!(matches!(decoded.latitudes, GribCoords::Projected2D(_)));
    }

    #[test]
    #[ignore]
    fn debug_nbm_serpentine() {
        // Try run_20260228_15 f1 first, fall back to run_20260228_04
        let grib_path = if std::path::Path::new("/tmp/nbm_t2m_r15_f1.grib2").exists() {
            "/tmp/nbm_t2m_r15_f1.grib2"
        } else {
            "/tmp/nbm_t2m.grib2"
        };
        let grib_bytes = std::fs::read(grib_path)
            .expect("Missing NBM t2m GRIB file");
        let decoded = decode_grib_message(&grib_bytes).expect("Failed to decode");
        println!("File: {}", grib_path);
        println!("Grid: ny={}, nx={}", decoded.ny, decoded.nx);

        let v = &decoded.values;
        if let GribCoords::Projected2D(ref lats) = decoded.latitudes {
            if let GribCoords::Projected2D(ref lons) = decoded.longitudes {
                // Check grid points that map to tile cell [139,8]
                // lat 46.9-47.0, lon 272.8-272.9
                println!("\nGrid points in tile cell [139,8] (lat 46.9-47.0, lon 272.8-272.9):");
                let mut cell_vals = Vec::new();
                for j in 0..decoded.ny {
                    for i in 0..decoded.nx {
                        let lat = lats[[j,i]];
                        let lon = lons[[j,i]];
                        if lat >= 46.9 && lat < 47.0 && lon >= 272.8 && lon < 272.9 {
                            let val = v[[j,i]];
                            let val_f = val as f64 * 9.0/5.0 - 459.67;
                            println!("  [{},{}]: lat={:.4} lon={:.4} val={:.2}K = {:.2}°F",
                                j, i, lat, lon, val, val_f);
                            cell_vals.push(val_f);
                        }
                    }
                }
                if !cell_vals.is_empty() {
                    let mean: f64 = cell_vals.iter().sum::<f64>() / cell_vals.len() as f64;
                    println!("  Count: {}, Mean: {:.2}°F", cell_vals.len(), mean);
                }

                // Reference point
                println!("\nReference [1095,1515]: lat={:.4} lon={:.4} val={:.2}K",
                    lats[[1095,1515]], lons[[1095,1515]], v[[1095,1515]]);
                println!("[0,0]: lat={:.4} lon={:.4} val={:.2}K = {:.2}°F",
                    lats[[0,0]], lons[[0,0]], v[[0,0]], v[[0,0]] as f64 * 9.0/5.0 - 459.67);
            }
        }
    }
}
