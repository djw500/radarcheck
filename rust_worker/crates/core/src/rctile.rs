//! `.rctile` — mmap-friendly cell-major tile format.
//!
//! Binary layout:
//! ```text
//! ┌─────────────────────────────────────────────────────┐
//! │ HEADER (64 bytes, fixed)                            │
//! ├─────────────────────────────────────────────────────┤
//! │ HOURS TABLE (max_hours × 4 bytes)                   │
//! │   i32 per slot, -1 = unwritten                      │
//! ├─────────────────────────────────────────────────────┤
//! │ DATA (n_cells × max_hours × 4 bytes)                │
//! │   cell-major: [cell0_h0, cell0_h1, ...]             │
//! │                [cell1_h0, cell1_h1, ...]             │
//! │   NaN for unwritten hour slots                      │
//! └─────────────────────────────────────────────────────┘
//! ```
//!
//! Point queries read ~300 bytes via mmap instead of 1-19 MB NPZ decompression.

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use anyhow::{Context, Result, ensure};
use bytemuck::{Pod, Zeroable};

/// Magic bytes for .rctile files
const MAGIC: [u8; 4] = *b"RCT1";
const HEADER_SIZE: u32 = 64;
const UNWRITTEN_HOUR: i32 = -1;

/// Fixed 64-byte header for .rctile files.
#[repr(C)]
#[derive(Debug, Clone, Copy, Pod, Zeroable)]
pub struct RcTileHeader {
    pub magic: [u8; 4],     // b"RCT1"
    pub version: u16,       // 1
    pub ny: u16,
    pub nx: u16,
    pub max_hours: u16,     // pre-allocated hour slots
    pub n_hours_written: u16,
    pub _reserved1: u16,
    pub n_cells: u32,       // ny × nx
    pub lat_min: f32,
    pub lon_min_index: f32, // adjusted for 0-360
    pub resolution_deg: f32,
    pub lon_0_360: u8,      // 0 or 1
    pub _padding: [u8; 3],
    pub hours_offset: u32,  // always 64
    pub data_offset: u32,   // 64 + max_hours * 4
    pub _reserved2: [u8; 20],
}

impl RcTileHeader {
    pub fn from_bytes(data: &[u8]) -> Result<Self> {
        ensure!(data.len() >= HEADER_SIZE as usize, "Header too short");
        let hdr: Self = *bytemuck::from_bytes(&data[..HEADER_SIZE as usize]);
        ensure!(&hdr.magic == &MAGIC, "Invalid magic: expected RCT1");
        ensure!(hdr.version == 1, "Unsupported version: {}", hdr.version);
        Ok(hdr)
    }

    /// Byte offset to the start of a cell's timeseries data.
    #[inline]
    pub fn cell_data_offset(&self, cell_idx: u32) -> u64 {
        self.data_offset as u64 + cell_idx as u64 * self.max_hours as u64 * 4
    }

    /// Total file size.
    pub fn file_size(&self) -> u64 {
        self.data_offset as u64 + self.n_cells as u64 * self.max_hours as u64 * 4
    }
}

/// Get the pre-allocated max_hours for a model.
pub fn max_hours_for_model(model_id: &str) -> u16 {
    match model_id {
        "hrrr" => 48,
        "nam_nest" => 60,
        "nbm" => 80,
        "gfs" => 110,
        "ecmwf_hres" => 80,
        _ => 80, // safe default
    }
}

