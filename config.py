import os

repomap = {
    "CACHE_DIR": "cache",
    "HRRR_FILE_PREFIX": "hrrr.t",
    "HRRR_FILE_SUFFIX": "z.wrfsfcf",
    "HRRR_VARS": "var_REFC=on&",
    "COUNTY_ZIP_NAME": "cb_2018_us_county_20m.zip",
    "COUNTY_DIR_NAME": "county_shapefile",
    "COUNTY_SHP_NAME": "cb_2018_us_county_20m.shp",
    
    # Location configurations
    "LOCATIONS": {
        "philly": {
            "name": "Philadelphia",
            "center_lat": 40.04877,
            "center_lon": -75.38903,
            "zoom": 1.5,
            "lat_min": "38.8",
            "lat_max": "40.7",
            "lon_min": "-76.5",
            "lon_max": "-73.5"
        },
        "nyc": {
            "name": "New York City",
            "center_lat": 40.7128,
            "center_lon": -74.0060,
            "zoom": 1.5,
            "lat_min": "39.5",
            "lat_max": "41.5",
            "lon_min": "-75.0",
            "lon_max": "-72.0"
        }
    }
}

if not os.path.exists(repomap["CACHE_DIR"]):
    os.makedirs(repomap["CACHE_DIR"])
