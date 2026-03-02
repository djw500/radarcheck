//! Precomputed bucket→GRIB mapping for gather-based tile building.
//!
//! Instead of scatter (for each GRIB point, find its bucket), we gather
//! (for each bucket center, find nearest GRIB point(s)). This guarantees
//! every bucket has a value — no NaN gaps.

use ndarray::Array2;

use crate::config::{Conversion, TilingRegion};
use crate::grib::GribCoords;

/// One bucket's source: which GRIB flat indices to read and their weights.
/// Most buckets map to exactly 1 GRIB point (weight 1.0).
#[derive(Debug, Clone)]
pub struct CellSource {
    /// (grib_flat_index, weight) pairs
    pub sources: Vec<(usize, f32)>,
}

/// Precomputed mapping from tile buckets → GRIB grid points.
/// Built once per (model, region, resolution), reused across all hours/runs.
#[derive(Debug)]
pub struct BucketMapping {
    /// One entry per tile cell (ny × nx), in row-major order.
    pub cells: Vec<CellSource>,
    pub ny: usize,
    pub nx: usize,
}

/// Per-variable snap thresholds (in display units after conversion).
/// Values below threshold are snapped to 0.0 to improve compression.
pub fn snap_threshold(variable_id: &str) -> f32 {
    match variable_id {
        "apcp" => 0.005,  // 0.005 in = 0.1 mm
        "asnow" => 0.005, // 0.005 in
        "snod" => 0.01,   // 0.01 in = 0.25 mm
        _ => 0.0,          // t2m: no snapping
    }
}

impl BucketMapping {
    /// Build mapping from GRIB coordinate arrays and a target tile grid.
    pub fn build(
        lats: &GribCoords,
        lons: &GribCoords,
        region: &TilingRegion,
        resolution_deg: f64,
    ) -> Self {
        let ny = ((region.lat_max - region.lat_min) / resolution_deg).ceil() as usize;
        let nx = ((region.lon_max - region.lon_min) / resolution_deg).ceil() as usize;

        match (lats, lons) {
            (GribCoords::Regular1D(lat_vec), GribCoords::Regular1D(lon_vec)) => {
                Self::build_regular(lat_vec, lon_vec, region, resolution_deg, ny, nx)
            }
            (GribCoords::Projected2D(lat_arr), GribCoords::Projected2D(lon_arr)) => {
                Self::build_projected(lat_arr, lon_arr, region, resolution_deg, ny, nx)
            }
            _ => {
                // Mixed 1D/2D — shouldn't happen
                log::warn!("Mixed 1D/2D GRIB coords — returning empty mapping");
                Self {
                    cells: vec![CellSource { sources: vec![] }; ny * nx],
                    ny,
                    nx,
                }
            }
        }
    }

    /// Build mapping for regular lat/lon grids (GFS, ECMWF, NBM).
    /// Nearest GRIB point found via binary search on sorted 1D arrays.
    fn build_regular(
        grib_lats: &[f64],
        grib_lons: &[f64],
        region: &TilingRegion,
        resolution_deg: f64,
        ny: usize,
        nx: usize,
    ) -> Self {
        let n_cells = ny * nx;
        let mut cells = Vec::with_capacity(n_cells);

        // Detect if GRIB lons are 0-360
        let grib_lon_0_360 = grib_lons.iter().any(|&l| l > 180.0);

        // Pre-sort lats (may be descending in GRIB)
        let lat_sorted: Vec<f64> = {
            let mut v = grib_lats.to_vec();
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            v
        };
        let lat_ascending = grib_lats.len() >= 2 && grib_lats[0] < grib_lats[1];

        // Sort lons and build reverse index mapping.
        // ECMWF GRIB starts at lon=180° wrapping through 360° to 180°, so
        // the raw lon array is NOT sorted. We need sorted lons for binary search
        // and a mapping back to original GRIB indices.
        let mut lon_order: Vec<usize> = (0..grib_lons.len()).collect();
        lon_order.sort_by(|&a, &b| grib_lons[a].partial_cmp(&grib_lons[b]).unwrap());
        let lon_sorted: Vec<f64> = lon_order.iter().map(|&i| grib_lons[i]).collect();

        for iy in 0..ny {
            let lat_c = region.lat_min + (iy as f64 + 0.5) * resolution_deg;
            // Find nearest lat index
            let lat_idx_sorted = nearest_index_sorted(&lat_sorted, lat_c);
            // Convert back to original GRIB ordering
            let lat_idx = if lat_ascending {
                lat_idx_sorted
            } else {
                grib_lats.len() - 1 - lat_idx_sorted
            };

            for ix in 0..nx {
                let lon_c = region.lon_min + (ix as f64 + 0.5) * resolution_deg;
                let lon_query = if grib_lon_0_360 && lon_c < 0.0 {
                    lon_c + 360.0
                } else {
                    lon_c
                };
                let lon_idx_sorted = nearest_index_sorted(&lon_sorted, lon_query);
                let lon_idx = lon_order[lon_idx_sorted];

                let flat_idx = lat_idx * grib_lons.len() + lon_idx;
                cells.push(CellSource {
                    sources: vec![(flat_idx, 1.0)],
                });
            }
        }

        Self { cells, ny, nx }
    }

