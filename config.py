import os


WEATHER_VARIABLES = {
    "refc": {
        "nomads_params": ["var_REFC"],
        "level_params": [],
        "display_name": "Radar Reflectivity",
        "units": "dBZ",
        "short_name": "refc",
        "colormap": "nws_reflectivity",
        "vmin": 5,
        "vmax": 75,
        "category": "precipitation",
        "model_exclusions": ["gfs", "nbm", "icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "asnow": {
        "nomads_params": ["var_ASNOW"],
        "level_params": [],
        "display_name": "Accumulated Snowfall",
        "units": "in",
        "short_name": "asnow",
        "colormap": "snow_accumulation",
        "vmin": 0,
        "vmax": 24,
        "category": "winter",
        "conversion": "m_to_in",
        "is_accumulation": True,
        "model_exclusions": ["nam_nest", "gfs", "icon", "ecmwf_hres", "ecmwf_eps"],
        "unit_conversions_by_units": {
            "m": "m_to_in",
            "kg m-2": "kg_m2_to_in",
            "kg m**-2": "kg_m2_to_in",
        },
    },
    "csnow": {
        "nomads_params": ["var_CSNOW"],
        "level_params": ["lev_surface=on"],
        "display_name": "Categorical Snow (Yes/No)",
        "units": "bool",
        "short_name": "csnow",
        "colormap": "snow_depth",
        "vmin": 0,
        "vmax": 1,
        "category": "winter",
        "preferred_step_type": "instant",
        "model_exclusions": ["icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "snod": {
        "nomads_params": ["var_SNOD"],
        "level_params": [],
        "display_name": "Snow Depth",
        "units": "in",
        "short_name": "snod",
        "colormap": "snow_depth",
        "vmin": 0,
        "vmax": 36,
        "category": "winter",
        "conversion": "m_to_in",
        "dwd_var": "h_snow",
        "model_exclusions": ["ecmwf_hres", "ecmwf_eps"],
    },
    "apcp": {
        "nomads_params": ["var_APCP"],
        "level_params": [],
        "display_name": "Accumulated Precipitation",
        "units": "in",
        "short_name": "apcp",
        "colormap": "precip_accumulation",
        "vmin": 0,
        "vmax": 6,
        "category": "precipitation",
        "conversion": "kg_m2_to_in",
        "dwd_var": "tot_prec",
        # Common GRIB short names across centers (e.g., ECMWF uses 'tp')
        "source_short_names": ["tp", "apcp"],
        # Prefer conversion based on source units when available
        # e.g., ECMWF 'tp' has units 'm' (meters of water)
        "unit_conversions_by_units": {
            "m": "m_to_in",
            "kg m-2": "kg_m2_to_in",
            "kg m**-2": "kg_m2_to_in",
        },
        "is_accumulation": True,
    },
    "prate": {
        "nomads_params": ["var_PRATE"],
        "level_params": [],
        "display_name": "Precipitation Rate",
        "units": "in/hr",
        "short_name": "prate",
        "colormap": "precip_rate",
        "vmin": 0,
        "vmax": 2,
        "category": "precipitation",
        "conversion": "kg_m2_s_to_in_hr",
        "preferred_step_type": "instant",
        "model_exclusions": ["icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "t2m": {
        "nomads_params": ["var_TMP"],
        "level_params": ["lev_2_m_above_ground=on"],
        "display_name": "2m Temperature",
        "units": "°F",
        "short_name": "t2m",
        "colormap": "temperature",
        "vmin": -40,
        "vmax": 110,
        "category": "temperature",
        "conversion": "k_to_f",
        "dwd_var": "t_2m",
        "source_short_names": ["t2m", "2t", "tmp"],
        "unit_conversions_by_units": {
            "K": "k_to_f",
            "degC": "c_to_f",
            "°C": "c_to_f",
            "degF": None,
            "°F": None
        },
    },
    "dpt": {
        "nomads_params": ["var_DPT"],
        "level_params": ["lev_2_m_above_ground=on"],
        "display_name": "2m Dew Point",
        "units": "°F",
        "short_name": "dpt",
        "colormap": "temperature",
        "vmin": -40,
        "vmax": 80,
        "category": "temperature",
        "conversion": "k_to_f",
        "source_short_names": ["dpt", "2d"],
        "unit_conversions_by_units": {
            "K": "k_to_f",
            "degC": "c_to_f",
            "°C": "c_to_f",
            "degF": None,
            "°F": None
        },
        "model_exclusions": ["icon"],
    },
    "rh": {
        "nomads_params": ["var_RH"],
        "level_params": ["lev_2_m_above_ground=on"],
        "display_name": "Relative Humidity",
        "units": "%",
        "short_name": "rh",
        "colormap": "humidity",
        "vmin": 0,
        "vmax": 100,
        "category": "temperature",
        "model_exclusions": ["icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "wind_10m": {
        "nomads_params": ["var_UGRD", "var_VGRD"],
        "level_params": ["lev_10_m_above_ground=on"],
        "display_name": "10m Wind Speed",
        "units": "mph",
        "short_name": "wind_10m",
        "colormap": "wind",
        "vmin": 0,
        "vmax": 80,
        "category": "wind",
        "conversion": "m_s_to_mph",
        "vector_components": ["ugrd", "vgrd"],
        # Accept common shortName variants from GRIB files
        "vector_component_candidates": [
            ["ugrd", "10u", "u10", "UGRD"],  # u-component
            ["vgrd", "10v", "v10", "VGRD"],  # v-component
        ],
        # Some centers (e.g., NBM) provide 10m wind magnitude as WIND
        "magnitude_short_names": ["wind", "WIND"],
        "model_exclusions": ["icon"],
    },
    "gust": {
        "nomads_params": ["var_GUST"],
        "level_params": ["lev_10_m_above_ground=on"],
        "display_name": "Wind Gusts",
        "units": "mph",
        "short_name": "gust",
        "colormap": "wind",
        "vmin": 0,
        "vmax": 90,
        "category": "wind",
        "conversion": "m_s_to_mph",
        "model_exclusions": ["icon"],
    },
    "cape": {
        "nomads_params": ["var_CAPE"],
        "level_params": ["lev_surface=on"],
        "display_name": "CAPE",
        "units": "J/kg",
        "short_name": "cape",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 4000,
        "category": "severe",
        "model_exclusions": ["icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "hlcy": {
        "nomads_params": ["var_HLCY"],
        "level_params": ["lev_0-3_km_above_ground=on"],
        "display_name": "Storm Relative Helicity",
        "units": "m²/s²",
        "short_name": "hlcy",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 600,
        "category": "severe",
        "model_exclusions": ["nam_nest", "gfs", "nbm", "icon", "ecmwf_hres", "ecmwf_eps"],
    },
    "hail": {
        "nomads_params": ["var_HAIL"],
        "level_params": [],
        "display_name": "Hail",
        "units": "in",
        "short_name": "hail",
        "colormap": "severe",
        "vmin": 0,
        "vmax": 3,
        "category": "severe",
        "model_exclusions": ["nam_nest", "gfs", "nbm", "icon", "ecmwf_hres", "ecmwf_eps"],
    },
}

WEATHER_CATEGORIES = {
    "precipitation": "Precipitation & Storms",
    "winter": "Winter Weather",
    "wind": "Wind",
    "temperature": "Temperature & Moisture",
    "severe": "Severe Weather",
    "surface": "Surface",
}

MODELS = {
    "hrrr": {
        "name": "HRRR",
        "max_forecast_hours": 48,  # Synoptic runs; non-synoptic are 18h
        "update_frequency_hours": 1,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl",
        "dir_pattern": "%2Fhrrr.{date_str}%2Fconus",
        "file_pattern": "hrrr.t{init_hour}z.wrfsfcf{forecast_hour}.grib2",
        "availability_check_var": "var_REFC",
        "forecast_hour_digits": 2,
        # HRRR: synoptic runs (00,06,12,18z) have 48h, others have 18h
        "max_hours_by_init": {"00": 48, "06": 48, "12": 48, "18": 48, "default": 18},
    },
    "nam_nest": {
        "name": "NAM 3km CONUS",
        "max_forecast_hours": 60,
        "update_frequency_hours": 6,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_nam_conusnest.pl",
        "dir_pattern": "%2Fnam.{date_str}",
        "file_pattern": "nam.t{init_hour}z.conusnest.hiresf{forecast_hour}.tm00.grib2",
        "availability_check_var": "var_REFC",
        "forecast_hour_digits": 2,
    },
    "nam_12km": {
        "name": "NAM 12km",
        "max_forecast_hours": 84,
        "update_frequency_hours": 6,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_nam.pl",
        "dir_pattern": "%2Fnam.{date_str}",
        "file_pattern": "nam.t{init_hour}z.awphys{forecast_hour}.tm00.grib2",
        "availability_check_var": "var_REFC",
        "forecast_hour_digits": 2,
    },
    "rap": {
        "name": "RAP",
        "max_forecast_hours": 21,
        "update_frequency_hours": 1,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_rap.pl",
        "dir_pattern": "%2Frap.{date_str}",
        "file_pattern": "rap.t{init_hour}z.awp130pgrbf{forecast_hour}.grib2",
        "availability_check_var": "var_TMP",
        "forecast_hour_digits": 2,
    },
    "gfs": {
        "name": "GFS",
        "max_forecast_hours": 384,
        "update_frequency_hours": 6,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",
        "dir_pattern": "%2Fgfs.{date_str}%2F{init_hour}%2Fatmos",
        "file_pattern": "gfs.t{init_hour}z.pgrb2.0p25.f{forecast_hour}",
        # Some installations provide hourly pgrb2b stream; enable detection+use if present
        "file_pattern_hourly": "gfs.t{init_hour}z.pgrb2b.0p25.f{forecast_hour}",
        "availability_check_var": "var_TMP",
        "forecast_hour_digits": 3,
        # Attempt hourly for first 48h when supported
        "hourly_override_first_hours": 48,
        # GFS 0.25° provides 3-hourly output to 240h, then 6-hourly to 384h
        "forecast_hour_schedule": [
            {"start": 3, "end": 240, "step": 3},
            {"start": 246, "end": 384, "step": 6},
        ],
    },
    "nbm": {
        "name": "National Blend (NBM)",
        "max_forecast_hours": 264,
        "update_frequency_hours": 1,
        "nomads_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_blend.pl",
        "dir_pattern": "%2Fblend.{date_str}%2F{init_hour}%2Fcore",
        "file_pattern": "blend.t{init_hour}z.core.f{forecast_hour}.co.grib2",
        "availability_check_var": "var_TMP",
        "forecast_hour_digits": 3,
        # NBM: hourly 1-36, then 6-hourly 42-264 (some vars like TMP are 3-hourly, but ASNOW is 6-hourly)
        "forecast_hour_schedule": [
            {"start": 1, "end": 36, "step": 1},
            {"start": 42, "end": 264, "step": 6},
        ],
    },
    "icon": {
        "name": "ICON (DWD)",
        "max_forecast_hours": 180,
        "update_frequency_hours": 6,
        "source": "dwd",
        "nomads_url": "https://opendata.dwd.de", # Base URL for DWD
        "dir_pattern": "weather/nwp/icon/grib/{init_hour}/{dwd_var}",
        "file_pattern": "icon_global_icosahedral_single-level_{date_str}{init_hour}_{forecast_hour}_{dwd_var_upper}.grib2.bz2",
        "forecast_hour_digits": 3,
        "availability_check_var": "t_2m", # Used for dwd_var in check
    },
    # ECMWF integrations via Herbie (Open Data)
    "ecmwf_hres": {
        "name": "ECMWF HRES",
        "max_forecast_hours": 240,
        "update_frequency_hours": 6,  # 00, 06, 12, 18
        "source": "herbie",
        "dataset": "ecmwf-high-resolution-forecast",
        "forecast_hour_digits": 3,
        "availability_check_var": "t2m",
        # Hourly data often available to ~90h; use 48h to match requirement
        "hourly_override_first_hours": 48,
        "forecast_hour_schedule": [
            {"start": 3, "end": 144, "step": 3},
            {"start": 150, "end": 240, "step": 6},
        ],
        # Placeholders
        "nomads_url": "",
        "dir_pattern": "",
        "file_pattern": "",
    },
    "ecmwf_eps": {
        "name": "ECMWF EPS",
        "max_forecast_hours": 360,
        "update_frequency_hours": 6,
        "source": "herbie",
        "dataset": "ecmwf-ensemble-forecast",
        "forecast_hour_digits": 3,
        "availability_check_var": "t2m",
        # Placeholders
        "nomads_url": "",
        "dir_pattern": "",
        "file_pattern": "",
    },
}

MAP_LAYERS = {
    "observed_radar": {
        "name": "Observed Radar (NEXRAD)",
        "type": "tile",
        "url": "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "nexrad-n0q-900913/{z}/{x}/{y}.png",
        "attribution": "Weather data © Iowa Environmental Mesonet",
        "max_zoom": 12,
        "min_zoom": 4,
        "opacity": 0.7,
    },
    "goes_ir": {
        "name": "GOES-16 IR Satellite",
        "type": "tile",
        "url": "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "goes-16-ch13-0p5km/{z}/{x}/{y}.png",
        "attribution": "Satellite data © NOAA GOES",
        "max_zoom": 12,
        "min_zoom": 4,
        "opacity": 0.6,
    },
}

repomap = {
    "CACHE_DIR": "cache",
    "GRIB_CACHE_DIR": "cache/gribs",
    "TILES_DIR": "cache/tiles",
    "DEFAULT_MODEL": "hrrr",
    "DEFAULT_VARIABLE": "refc",
    "WEATHER_VARIABLES": WEATHER_VARIABLES,
    "WEATHER_CATEGORIES": WEATHER_CATEGORIES,
    "MODELS": MODELS,
    "MAP_LAYERS": MAP_LAYERS,
    "COUNTY_ZIP_NAME": "cb_2018_us_county_20m.zip",
    "COUNTY_DIR_NAME": "county_shapefile",
    "COUNTY_SHP_NAME": "cb_2018_us_county_20m.shp",
    "MAX_RUNS_TO_KEEP": 24,  # Number of model runs to keep in cache (24 = last 24 hours)

    # Network settings
    "DOWNLOAD_TIMEOUT_SECONDS": 60,
    "HEAD_REQUEST_TIMEOUT_SECONDS": 10,
    "MAX_DOWNLOAD_RETRIES": 3,
    "RETRY_DELAY_SECONDS": 2,

    # File validation
    "MIN_GRIB_FILE_SIZE_BYTES": 1000,
    "MIN_PNG_FILE_SIZE_BYTES": 1000,

    # Cache settings
    "CACHE_REFRESH_INTERVAL_MINUTES": 15,

    # Model discovery
    "HOURS_TO_CHECK_FOR_RUNS": 27,

    # File locking
    "FILELOCK_TIMEOUT_SECONDS": 30,

    # Parallel download settings
    "PARALLEL_DOWNLOAD_WORKERS": 1,

    # Map tile generation settings
    "GENERATE_STATIC_IMAGES": True,
    "GENERATE_MAP_TILES": False,
    "GENERATE_VECTOR_CONTOURS": False,
    "TILE_MIN_ZOOM": 4,
    "TILE_MAX_ZOOM": 10,

    # Download regions for centralized GRIB fetching
    "DOWNLOAD_REGIONS": {
        "conus": {
            "id": "conus",
            "name": "Continental US",
            "lat_min": 20.0,
            "lat_max": 55.0,
            "lon_min": -135.0,
            "lon_max": -60.0,
        }
    },

    # Region tiling (grid statistics) configuration
    "TILING_REGIONS": {
        # Expanded Northeast region to include Charlotte (NC) and Nashville (TN)
        # Previous bounds: lat 38–47, lon -80–-66
        # New bounds:     lat 33–47, lon -88–-66
        # This minimally expands south and west to cover both metros
        "ne": {
            "name": "Northeast US (Expanded)",
            "lat_min": 33.0,
            "lat_max": 47.0,
            "lon_min": -88.0,
            "lon_max": -66.0,
            "default_resolution_deg": 0.1,
            # Generate means only to reduce storage
            "stats": ["mean"],
        }
    },

    # Location configurations
    "LOCATIONS": {
        "philly": {
            "name": "Philadelphia",
            "center_lat": 40.04877,
            "center_lon": -75.38903,
            "zoom": 1.5,
            "lat_min": 38.8,
            "lat_max": 40.7,
            "lon_min": -76.5,
            "lon_max": -73.5,
        },
        "nyc": {
            "name": "New York City",
            "center_lat": 40.7128,
            "center_lon": -74.0060,
            "zoom": 1.5,
            "lat_min": 39.5,
            "lat_max": 41.5,
            "lon_min": -75.0,
            "lon_max": -72.0,
        },
        "boston": {
            "name": "Boston",
            "center_lat": 42.3601,
            "center_lon": -71.0589,
            "zoom": 1.5,
            "lat_min": 41.0,
            "lat_max": 43.5,
            "lon_min": -72.5,
            "lon_max": -69.5,
        },
        "dc": {
            "name": "Washington DC",
            "center_lat": 38.9072,
            "center_lon": -77.0369,
            "zoom": 1.5,
            "lat_min": 37.5,
            "lat_max": 40.0,
            "lon_min": -78.5,
            "lon_max": -75.5,
        },
        "chicago": {
            "name": "Chicago",
            "center_lat": 41.8781,
            "center_lon": -87.6298,
            "zoom": 1.5,
            "lat_min": 40.5,
            "lat_max": 43.0,
            "lon_min": -89.5,
            "lon_max": -86.0,
        },
        "denver": {
            "name": "Denver",
            "center_lat": 39.7392,
            "center_lon": -104.9903,
            "zoom": 1.5,
            "lat_min": 38.5,
            "lat_max": 41.0,
            "lon_min": -106.5,
            "lon_max": -103.5,
        },
        "la": {
            "name": "Los Angeles",
            "center_lat": 34.0522,
            "center_lon": -118.2437,
            "zoom": 1.5,
            "lat_min": 32.5,
            "lat_max": 35.5,
            "lon_min": -120.0,
            "lon_max": -116.5,
        },
        "seattle": {
            "name": "Seattle",
            "center_lat": 47.6062,
            "center_lon": -122.3321,
            "zoom": 1.5,
            "lat_min": 46.0,
            "lat_max": 49.0,
            "lon_min": -124.0,
            "lon_max": -121.0,
        },
    },
}

if not os.path.exists(repomap["CACHE_DIR"]):
    os.makedirs(repomap["CACHE_DIR"])
