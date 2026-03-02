//! Benchmark: v1 rctile vs v2 rctile — file size and query performance.
//!
//! Creates realistic-scale tile data matching each model's grid dimensions,
//! writes in both formats, and compares:
//! 1. File size (bytes on disk)
//! 2. Single point query latency (microseconds)
//! 3. Multi-model "model=all" query simulation

use std::time::Instant;
use tempfile::TempDir;

use radarcheck_core::rctile;
use radarcheck_core::rctile_v2;

/// Model config for benchmark.
struct BenchModel {
    id: &'static str,
    ny: u16,
    nx: u16,
    n_hours: usize,
    lat_min: f32,
    lon_min: f32,
    resolution: f32,
    /// Fraction of cells that are all-zero (precip sparsity)
    zero_frac: f64,
}

const MODELS: &[BenchModel] = &[
    BenchModel {
        id: "gfs",
        ny: 56,
        nx: 88,
        n_hours: 104,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.25,
        zero_frac: 0.0,
    },
    BenchModel {
        id: "nam_nest",
        ny: 140,
        nx: 220,
        n_hours: 60,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.1,
        zero_frac: 0.0,
    },
    BenchModel {
        id: "nbm",
        ny: 140,
        nx: 220,
        n_hours: 74,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.1,
        zero_frac: 0.0,
    },
    BenchModel {
        id: "hrrr",
        ny: 467,
        nx: 734,
        n_hours: 48,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.03,
        zero_frac: 0.0,
    },
    BenchModel {
        id: "ecmwf_hres",
        ny: 140,
        nx: 220,
        n_hours: 72,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.1,
        zero_frac: 0.0,
    },
];

/// Sparse precip version — 70% zeros.
const PRECIP_MODELS: &[BenchModel] = &[
    BenchModel {
        id: "gfs_apcp",
        ny: 56,
        nx: 88,
        n_hours: 104,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.25,
        zero_frac: 0.7,
    },
    BenchModel {
        id: "hrrr_apcp",
        ny: 467,
        nx: 734,
        n_hours: 48,
        lat_min: 33.0,
        lon_min: -88.0,
        resolution: 0.03,
        zero_frac: 0.7,
    },
];

fn make_cell_values(model: &BenchModel) -> Vec<Vec<f32>> {
    let n_cells = model.ny as usize * model.nx as usize;
    let nonzero_count = ((1.0 - model.zero_frac) * n_cells as f64) as usize;
    let mut cell_values = Vec::with_capacity(n_cells);

    for cell_idx in 0..n_cells {
        let vals: Vec<f32> = if cell_idx < nonzero_count {
            (0..model.n_hours)
                .map(|h| 30.0 + cell_idx as f32 * 0.001 + h as f32 * 0.1)
                .collect()
        } else {
            vec![0.0; model.n_hours]
        };
        cell_values.push(vals);
    }
    cell_values
}