/// Create a new .rctile file pre-allocated with NaN data.
pub fn create_rctile(
    path: &Path,
    ny: u16,
    nx: u16,
    max_hours: u16,
    lat_min: f32,
    lon_min_index: f32,
    resolution_deg: f32,
    lon_0_360: bool,
) -> Result<()> {
    let n_cells = ny as u32 * nx as u32;
    let hours_offset = HEADER_SIZE;
    let data_offset = HEADER_SIZE + max_hours as u32 * 4;

    let hdr = RcTileHeader {
        magic: MAGIC,
        version: 1,
        ny,
        nx,
        max_hours,
        n_hours_written: 0,
        _reserved1: 0,
        n_cells,
        lat_min,
        lon_min_index,
        resolution_deg,
        lon_0_360: if lon_0_360 { 1 } else { 0 },
        _padding: [0; 3],
        hours_offset,
        data_offset,
        _reserved2: [0; 20],
    };

    let total_size = hdr.file_size();

    let mut f = File::create(path).context("Failed to create .rctile file")?;

    // Write header
    f.write_all(bytemuck::bytes_of(&hdr))?;

    // Write hours table: all -1 (unwritten)
    let unwritten = UNWRITTEN_HOUR.to_le_bytes();
    for _ in 0..max_hours {
        f.write_all(&unwritten)?;
    }

    // Write data region: all NaN
    // Write in chunks to avoid huge single allocation for HRRR (66MB)
    let nan_bytes = f32::NAN.to_le_bytes();
    let chunk_cells = 4096; // 4K cells per write batch
    let chunk_buf: Vec<u8> = nan_bytes
        .iter()
        .cycle()
        .take(chunk_cells * max_hours as usize * 4)
        .copied()
        .collect();

    let total_cells = n_cells as usize;
    let mut written = 0;
    while written < total_cells {
        let batch = (total_cells - written).min(chunk_cells);
        let bytes_to_write = batch * max_hours as usize * 4;
        f.write_all(&chunk_buf[..bytes_to_write])?;
        written += batch;
    }

    // Verify file size
    let actual = f.seek(SeekFrom::End(0))?;
    ensure!(
        actual == total_size,
        "File size mismatch: expected {}, got {}",
        total_size,
        actual
    );

    f.sync_all()?;
    Ok(())
}

/// Write one forecast hour's data (means only) into an existing .rctile file.
///
/// `hour_values` must have exactly `n_cells` f32 values in row-major order (same as tile grid).
/// The hour is appended at index `n_hours_written` (or overwrites if already present).
pub fn write_hour(
    path: &Path,
    forecast_hour: i32,
    hour_values: &[f32],
) -> Result<u16> {
    let mut f = OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .context("Failed to open .rctile for writing")?;

    // Read header
    let mut hdr_buf = [0u8; HEADER_SIZE as usize];
    f.read_exact(&mut hdr_buf)?;
    let mut hdr = RcTileHeader::from_bytes(&hdr_buf)?;

    ensure!(
        hour_values.len() == hdr.n_cells as usize,
        "Expected {} values, got {}",
        hdr.n_cells,
        hour_values.len()
    );

    // Read existing hours table to find if this hour already exists
    let hours_table_bytes = hdr.max_hours as usize * 4;
    let mut hours_buf = vec![0u8; hours_table_bytes];
    f.seek(SeekFrom::Start(hdr.hours_offset as u64))?;
    f.read_exact(&mut hours_buf)?;
    let hours_table: &[i32] = bytemuck::cast_slice(&hours_buf);

    // Find the slot for this hour
    let slot = {
        // Check if hour already written
        let existing = hours_table[..hdr.n_hours_written as usize]
            .iter()
            .position(|&h| h == forecast_hour);

        match existing {
            Some(idx) => idx,
            None => {
                // Append at n_hours_written
                ensure!(
                    hdr.n_hours_written < hdr.max_hours,
                    "No more hour slots (max={})",
                    hdr.max_hours
                );

                let slot = hdr.n_hours_written as usize;

                // Find sorted insertion position to keep hours ordered
                let insert_pos = hours_table[..slot]
                    .iter()
                    .position(|&h| h > forecast_hour)
                    .unwrap_or(slot);

                if insert_pos < slot {
                    // Need to shift hours and data to maintain sorted order
                    shift_hours_and_data(&mut f, &hdr, &hours_buf, insert_pos, slot, forecast_hour, hour_values)?;
                    hdr.n_hours_written += 1;
                    // Update header
                    f.seek(SeekFrom::Start(0))?;
                    f.write_all(bytemuck::bytes_of(&hdr))?;
                    return Ok(hdr.n_hours_written);
                }

                // Write hour value at slot position in hours table
                f.seek(SeekFrom::Start(
                    hdr.hours_offset as u64 + slot as u64 * 4,
                ))?;
                f.write_all(&forecast_hour.to_le_bytes())?;

                hdr.n_hours_written += 1;
                slot
            }
        }
    };

    // Write cell data: for each cell, write the value at the hour's slot
    for cell_idx in 0..hdr.n_cells {
        let offset = hdr.cell_data_offset(cell_idx) + slot as u64 * 4;
        f.seek(SeekFrom::Start(offset))?;
        f.write_all(&hour_values[cell_idx as usize].to_le_bytes())?;
    }

    // Update header (n_hours_written may have changed)
    f.seek(SeekFrom::Start(0))?;
    f.write_all(bytemuck::bytes_of(&hdr))?;

    Ok(hdr.n_hours_written)
}

