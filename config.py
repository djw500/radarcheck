import os

repomap = {
    "CACHE_DIR": "cache",
    "HRRR_FILE_PREFIX": "hrrr.t",
    "HRRR_FILE_SUFFIX": "z.wrfsfcf",
    "HRRR_VARS": "var_REFC=on&",
    "HRRR_LAT_MIN": "39.0",
    "HRRR_LAT_MAX": "40.5",
    "HRRR_LON_MIN": "-76",
    "HRRR_LON_MAX": "-74",
    "COUNTY_ZIP_NAME": "cb_2018_us_county_20m.zip",
    "COUNTY_DIR_NAME": "county_shapefile",
    "COUNTY_SHP_NAME": "cb_2018_us_county_20m.shp"
}

if not os.path.exists(repomap["CACHE_DIR"]):
    os.makedirs(repomap["CACHE_DIR"])
