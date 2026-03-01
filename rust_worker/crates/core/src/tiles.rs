//! Tile generation: regrid decoded GRIB data into statistical tiles.
//!
//! Takes decoded GRIB data and a region definition, computes min/max/mean
//! statistics per tile cell, outputs arrays shaped (ny_tile, nx_tile).

use anyhow::{Result, bail};
use ndarray::Array2;

use crate::config::{Conversion, TilingRegion};
use crate::grib::{DecodedGrib, GribCoords};

/// Output tile statistics for a single forecast hour
#[derive(Debug)]
pub struct TileStats {
    pub mins: Array2<f32>,
    pub maxs: Array2<f32>,
    pub means: Array2<f32>,
    pub ny: usize,
    pub nx: usize,
    /// Whether the source data used 0-360 longitude convention
    pub lon_0_360: bool,
    /// The longitude min used for indexing (adjusted for 0-360 if needed)
    pub index_lon_min: f64,
}

/// Build tile statistics from decoded GRIB data
///
/// This mirrors Python tiles.py: build_tiles_for_variable + _prep_cell_index + _reduce_stats
pub fn build_tile_stats(
    decoded: &DecodedGrib,
    region: &TilingRegion,
    resolution_deg: f64,
    conversion: Conversion,
) -> Result<TileStats> {
    let ny_tile = ((region.lat_max - region.lat_min) / resolution_deg).ceil() as usize;
    let nx_tile = ((region.lon_max - region.lon_min) / resolution_deg).ceil() as usize;

    if ny_tile == 0 || nx_tile == 0 {
        bail!("Invalid tile grid dimensions: {}x{}", ny_tile, nx_tile);
    }

    // Get flat lat/lon arrays
    let (lat_flat, lon_flat, n_points) = flatten_coords(&decoded.latitudes, &decoded.longitudes);

    // Detect 0-360 longitude convention
    let lon_min_val = lon_flat.iter().cloned().reduce(f64::min).unwrap_or(0.0);
    let lon_0_360 = lon_min_val >= 0.0 && region.lon_min < 0.0;

    let lon_min_adj = if lon_0_360 {
        360.0 + region.lon_min
    } else {
        region.lon_min
    };
    let lon_max_adj = if lon_0_360 {
        360.0 + region.lon_max
    } else {
        region.lon_max
    };

    // Accumulate stats per cell
    let n_cells = ny_tile * nx_tile;
    let mut sum = vec![0.0f64; n_cells];
    let mut count = vec![0u32; n_cells];
    let mut mins_vec = vec![f32::INFINITY; n_cells];
    let mut maxs_vec = vec![f32::NEG_INFINITY; n_cells];

    let values = decoded.values.as_slice().unwrap();

    for i in 0..n_points {
        let lat = lat_flat[i];
        let lon = lon_flat[i];

        // Check bounds
        if lat < region.lat_min || lat >= region.lat_max {
            continue;
        }
        if lon < lon_min_adj || lon >= lon_max_adj {
            continue;
        }

        let iy = ((lat - region.lat_min) / resolution_deg).floor() as usize;
        let ix = ((lon - lon_min_adj) / resolution_deg).floor() as usize;

        let iy = iy.min(ny_tile - 1);
        let ix = ix.min(nx_tile - 1);

        let val = conversion.apply(values[i]);

        if val.is_nan() {
            continue;
        }

        let cell = iy * nx_tile + ix;
        sum[cell] += val as f64;
        count[cell] += 1;
        if val < mins_vec[cell] {
            mins_vec[cell] = val;
        }
        if val > maxs_vec[cell] {
            maxs_vec[cell] = val;
        }
    }

    // Build output arrays, NaN for empty cells
    let mut mins_out = vec![f32::NAN; n_cells];
    let mut maxs_out = vec![f32::NAN; n_cells];
    let mut means_out = vec![f32::NAN; n_cells];

    for i in 0..n_cells {
        if count[i] > 0 {
            means_out[i] = (sum[i] / count[i] as f64) as f32;
            mins_out[i] = mins_vec[i];
            maxs_out[i] = maxs_vec[i];
        }
    }

    // Nearest-neighbor fill: replace NaN cells with nearest valid neighbor.
    // This fixes edge gaps in Lambert projected grids (HRRR/NAM right edge)
    // and any grid-alignment gaps in regular grids.
    nn_fill_nan(&mut means_out, &mut mins_out, &mut maxs_out, ny_tile, nx_tile);

    let mins = Array2::from_shape_vec((ny_tile, nx_tile), mins_out)?;
    let maxs = Array2::from_shape_vec((ny_tile, nx_tile), maxs_out)?;
    let means = Array2::from_shape_vec((ny_tile, nx_tile), means_out)?;

    Ok(TileStats {
        mins,
        maxs,
        means,
        ny: ny_tile,
        nx: nx_tile,
        lon_0_360,
        index_lon_min: lon_min_adj,
    })
}