/// Shift hours and data right by one position to insert at `insert_pos`.
fn shift_hours_and_data(
    f: &mut File,
    hdr: &RcTileHeader,
    hours_buf: &[u8],
    insert_pos: usize,
    current_count: usize,
    new_hour: i32,
    new_values: &[f32],
) -> Result<()> {
    // Shift hours table: move [insert_pos..current_count] to [insert_pos+1..current_count+1]
    let hours_table: &[i32] = bytemuck::cast_slice(hours_buf);
    let mut new_hours = vec![UNWRITTEN_HOUR; hdr.max_hours as usize];
    new_hours[..insert_pos].copy_from_slice(&hours_table[..insert_pos]);
    new_hours[insert_pos] = new_hour;
    new_hours[insert_pos + 1..=current_count].copy_from_slice(&hours_table[insert_pos..current_count]);

    // Write updated hours table
    f.seek(SeekFrom::Start(hdr.hours_offset as u64))?;
    f.write_all(bytemuck::cast_slice(&new_hours[..hdr.max_hours as usize]))?;

    // For each cell, shift the data values right by one at the insert position
    // Read old values, shift, write back
    let max_h = hdr.max_hours as usize;
    let mut cell_buf = vec![0u8; max_h * 4];
    for cell_idx in 0..hdr.n_cells {
        let cell_off = hdr.cell_data_offset(cell_idx);

        // Read existing cell data
        f.seek(SeekFrom::Start(cell_off))?;
        f.read_exact(&mut cell_buf[..max_h * 4])?;
        let cell_vals: &mut [f32] = bytemuck::cast_slice_mut(&mut cell_buf[..max_h * 4]);

        // Shift right
        for i in (insert_pos + 1..=current_count).rev() {
            cell_vals[i] = cell_vals[i - 1];
        }
        // Insert new value
        cell_vals[insert_pos] = new_values[cell_idx as usize];

        // Write back
        f.seek(SeekFrom::Start(cell_off))?;
        f.write_all(&cell_buf[..max_h * 4])?;
    }

    Ok(())
}

/// Recover a clean f64 from an f32 that stored a "nice" decimal value.
///
/// f32 can't represent 0.1 exactly — it stores 0.10000000149...
/// When we widen that to f64, we get 0.10000000149... instead of 0.1.
/// This causes cell index drift (floor(7.0/0.10000000149) = 69 not 70).
///
/// Fix: round to 6 decimal places, which recovers 0.1, 0.03, 0.25, etc.
#[inline]
fn f32_to_f64_clean(v: f32) -> f64 {
    let d = v as f64;
    (d * 1e6).round() / 1e6
}

/// Compute cell index from lat/lon and header parameters.
#[inline]
fn compute_cell(hdr: &RcTileHeader, lat: f64, lon: f64) -> u32 {
    let lat_min = f32_to_f64_clean(hdr.lat_min);
    let lon_min = f32_to_f64_clean(hdr.lon_min_index);
    let res = f32_to_f64_clean(hdr.resolution_deg);

    let iy = ((lat - lat_min) / res).floor() as isize;
    let target_lon = if hdr.lon_0_360 != 0 && lon < 0.0 {
        lon + 360.0
    } else {
        lon
    };
    let ix = ((target_lon - lon_min) / res).floor() as isize;

    let iy = iy.max(0).min(hdr.ny as isize - 1) as usize;
    let ix = ix.max(0).min(hdr.nx as isize - 1) as usize;
    (iy * hdr.nx as usize + ix) as u32
}

