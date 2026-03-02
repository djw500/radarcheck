//! `.rctile` v2 — compressed multi-run cell-major tile format.
//!
//! One file per (region, model, variable) containing all retained runs.
//! Per-cell gzip chunks with offsets-only index for O(1) point queries.
//! Zero-chunk elision: all-zero cells store no data (chunk size = 0).
//!
//! Binary layout:
//! ```text
//! ┌──────────────────────────────────────────┐
//! │ HEADER (128 bytes)                       │
//! ├──────────────────────────────────────────┤
//! │ RUNS TABLE (variable size)               │
//! │   run_id, init_unix, n_hours, hours[]    │
//! ├──────────────────────────────────────────┤
//! │ CELL INDEX ((n_cells + 1) × 8 bytes)     │
//! │   u64 offsets into DATA                  │
//! │   size 0 = all-zero cell (elided)        │
//! ├──────────────────────────────────────────┤
//! │ DATA (variable size)                     │
//! │   per-cell gzip chunks                   │
//! │   each → f32[] for all runs' hours       │
//! └──────────────────────────────────────────┘
//! ```

use std::fs;
use std::io::Write;
use std::path::Path;

use anyhow::{Context, Result, ensure};
use flate2::read::GzDecoder;
use flate2::write::GzEncoder;
use flate2::Compression;

const MAGIC: [u8; 4] = *b"RCT2";
const HEADER_SIZE: usize = 128;

/// Data for a single run, used during write and returned from read.
#[derive(Debug, Clone)]
pub struct RunData {
    pub run_id: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    /// cell_values[cell_idx][hour_slot] — one value per cell per hour
    pub cell_values: Vec<Vec<f32>>,
}

/// Result of a point query — data for all runs at one cell.
#[derive(Debug)]
pub struct PointResult {
    pub runs: Vec<PointRunData>,
}

/// One run's timeseries at a queried point.
#[derive(Debug)]
pub struct PointRunData {
    pub run_id: String,
    pub init_unix: i64,
    pub hours: Vec<i32>,
    pub values: Vec<f32>,
}

/// Write a complete v2 rctile file from multiple runs' data.
///
/// Applies zero-chunk elision: cells where all values across all runs are 0.0
/// store no chunk data (offset[i] == offset[i+1]).
///
/// Writes atomically via temp file + rename.
pub fn write_v2(
    path: &Path,
    runs: &[RunData],
    ny: u16,
    nx: u16,
    lat_min: f32,
    lat_max: f32,
    lon_min: f32,
    lon_max: f32,
    resolution_deg: f32,
) -> Result<()> {
    let n_cells = ny as u32 * nx as u32;
    let n_runs = runs.len() as u16;
    let total_values_per_cell: u16 = runs.iter()
        .map(|r| r.hours.len() as u16)
        .sum();

    // Build runs table bytes
    let runs_table_bytes = serialize_runs_table(runs);
    let runs_table_offset = HEADER_SIZE as u64;
    let index_offset = runs_table_offset + runs_table_bytes.len() as u64;
    let index_size = (n_cells as u64 + 1) * 8;
    let data_offset = index_offset + index_size;

    // Build compressed chunks + offsets
    let mut offsets: Vec<u64> = Vec::with_capacity(n_cells as usize + 1);
    let mut data_buf: Vec<u8> = Vec::new();
    let mut current_offset: u64 = 0;

    for cell_idx in 0..n_cells as usize {
        offsets.push(current_offset);

        // Check if all values for this cell are zero (elision)
        let all_zero = runs.iter().all(|run| {
            cell_idx < run.cell_values.len()
                && run.cell_values[cell_idx].iter().all(|&v| v == 0.0)
        });

        if all_zero {
            // Elided — push same offset again (done at next iteration)
            continue;
        }

        // Collect all values for this cell across runs
        let mut raw_values: Vec<f32> = Vec::with_capacity(total_values_per_cell as usize);
        for run in runs {
            if cell_idx < run.cell_values.len() {
                raw_values.extend_from_slice(&run.cell_values[cell_idx]);
            }
        }

        // Gzip compress
        let compressed = gzip_compress_f32s(&raw_values)?;
        data_buf.extend_from_slice(&compressed);
        current_offset += compressed.len() as u64;
    }
    // Final sentinel offset
    offsets.push(current_offset);

    // Build header
    let mut header = [0u8; HEADER_SIZE];
    header[0..4].copy_from_slice(&MAGIC);
    header[4..6].copy_from_slice(&2u16.to_le_bytes()); // version
    header[6..8].copy_from_slice(&ny.to_le_bytes());
    header[8..10].copy_from_slice(&nx.to_le_bytes());
    header[10..14].copy_from_slice(&n_cells.to_le_bytes());
    header[14..18].copy_from_slice(&lat_min.to_le_bytes());
    header[18..22].copy_from_slice(&lat_max.to_le_bytes());
    header[22..26].copy_from_slice(&lon_min.to_le_bytes());
    header[26..30].copy_from_slice(&lon_max.to_le_bytes());
    header[30..34].copy_from_slice(&resolution_deg.to_le_bytes());
    header[34..36].copy_from_slice(&n_runs.to_le_bytes());
    header[36..38].copy_from_slice(&total_values_per_cell.to_le_bytes());
    header[38..46].copy_from_slice(&runs_table_offset.to_le_bytes());
    header[46..54].copy_from_slice(&index_offset.to_le_bytes());
    header[54..62].copy_from_slice(&data_offset.to_le_bytes());
    // bytes 62..128 are reserved (zero-filled)

    // Write atomically: temp file → rename
    let temp_path = path.with_extension("rctile.tmp");
    {
        let mut f = fs::File::create(&temp_path)
            .context("Failed to create temp rctile v2 file")?;

        f.write_all(&header)?;
        f.write_all(&runs_table_bytes)?;

        // Write index (offsets)
        for &off in &offsets {
            f.write_all(&off.to_le_bytes())?;
        }

        // Write data
        f.write_all(&data_buf)?;
        f.sync_all()?;
    }

    fs::rename(&temp_path, path)
        .context("Failed to rename temp file to final rctile v2")?;

    Ok(())
}

