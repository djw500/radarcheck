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

def get_latest_hrrr_run():
    """Find the most recent HRRR run available."""
    now = datetime.utcnow()
    # HRRR runs every hour, but we'll check the last 3 hours in case of delays
    for hours_ago in range(3):
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        
        # Check if the directory exists
        url = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.{date_str}/"
        response = requests.head(url)
        if response.status_code == 200:
            return date_str, init_hour, check_time.strftime("%Y-%m-%d %H:%M")
    
    raise Exception("Could not find a recent HRRR run")

# Get the most recent HRRR run
date_str, init_hour, init_time = get_latest_hrrr_run()
forecast_hour = "01"  # Use 1-hour forecast for most recent data

# Construct URL for HRRR surface forecast file
HRRR_URL = (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/hrrr.{date_str}/conus/"
            f"hrrr.t{init_hour}z.wrfsfcf{forecast_hour}.grib2")

# Local cache filenames
GRIB_FILENAME = os.path.join(CACHE_DIR, f"hrrr.t{init_hour}z.wrfsfcf{forecast_hour}.grib2")
COUNTY_ZIP = os.path.join(CACHE_DIR, "cb_2018_us_county_20m.zip")
COUNTY_DIR = os.path.join(CACHE_DIR, "county_shapefile")
COUNTY_SHP = os.path.join(COUNTY_DIR, "cb_2018_us_county_20m.shp")

# --- Utility Functions ---

def download_file(url, local_path):
    """Download a file if it doesn't exist in cache."""
    if not os.path.exists(local_path):
        print(f"Downloading from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"Downloaded: {local_path}")
    else:
        print(f"Using cached file: {local_path}")

def fetch_grib():
    """Download and cache the HRRR GRIB file."""
    download_file(HRRR_URL, GRIB_FILENAME)
    return GRIB_FILENAME

def fetch_county_shapefile():
    """Download and extract the county shapefile if needed."""
    url_county = "https://www2.census.gov/geo/tiger/GENZ2018/shp/cb_2018_us_county_20m.zip"
    download_file(url_county, COUNTY_ZIP)
    if not os.path.exists(COUNTY_DIR):
        with zipfile.ZipFile(COUNTY_ZIP, "r") as zip_ref:
            zip_ref.extractall(COUNTY_DIR)
        print("Extracted county shapefile.")
    else:
        print("County shapefile already extracted.")
    return COUNTY_SHP

def get_local_time_text(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    utc_zone = pytz.timezone("UTC")
    eastern_zone = pytz.timezone("America/New_York")
    utc_time = utc_zone.localize(utc_time)
    local_time = utc_time.astimezone(eastern_zone)
    return local_time.strftime("Forecast valid at: %Y-%m-%d %I:%M %p %Z")

def create_plot():
    # --- Step 1: Download and open GRIB file ---
    grib_path = fetch_grib()
    # Try using filter by shortName for 2m temperature. If that fails, fallback to surface.
    try:
        ds = xr.open_dataset(grib_path, engine="cfgrib", filter_by_keys={'shortName': '2t'})
        print("Loaded dataset with filter_by_keys={'shortName': '2t'}")
    except Exception as e:
        print("Error with shortName filter, trying typeOfLevel filter.")
        ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={'filter_by_keys': {'typeOfLevel': 'surface', 'stepType': 'accum'}})
    
    # Determine temperature variable (or use snowfall variable if available)
    if "t2m" in ds.data_vars:
        temp_var = "t2m"
        temp_celsius = ds[temp_var] - 273.15
        data_to_plot = temp_celsius
        var_label = "2-m Temperature (°C)"
    elif "TMP" in ds.data_vars:
        temp_var = "TMP"
        temp_celsius = ds[temp_var] - 273.15
        data_to_plot = temp_celsius
        var_label = "2-m Temperature (°C)"
    elif "sdwe" in ds.data_vars:
        # Assume sdwe is snow water equivalent in mm; convert to cm of snow
        sdwe = ds["sdwe"]
        snowfall_cm = sdwe / 10.0
        snowfall_in = snowfall_cm / 2.54  # inches
        data_to_plot = snowfall_in
        var_label = "Snowfall (inches)"
    else:
        # Fallback to first variable
        var_label = list(ds.data_vars.keys())[0]
        data_to_plot = ds[var_label]

    # --- Step 2: Subset the data for Philadelphia region ---
    # Desired bounds (you can adjust as needed)
    desired_lat_min, desired_lat_max = 39.0, 40.5
    desired_lon_min, desired_lon_max = -76, -74.0

    # Check file coordinate system (convert if necessary)
    lon = data_to_plot.longitude
    if float(lon.min()) >= 0:
        philly_lon_min = 360 + desired_lon_min
        philly_lon_max = 360 + desired_lon_max
        print("Adjusted longitude bounds to 0-360:", philly_lon_min, philly_lon_max)
    else:
        philly_lon_min = desired_lon_min
        philly_lon_max = desired_lon_max

    def get_subset(data):
        return data.where(
            (data.latitude >= desired_lat_min) & (data.latitude <= desired_lat_max) &
            (data.longitude >= philly_lon_min) & (data.longitude <= philly_lon_max),
            drop=True
        )

    subset = get_subset(data_to_plot)
    print("Subset shape:", subset.shape)

    # --- Step 3: Create the plot ---
    fig = plt.figure(figsize=(8, 6))
    ax = plt.axes(projection=ccrs.PlateCarree())
    subset.plot.pcolormesh(
        ax=ax,
        x="longitude",
        y="latitude",
        cmap="coolwarm",
        add_colorbar=True,
        transform=ccrs.PlateCarree()
    )
    ax.set_title(f"HRRR Forecast: {var_label}\nInit: {init_time}, fxx={forecast_hour}")
    ax.coastlines(resolution='50m')
    gl = ax.gridlines(draw_labels=True, linewidth=0.5, color="gray", alpha=0.5, linestyle="--")
    gl.top_labels = False
    gl.right_labels = False

    # --- Step 4: Overlay county boundaries ---
    shp_path = fetch_county_shapefile()
    counties = gpd.read_file(shp_path)
    # Correct the subset longitude from 0-360 to -180 to 180 for plotting
    subset_corrected = subset.assign_coords(
        longitude=(((subset.longitude + 180) % 360) - 180)
    )
    bbox = box(
        float(subset_corrected.longitude.min()),
        float(subset_corrected.latitude.min()),
        float(subset_corrected.longitude.max()),
        float(subset_corrected.latitude.max())
    )
    print("Plot bounding box:", bbox)
    counties_philly = counties[counties.intersects(bbox)]
    ax.add_geometries(
        counties_philly.geometry,
        crs=ccrs.PlateCarree(),
        edgecolor='gray',
        facecolor='none',
        linewidth=1.0
    )

    # Mark a specific region of interest (e.g., center of Philadelphia)
    roi_lat = 40.04877
    roi_lon = -75.38903
    ax.plot(roi_lon, roi_lat, marker='*', markersize=15, color='gold', transform=ccrs.PlateCarree())

    # Save plot to a BytesIO buffer
    buf = BytesIO()
    plt.savefig(buf, format="PNG", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

# --- Flask Endpoints ---

@app.route("/forecast")
def forecast():
    try:
        img_buf = create_plot()
    except Exception as e:
        return f"Error generating plot: {e}", 500
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