/// Read timeseries for a single point from an .rctile file via direct file I/O.
///
/// Returns (hours, values) where hours only includes written hours
/// and values are the corresponding f32 values for the cell.
pub fn read_timeseries(
    path: &Path,
    lat: f64,
    lon: f64,
) -> Result<(Vec<i32>, Vec<f32>)> {
    let mut f = File::open(path).context("Failed to open .rctile")?;

    // Read header
    let mut hdr_buf = [0u8; HEADER_SIZE as usize];
    f.read_exact(&mut hdr_buf)?;
    let hdr = RcTileHeader::from_bytes(&hdr_buf)?;

    if hdr.n_hours_written == 0 {
        return Ok((vec![], vec![]));
    }

    // Read hours table
    let n_written = hdr.n_hours_written as usize;
    let mut hours_buf = vec![0u8; n_written * 4];
    f.seek(SeekFrom::Start(hdr.hours_offset as u64))?;
    f.read_exact(&mut hours_buf)?;
    let hours: Vec<i32> = bytemuck::cast_slice::<u8, i32>(&hours_buf)
        .iter()
        .copied()
        .collect();

    let cell_idx = compute_cell(&hdr, lat, lon);

    // Read cell timeseries (only n_hours_written values)
    let cell_off = hdr.cell_data_offset(cell_idx);
    let mut val_buf = vec![0u8; n_written * 4];
    f.seek(SeekFrom::Start(cell_off))?;
    f.read_exact(&mut val_buf)?;
    let values: Vec<f32> = bytemuck::cast_slice::<u8, f32>(&val_buf)
        .iter()
        .copied()
        .collect();

    Ok((hours, values))
}

/// Read timeseries from an mmap'd buffer. The caller owns the mmap.
///
/// This is the fast path used by the server — no file I/O, just pointer arithmetic.
pub fn read_timeseries_mmap(
    data: &[u8],
    lat: f64,
    lon: f64,
) -> Result<(Vec<i32>, Vec<f32>)> {
    ensure!(data.len() >= HEADER_SIZE as usize, "mmap too small for header");
    let hdr = RcTileHeader::from_bytes(&data[..HEADER_SIZE as usize])?;

    if hdr.n_hours_written == 0 {
        return Ok((vec![], vec![]));
    }

    let n_written = hdr.n_hours_written as usize;

    // Read hours
    let hours_start = hdr.hours_offset as usize;
    let hours_end = hours_start + n_written * 4;
    ensure!(data.len() >= hours_end, "mmap too small for hours table");
    let hours: Vec<i32> = bytemuck::cast_slice::<u8, i32>(&data[hours_start..hours_end])
        .iter()
        .copied()
        .collect();

    let cell_idx = compute_cell(&hdr, lat, lon);

    // Read cell timeseries
    let cell_off = hdr.cell_data_offset(cell_idx) as usize;
    let cell_end = cell_off + n_written * 4;
    ensure!(data.len() >= cell_end, "mmap too small for cell data");
    let values: Vec<f32> = bytemuck::cast_slice::<u8, f32>(&data[cell_off..cell_end])
        .iter()
        .copied()
        .collect();

    Ok((hours, values))
}

/// Read just the header from a .rctile file.
pub fn read_header(path: &Path) -> Result<RcTileHeader> {
    let mut f = File::open(path).context("Failed to open .rctile")?;
    let mut buf = [0u8; HEADER_SIZE as usize];
    f.read_exact(&mut buf)?;
    RcTileHeader::from_bytes(&buf)
}