/// Query a single point from an mmap'd v2 rctile file.
/// Returns data for all runs. Elided cells (chunk size 0) return zero-filled values.
pub fn query_point_v2(data: &[u8], lat: f64, lon: f64) -> Result<PointResult> {
    ensure!(data.len() >= HEADER_SIZE, "File too small for v2 header");

    // Parse header
    let magic = &data[0..4];
    ensure!(magic == &MAGIC, "Invalid magic: expected RCT2");
    let version = u16::from_le_bytes([data[4], data[5]]);
    ensure!(version == 2, "Unsupported version: {}", version);

    let ny = u16::from_le_bytes([data[6], data[7]]) as usize;
    let nx = u16::from_le_bytes([data[8], data[9]]) as usize;
    let n_cells = u32::from_le_bytes([data[10], data[11], data[12], data[13]]) as usize;
    let lat_min = f32::from_le_bytes([data[14], data[15], data[16], data[17]]) as f64;
    let lon_min = f32::from_le_bytes([data[22], data[23], data[24], data[25]]) as f64;
    let resolution_deg = f32::from_le_bytes([data[30], data[31], data[32], data[33]]) as f64;
    let n_runs = u16::from_le_bytes([data[34], data[35]]) as usize;
    let total_values_per_cell = u16::from_le_bytes([data[36], data[37]]) as usize;
    let runs_table_offset = u64::from_le_bytes(data[38..46].try_into().unwrap()) as usize;
    let index_offset = u64::from_le_bytes(data[46..54].try_into().unwrap()) as usize;
    let data_offset = u64::from_le_bytes(data[54..62].try_into().unwrap()) as usize;

    // Parse runs table
    let runs_meta = parse_runs_table(&data[runs_table_offset..], n_runs)?;

    if n_runs == 0 || total_values_per_cell == 0 {
        return Ok(PointResult { runs: vec![] });
    }

    // Compute cell index
    let iy = ((lat - lat_min) / resolution_deg).floor() as isize;
    let ix = ((lon - lon_min) / resolution_deg).floor() as isize;
    let iy = iy.max(0).min(ny as isize - 1) as usize;
    let ix = ix.max(0).min(nx as isize - 1) as usize;
    let cell_idx = iy * nx + ix;
    ensure!(cell_idx < n_cells, "Cell index out of bounds");

    // Read chunk bounds from index
    let idx_pos = index_offset + cell_idx * 8;
    ensure!(data.len() >= idx_pos + 16, "File too small for index entry");
    let chunk_start = u64::from_le_bytes(data[idx_pos..idx_pos + 8].try_into().unwrap()) as usize;
    let chunk_end = u64::from_le_bytes(data[idx_pos + 8..idx_pos + 16].try_into().unwrap()) as usize;

    // Check for elided (all-zero) cell
    let values: Vec<f32> = if chunk_start == chunk_end {
        vec![0.0f32; total_values_per_cell]
    } else {
        // Decompress chunk
        let abs_start = data_offset + chunk_start;
        let abs_end = data_offset + chunk_end;
        ensure!(data.len() >= abs_end, "File too small for chunk data");
        gzip_decompress_f32s(&data[abs_start..abs_end], total_values_per_cell)?
    };

    // Split values by run
    let mut result_runs = Vec::with_capacity(n_runs);
    let mut offset = 0;
    for meta in &runs_meta {
        let n = meta.hours.len();
        let run_values = values[offset..offset + n].to_vec();
        offset += n;
        result_runs.push(PointRunData {
            run_id: meta.run_id.clone(),
            init_unix: meta.init_unix,
            hours: meta.hours.clone(),
            values: run_values,
        });
    }

    Ok(PointResult { runs: result_runs })
}