    /// Build mapping for projected 2D grids (HRRR, NAM — Lambert Conformal).
    /// Uses a coarse grid hash for fast nearest-neighbor lookup.
    fn build_projected(
        grib_lats: &Array2<f64>,
        grib_lons: &Array2<f64>,
        region: &TilingRegion,
        resolution_deg: f64,
        ny: usize,
        nx: usize,
    ) -> Self {
        let n_cells = ny * nx;
        let mut cells = Vec::with_capacity(n_cells);

        let grib_ny = grib_lats.nrows();
        let grib_nx = grib_lats.ncols();

        // Detect if GRIB lons are 0-360 while region uses -180..180
        let lon_flat = grib_lons.as_slice().unwrap();
        let grib_lon_0_360 = lon_flat.iter().any(|&l| l > 180.0);

        // If GRIB uses 0-360 convention, normalize to -180..180 for the hash
        let normalized_lons;
        let lons_for_hash = if grib_lon_0_360 {
            normalized_lons = grib_lons.mapv(|l| if l > 180.0 { l - 360.0 } else { l });
            &normalized_lons
        } else {
            grib_lons
        };

        // Build coarse spatial hash for GRIB points
        // Hash cell size = ~0.5° (covers ~50km, enough for 3km GRIB grids)
        let hash_res = 0.5;
        let hash = SpatialHash::build(grib_lats, lons_for_hash, grib_ny, grib_nx, hash_res);

        for iy in 0..ny {
            let lat_c = region.lat_min + (iy as f64 + 0.5) * resolution_deg;
            for ix in 0..nx {
                let lon_c = region.lon_min + (ix as f64 + 0.5) * resolution_deg;

                // Search hash for nearest GRIB point (both using -180..180)
                let (best_idx, _best_dist_sq) = hash.nearest(lat_c, lon_c, grib_lats, lons_for_hash, grib_ny, grib_nx);

                cells.push(CellSource {
                    sources: if let Some(idx) = best_idx {
                        vec![(idx, 1.0)]
                    } else {
                        vec![]
                    },
                });
            }
        }

        // NN fill: empty cells at Lambert projection edges get their nearest non-empty neighbor.
        // Walk outward in a spiral until we find a non-empty cell (typically 1-2 steps).
        let empty_count = cells.iter().filter(|c| c.sources.is_empty()).count();
        if empty_count > 0 {
            let empty_indices: Vec<usize> = cells.iter().enumerate()
                .filter(|(_, c)| c.sources.is_empty())
                .map(|(i, _)| i)
                .collect();
            for &idx in &empty_indices {
                let cy = idx / nx;
                let cx = idx % nx;
                let mut found = false;
                for radius in 1..=20 {
                    if found { break; }
                    let r = radius as isize;
                    for dy in -r..=r {
                        for dx in -r..=r {
                            if dy.unsigned_abs() < radius && dx.unsigned_abs() < radius { continue; }
                            let ny2 = cy as isize + dy;
                            let nx2 = cx as isize + dx;
                            if ny2 < 0 || ny2 >= ny as isize || nx2 < 0 || nx2 >= nx as isize { continue; }
                            let neighbor = ny2 as usize * nx + nx2 as usize;
                            if !cells[neighbor].sources.is_empty() {
                                cells[idx] = cells[neighbor].clone();
                                found = true;
                                break;
                            }
                        }
                        if found { break; }
                    }
                }
            }
            log::info!("NN-filled {} empty edge cells in projected grid", empty_count);
        }

        Self { cells, ny, nx }
    }

