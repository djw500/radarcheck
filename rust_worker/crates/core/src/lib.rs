pub mod bucket_mapping;
pub mod config;
pub mod db;
pub mod fetch;
pub mod grib;
pub mod idx;
/// NPZ reader/writer — only compiled when "npz" feature is enabled.
/// Used by test fixtures for reference comparison. Production uses .rctile exclusively.
#[cfg(feature = "npz")]
pub mod npz;
pub mod rctile;
pub mod rctile_v2;
pub mod solar;
pub mod tile_query;
pub mod tiles;
pub mod worker;