/// Load all runs' cell data from an existing v2 file (for merge during finalize).
pub fn load_all_runs(data: &[u8]) -> Result<Vec<RunData>> {
    ensure!(data.len() >= HEADER_SIZE, "File too small for v2 header");

    let magic = &data[0..4];
    ensure!(magic == &MAGIC, "Invalid magic: expected RCT2");

    let n_cells = u32::from_le_bytes([data[10], data[11], data[12], data[13]]) as usize;
    let n_runs = u16::from_le_bytes([data[34], data[35]]) as usize;
    let total_values_per_cell = u16::from_le_bytes([data[36], data[37]]) as usize;
    let runs_table_offset = u64::from_le_bytes(data[38..46].try_into().unwrap()) as usize;
    let index_offset = u64::from_le_bytes(data[46..54].try_into().unwrap()) as usize;
    let data_offset = u64::from_le_bytes(data[54..62].try_into().unwrap()) as usize;

    let runs_meta = parse_runs_table(&data[runs_table_offset..], n_runs)?;

    if n_runs == 0 {
        return Ok(vec![]);
    }

    // Initialize RunData with empty cell_values
    let mut runs: Vec<RunData> = runs_meta.iter().map(|meta| RunData {
        run_id: meta.run_id.clone(),
        init_unix: meta.init_unix,
        hours: meta.hours.clone(),
        cell_values: vec![vec![0.0; meta.hours.len()]; n_cells],
    }).collect();

    // Decompress each cell's chunk and distribute to runs
    for cell_idx in 0..n_cells {
        let idx_pos = index_offset + cell_idx * 8;
        let chunk_start = u64::from_le_bytes(data[idx_pos..idx_pos + 8].try_into().unwrap()) as usize;
        let chunk_end = u64::from_le_bytes(data[idx_pos + 8..idx_pos + 16].try_into().unwrap()) as usize;

        if chunk_start == chunk_end {
            // Elided cell — values already initialized to 0.0
            continue;
        }

        let abs_start = data_offset + chunk_start;
        let abs_end = data_offset + chunk_end;
        let values = gzip_decompress_f32s(&data[abs_start..abs_end], total_values_per_cell)?;

        // Split values into runs
        let mut offset = 0;
        for run in runs.iter_mut() {
            let n = run.hours.len();
            run.cell_values[cell_idx].copy_from_slice(&values[offset..offset + n]);
            offset += n;
        }
    }

    Ok(runs)
}

// ── Serialization helpers ────────────────────────────────────────────────────

/// Run metadata parsed from the runs table.
#[derive(Debug, Clone)]
struct RunMeta {
    run_id: String,
    init_unix: i64,
    hours: Vec<i32>,
}