#[test]
fn benchmark_v1_vs_v2() {
    let dir = TempDir::new().unwrap();
    let lat = 40.75;
    let lon = -74.0;
    let n_queries = 100;

    let sep = "=".repeat(80);
    let dash = "-".repeat(80);
    println!("\n{}", sep);
    println!("  rctile v1 vs v2 Benchmark (single run, t2m-like dense data)");
    println!("{}", sep);
    println!(
        "{:<14} {:>8} {:>8} {:>10} {:>10} {:>8}",
        "Model", "v1 size", "v2 size", "v1 us/q", "v2 us/q", "speedup"
    );
    println!("{}", dash);

    for model in MODELS {
        let hours: Vec<i32> = (0..model.n_hours as i32).collect();
        let cell_values = make_cell_values(model);

        // ── v1: create + write all hours ──
        let v1_path = dir.path().join(format!("{}_v1.rctile", model.id));
        let max_hours = rctile::max_hours_for_model(model.id);
        rctile::create_rctile(
            &v1_path,
            model.ny,
            model.nx,
            max_hours,
            model.lat_min,
            model.lon_min,
            model.resolution,
            false,
        )
        .unwrap();

        for (h_idx, &h) in hours.iter().enumerate() {
            let hour_vals: Vec<f32> = cell_values.iter().map(|cv| cv[h_idx]).collect();
            rctile::write_hour(&v1_path, h, &hour_vals).unwrap();
        }

        let v1_size = std::fs::metadata(&v1_path).unwrap().len();

        // ── v2: write single run ──
        let v2_path = dir.path().join(format!("{}_v2.rctile", model.id));
        let run = rctile_v2::RunData {
            run_id: "run_20260301_00".to_string(),
            init_unix: 1740787200,
            hours: hours.clone(),
            cell_values: cell_values.clone(),
        };

        rctile_v2::write_v2(
            &v2_path,
            &[run],
            model.ny,
            model.nx,
            model.lat_min,
            model.lat_min + model.ny as f32 * model.resolution,
            model.lon_min,
            model.lon_min + model.nx as f32 * model.resolution,
            model.resolution,
        )
        .unwrap();

        let v2_size = std::fs::metadata(&v2_path).unwrap().len();

        // ── Benchmark v1 reads ──
        let v1_data = std::fs::read(&v1_path).unwrap();
        let t0 = Instant::now();
        for _ in 0..n_queries {
            let _ = rctile::read_timeseries_mmap(&v1_data, lat, lon).unwrap();
        }
        let v1_elapsed = t0.elapsed();
        let v1_us = v1_elapsed.as_micros() as f64 / n_queries as f64;

        // ── Benchmark v2 reads ──
        let v2_data = std::fs::read(&v2_path).unwrap();
        let t0 = Instant::now();
        for _ in 0..n_queries {
            let _ = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();
        }
        let v2_elapsed = t0.elapsed();
        let v2_us = v2_elapsed.as_micros() as f64 / n_queries as f64;

        let speedup = v1_us / v2_us;

        println!(
            "{:<14} {:>7.1}M {:>7.1}M {:>9.0} {:>9.0} {:>7.1}x",
            model.id,
            v1_size as f64 / 1_048_576.0,
            v2_size as f64 / 1_048_576.0,
            v1_us,
            v2_us,
            speedup,
        );

        // Verify values match
        let (v1_hours, v1_vals) = rctile::read_timeseries_mmap(&v1_data, lat, lon).unwrap();
        let v2_result = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();
        assert_eq!(v1_hours.len(), v2_result.runs[0].hours.len());
        for (i, (v1, v2)) in v1_vals
            .iter()
            .zip(v2_result.runs[0].values.iter())
            .enumerate()
        {
            assert!(
                (v1 - v2).abs() < 1e-3,
                "{} hour {} mismatch: v1={} v2={}",
                model.id,
                i,
                v1,
                v2
            );
        }
    }

    // ── Sparse precip comparison ──
    println!("\n{}", sep);
    println!("  Sparse precip benchmark (70% zero cells)");
    println!("{}", sep);
    println!(
        "{:<14} {:>8} {:>8} {:>10} {:>10} {:>8}",
        "Model", "v1 size", "v2 size", "v1 us/q", "v2 us/q", "savings"
    );
    println!("{}", dash);

    for model in PRECIP_MODELS {
        let hours: Vec<i32> = (0..model.n_hours as i32).collect();
        let cell_values = make_cell_values(model);

        // v1
        let v1_path = dir.path().join(format!("{}_v1.rctile", model.id));
        let base_model = model.id.split('_').next().unwrap();
        let max_hours = rctile::max_hours_for_model(base_model);
        rctile::create_rctile(
            &v1_path,
            model.ny,
            model.nx,
            max_hours,
            model.lat_min,
            model.lon_min,
            model.resolution,
            false,
        )
        .unwrap();
        for (h_idx, &h) in hours.iter().enumerate() {
            let hour_vals: Vec<f32> = cell_values.iter().map(|cv| cv[h_idx]).collect();
            rctile::write_hour(&v1_path, h, &hour_vals).unwrap();
        }
        let v1_size = std::fs::metadata(&v1_path).unwrap().len();

        // v2
        let v2_path = dir.path().join(format!("{}_v2.rctile", model.id));
        let run = rctile_v2::RunData {
            run_id: "run_20260301_00".to_string(),
            init_unix: 1740787200,
            hours: hours.clone(),
            cell_values,
        };
        rctile_v2::write_v2(
            &v2_path,
            &[run],
            model.ny,
            model.nx,
            model.lat_min,
            model.lat_min + model.ny as f32 * model.resolution,
            model.lon_min,
            model.lon_min + model.nx as f32 * model.resolution,
            model.resolution,
        )
        .unwrap();
        let v2_size = std::fs::metadata(&v2_path).unwrap().len();

        let v1_data = std::fs::read(&v1_path).unwrap();
        let v2_data = std::fs::read(&v2_path).unwrap();

        let t0 = Instant::now();
        for _ in 0..n_queries {
            let _ = rctile::read_timeseries_mmap(&v1_data, lat, lon).unwrap();
        }
        let v1_us = t0.elapsed().as_micros() as f64 / n_queries as f64;

        let t0 = Instant::now();
        for _ in 0..n_queries {
            let _ = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();
        }
        let v2_us = t0.elapsed().as_micros() as f64 / n_queries as f64;

        let savings_pct = (1.0 - v2_size as f64 / v1_size as f64) * 100.0;
        println!(
            "{:<14} {:>7.1}M {:>7.1}M {:>9.0} {:>9.0} {:>6.0}%",
            model.id,
            v1_size as f64 / 1_048_576.0,
            v2_size as f64 / 1_048_576.0,
            v1_us,
            v2_us,
            savings_pct,
        );
    }

    // ── Multi-run v2 benchmark (5 runs, simulating model=all query) ──
    println!("\n{}", sep);
    println!("  Multi-run v2 (5 runs per file, simulating 'model=all' query)");
    println!("{}", sep);
    println!(
        "{:<14} {:>10} {:>12} {:>10}",
        "Model", "v2 5-run", "us/query", "runs/file"
    );
    println!("{}", dash);

    for model in MODELS {
        let hours: Vec<i32> = (0..model.n_hours as i32).collect();

        let runs: Vec<rctile_v2::RunData> = (0..5)
            .map(|i| {
                let cell_values = make_cell_values(model);
                rctile_v2::RunData {
                    run_id: format!("run_2026030{}_00", i + 1),
                    init_unix: 1740787200 + i as i64 * 21600,
                    hours: hours.clone(),
                    cell_values,
                }
            })
            .collect();

        let v2_path = dir.path().join(format!("{}_5run.rctile", model.id));
        rctile_v2::write_v2(
            &v2_path,
            &runs,
            model.ny,
            model.nx,
            model.lat_min,
            model.lat_min + model.ny as f32 * model.resolution,
            model.lon_min,
            model.lon_min + model.nx as f32 * model.resolution,
            model.resolution,
        )
        .unwrap();
        let v2_size = std::fs::metadata(&v2_path).unwrap().len();

        let v2_data = std::fs::read(&v2_path).unwrap();
        let t0 = Instant::now();
        for _ in 0..n_queries {
            let _ = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();
        }
        let v2_us = t0.elapsed().as_micros() as f64 / n_queries as f64;

        let result = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();

        println!(
            "{:<14} {:>9.1}M {:>11.0} {:>10}",
            model.id,
            v2_size as f64 / 1_048_576.0,
            v2_us,
            result.runs.len(),
        );
    }

    // ── model=all simulation: v1 (5 opens) vs v2 (1 open) ──
    println!("\n{}", sep);
    println!("  model=all query simulation (5 models x 1 var)");
    println!("{}", sep);

    // v1: 5 models × 5 runs × 1 var = 25 file reads
    let mut v1_total_us = 0.0;
    let mut v1_total_bytes = 0u64;
    // v2: 5 models × 1 file (5 runs inside) × 1 var = 5 file reads
    let mut v2_total_us = 0.0;
    let mut v2_total_bytes = 0u64;

    for model in MODELS {
        let hours: Vec<i32> = (0..model.n_hours as i32).collect();
        let cell_values = make_cell_values(model);

        // v1: 5 separate files (one per run)
        for run_idx in 0..5 {
            let v1_path = dir
                .path()
                .join(format!("{}_run{}_v1.rctile", model.id, run_idx));
            let max_hours = rctile::max_hours_for_model(model.id);
            rctile::create_rctile(
                &v1_path,
                model.ny,
                model.nx,
                max_hours,
                model.lat_min,
                model.lon_min,
                model.resolution,
                false,
            )
            .unwrap();
            for (h_idx, &h) in hours.iter().enumerate() {
                let hour_vals: Vec<f32> = cell_values.iter().map(|cv| cv[h_idx]).collect();
                rctile::write_hour(&v1_path, h, &hour_vals).unwrap();
            }
            let v1_data = std::fs::read(&v1_path).unwrap();
            v1_total_bytes += v1_data.len() as u64;

            let t0 = Instant::now();
            let _ = rctile::read_timeseries_mmap(&v1_data, lat, lon).unwrap();
            v1_total_us += t0.elapsed().as_micros() as f64;
        }

        // v2: 1 file with 5 runs
        let runs: Vec<rctile_v2::RunData> = (0..5)
            .map(|i| rctile_v2::RunData {
                run_id: format!("run_2026030{}_00", i + 1),
                init_unix: 1740787200 + i as i64 * 21600,
                hours: hours.clone(),
                cell_values: cell_values.clone(),
            })
            .collect();

        let v2_path = dir.path().join(format!("{}_all_v2.rctile", model.id));
        rctile_v2::write_v2(
            &v2_path,
            &runs,
            model.ny,
            model.nx,
            model.lat_min,
            model.lat_min + model.ny as f32 * model.resolution,
            model.lon_min,
            model.lon_min + model.nx as f32 * model.resolution,
            model.resolution,
        )
        .unwrap();
        let v2_data = std::fs::read(&v2_path).unwrap();
        v2_total_bytes += v2_data.len() as u64;

        let t0 = Instant::now();
        let _ = rctile_v2::query_point_v2(&v2_data, lat, lon).unwrap();
        v2_total_us += t0.elapsed().as_micros() as f64;
    }

    println!(
        "  v1: {:>3} file reads, {:>7.1} MB total, {:>7.0} µs total",
        25,
        v1_total_bytes as f64 / 1_048_576.0,
        v1_total_us,
    );
    println!(
        "  v2: {:>3} file reads, {:>7.1} MB total, {:>7.0} µs total",
        5,
        v2_total_bytes as f64 / 1_048_576.0,
        v2_total_us,
    );
    println!(
        "  File reads: {} → {} ({}x fewer)",
        25,
        5,
        25 / 5,
    );
    println!(
        "  Speed: {:.0} µs → {:.0} µs ({:.1}x faster)",
        v1_total_us,
        v2_total_us,
        v1_total_us / v2_total_us,
    );
    println!();
}
