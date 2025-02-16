import os
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

import requests
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from flask import Flask, send_file, render_template_string
from PIL import Image, ImageDraw, ImageFont
import geopandas as gpd
from shapely.geometry import box
import pytz

app = Flask(__name__)

# --- Configuration ---
CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

from utils import download_file, fetch_county_shapefile

def get_latest_hrrr_run():
    """Find the most recent HRRR run available."""
    now = datetime.utcnow()
    # HRRR runs every hour, but we'll check the last 3 hours in case of delays
    for hours_ago in range(3):
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        
        # Check if the file exists using the filter URL
        url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
               f"file=hrrr.t{init_hour}z.wrfsfcf01.grib2&"
               f"dir=%2Fhrrr.{date_str}%2Fconus&"
               f"var_REFC=on")
        response = requests.head(url)
        if response.status_code == 200:
            return date_str, init_hour, check_time.strftime("%Y-%m-%d %H:%M")
    
    raise Exception("Could not find a recent HRRR run")

# Get the most recent HRRR run
date_str, init_hour, init_time = get_latest_hrrr_run()
forecast_hour = "01"  # Use 1-hour forecast for most recent data

# Construct URL for HRRR surface forecast file
HRRR_URL = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
            f"file=hrrr.t{init_hour}z.wrfsfcf{forecast_hour}.grib2&"
            f"dir=%2Fhrrr.{date_str}%2Fconus&"
            f"var_REFC=on&var_TMP=on&var_HGT=on&var_UGRD=on&var_VGRD=on&"  # Request multiple variables
            f"leftlon=-76&rightlon=-74&toplat=40.5&bottomlat=39.0&")  # Specify region

# Local cache filenames
GRIB_FILENAME = os.path.join(CACHE_DIR, f"hrrr.t{init_hour}z.wrfsfcf{forecast_hour}.grib2")
COUNTY_ZIP = os.path.join(CACHE_DIR, "cb_2018_us_county_20m.zip")
COUNTY_DIR = os.path.join(CACHE_DIR, "county_shapefile")
COUNTY_SHP = os.path.join(COUNTY_DIR, "cb_2018_us_county_20m.shp")

# --- Utility Functions ---


def fetch_grib():
    """Download and cache the HRRR GRIB file."""
    download_file(HRRR_URL, GRIB_FILENAME)
    return GRIB_FILENAME

def fetch_county_shapefile():
    """Wrapper to maintain compatibility with existing code"""
    return fetch_county_shapefile(CACHE_DIR)

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

from plotting import create_plot

# --- Flask Endpoints ---

@app.route("/forecast")
def forecast():
    try:
        grib_path = fetch_grib()
        img_buf = create_plot(grib_path, init_time, forecast_hour, CACHE_DIR)
    except Exception as e:
        import traceback
        error_msg = f"""
        <html>
            <body>
                <h1>Error Generating Plot</h1>
                <pre>
Error: {str(e)}

Full Traceback:
{traceback.format_exc()}
                </pre>
            </body>
        </html>
        """
        return error_msg, 500
    return send_file(img_buf, mimetype="image/png")

@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>HRRR Forecast Visualization</title>
      <style>
        body { margin: 0; padding: 0; text-align: center; font-family: Arial, sans-serif; background: #f0f0f0; }
        header { background: #004080; color: white; padding: 1em; }
        img { max-width: 100%; height: auto; }
        footer { background: #004080; color: white; padding: 0.5em; position: fixed; bottom: 0; width: 100%; }
      </style>
    </head>
    <body>
      <header>
        <h1>HRRR Forecast Visualization</h1>
      </header>
      <main>
        <img src="/forecast" alt="HRRR Forecast Plot">
      </main>
      <footer>
        &copy; 2025 Weather App
      </footer>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