fn serialize_runs_table(runs: &[RunData]) -> Vec<u8> {
    let mut buf = Vec::new();
    for run in runs {
        // run_id: 16 bytes, null-padded
        let mut run_id_bytes = [0u8; 16];
        let id_bytes = run.run_id.as_bytes();
        let copy_len = id_bytes.len().min(16);
        run_id_bytes[..copy_len].copy_from_slice(&id_bytes[..copy_len]);
        buf.extend_from_slice(&run_id_bytes);

        // init_unix: i64le
        buf.extend_from_slice(&run.init_unix.to_le_bytes());

        // n_hours: u16le
        buf.extend_from_slice(&(run.hours.len() as u16).to_le_bytes());

        // hours: i32le[]
        for &h in &run.hours {
            buf.extend_from_slice(&h.to_le_bytes());
        }
    }
    buf
}

fn parse_runs_table(data: &[u8], n_runs: usize) -> Result<Vec<RunMeta>> {
    let mut runs = Vec::with_capacity(n_runs);
    let mut pos = 0;
    for _ in 0..n_runs {
        ensure!(pos + 26 <= data.len(), "Runs table truncated");

        // run_id: 16 bytes
        let run_id_bytes = &data[pos..pos + 16];
        let run_id = std::str::from_utf8(run_id_bytes)
            .unwrap_or("")
            .trim_end_matches('\0')
            .to_string();
        pos += 16;

        // init_unix: i64le
        let init_unix = i64::from_le_bytes(data[pos..pos + 8].try_into().unwrap());
        pos += 8;

        // n_hours: u16le
        let n_hours = u16::from_le_bytes(data[pos..pos + 2].try_into().unwrap()) as usize;
        pos += 2;

        // hours: i32le[]
        ensure!(pos + n_hours * 4 <= data.len(), "Hours table truncated");
        let mut hours = Vec::with_capacity(n_hours);
        for _ in 0..n_hours {
            hours.push(i32::from_le_bytes(data[pos..pos + 4].try_into().unwrap()));
            pos += 4;
        }

        runs.push(RunMeta {
            run_id,
            init_unix,
            hours,
        });
    }
    Ok(runs)
}

fn gzip_compress_f32s(values: &[f32]) -> Result<Vec<u8>> {
    let raw_bytes: &[u8] = bytemuck::cast_slice(values);
    let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
    encoder.write_all(raw_bytes)?;
    Ok(encoder.finish()?)
}

