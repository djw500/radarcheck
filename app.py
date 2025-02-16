import os
from io import BytesIO
from datetime import datetime, timedelta

import requests
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from flask import Flask, send_file, render_template_string, redirect, url_for
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
    # HRRR runs every hour but has ~2 hour delay, so start checking 3 hours ago
    for hours_ago in range(3, 6):
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
            model_time = datetime(
                year=check_time.year,
                month=check_time.month,
                day=check_time.day,
                hour=int(init_hour),
                minute=0,
                second=0,
                tzinfo=pytz.UTC
            )
            return date_str, init_hour, model_time.strftime("%Y-%m-%d %H:%M:%S")
    
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
           f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2&"
           f"dir=%2Fhrrr.{date_str}%2Fconus&"
           f"{repomap['HRRR_VARS']}"
           f"leftlon={repomap['HRRR_LON_MIN']}&rightlon={repomap['HRRR_LON_MAX']}&toplat={repomap['HRRR_LAT_MAX']}&bottomlat={repomap['HRRR_LAT_MIN']}&")
    filename = os.path.join(repomap["CACHE_DIR"], f"{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2")
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

@app.route("/frame/<int:hour>")
def get_frame(hour):
    """Serve a single forecast frame."""
    try:
        if not 1 <= hour <= 24:
            return "Invalid forecast hour", 400
            
        # Format hour as two digits
        hour_str = f"{hour:02d}"
        
        # Get or create the frame
        grib_path = fetch_grib(hour_str)
        image_buffer = create_plot(grib_path, init_time, hour_str, repomap["CACHE_DIR"])
        
        return send_file(image_buffer, mimetype="image/png")
    except Exception as e:
        return str(e), 500

@app.route("/forecast")
def forecast():
    """Legacy endpoint for GIF - redirect to main page"""
    return redirect(url_for('index'))

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
            body { margin: 0; padding: 20px; font-family: Arial, sans-serif; background: #f0f0f0; }
            .container { max-width: 1200px; margin: 0 auto; }
            header { background: #004080; color: white; padding: 1em; margin-bottom: 20px; border-radius: 5px; }
            .forecast-container { 
                background: white;
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .controls {
                margin: 20px 0;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            #timeSlider {
                flex-grow: 1;
            }
            .loading {
                display: none;
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background: rgba(255,255,255,0.9);
                padding: 20px;
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }
            footer { margin-top: 20px; text-align: center; color: #666; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>HRRR Forecast Visualization</h1>
            </header>
            <div class="forecast-container">
                <div class="controls">
                    <button id="playButton">Play</button>
                    <input type="range" id="timeSlider" min="1" max="24" value="1">
                    <span id="timeDisplay">Hour +1</span>
                </div>
                <div style="position: relative;">
                    <img id="forecastImage" src="/frame/1" alt="HRRR Forecast Plot" style="width: 100%; height: auto;">
                    <div id="loading" class="loading">Loading...</div>
                </div>
            </div>
            <footer>&copy; 2025 Weather App</footer>
        </div>
        
        <script>
            const slider = document.getElementById('timeSlider');
            const timeDisplay = document.getElementById('timeDisplay');
            const forecastImage = document.getElementById('forecastImage');
            const loading = document.getElementById('loading');
            const playButton = document.getElementById('playButton');
            
            let isPlaying = false;
            let playInterval;
            
            // Preload images
            const images = new Array(24);
            function preloadImage(hour) {
                return new Promise((resolve, reject) => {
                    const img = new Image();
                    img.onload = () => {
                        images[hour-1] = img;
                        resolve();
                    };
                    img.onerror = reject;
                    img.src = `/frame/${hour}`;
                });
            }
            
            // Preload first few frames immediately
            Promise.all([1,2,3].map(preloadImage)).then(() => {
                // Then load the rest in background
                for (let hour = 4; hour <= 24; hour++) {
                    preloadImage(hour);
                }
            });
            
            function updateDisplay(hour) {
                timeDisplay.textContent = `Hour +${hour}`;
                if (images[hour-1]) {
                    forecastImage.src = images[hour-1].src;
                } else {
                    forecastImage.src = `/frame/${hour}`;
                }
            }
            
            slider.addEventListener('input', () => {
                const hour = parseInt(slider.value);
                updateDisplay(hour);
            });
            
            playButton.addEventListener('click', () => {
                if (isPlaying) {
                    clearInterval(playInterval);
                    playButton.textContent = 'Play';
                } else {
                    playInterval = setInterval(() => {
                        let hour = parseInt(slider.value);
                        hour = hour >= 24 ? 1 : hour + 1;
                        slider.value = hour;
                        updateDisplay(hour);
                    }, 500);
                    playButton.textContent = 'Pause';
                }
                isPlaying = !isPlaying;
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
