//! Model and variable configuration — mirrors Python config.py


/// URL template for a model's GRIB2 files on NOAA servers
#[derive(Debug, Clone)]
pub struct ModelConfig {
    pub name: &'static str,
    pub herbie_model: &'static str,
    /// Template for GRIB URL: {date} = YYYYMMDD, {hh} = init hour, {fxx} = forecast hour (zero-padded)
    pub grib_url_template: &'static str,
    /// Template for IDX URL (usually same as grib + ".idx")
    pub idx_url_template: &'static str,
    pub forecast_hour_digits: usize,
    pub tile_resolution_deg: f64,
}

/// Search string to find a variable in the idx file
#[derive(Debug, Clone)]
pub struct VariableSearch {
    pub default: &'static str,
    /// Model-specific overrides (keyed by herbie_model name)
    pub overrides: &'static [(&'static str, &'static str)],
}

impl VariableSearch {
    pub fn get_search(&self, herbie_model: &str) -> &str {
        for (model, search) in self.overrides {
            if *model == herbie_model {
                return search;
            }
        }
        self.default
    }
}

/// Unit conversion function
#[derive(Debug, Clone, Copy)]
pub enum Conversion {
    KToF,
    CToF,
    MSToMph,
    KgM2ToIn,
    MToIn,
    KgM2SToInHr,
    PaToMb,
    MToFt,
    None,
}

impl Conversion {
    pub fn apply(&self, value: f32) -> f32 {
        match self {
            Conversion::KToF => (value - 273.15) * 9.0 / 5.0 + 32.0,
            Conversion::CToF => value * 9.0 / 5.0 + 32.0,
            Conversion::MSToMph => value * 2.23694,
            Conversion::KgM2ToIn => value * 0.0393701,
            Conversion::MToIn => value * 39.3701,
            Conversion::KgM2SToInHr => value * 0.0393701 * 3600.0,
            Conversion::PaToMb => value / 100.0,
            Conversion::MToFt => value * 3.28084,
            Conversion::None => value,
        }
    }
}

#[derive(Debug, Clone)]
pub struct VariableConfig {
    pub id: &'static str,
    pub display_name: &'static str,
    pub units: &'static str,
    pub conversion: Conversion,
    pub search: VariableSearch,
    /// Map from source units string to conversion
    pub unit_conversions_by_units: &'static [(&'static str, Conversion)],
    /// Whether this variable is an accumulation (apcp, asnow)
    pub is_accumulation: bool,
    /// Models that don't have this variable
    pub model_exclusions: &'static [&'static str],
}

impl VariableConfig {
    /// Get the appropriate conversion for given source units
    pub fn conversion_for_units(&self, src_units: Option<&str>) -> Conversion {
        if let Some(units) = src_units {
            for (u, conv) in self.unit_conversions_by_units {
                if *u == units {
                    return *conv;
                }
            }
        }
        self.conversion
    }
}

/// Region for tiling
#[derive(Debug, Clone)]
pub struct TilingRegion {
    pub id: &'static str,
    pub name: &'static str,
    pub lat_min: f64,
    pub lat_max: f64,
    pub lon_min: f64,
    pub lon_max: f64,
    pub default_resolution_deg: f64,
    /// Which stats to save: "mean", "min", "max"
    pub stats: &'static [&'static str],
}

// ── Static config ────────────────────────────────────────────────────────────

pub static NE_REGION: TilingRegion = TilingRegion {
    id: "ne",
    name: "Northeast US (Expanded)",
    lat_min: 33.0,
    lat_max: 47.0,
    lon_min: -88.0,
    lon_max: -66.0,
    default_resolution_deg: 0.1,
    stats: &["mean"],
};

pub fn get_region(region_id: &str) -> Option<&'static TilingRegion> {
    match region_id {
        "ne" => Some(&NE_REGION),
        _ => None,
    }
}

/// Format resolution as directory name, matching Python: f"{res:.3f}deg".rstrip("0").rstrip(".")
pub fn format_res_dir(resolution_deg: f64) -> String {
    // Match Python: f"{resolution_deg:.3f}deg" (no trimming — Python's rstrip is a no-op here)
    format!("{:.3}deg", resolution_deg)
}

pub fn get_model(model_id: &str) -> Option<ModelConfig> {
    Some(match model_id {
        "hrrr" => ModelConfig {
            name: "HRRR",
            herbie_model: "hrrr",
            grib_url_template: "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus/hrrr.t{hh}z.wrfsfcf{fxx}.grib2",
            idx_url_template: "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{date}/conus/hrrr.t{hh}z.wrfsfcf{fxx}.grib2.idx",
            forecast_hour_digits: 2,
            tile_resolution_deg: 0.03,
        },
        "nam_nest" => ModelConfig {
            name: "NAM 3km CONUS",
            herbie_model: "nam",
            grib_url_template: "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam/prod/nam.{date}/nam.t{hh}z.conusnest.hiresf{fxx}.tm00.grib2",
            idx_url_template: "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam/prod/nam.{date}/nam.t{hh}z.conusnest.hiresf{fxx}.tm00.grib2.idx",
            forecast_hour_digits: 2,
            tile_resolution_deg: 0.1,
        },
        "gfs" => ModelConfig {
            name: "GFS",
            herbie_model: "gfs",
            grib_url_template: "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{date}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fxx}",
            idx_url_template: "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{date}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{fxx}.idx",
            forecast_hour_digits: 3,
            tile_resolution_deg: 0.1,
        },
        "nbm" => ModelConfig {
            name: "National Blend (NBM)",
            herbie_model: "nbm",
            grib_url_template: "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{date}/{hh}/core/blend.t{hh}z.core.f{fxx}.co.grib2",
            idx_url_template: "https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{date}/{hh}/core/blend.t{hh}z.core.f{fxx}.co.grib2.idx",
            forecast_hour_digits: 3,
            tile_resolution_deg: 0.1,
        },
        "ecmwf_hres" => ModelConfig {
            name: "ECMWF HRES",
            herbie_model: "ifs",
            grib_url_template: "https://data.ecmwf.int/forecasts/{date}/{hh}z/ifs/0p25/oper/{date}{hh}0000-{fxx}h-oper-fc.grib2",
            idx_url_template: "https://data.ecmwf.int/forecasts/{date}/{hh}z/ifs/0p25/oper/{date}{hh}0000-{fxx}h-oper-fc.index",
            forecast_hour_digits: 1,
            tile_resolution_deg: 0.1,
        },
        _ => return None,
    })
}

