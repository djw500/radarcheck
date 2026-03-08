import os


WEATHER_VARIABLES = {
    "t2m": {
        "display_name": "2m Temperature",
        "units": "°F",
        "short_name": "t2m",
        "colormap": "temperature",
        "vmin": -40,
        "vmax": 110,
        "category": "temperature",
        "conversion": "k_to_f",
        "unit_conversions_by_units": {
            "K": "k_to_f",
            "degC": "c_to_f",
            "°C": "c_to_f",
        },
        "herbie_search": {
            "default": ":TMP:2 m above ground",
            "ifs": ":2t:",
        },
    },
    "dpt": {
        "display_name": "2m Dew Point",
        "units": "°F",
        "short_name": "dpt",
        "colormap": "temperature",
        "vmin": -40,
        "vmax": 80,
        "category": "temperature",
        "conversion": "k_to_f",
        "unit_conversions_by_units": {
            "K": "k_to_f",
            "degC": "c_to_f",
            "°C": "c_to_f",
        },
        "herbie_search": {
            "default": ":DPT:2 m above ground",
            "ifs": ":2d:",
        },
    },
    "rh": {
        "display_name": "Relative Humidity",
        "units": "%",
        "short_name": "rh",
        "colormap": "humidity",
        "vmin": 0,
        "vmax": 100,
        "category": "temperature",
        "herbie_search": {
            "default": ":RH:2 m above ground",
        },
        "model_exclusions": ["ecmwf_hres"],
    },
    "wind_10m": {
        "display_name": "10m Wind Speed",
        "units": "mph",
        "short_name": "wind_10m",
        "colormap": "wind",
        "vmin": 0,
        "vmax": 80,
        "category": "wind",
        "conversion": "m_s_to_mph",
        "herbie_search": {
            "default": ":[UV]GRD:10 m above ground",
            "nbm": ":WIND:10 m",
            "ifs": ":10[uv]:",
        },
    },
    "gust": {
        "display_name": "Wind Gusts",
        "units": "mph",
        "short_name": "gust",
        "colormap": "wind",
        "vmin": 0,
        "vmax": 90,
        "category": "wind",
        "conversion": "m_s_to_mph",
        "herbie_search": {
            "default": ":GUST:surface",
            "nbm": ":GUST:10 m",
            "ifs": ":10fg:",
        },
    },
    "apcp": {
        "display_name": "Accumulated Precipitation",
        "units": "in",
        "short_name": "apcp",
        "colormap": "precip_accumulation",
        "vmin": 0,
        "vmax": 6,
        "category": "precipitation",
        "conversion": "kg_m2_to_in",
        "is_accumulation": True,
        "unit_conversions_by_units": {
            "m": "m_to_in",
            "kg m-2": "kg_m2_to_in",
            "kg m**-2": "kg_m2_to_in",
        },
        "herbie_search": {
            "default": ":APCP:surface",
            "nbm": ":APCP:surface:.*acc fcst:$",
            "ifs": ":tp:",
        },
    },
    "prate": {
        "display_name": "Precipitation Rate",
        "units": "in/hr",
        "short_name": "prate",
        "colormap": "precip_rate",
        "vmin": 0,
        "vmax": 2,
        "category": "precipitation",
        "conversion": "kg_m2_s_to_in_hr",
        "herbie_search": {
            "default": ":PRATE:surface",
            "ifs": ":tprate:",
        },
        "model_exclusions": ["nbm"],
    },
    "asnow": {
        "display_name": "Accumulated Snowfall",
        "units": "in",
        "short_name": "asnow",
        "colormap": "snow_accumulation",
        "vmin": 0,
        "vmax": 24,
        "category": "winter",
        "conversion": "m_to_in",
        "is_accumulation": True,
        "herbie_search": {
            "default": ":ASNOW:surface",
        },
        # Only models with native ASNOW: HRRR, RAP, NBM
        "model_exclusions": ["gfs", "nam_nest", "ecmwf_hres"],
    },
    "snod": {
        "display_name": "Snow Depth",
        "units": "in",
        "short_name": "snod",
        "colormap": "snow_depth",
        "vmin": 0,
        "vmax": 36,
        "category": "winter",
        "conversion": "m_to_in",
        "herbie_search": {
            "default": ":SNOD:surface",
            # ECMWF sd is water equiv; grib_fetcher computes physical depth via sd*1000/rsn
            "ifs": ":sd:",
        },
        "model_exclusions": ["nbm"],
    },
    "refc": {
        "display_name": "Radar Reflectivity",
        "units": "dBZ",
        "short_name": "refc",
        "colormap": "nws_reflectivity",
        "vmin": 5,
        "vmax": 75,
        "category": "precipitation",
        "herbie_search": {
            "default": ":REFC:entire atmosphere",
        },
        "model_exclusions": ["gfs", "nbm", "ecmwf_hres"],
    },
    "cape": {
        "display_name": "CAPE",
        "units": "J/kg",
        "short_name": "cape",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 4000,
        "category": "severe",
        "herbie_search": {
            "default": ":CAPE:surface",
            "ifs": ":mucape:",
        },
    },
    "msl": {
        "display_name": "MSL Pressure",
        "units": "mb",
        "short_name": "msl",
        "colormap": "viridis",
        "vmin": 950,
        "vmax": 1050,
        "category": "surface",
        "conversion": "pa_to_mb",
        "herbie_search": {
            "default": ":PRMSL:mean sea level",
            "hrrr": ":MSLMA:mean sea level",
            "ifs": ":msl:",
        },
        "model_exclusions": ["nbm"],
    },
    "hlcy": {
        "display_name": "Storm Relative Helicity",
        "units": "m²/s²",
        "short_name": "hlcy",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 600,
        "category": "severe",
        "herbie_search": {
            "default": ":HLCY:3000-0 m above ground",
        },
        "model_exclusions": ["gfs", "nbm", "ecmwf_hres"],
    },
    "hail": {
        "display_name": "Hail",
        "units": "in",
        "short_name": "hail",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 3,
        "category": "severe",
        "herbie_search": {
            "default": ":HAIL:surface",
        },
        "model_exclusions": ["gfs", "nam_nest", "nbm", "ecmwf_hres"],
    },
    "snowlr": {
        "display_name": "Snow-Liquid Ratio",
        "units": ":1",
        "short_name": "snowlr",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 22,
        "category": "winter",
        "herbie_search": {
            "default": ":SNOWLR:",
        },
        # NBM only
        "model_exclusions": ["hrrr", "gfs", "nam_nest", "ecmwf_hres"],
    },
    "snowlvl": {
        "display_name": "Snow Level",
        "units": "ft",
        "short_name": "snowlvl",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 10000,
        "category": "winter",
        "conversion": "m_to_ft",
        "herbie_search": {
            "default": ":SNOWLVL:",
        },
        # NBM only
        "model_exclusions": ["hrrr", "gfs", "nam_nest", "ecmwf_hres"],
    },
    "cloud_cover": {
        "display_name": "Cloud Cover",
        "units": "%",
        "short_name": "cloud_cover",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 100,
        "category": "cloud",
        "herbie_search": {
            "default": ":TCDC:entire atmosphere",
            "nbm": ":SKY:surface",
            "ifs": ":tcc:",
        },
        "unit_conversions_by_units": {
            "(0 - 1)": "fraction_to_pct",
            "Proportion": "fraction_to_pct",
        },
    },
}

