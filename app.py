import os
from io import BytesIO
from datetime import datetime, timedelta

import requests
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from flask import Flask, send_file, render_template_string
from PIL import Image, ImageDraw, ImageFont
import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from flask import Flask, send_file, render_template_string
import geopandas as gpd
from shapely.geometry import box
import pytz

from config import repomap
from utils import download_file, fetch_county_shapefile

app = Flask(__name__)

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
               f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}z.wrfsfcf01.grib2&"
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
            f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2&"
            f"dir=%2Fhrrr.{date_str}%2Fconus&"
            f"{repomap['HRRR_VARS']}"  # Request multiple variables
            f"leftlon={repomap['HRRR_LON_MIN']}&rightlon={repomap['HRRR_LON_MAX']}&toplat={repomap['HRRR_LAT_MAX']}&bottomlat={repomap['HRRR_LAT_MIN']}&")  # Specify region

# Local cache filenames
GRIB_FILENAME = os.path.join(repomap["CACHE_DIR"], f"{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2")
COUNTY_ZIP = os.path.join(repomap["CACHE_DIR"], repomap["COUNTY_ZIP_NAME"])
COUNTY_DIR = os.path.join(repomap["CACHE_DIR"], repomap["COUNTY_DIR_NAME"])
COUNTY_SHP = os.path.join(COUNTY_DIR, repomap["COUNTY_SHP_NAME"])

# --- Utility Functions ---


def fetch_grib(forecast_hour):
    """Download and cache the HRRR GRIB file for a specific forecast hour."""
    url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
           f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour:02d}.grib2&"
           f"dir=%2Fhrrr.{date_str}%2Fconus&"
           f"{repomap['HRRR_VARS']}"
           f"leftlon={repomap['HRRR_LON_MIN']}&rightlon={repomap['HRRR_LON_MAX']}&toplat={repomap['HRRR_LAT_MAX']}&bottomlat={repomap['HRRR_LAT_MIN']}&")
    filename = os.path.join(repomap["CACHE_DIR"], f"{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour:02d}.grib2")
    download_file(url, filename)
    return filename

def fetch_county_shapefile():
    """Wrapper to maintain compatibility with existing code"""
    return fetch_county_shapefile(repomap["CACHE_DIR"])

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

from plotting import create_plot, create_forecast_gif

# --- Flask Endpoints ---

@app.route("/forecast")
def forecast():
    try:
        # Fetch GRIB files for the next 12 hours
        grib_paths = []
        for hour in range(1, 13):
            grib_path = fetch_grib(hour)
            grib_paths.append(grib_path)
        
        # Create animated GIF
        gif_buf = create_forecast_gif(grib_paths, init_time, repomap["CACHE_DIR"], duration=750)
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
    return send_file(gif_buf, mimetype="image/gif")

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