    /// Apply the mapping to produce tile values from decoded GRIB data.
    ///
    /// For each bucket: look up GRIB point(s) via precomputed mapping,
    /// apply unit conversion and threshold snap.
    pub fn apply(
        &self,
        grib_values: &[f32],
        conversion: Conversion,
        threshold: f32,
    ) -> Vec<f32> {
        let mut out = vec![0.0f32; self.cells.len()];
        for (cell_idx, cell) in self.cells.iter().enumerate() {
            if cell.sources.is_empty() {
                out[cell_idx] = f32::NAN;
                continue;
            }
            let mut sum = 0.0f64;
            let mut w_sum = 0.0f64;
            for &(grib_idx, w) in &cell.sources {
                if grib_idx < grib_values.len() {
                    let v = conversion.apply(grib_values[grib_idx]);
                    if !v.is_nan() {
                        sum += v as f64 * w as f64;
                        w_sum += w as f64;
                    }
                }
            }
            if w_sum > 0.0 {
                let val = (sum / w_sum) as f32;
                out[cell_idx] = if threshold > 0.0 && val.abs() < threshold {
                    0.0
                } else {
                    val
                };
            } else {
                out[cell_idx] = f32::NAN;
            }
        }
        out
    }
}

/// Find the index of the nearest value in a sorted slice.
fn nearest_index_sorted(sorted: &[f64], target: f64) -> usize {
    if sorted.is_empty() {
        return 0;
    }
    match sorted.binary_search_by(|v| v.partial_cmp(&target).unwrap()) {
        Ok(i) => i,
        Err(i) => {
            if i == 0 {
                0
            } else if i >= sorted.len() {
                sorted.len() - 1
            } else {
                // Check which neighbor is closer
                if (sorted[i] - target).abs() < (sorted[i - 1] - target).abs() {
                    i
                } else {
                    i - 1
                }
            }
        }
    }
}

/// Coarse spatial hash for fast 2D nearest-neighbor lookup on projected grids.
struct SpatialHash {
    /// (hash_lat_idx, hash_lon_idx) → vec of GRIB flat indices
    buckets: Vec<Vec<usize>>,
    lat_min: f64,
    lon_min: f64,
    resolution: f64,
    n_lat: usize,
    n_lon: usize,
}

impl SpatialHash {
    fn build(
        lats: &Array2<f64>,
        lons: &Array2<f64>,
        grib_ny: usize,
        grib_nx: usize,
        resolution: f64,
    ) -> Self {
        // Find bounding box of GRIB points
        let lat_flat = lats.as_slice().unwrap();
        let lon_flat = lons.as_slice().unwrap();

        let lat_min = lat_flat.iter().cloned().reduce(f64::min).unwrap_or(0.0);
        let lat_max = lat_flat.iter().cloned().reduce(f64::max).unwrap_or(90.0);
        let lon_min = lon_flat.iter().cloned().reduce(f64::min).unwrap_or(-180.0);
        let lon_max = lon_flat.iter().cloned().reduce(f64::max).unwrap_or(180.0);

        let n_lat = ((lat_max - lat_min) / resolution).ceil() as usize + 1;
        let n_lon = ((lon_max - lon_min) / resolution).ceil() as usize + 1;

        let mut buckets = vec![vec![]; n_lat * n_lon];

        for gy in 0..grib_ny {
            for gx in 0..grib_nx {
                let flat = gy * grib_nx + gx;
                let lat = lat_flat[flat];
                let lon = lon_flat[flat];
                let hi = ((lat - lat_min) / resolution) as usize;
                let hj = ((lon - lon_min) / resolution) as usize;
                let hi = hi.min(n_lat - 1);
                let hj = hj.min(n_lon - 1);
                buckets[hi * n_lon + hj].push(flat);
            }
        }

        Self {
            buckets,
            lat_min,
            lon_min,
            resolution,
            n_lat,
            n_lon,
        }
    }

