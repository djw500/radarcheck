//! NPZ file reading and writing.
//!
//! NPZ is a ZIP archive containing .npy files. We need to:
//! - Read existing NPZ (to merge new hours into existing tiles)
//! - Write NPZ with compressed arrays (to save tiles)

use anyhow::{Context, Result, bail};
use ndarray::Array3;
use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;

/// Contents of a tile NPZ file
#[derive(Debug)]
pub struct TileNpz {
    pub hours: Vec<i32>,
    pub means: Option<Array3<f32>>,
    pub mins: Option<Array3<f32>>,
    pub maxs: Option<Array3<f32>>,
}

/// Write a tile NPZ file with compressed arrays
pub fn write_tile_npz(path: &Path, tile: &TileNpz) -> Result<()> {
    let file = File::create(path).context("Failed to create NPZ file")?;
    let mut zip = zip::ZipWriter::new(file);

    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);

    // Write hours array
    let hours_npy = array_to_npy_i32(&tile.hours);
    zip.start_file("hours.npy", options)?;
    zip.write_all(&hours_npy)?;

    // Write stat arrays
    if let Some(ref means) = tile.means {
        let npy = array3_to_npy_f32(means);
        zip.start_file("means.npy", options)?;
        zip.write_all(&npy)?;
    }

    if let Some(ref mins) = tile.mins {
        let npy = array3_to_npy_f32(mins);
        zip.start_file("mins.npy", options)?;
        zip.write_all(&npy)?;
    }

    if let Some(ref maxs) = tile.maxs {
        let npy = array3_to_npy_f32(maxs);
        zip.start_file("maxs.npy", options)?;
        zip.write_all(&npy)?;
    }

    zip.finish()?;
    Ok(())
}

/// Read a tile NPZ file
pub fn read_tile_npz(path: &Path) -> Result<TileNpz> {
    let file = File::open(path).context("Failed to open NPZ file")?;
    let mut archive = zip::ZipArchive::new(file).context("Failed to read ZIP")?;

    let mut hours = Vec::new();
    let mut means = None;
    let mut mins = None;
    let mut maxs = None;

    for i in 0..archive.len() {
        let mut entry = archive.by_index(i)?;
        let name = entry.name().to_string();

        let mut data = Vec::new();
        entry.read_to_end(&mut data)?;

        match name.as_str() {
            "hours.npy" => {
                hours = npy_to_i32_vec(&data)?;
            }
            "means.npy" => {
                means = Some(npy_to_array3_f32(&data)?);
            }
            "mins.npy" => {
                mins = Some(npy_to_array3_f32(&data)?);
            }
            "maxs.npy" => {
                maxs = Some(npy_to_array3_f32(&data)?);
            }
            _ => {} // ignore unknown arrays
        }
    }

    Ok(TileNpz {
        hours,
        means,
        mins,
        maxs,
    })
}

// ── NPY format helpers ───────────────────────────────────────────────────────
// NPY v1.0 format:
//   magic: \x93NUMPY
//   major: 1, minor: 0
//   header_len: u16 (LE)
//   header: Python dict string like "{'descr': '<f4', 'fortran_order': False, 'shape': (10, 20), }\n"
//   data: raw bytes

/// Serialize a 1D i32 array to NPY bytes
fn array_to_npy_i32(data: &[i32]) -> Vec<u8> {
    let header = format!(
        "{{'descr': '<i4', 'fortran_order': False, 'shape': ({},), }}",
        data.len()
    );
    build_npy(&header, bytemuck::cast_slice(data))
}

/// Serialize a 3D f32 array to NPY bytes
fn array3_to_npy_f32(arr: &Array3<f32>) -> Vec<u8> {
    let shape = arr.shape();
    let header = format!(
        "{{'descr': '<f4', 'fortran_order': False, 'shape': ({}, {}, {}), }}",
        shape[0], shape[1], shape[2]
    );
    // ndarray stores data in row-major (C) order by default
    let data: Vec<f32> = arr.iter().cloned().collect();
    build_npy(&header, bytemuck::cast_slice(&data))
}