fn gzip_decompress_f32s(compressed: &[u8], expected_count: usize) -> Result<Vec<f32>> {
    use std::io::Read;
    let mut decoder = GzDecoder::new(compressed);
    let mut raw_bytes = vec![0u8; expected_count * 4];
    decoder.read_exact(&mut raw_bytes)
        .context("Failed to decompress gzip chunk")?;
    let values: &[f32] = bytemuck::cast_slice(&raw_bytes);
    Ok(values.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn make_run(run_id: &str, init_unix: i64, hours: &[i32], ny: usize, nx: usize, base_val: f32) -> RunData {
        let n_cells = ny * nx;
        let mut cell_values = Vec::with_capacity(n_cells);
        for cell_idx in 0..n_cells {
            let vals: Vec<f32> = hours.iter()
                .map(|&h| base_val + cell_idx as f32 * 0.1 + h as f32 * 0.01)
                .collect();
            cell_values.push(vals);
        }
        RunData {
            run_id: run_id.to_string(),
            init_unix,
            hours: hours.to_vec(),
            cell_values,
        }
    }

    fn make_sparse_run(run_id: &str, init_unix: i64, hours: &[i32], ny: usize, nx: usize, nonzero_frac: f64) -> RunData {
        let n_cells = ny * nx;
        let nonzero_count = (n_cells as f64 * nonzero_frac) as usize;
        let mut cell_values = Vec::with_capacity(n_cells);
        for cell_idx in 0..n_cells {
            if cell_idx < nonzero_count {
                let vals: Vec<f32> = hours.iter()
                    .map(|&h| 0.5 + h as f32 * 0.1)
                    .collect();
                cell_values.push(vals);
            } else {
                cell_values.push(vec![0.0; hours.len()]);
            }
        }
        RunData {
            run_id: run_id.to_string(),
            init_unix,
            hours: hours.to_vec(),
            cell_values,
        }
    }

    #[test]
    fn test_write_read_single_run() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 4u16;
        let nx = 5u16;
        let hours: Vec<i32> = (0..10).collect();
        let run = make_run("run_20260301_00", 1740787200, &hours, ny as usize, nx as usize, 30.0);

        write_v2(&path, &[run.clone()], ny, nx, 33.0, 37.0, -88.0, -83.0, 1.0).unwrap();

        // Read back via mmap simulation
        let data = fs::read(&path).unwrap();
        let result = query_point_v2(&data, 34.5, -86.5).unwrap();

        assert_eq!(result.runs.len(), 1);
        assert_eq!(result.runs[0].run_id, "run_20260301_00");
        assert_eq!(result.runs[0].hours.len(), 10);

        // Cell (1, 1) → cell_idx = 1*5+1 = 6
        // lat=34.5 → iy=floor((34.5-33.0)/1.0)=1, lon=-86.5 → ix=floor((-86.5-(-88.0))/1.0)=1
        let cell_idx = 6;
        for (i, &h) in hours.iter().enumerate() {
            let expected = 30.0 + cell_idx as f32 * 0.1 + h as f32 * 0.01;
            assert!((result.runs[0].values[i] - expected).abs() < 1e-4,
                "Hour {} mismatch: got {} expected {}", h, result.runs[0].values[i], expected);
        }
    }

    #[test]
    fn test_write_read_multi_run() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 3u16;
        let nx = 3u16;
        let run1 = make_run("run_20260301_00", 1740787200, &[0, 1, 2, 3], ny as usize, nx as usize, 10.0);
        let run2 = make_run("run_20260301_06", 1740808800, &[0, 1, 2, 3, 4, 5], ny as usize, nx as usize, 20.0);
        let run3 = make_run("run_20260301_12", 1740830400, &[0, 1, 2], ny as usize, nx as usize, 30.0);

        write_v2(&path, &[run1, run2, run3], ny, nx, 40.0, 43.0, -75.0, -72.0, 1.0).unwrap();

        let data = fs::read(&path).unwrap();
        let result = query_point_v2(&data, 41.5, -73.5).unwrap();

        assert_eq!(result.runs.len(), 3);
        assert_eq!(result.runs[0].hours.len(), 4);
        assert_eq!(result.runs[1].hours.len(), 6);
        assert_eq!(result.runs[2].hours.len(), 3);

        assert_eq!(result.runs[0].run_id, "run_20260301_00");
        assert_eq!(result.runs[1].run_id, "run_20260301_06");
        assert_eq!(result.runs[2].run_id, "run_20260301_12");
    }

    #[test]
    fn test_zero_chunk_elision() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 4u16;
        let nx = 5u16;
        let hours = vec![0, 1, 2];
        // 40% non-zero cells
        let run = make_sparse_run("run_20260301_00", 1740787200, &hours, ny as usize, nx as usize, 0.4);

        write_v2(&path, &[run], ny, nx, 33.0, 37.0, -88.0, -83.0, 1.0).unwrap();

        let data = fs::read(&path).unwrap();

        // Check index for elided cells
        let index_offset = u64::from_le_bytes(data[46..54].try_into().unwrap()) as usize;
        let n_cells = 20;
        let nonzero_count = 8; // 40% of 20

        let mut elided = 0;
        for i in 0..n_cells {
            let pos = index_offset + i * 8;
            let off_start = u64::from_le_bytes(data[pos..pos + 8].try_into().unwrap());
            let off_end = u64::from_le_bytes(data[pos + 8..pos + 16].try_into().unwrap());
            if off_start == off_end {
                elided += 1;
            }
        }
        assert_eq!(elided, n_cells - nonzero_count, "Expected {} elided cells", n_cells - nonzero_count);

        // Query an elided cell — should return zeros
        // Cell (3, 4) → cell_idx = 19 (last cell, should be zero)
        let result = query_point_v2(&data, 36.5, -83.5).unwrap();
        assert_eq!(result.runs.len(), 1);
        assert!(result.runs[0].values.iter().all(|&v| v == 0.0), "Elided cell should return zeros");

        // Query a non-zero cell
        let result = query_point_v2(&data, 33.5, -87.5).unwrap();
        assert!(result.runs[0].values.iter().any(|&v| v > 0.0), "Non-zero cell should have values");
    }

    #[test]
    fn test_merge_runs() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 3u16;
        let nx = 3u16;
        let run1 = make_run("run_20260301_00", 1740787200, &[0, 1, 2], ny as usize, nx as usize, 10.0);
        let run2 = make_run("run_20260301_06", 1740808800, &[0, 1, 2], ny as usize, nx as usize, 20.0);

        // Write initial file with 2 runs
        write_v2(&path, &[run1, run2], ny, nx, 40.0, 43.0, -75.0, -72.0, 1.0).unwrap();

        // Load, add a third run, write again
        let data = fs::read(&path).unwrap();
        let mut existing = load_all_runs(&data).unwrap();
        assert_eq!(existing.len(), 2);

        let run3 = make_run("run_20260301_12", 1740830400, &[0, 1, 2, 3], ny as usize, nx as usize, 30.0);
        existing.push(run3);

        write_v2(&path, &existing, ny, nx, 40.0, 43.0, -75.0, -72.0, 1.0).unwrap();

        // Read back — should have 3 runs
        let data2 = fs::read(&path).unwrap();
        let result = query_point_v2(&data2, 41.5, -73.5).unwrap();
        assert_eq!(result.runs.len(), 3);
        assert_eq!(result.runs[2].hours.len(), 4);

        // Verify values survived the merge
        let cell_idx = 4; // center cell
        let expected_run1_h0 = 10.0 + cell_idx as f32 * 0.1;
        assert!((result.runs[0].values[0] - expected_run1_h0).abs() < 1e-4);
    }

    #[test]
    fn test_merge_with_expiry() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 2u16;
        let nx = 2u16;
        let hours = vec![0, 1, 2];

        // Write 5 runs
        let runs: Vec<RunData> = (0..5).map(|i| {
            make_run(
                &format!("run_2026030{}_00", i + 1),
                1740787200 + i as i64 * 86400,
                &hours,
                ny as usize, nx as usize,
                10.0 * (i + 1) as f32,
            )
        }).collect();

        write_v2(&path, &runs, ny, nx, 40.0, 42.0, -75.0, -73.0, 1.0).unwrap();

        // Load, add run 6, keep only 5 newest (drop oldest)
        let data = fs::read(&path).unwrap();
        let mut existing = load_all_runs(&data).unwrap();
        assert_eq!(existing.len(), 5);

        let run6 = make_run("run_20260306_00", 1740787200 + 5 * 86400, &hours, ny as usize, nx as usize, 60.0);
        existing.push(run6);

        // Keep only 5 newest
        existing.sort_by_key(|r| r.init_unix);
        let retained: Vec<RunData> = existing.into_iter().skip(1).collect();
        assert_eq!(retained.len(), 5);

        write_v2(&path, &retained, ny, nx, 40.0, 42.0, -75.0, -73.0, 1.0).unwrap();

        let data2 = fs::read(&path).unwrap();
        let result = query_point_v2(&data2, 40.5, -74.5).unwrap();
        assert_eq!(result.runs.len(), 5);
        // Oldest should be run 2 (run 1 was dropped)
        assert_eq!(result.runs[0].run_id, "run_20260302_00");
        assert_eq!(result.runs[4].run_id, "run_20260306_00");
    }

    #[test]
    fn test_header_fields() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        let ny = 10u16;
        let nx = 20u16;
        let run = make_run("run_20260301_00", 1740787200, &[0, 3, 6], ny as usize, nx as usize, 50.0);

        write_v2(&path, &[run], ny, nx, 33.0, 43.0, -88.0, -68.0, 1.0).unwrap();

        let data = fs::read(&path).unwrap();
        assert_eq!(&data[0..4], b"RCT2");
        assert_eq!(u16::from_le_bytes([data[4], data[5]]), 2); // version
        assert_eq!(u16::from_le_bytes([data[6], data[7]]), 10); // ny
        assert_eq!(u16::from_le_bytes([data[8], data[9]]), 20); // nx
        assert_eq!(u32::from_le_bytes([data[10], data[11], data[12], data[13]]), 200); // n_cells
        assert_eq!(u16::from_le_bytes([data[34], data[35]]), 1); // n_runs
        assert_eq!(u16::from_le_bytes([data[36], data[37]]), 3); // total_values_per_cell
    }

    #[test]
    fn test_empty_file() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("test.rctile");

        write_v2(&path, &[], 4, 5, 33.0, 37.0, -88.0, -83.0, 1.0).unwrap();

        let data = fs::read(&path).unwrap();
        let result = query_point_v2(&data, 35.0, -85.0).unwrap();
        assert_eq!(result.runs.len(), 0);
    }
}