pub fn get_variable(var_id: &str) -> Option<VariableConfig> {
    Some(match var_id {
        "t2m" => VariableConfig {
            id: "t2m",
            display_name: "2m Temperature",
            units: "°F",
            conversion: Conversion::KToF,
            search: VariableSearch {
                default: ":TMP:2 m above ground",
                overrides: &[("ifs", ":2t:")],
            },
            unit_conversions_by_units: &[
                ("K", Conversion::KToF),
                ("degC", Conversion::CToF),
                ("°C", Conversion::CToF),
            ],
            is_accumulation: false,
            model_exclusions: &[],
        },
        "apcp" => VariableConfig {
            id: "apcp",
            display_name: "Accumulated Precipitation",
            units: "in",
            conversion: Conversion::KgM2ToIn,
            search: VariableSearch {
                default: ":APCP:surface",
                overrides: &[
                    ("nbm", ":APCP:surface:.*acc fcst:$"),
                    ("ifs", ":tp:"),
                ],
            },
            unit_conversions_by_units: &[
                ("m", Conversion::MToIn),
                ("kg m-2", Conversion::KgM2ToIn),
                ("kg m**-2", Conversion::KgM2ToIn),
            ],
            is_accumulation: true,
            model_exclusions: &[],
        },
        "asnow" => VariableConfig {
            id: "asnow",
            display_name: "Accumulated Snowfall",
            units: "in",
            conversion: Conversion::MToIn,
            search: VariableSearch {
                default: ":ASNOW:surface",
                overrides: &[],
            },
            unit_conversions_by_units: &[],
            is_accumulation: true,
            model_exclusions: &["gfs", "nam_nest", "ecmwf_hres"],
        },
        "snod" => VariableConfig {
            id: "snod",
            display_name: "Snow Depth",
            units: "in",
            conversion: Conversion::MToIn,
            search: VariableSearch {
                default: ":SNOD:surface",
                overrides: &[("ifs", ":sd:")],
            },
            unit_conversions_by_units: &[],
            is_accumulation: false,
            model_exclusions: &["nbm"],
        },
        _ => return None,
    })
}

/// Get tile resolution for model+region (per-model override support)
pub fn get_tile_resolution(region: &TilingRegion, model_id: &str) -> f64 {
    match get_model(model_id) {
        Some(m) if m.tile_resolution_deg > 0.0 => m.tile_resolution_deg,
        _ => region.default_resolution_deg,
    }
}

/// Build GRIB URL from model config
pub fn build_grib_url(model: &ModelConfig, date: &str, init_hour: &str, forecast_hour: u32) -> String {
    let fxx = format!("{:0>width$}", forecast_hour, width = model.forecast_hour_digits);
    model
        .grib_url_template
        .replace("{date}", date)
        .replace("{hh}", init_hour)
        .replace("{fxx}", &fxx)
}

/// Build IDX URL from model config
pub fn build_idx_url(model: &ModelConfig, date: &str, init_hour: &str, forecast_hour: u32) -> String {
    let fxx = format!("{:0>width$}", forecast_hour, width = model.forecast_hour_digits);
    model
        .idx_url_template
        .replace("{date}", date)
        .replace("{hh}", init_hour)
        .replace("{fxx}", &fxx)
}

/// All known model IDs
pub static ALL_MODEL_IDS: &[&str] = &["hrrr", "nam_nest", "gfs", "nbm", "ecmwf_hres"];

/// All tile build variable IDs (the 4 variables built by the scheduler)
pub static TILE_BUILD_VARIABLE_IDS: &[&str] = &["t2m", "apcp", "asnow", "snod"];

/// Get tile resolution for model+region by region_id string
pub fn get_tile_resolution_by_id(region_id: &str, model_id: &str) -> f64 {
    if let Some(region) = get_region(region_id) {
        get_tile_resolution(region, model_id)
    } else {
        0.1
    }
}

/// Infer region for a lat/lon point
pub fn infer_region_for_latlon(lat: f64, lon: f64) -> Option<&'static str> {
    // Check NE region
    if lat >= NE_REGION.lat_min
        && lat <= NE_REGION.lat_max
        && lon >= NE_REGION.lon_min
        && lon <= NE_REGION.lon_max
    {
        return Some(NE_REGION.id);
    }
    None
}