fn build_npy(header: &str, data: &[u8]) -> Vec<u8> {
    // Pad header to align to 64 bytes (including magic + version + header_len)
    let preamble_len = 10; // magic(6) + version(2) + header_len(2)
    let header_bytes = header.as_bytes();
    let total = preamble_len + header_bytes.len() + 1; // +1 for newline
    let padding = (64 - (total % 64)) % 64;
    let header_len = header_bytes.len() + 1 + padding;

    let mut buf = Vec::with_capacity(preamble_len + header_len + data.len());
    buf.extend_from_slice(b"\x93NUMPY"); // magic
    buf.push(1); // major version
    buf.push(0); // minor version
    buf.extend_from_slice(&(header_len as u16).to_le_bytes());
    buf.extend_from_slice(header_bytes);
    for _ in 0..padding {
        buf.push(b' ');
    }
    buf.push(b'\n');
    buf.extend_from_slice(data);
    buf
}

/// Parse NPY bytes into a Vec<i32>
fn npy_to_i32_vec(data: &[u8]) -> Result<Vec<i32>> {
    let (header, payload) = parse_npy_header(data)?;
    if !header.contains("<i4") && !header.contains("int32") {
        bail!("Expected i32 array, got header: {}", header);
    }
    let values: &[i32] = bytemuck::cast_slice(payload);
    Ok(values.to_vec())
}

/// Parse NPY bytes into a 3D f32 array
fn npy_to_array3_f32(data: &[u8]) -> Result<Array3<f32>> {
    let (header, payload) = parse_npy_header(data)?;
    if !header.contains("<f4") && !header.contains("float32") {
        bail!("Expected f32 array, got header: {}", header);
    }

    // Parse shape from header
    let shape = parse_shape(&header)?;
    if shape.len() != 3 {
        bail!("Expected 3D array, got shape: {:?}", shape);
    }

    let values: &[f32] = bytemuck::cast_slice(payload);
    let arr = Array3::from_shape_vec((shape[0], shape[1], shape[2]), values.to_vec())?;
    Ok(arr)
}

fn parse_npy_header(data: &[u8]) -> Result<(String, &[u8])> {
    if data.len() < 10 || &data[0..6] != b"\x93NUMPY" {
        bail!("Not a valid NPY file");
    }
    let major = data[6];
    let _minor = data[7];

    let header_len = if major == 1 {
        u16::from_le_bytes([data[8], data[9]]) as usize
    } else {
        // NPY v2.0 uses u32
        u32::from_le_bytes([data[8], data[9], data[10], data[11]]) as usize
    };

    let header_start = if major == 1 { 10 } else { 12 };
    let header_end = header_start + header_len;

    if data.len() < header_end {
        bail!("NPY file too short");
    }

    let header = String::from_utf8_lossy(&data[header_start..header_end])
        .trim()
        .to_string();

    Ok((header, &data[header_end..]))
}

fn parse_shape(header: &str) -> Result<Vec<usize>> {
    // Parse shape tuple from header like "...'shape': (1, 140, 220), ..."
    let shape_start = header
        .find("'shape':")
        .or_else(|| header.find("\"shape\":"))
        .context("No shape in header")?;

    let after_shape = &header[shape_start..];
    let paren_start = after_shape.find('(').context("No ( in shape")? + 1;
    let paren_end = after_shape.find(')').context("No ) in shape")?;
    let shape_str = &after_shape[paren_start..paren_end];

    let dims: Result<Vec<usize>> = shape_str
        .split(',')
        .filter(|s| !s.trim().is_empty())
        .map(|s| {
            s.trim()
                .parse::<usize>()
                .context(format!("Invalid shape dim: {}", s.trim()))
        })
        .collect();

    dims
}