    fn nearest(
        &self,
        lat: f64,
        lon: f64,
        grib_lats: &Array2<f64>,
        grib_lons: &Array2<f64>,
        _grib_ny: usize,
        _grib_nx: usize,
    ) -> (Option<usize>, f64) {
        let lat_flat = grib_lats.as_slice().unwrap();
        let lon_flat = grib_lons.as_slice().unwrap();

        let hi = ((lat - self.lat_min) / self.resolution) as isize;
        let hj = ((lon - self.lon_min) / self.resolution) as isize;

        let mut best_idx: Option<usize> = None;
        let mut best_dist_sq = f64::MAX;

        // Search 3×3 neighborhood of hash cells
        for di in -1..=1 {
            for dj in -1..=1 {
                let bi = hi + di;
                let bj = hj + dj;
                if bi < 0 || bi >= self.n_lat as isize || bj < 0 || bj >= self.n_lon as isize {
                    continue;
                }
                let bucket_idx = bi as usize * self.n_lon + bj as usize;
                for &flat in &self.buckets[bucket_idx] {
                    let dlat = lat_flat[flat] - lat;
                    let dlon = lon_flat[flat] - lon;
                    let dist_sq = dlat * dlat + dlon * dlon;
                    if dist_sq < best_dist_sq {
                        best_dist_sq = dist_sq;
                        best_idx = Some(flat);
                    }
                }
            }
        }

        (best_idx, best_dist_sq)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Conversion;

    /// Helper: build a regular 1D lat/lon grid
    fn make_regular_grid(
        lat_start: f64, lat_end: f64, lat_step: f64,
        lon_start: f64, lon_end: f64, lon_step: f64,
    ) -> (Vec<f64>, Vec<f64>) {
        let mut lats = vec![];
        let mut lat = lat_start;
        while lat <= lat_end + 1e-9 {
            lats.push(lat);
            lat += lat_step;
        }
        let mut lons = vec![];
        let mut lon = lon_start;
        while lon <= lon_end + 1e-9 {
            lons.push(lon);
            lon += lon_step;
        }
        (lats, lons)
    }

    #[test]
    fn test_regular_grid_1to1() {
        // GFS 0.25° GRIB → 0.25° tiles: 1:1 mapping
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 33.0, lat_max: 35.0,
            lon_min: -80.0, lon_max: -78.0, default_resolution_deg: 0.25,
            stats: &["mean"],
        };
        // 0-360 longitude convention (like GFS)
        let (lats, lons) = make_regular_grid(33.0, 35.0, 0.25, 280.0, 282.0, 0.25);
        let lat_coords = GribCoords::Regular1D(lats);
        let lon_coords = GribCoords::Regular1D(lons);

        let mapping = BucketMapping::build(&lat_coords, &lon_coords, &region, 0.25);

        // 8 × 8 tile grid
        assert_eq!(mapping.ny, 8);
        assert_eq!(mapping.nx, 8);

        // Every cell should have exactly one source
        for cell in &mapping.cells {
            assert_eq!(cell.sources.len(), 1, "Every cell must have a source");
            assert_eq!(cell.sources[0].1, 1.0, "Weight should be 1.0");
        }
    }

    #[test]
    fn test_regular_grid_coarse_to_fine() {
        // GFS 0.25° GRIB → 0.1° tiles: multiple buckets map to same point
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 33.0, lat_max: 34.0,
            lon_min: -80.0, lon_max: -79.0, default_resolution_deg: 0.1,
            stats: &["mean"],
        };
        let (lats, lons) = make_regular_grid(33.0, 34.0, 0.25, 280.0, 281.0, 0.25);
        let lat_coords = GribCoords::Regular1D(lats);
        let lon_coords = GribCoords::Regular1D(lons);

        let mapping = BucketMapping::build(&lat_coords, &lon_coords, &region, 0.1);

        // 10 × 10 tile grid
        assert_eq!(mapping.ny, 10);
        assert_eq!(mapping.nx, 10);

        // Every cell must have a source (no NaN)
        for (i, cell) in mapping.cells.iter().enumerate() {
            assert!(!cell.sources.is_empty(), "Cell {} has no source", i);
        }