/// Read just the written hours from a .rctile file (header + hours table only).
pub fn read_hours(path: &Path) -> Result<Vec<i32>> {
    let mut f = File::open(path).context("Failed to open .rctile")?;
    let mut hdr_buf = [0u8; HEADER_SIZE as usize];
    f.read_exact(&mut hdr_buf)?;
    let hdr = RcTileHeader::from_bytes(&hdr_buf)?;

    let n = hdr.n_hours_written as usize;
    if n == 0 {
        return Ok(vec![]);
    }

    let mut hours_buf = vec![0u8; n * 4];
    f.read_exact(&mut hours_buf)?;
    let hours: &[i32] = bytemuck::cast_slice(&hours_buf);
    Ok(hours.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    #[test]
    fn test_create_and_write_read_roundtrip() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path();

        let ny = 4u16;
        let nx = 5u16;
        let max_hours = 10u16;
        let n_cells = ny as usize * nx as usize;

        create_rctile(path, ny, nx, max_hours, 33.0, -88.0, 0.1, false).unwrap();

        // Verify header
        let hdr = read_header(path).unwrap();
        assert_eq!(hdr.ny, ny);
        assert_eq!(hdr.nx, nx);
        assert_eq!(hdr.max_hours, max_hours);
        assert_eq!(hdr.n_hours_written, 0);
        assert_eq!(hdr.n_cells, n_cells as u32);

        // Write hour 3
        let values_h3: Vec<f32> = (0..n_cells).map(|i| i as f32 * 1.5).collect();
        let written = write_hour(path, 3, &values_h3).unwrap();
        assert_eq!(written, 1);

        // Write hour 1 (out of order — should insert before hour 3)
        let values_h1: Vec<f32> = (0..n_cells).map(|i| i as f32 * 0.5).collect();
        let written = write_hour(path, 1, &values_h1).unwrap();
        assert_eq!(written, 2);

        // Write hour 6 (appended at end)
        let values_h6: Vec<f32> = (0..n_cells).map(|i| i as f32 * 3.0).collect();
        let written = write_hour(path, 6, &values_h6).unwrap();
        assert_eq!(written, 3);

        // Read back at cell (1, 2) → cell_idx = 1*5+2 = 7
        let lat = 33.0 + 1.0 * 0.1 + 0.05; // middle of row 1
        let lon = -88.0 + 2.0 * 0.1 + 0.05; // middle of col 2
        let (hours, vals) = read_timeseries(path, lat, lon).unwrap();

        assert_eq!(hours, vec![1, 3, 6]);
        assert_eq!(vals.len(), 3);

        let cell_idx = 7;
        assert!((vals[0] - cell_idx as f32 * 0.5).abs() < 1e-6); // hour 1
        assert!((vals[1] - cell_idx as f32 * 1.5).abs() < 1e-6); // hour 3
        assert!((vals[2] - cell_idx as f32 * 3.0).abs() < 1e-6); // hour 6
    }

    #[test]
    fn test_overwrite_existing_hour() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path();

        let ny = 2u16;
        let nx = 3u16;
        let n_cells = 6;

        create_rctile(path, ny, nx, 5, 0.0, 0.0, 1.0, false).unwrap();

        let v1: Vec<f32> = vec![1.0; n_cells];
        write_hour(path, 0, &v1).unwrap();

        // Overwrite same hour with different values
        let v2: Vec<f32> = vec![99.0; n_cells];
        let written = write_hour(path, 0, &v2).unwrap();
        assert_eq!(written, 1); // still 1 hour

        let (hours, vals) = read_timeseries(path, 0.5, 0.5).unwrap();
        assert_eq!(hours, vec![0]);
        assert!((vals[0] - 99.0).abs() < 1e-6);
    }

    #[test]
    fn test_mmap_read() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path();

        create_rctile(path, 3, 4, 8, 40.0, -80.0, 0.5, false).unwrap();

        let vals: Vec<f32> = (0..12).map(|i| i as f32 * 10.0).collect();
        write_hour(path, 5, &vals).unwrap();

        // Read file into memory to simulate mmap
        let data = std::fs::read(path).unwrap();
        let (hours, values) = read_timeseries_mmap(&data, 40.25, -79.75).unwrap();
        assert_eq!(hours, vec![5]);
        // cell (0, 0) → value = 0.0 ... cell (0, 0) at lat=40.25 (row 0), lon=-79.75 (col 0.5/0.5=col 0)
        // Actually: iy = floor((40.25-40.0)/0.5) = 0, ix = floor((-79.75-(-80.0))/0.5) = 0
        assert!((values[0] - 0.0).abs() < 1e-6);
    }

    #[test]
    fn test_lon_0_360() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path();

        // Simulate 0-360 convention: lon_min_index = 360 + (-80) = 280
        create_rctile(path, 2, 2, 4, 40.0, 280.0, 1.0, true).unwrap();

        let vals: Vec<f32> = vec![10.0, 20.0, 30.0, 40.0];
        write_hour(path, 0, &vals).unwrap();

        // Query with negative lon — should be converted to 0-360
        let (hours, values) = read_timeseries(path, 40.5, -79.5).unwrap();
        assert_eq!(hours, vec![0]);
        // lon = -79.5 + 360 = 280.5, ix = floor((280.5-280.0)/1.0) = 0, iy = 0 → cell 0
        assert!((values[0] - 10.0).abs() < 1e-6);
    }
}