/// Fill NaN cells with nearest non-NaN neighbor value.
/// Uses expanding search radius up to 5 cells. Fills all three stat arrays in lockstep.
fn nn_fill_nan(
    means: &mut [f32],
    mins: &mut [f32],
    maxs: &mut [f32],
    ny: usize,
    nx: usize,
) {
    // Collect indices of NaN cells
    let nan_cells: Vec<usize> = means
        .iter()
        .enumerate()
        .filter(|(_, v)| v.is_nan())
        .map(|(i, _)| i)
        .collect();

    if nan_cells.is_empty() {
        return;
    }

    let max_radius: isize = 5;

    for &cell in &nan_cells {
        let cy = (cell / nx) as isize;
        let cx = (cell % nx) as isize;
        let mut best_dist_sq = i64::MAX;
        let mut best_mean = f32::NAN;
        let mut best_min = f32::NAN;
        let mut best_max = f32::NAN;

        'search: for r in 1..=max_radius {
            // Search the ring at distance r (chebyshev)
            for dy in -r..=r {
                for dx in -r..=r {
                    // Only look at cells on the edge of this ring
                    if dy.abs() != r && dx.abs() != r {
                        continue;
                    }
                    let ny2 = cy + dy;
                    let nx2 = cx + dx;
                    if ny2 < 0 || ny2 >= ny as isize || nx2 < 0 || nx2 >= nx as isize {
                        continue;
                    }
                    let idx = ny2 as usize * nx + nx2 as usize;
                    if !means[idx].is_nan() {
                        let dist_sq = (dy as i64) * (dy as i64) + (dx as i64) * (dx as i64);
                        if dist_sq < best_dist_sq {
                            best_dist_sq = dist_sq;
                            best_mean = means[idx];
                            best_min = mins[idx];
                            best_max = maxs[idx];
                        }
                    }
                }
            }
            if best_dist_sq < i64::MAX {
                break 'search;
            }
        }

        if !best_mean.is_nan() {
            means[cell] = best_mean;
            mins[cell] = best_min;
            maxs[cell] = best_max;
        }
    }
}

/// Flatten coordinate arrays to 1D vecs for iteration
fn flatten_coords(lats: &GribCoords, lons: &GribCoords) -> (Vec<f64>, Vec<f64>, usize) {
    match (lats, lons) {
        (GribCoords::Regular1D(lat_vec), GribCoords::Regular1D(lon_vec)) => {
            // Meshgrid: expand 1D → flat arrays
            let ny = lat_vec.len();
            let nx = lon_vec.len();
            let n = ny * nx;
            let mut lat_flat = Vec::with_capacity(n);
            let mut lon_flat = Vec::with_capacity(n);
            for &lat in lat_vec {
                for &lon in lon_vec {
                    lat_flat.push(lat);
                    lon_flat.push(lon);
                }
            }
            (lat_flat, lon_flat, n)
        }
        (GribCoords::Projected2D(lat_arr), GribCoords::Projected2D(lon_arr)) => {
            let lat_flat = lat_arr.as_slice().unwrap().to_vec();
            let lon_flat = lon_arr.as_slice().unwrap().to_vec();
            let n = lat_flat.len();
            (lat_flat, lon_flat, n)
        }
        _ => {
            // Mixed 1D/2D shouldn't happen
            (vec![], vec![], 0)
        }
    }
}
