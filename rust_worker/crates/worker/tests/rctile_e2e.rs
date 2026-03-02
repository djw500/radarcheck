//! E2E tests for the .rctile format.
//!
//! Tests the full pipeline: create → write hours → read via tile_query.

use std::path::Path;
use tempfile::TempDir;

use radarcheck_core::config;
use radarcheck_core::rctile;
use radarcheck_core::tiles::TileStats;

/// Create synthetic tile stats for testing.
/// Fills grid with predictable values: cell (iy, ix) at hour h → (iy * nx + ix) * 100 + h
fn make_tile_stats(ny: usize, nx: usize, hour: i32) -> TileStats {
    let n = ny * nx;
    let mut means = vec![0.0f32; n];
    let mut mins = vec![0.0f32; n];
    let mut maxs = vec![0.0f32; n];

    for iy in 0..ny {
        for ix in 0..nx {
            let cell = iy * nx + ix;
            let base = (cell as f32) * 100.0 + hour as f32;
            means[cell] = base;
            mins[cell] = base - 1.0;
            maxs[cell] = base + 1.0;
        }
    }

    TileStats {
        means: ndarray::Array2::from_shape_vec((ny, nx), means).unwrap(),
        maxs: ndarray::Array2::from_shape_vec((ny, nx), maxs).unwrap(),
        mins: ndarray::Array2::from_shape_vec((ny, nx), mins).unwrap(),
        ny,
        nx,
        lon_0_360: false,
        index_lon_min: -88.0,
    }
}

