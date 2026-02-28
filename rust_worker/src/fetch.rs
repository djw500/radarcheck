//! HTTP fetching for GRIB2 files and IDX indexes.

use anyhow::{Context, Result, bail};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::config::ModelConfig;
use crate::idx::{self, IdxEntry, IdxMatch};

const HTTP_TIMEOUT: Duration = Duration::from_secs(30);

/// Fetch and parse an IDX file from a URL
pub fn fetch_idx(url: &str) -> Result<Vec<IdxEntry>> {
    let client = reqwest::blocking::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .build()?;

    let resp = client
        .get(url)
        .send()
        .context(format!("Failed to fetch IDX: {}", url))?;

    if !resp.status().is_success() {
        bail!(
            "IDX fetch failed: {} {}",
            resp.status().as_u16(),
            url
        );
    }

    let content = resp.text()?;
    Ok(idx::parse_idx(&content))
}

/// Fetch a GRIB2 message by byte range
pub fn fetch_grib_range(url: &str, byte_start: u64, byte_end: Option<u64>) -> Result<Vec<u8>> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()?;

    let range = match byte_end {
        Some(end) => format!("bytes={}-{}", byte_start, end),
        None => format!("bytes={}-", byte_start),
    };

    let resp = client
        .get(url)
        .header("Range", &range)
        .send()
        .context(format!("Failed to fetch GRIB range: {}", url))?;

    let status = resp.status().as_u16();
    if status != 200 && status != 206 {
        bail!("GRIB fetch failed: {} {}", status, url);
    }

    let bytes = resp.bytes()?.to_vec();
    Ok(bytes)
}

/// Full pipeline: fetch idx → find variable → download GRIB subset → return raw bytes
pub fn fetch_variable_grib(
    grib_url: &str,
    idx_url: &str,
    search: &str,
) -> Result<Vec<u8>> {
    let entries = fetch_idx(idx_url)?;

    let m = idx::find_first(&entries, search).context(format!(
        "Variable '{}' not found in IDX",
        search
    ))?;

    fetch_grib_range(grib_url, m.byte_start, m.byte_end)
}

/// Load IDX from a local cache file, falling back to HTTP
pub fn fetch_idx_cached(url: &str, cache_dir: &Path, cache_key: &str) -> Result<Vec<IdxEntry>> {
    let cache_path = cache_dir.join(format!("{}.idx", cache_key));

    if cache_path.exists() {
        return idx::parse_idx_file(&cache_path);
    }

    let entries = fetch_idx(url)?;

    // Cache for next time
    if let Ok(content) = reqwest::blocking::get(url).and_then(|r| r.text()) {
        let _ = std::fs::create_dir_all(cache_dir);
        let _ = std::fs::write(&cache_path, content);
    }

    Ok(entries)
}