        // Some adjacent buckets should share a GRIB point (since 0.25° > 0.1°).
        // Not all adjacent pairs share — depends on which side of the midpoint they fall.
        // But the total number of unique GRIB indices used must be <= number of GRIB points.
        let unique_grib_indices: std::collections::HashSet<usize> = mapping.cells.iter()
            .flat_map(|c| c.sources.iter().map(|s| s.0))
            .collect();
        let n_grib_points = 5 * 5; // 4+1 lat × 4+1 lon at 0.25° over 1°
        assert!(unique_grib_indices.len() <= n_grib_points,
            "Unique GRIB indices ({}) should be <= GRIB points ({})",
            unique_grib_indices.len(), n_grib_points);
    }

    #[test]
    fn test_projected_grid_all_filled() {
        // Synthetic 2D projected grid (like HRRR Lambert)
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 40.0, lat_max: 41.0,
            lon_min: -75.0, lon_max: -74.0, default_resolution_deg: 0.1,
            stats: &["mean"],
        };

        // Create a 2D grid of coords (simulating Lambert projection)
        let grib_ny = 40;
        let grib_nx = 40;
        let mut lat_arr = Array2::zeros((grib_ny, grib_nx));
        let mut lon_arr = Array2::zeros((grib_ny, grib_nx));
        for iy in 0..grib_ny {
            for ix in 0..grib_nx {
                lat_arr[[iy, ix]] = 39.5 + iy as f64 * 0.03;
                lon_arr[[iy, ix]] = -75.5 + ix as f64 * 0.03;
            }
        }

        let lat_coords = GribCoords::Projected2D(lat_arr);
        let lon_coords = GribCoords::Projected2D(lon_arr);

        let mapping = BucketMapping::build(&lat_coords, &lon_coords, &region, 0.1);

        assert_eq!(mapping.ny, 10);
        assert_eq!(mapping.nx, 10);

        // Every cell must have a source
        for (i, cell) in mapping.cells.iter().enumerate() {
            assert!(!cell.sources.is_empty(), "Cell {} has no source", i);
        }
    }

    #[test]
    fn test_snap_threshold() {
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 0.0, lat_max: 1.0,
            lon_min: 0.0, lon_max: 1.0, default_resolution_deg: 0.5,
            stats: &["mean"],
        };
        let lats = vec![0.25, 0.75];
        let lons = vec![0.25, 0.75];
        let lat_coords = GribCoords::Regular1D(lats);
        let lon_coords = GribCoords::Regular1D(lons);

        let mapping = BucketMapping::build(&lat_coords, &lon_coords, &region, 0.5);
        assert_eq!(mapping.cells.len(), 4);

        // GRIB values: 0.0, 0.003, 0.006, 0.1
        let grib_values = vec![0.0f32, 0.003, 0.006, 0.1];
        let result = mapping.apply(&grib_values, Conversion::None, 0.005);

        assert_eq!(result[0], 0.0);   // 0.0 stays 0.0
        assert_eq!(result[1], 0.0);   // 0.003 < 0.005 → snapped to 0.0
        assert!((result[2] - 0.006).abs() < 1e-6); // 0.006 > 0.005 → kept
        assert!((result[3] - 0.1).abs() < 1e-6);   // 0.1 > 0.005 → kept
    }

    #[test]
    fn test_snap_no_effect_on_t2m() {
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 0.0, lat_max: 1.0,
            lon_min: 0.0, lon_max: 1.0, default_resolution_deg: 1.0,
            stats: &["mean"],
        };
        let lats = vec![0.5];
        let lons = vec![0.5];
        let mapping = BucketMapping::build(
            &GribCoords::Regular1D(lats),
            &GribCoords::Regular1D(lons),
            &region, 1.0,
        );

        // Temperature value 32.001°F — t2m snap threshold = 0.0 → no snap
        let grib_values = vec![32.001f32];
        let result = mapping.apply(&grib_values, Conversion::None, 0.0);
        assert!((result[0] - 32.001).abs() < 1e-4);
    }

    #[test]
    fn test_mapping_deterministic() {
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 33.0, lat_max: 34.0,
            lon_min: -80.0, lon_max: -79.0, default_resolution_deg: 0.1,
            stats: &["mean"],
        };
        let (lats, lons) = make_regular_grid(33.0, 34.0, 0.25, 280.0, 281.0, 0.25);

        let m1 = BucketMapping::build(
            &GribCoords::Regular1D(lats.clone()),
            &GribCoords::Regular1D(lons.clone()),
            &region, 0.1,
        );
        let m2 = BucketMapping::build(
            &GribCoords::Regular1D(lats),
            &GribCoords::Regular1D(lons),
            &region, 0.1,
        );

        assert_eq!(m1.cells.len(), m2.cells.len());
        for (a, b) in m1.cells.iter().zip(m2.cells.iter()) {
            assert_eq!(a.sources.len(), b.sources.len());
            for (sa, sb) in a.sources.iter().zip(b.sources.iter()) {
                assert_eq!(sa.0, sb.0);
                assert!((sa.1 - sb.1).abs() < 1e-9);
            }
        }
    }

    #[test]
    fn test_apply_with_conversion() {
        let region = TilingRegion {
            id: "test", name: "Test", lat_min: 0.0, lat_max: 1.0,
            lon_min: 0.0, lon_max: 1.0, default_resolution_deg: 1.0,
            stats: &["mean"],
        };
        let mapping = BucketMapping::build(
            &GribCoords::Regular1D(vec![0.5]),
            &GribCoords::Regular1D(vec![0.5]),
            &region, 1.0,
        );

        // 300 K → should convert to °F
        let grib_values = vec![300.0f32];
        let result = mapping.apply(&grib_values, Conversion::KToF, 0.0);
        // 300K = 26.85°C = 80.33°F
        let expected = (300.0 - 273.15) * 9.0 / 5.0 + 32.0;
        assert!((result[0] - expected).abs() < 0.1);
    }
}