MODELS = {
    "hrrr": {
        "name": "HRRR",
        "herbie_model": "hrrr",
        "herbie_product": "sfc",
        "max_forecast_hours": 48,
        "update_frequency_hours": 1,
        "forecast_hour_digits": 2,
        "max_hours_by_init": {"00": 48, "06": 48, "12": 48, "18": 48, "default": 18},
        "tile_resolution_deg": 0.03,
    },
    "nam_nest": {
        "name": "NAM 3km CONUS",
        "herbie_model": "nam",
        "herbie_product": "conusnest.hiresf",
        "max_forecast_hours": 60,
        "update_frequency_hours": 6,
        "forecast_hour_digits": 2,
    },
    "gfs": {
        "name": "GFS",
        "herbie_model": "gfs",
        "herbie_product": "pgrb2.0p25",
        "max_forecast_hours": 384,
        "update_frequency_hours": 6,
        "forecast_hour_digits": 3,
        "forecast_hour_schedule": [
            {"start": 3, "end": 240, "step": 3},
            {"start": 246, "end": 384, "step": 6},
        ],
        "tile_resolution_deg": 0.25,
    },
    "nbm": {
        "name": "National Blend (NBM)",
        "herbie_model": "nbm",
        "herbie_product": "co",
        "max_forecast_hours": 264,
        "update_frequency_hours": 1,
        "forecast_hour_digits": 3,
        "forecast_hour_schedule": [
            {"start": 1, "end": 36, "step": 1},
            {"start": 42, "end": 264, "step": 6},
        ],
        "max_hours_by_init": {
            "default": 36,
            "00": 264, "06": 264, "12": 264, "18": 264,
        },
    },
    "ecmwf_hres": {
        "name": "ECMWF HRES",
        "herbie_model": "ifs",
        "herbie_product": "oper",
        "max_forecast_hours": 240,
        "update_frequency_hours": 6,
        "forecast_hour_digits": 3,
        "forecast_hour_schedule": [
            {"start": 3, "end": 144, "step": 3},
            {"start": 150, "end": 240, "step": 6},
        ],
        # 06Z/18Z use ECMWF "scda" stream which only publishes to 144h.
        # Full 240h range only available on 00Z and 12Z ("oper" stream).
        "max_hours_by_init": {
            "00": 240, "12": 240,
            "06": 144, "18": 144,
            "default": 144,
        },
    },
}

repomap = {
    "CACHE_DIR": "cache",
    "TILES_DIR": "cache/tiles",
    "DB_PATH": "cache/jobs.db",
    "HERBIE_SAVE_DIR": os.environ.get("HERBIE_SAVE_DIR", "cache/herbie"),
    "DEFAULT_MODEL": "hrrr",
    "DEFAULT_VARIABLE": "t2m",
    "WEATHER_VARIABLES": WEATHER_VARIABLES,
    "MODELS": MODELS,
    "HEAD_REQUEST_TIMEOUT_SECONDS": 10,
    "HOURS_TO_CHECK_FOR_RUNS": 27,
    "FILELOCK_TIMEOUT_SECONDS": 30,
    "TILING_REGIONS": {
        "ne": {
            "name": "Northeast US (Expanded)",
            "lat_min": 33.0,
            "lat_max": 47.0,
            "lon_min": -88.0,
            "lon_max": -66.0,
            "default_resolution_deg": 0.1,
            "stats": ["mean"],
        }
    },
}

def get_tile_resolution(region_id: str, model_id: str) -> float:
    """Return tile resolution for a model+region, with per-model override support."""
    model_cfg = MODELS.get(model_id, {})
    if "tile_resolution_deg" in model_cfg:
        return model_cfg["tile_resolution_deg"]
    return repomap["TILING_REGIONS"].get(region_id, {}).get("default_resolution_deg", 0.1)


if not os.path.exists(repomap["CACHE_DIR"]):
    os.makedirs(repomap["CACHE_DIR"])