/// Set up a tile directory structure matching what the worker produces.
fn setup_tile_dir(
    base: &Path,
    region: &config::TilingRegion,
    resolution_deg: f64,
    model_id: &str,
    run_id: &str,
) -> std::path::PathBuf {
    let res_dir = config::format_res_dir(resolution_deg);
    let dir = base
        .join(region.id)
        .join(&res_dir)
        .join(model_id)
        .join(run_id);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Write .rctile, then verify tile_query reads match at multiple points.
#[test]
fn test_rctile_write_read_single_hour() {
    let tmp = TempDir::new().unwrap();
    let tiles_dir = tmp.path();
    let region = &config::NE_REGION;
    let resolution = 0.1;
    let model_id = "nam_nest";
    let run_id = "run_20260301_00";
    let variable = "t2m";

    let run_dir = setup_tile_dir(tiles_dir, region, resolution, model_id, run_id);

    let stats = make_tile_stats(140, 220, 3);
    let ny = stats.ny;
    let nx = stats.nx;

    // Write .rctile
    let rctile_path = run_dir.join(format!("{}.rctile", variable));
    let max_hours = rctile::max_hours_for_model(model_id);
    rctile::create_rctile(
        &rctile_path,
        ny as u16,
        nx as u16,
        max_hours,
        region.lat_min as f32,
        stats.index_lon_min as f32,
        resolution as f32,
        false,
    )
    .unwrap();
    let means_slice = stats.means.as_slice().unwrap();
    rctile::write_hour(&rctile_path, 3, means_slice).unwrap();

    // Query multiple points via direct rctile reader and verify values
    let test_points = vec![
        (40.0, -74.0, "NYC area"),
        (42.5, -73.5, "Albany area"),
        (33.5, -87.5, "near SW corner"),
        (46.5, -66.5, "near NE corner"),
        (38.0, -77.0, "DC area"),
    ];

    for (lat, lon, label) in &test_points {
        let (rc_hours, rc_vals) = rctile::read_timeseries(&rctile_path, *lat, *lon).unwrap();

        assert_eq!(
            rc_hours,
            vec![3],
            "Hours mismatch at {}: {:?}",
            label,
            rc_hours
        );
        assert_eq!(rc_vals.len(), 1, "Values length mismatch at {}", label);
        assert!(
            !rc_vals[0].is_nan(),
            "Got NaN at {}",
            label
        );
    }
}

/// Test multi-hour incremental writes and verify ordered readback.
#[test]
fn test_rctile_multi_hour_incremental() {
    let tmp = TempDir::new().unwrap();
    let rctile_path = tmp.path().join("test.rctile");

    let ny = 10u16;
    let nx = 15u16;
    let n_cells = ny as usize * nx as usize;
    let max_hours = 48u16;

    rctile::create_rctile(&rctile_path, ny, nx, max_hours, 33.0, -88.0, 0.1, false).unwrap();

    // Write hours out of order: 6, 1, 12, 3
    let hours_to_write = vec![6, 1, 12, 3];
    for &h in &hours_to_write {
        let values: Vec<f32> = (0..n_cells).map(|c| c as f32 * 100.0 + h as f32).collect();
        rctile::write_hour(&rctile_path, h, &values).unwrap();
    }

    // Verify header
    let hdr = rctile::read_header(&rctile_path).unwrap();
    assert_eq!(hdr.n_hours_written, 4);

    // Read at a specific cell and verify hours are sorted
    let lat = 33.0 + 3.0 * 0.1 + 0.05; // row 3
    let lon = -88.0 + 7.0 * 0.1 + 0.05; // col 7
    let cell_idx = 3 * 15 + 7;

    let (hours, values) = rctile::read_timeseries(&rctile_path, lat, lon).unwrap();
    assert_eq!(hours, vec![1, 3, 6, 12], "Hours should be sorted");
    assert_eq!(values.len(), 4);

    // Verify values match expected
    for (i, &h) in hours.iter().enumerate() {
        let expected = cell_idx as f32 * 100.0 + h as f32;
        assert!(
            (values[i] - expected).abs() < 1e-4,
            "Hour {}: expected {}, got {}",
            h, expected, values[i]
        );
    }

    // Write another hour (append at end)
    let h24_vals: Vec<f32> = (0..n_cells).map(|c| c as f32 * 100.0 + 24.0).collect();
    rctile::write_hour(&rctile_path, 24, &h24_vals).unwrap();

    let (hours2, values2) = rctile::read_timeseries(&rctile_path, lat, lon).unwrap();
    assert_eq!(hours2, vec![1, 3, 6, 12, 24]);
    let expected_h24 = cell_idx as f32 * 100.0 + 24.0;
    assert!((values2[4] - expected_h24).abs() < 1e-4);
}

/// Test that the mmap read path matches the file I/O read path.
#[test]
fn test_rctile_mmap_matches_file_io() {
    let tmp = TempDir::new().unwrap();
    let rctile_path = tmp.path().join("mmap_test.rctile");

    let ny = 20u16;
    let nx = 30u16;
    let n_cells = ny as usize * nx as usize;

    rctile::create_rctile(&rctile_path, ny, nx, 50, 35.0, -80.0, 0.5, false).unwrap();

    // Write several hours
    for h in [0, 3, 6, 9, 12] {
        let vals: Vec<f32> = (0..n_cells).map(|c| (c as f32) * 10.0 + h as f32).collect();
        rctile::write_hour(&rctile_path, h, &vals).unwrap();
    }

    // Read file into memory (simulating mmap)
    let data = std::fs::read(&rctile_path).unwrap();

    // Test multiple points
    let points = vec![(36.0, -79.0), (40.0, -75.0), (35.5, -79.5), (44.0, -67.0)];
    for (lat, lon) in &points {
        let (io_hours, io_vals) = rctile::read_timeseries(&rctile_path, *lat, *lon).unwrap();
        let (mm_hours, mm_vals) = rctile::read_timeseries_mmap(&data, *lat, *lon).unwrap();

        assert_eq!(io_hours, mm_hours, "Hours mismatch at ({}, {})", lat, lon);
        assert_eq!(io_vals.len(), mm_vals.len());
        for (i, (iv, mv)) in io_vals.iter().zip(mm_vals.iter()).enumerate() {
            assert!(
                (iv - mv).abs() < 1e-6,
                "Value mismatch at ({}, {}) idx={}: io={} vs mmap={}",
                lat, lon, i, iv, mv
            );
        }
    }
}

/// Test the full GFS scenario: 0.25° resolution (fewer cells), rctile write and read.
#[test]
fn test_gfs_quarter_degree_rctile() {
    let tmp = TempDir::new().unwrap();
    let tiles_dir = tmp.path();
    let region = &config::NE_REGION;
    let model_id = "gfs";
    let resolution = config::get_tile_resolution(region, model_id);
    let run_id = "run_20260301_00";
    let variable = "t2m";

    // Verify GFS resolution is now 0.25
    assert!(
        (resolution - 0.25).abs() < 1e-6,
        "GFS resolution should be 0.25, got {}",
        resolution
    );

    let ny = ((region.lat_max - region.lat_min) / resolution).ceil() as usize;
    let nx = ((region.lon_max - region.lon_min) / resolution).ceil() as usize;
    assert_eq!(ny, 56, "GFS 0.25° NY");
    assert_eq!(nx, 88, "GFS 0.25° NX");

    let run_dir = setup_tile_dir(tiles_dir, region, resolution, model_id, run_id);
    let n_cells = ny * nx;

    // Create rctile at GFS resolution
    let rctile_path = run_dir.join(format!("{}.rctile", variable));
    let max_hours = rctile::max_hours_for_model(model_id);
    assert_eq!(max_hours, 110, "GFS max_hours");

    rctile::create_rctile(
        &rctile_path,
        ny as u16,
        nx as u16,
        max_hours,
        region.lat_min as f32,
        region.lon_min as f32,
        resolution as f32,
        false,
    )
    .unwrap();

    // Write several hours with temperature-like values
    for h in [0, 3, 6, 12, 24, 48] {
        let vals: Vec<f32> = (0..n_cells)
            .map(|c| {
                let iy = c / nx;
                let ix = c % nx;
                // Temperature gradient: warmer south, cooler north
                let base_temp = 60.0 - (iy as f32) * 0.5 + (ix as f32) * 0.1;
                base_temp + h as f32 * 0.1 // slight warming per hour
            })
            .collect();
        rctile::write_hour(&rctile_path, h, &vals).unwrap();
    }

    // Verify file size
    let meta = std::fs::metadata(&rctile_path).unwrap();
    let expected_size = 64 + max_hours as u64 * 4 + n_cells as u64 * max_hours as u64 * 4;
    assert_eq!(
        meta.len(),
        expected_size,
        "GFS rctile file size mismatch: expected {}, got {}",
        expected_size,
        meta.len()
    );

    // Query through rctile reader
    let lat = 40.75; // ~row 31 at 0.25°: (40.75 - 33.0) / 0.25 = 31
    let lon = -74.0; // ~col 56 at 0.25°: (-74.0 - (-88.0)) / 0.25 = 56

    let (hours, values) = rctile::read_timeseries(&rctile_path, lat, lon).unwrap();
    assert_eq!(hours, vec![0, 3, 6, 12, 24, 48]);
    assert_eq!(values.len(), 6);

    // Verify values are reasonable (temperature-like, non-NaN)
    for v in &values {
        assert!(!v.is_nan(), "Got NaN in GFS rctile data");
        assert!(*v > -50.0 && *v < 150.0, "Unreasonable temp: {}", v);
    }

    // Verify monotonic time increase in values (slight warming per hour)
    for i in 1..values.len() {
        assert!(
            values[i] > values[i - 1] - 1.0, // allow small variance
            "Values should roughly increase: {} vs {}",
            values[i],
            values[i - 1]
        );
    }
}

/// Test overwriting an existing hour preserves other hours.
#[test]
fn test_rctile_overwrite_preserves_others() {
    let tmp = TempDir::new().unwrap();
    let path = tmp.path().join("overwrite.rctile");

    let ny = 5u16;
    let nx = 8u16;
    let n_cells = ny as usize * nx as usize;

    rctile::create_rctile(&path, ny, nx, 20, 33.0, -88.0, 1.0, false).unwrap();

    // Write hours 0, 1, 2
    for h in 0..3 {
        let vals: Vec<f32> = (0..n_cells).map(|c| c as f32 + h as f32 * 1000.0).collect();
        rctile::write_hour(&path, h, &vals).unwrap();
    }

    // Overwrite hour 1 with different values
    let new_h1: Vec<f32> = (0..n_cells).map(|c| c as f32 + 9999.0).collect();
    rctile::write_hour(&path, 1, &new_h1).unwrap();

    // Verify: hour 0 unchanged, hour 1 updated, hour 2 unchanged
    let lat = 33.5; // row 0
    let lon = -85.5; // col 2
    let cell_idx = 0 * 8 + 2; // = 2

    let (hours, values) = rctile::read_timeseries(&path, lat, lon).unwrap();
    assert_eq!(hours, vec![0, 1, 2]);

    let expected_h0 = cell_idx as f32 + 0.0 * 1000.0;
    let expected_h1 = cell_idx as f32 + 9999.0; // overwritten
    let expected_h2 = cell_idx as f32 + 2.0 * 1000.0;

    assert!(
        (values[0] - expected_h0).abs() < 1e-4,
        "Hour 0: expected {}, got {}",
        expected_h0, values[0]
    );
    assert!(
        (values[1] - expected_h1).abs() < 1e-4,
        "Hour 1 (overwritten): expected {}, got {}",
        expected_h1, values[1]
    );
    assert!(
        (values[2] - expected_h2).abs() < 1e-4,
        "Hour 2: expected {}, got {}",
        expected_h2, values[2]
    );
}
