import os
import pytest
import pytz
from datetime import datetime, timedelta
import requests
from flask import Flask
from io import BytesIO
from unittest.mock import patch  # Import patch

# Import the functions we want to test
from app import get_latest_hrrr_run, fetch_grib, forecast, app, index, HRRR_URL
from utils import download_file, fetch_county_shapefile
from config import repomap
from plotting import create_plot, create_forecast_gif

# --- Utility Functions ---

def create_test_app():
    """Create a test Flask app."""
    test_app = Flask(__name__)
    test_app.config['TESTING'] = True
    # Import routes
    test_app.add_url_rule('/', view_func=index)
    test_app.add_url_rule('/forecast', view_func=forecast)
    return test_app

@pytest.fixture
def test_client():
    """Create a test client for the Flask app."""
    test_app = create_test_app()
    with test_app.test_client() as client:
        with test_app.app_context():
            yield client

# --- Unit Tests for utils.py ---

def test_fetch_county_shapefile(tmpdir):
    """Test downloading and extracting county shapefile."""
    cache_dir = str(tmpdir)
    county_zip = os.path.join(cache_dir, "cb_2018_us_county_20m.zip")
    county_dir = os.path.join(cache_dir, "county_shapefile")
    county_shp = os.path.join(county_dir, "cb_2018_us_county_20m.shp")
    
    result_shp = fetch_county_shapefile(cache_dir)
    
    assert os.path.exists(county_shp)
    assert result_shp == county_shp

# --- Existing Tests (Review and Adjust) ---

def test_real_hrrr_availability():
    """Test actual HRRR server availability and time conversion"""
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # Check date string format (YYYYMMDD)
    assert len(date_str) == 8
    assert date_str.isdigit()
    
    # Check init hour format (HH)
    assert len(init_hour) == 2
    assert init_hour.isdigit()
    assert 0 <= int(init_hour) <= 23
    
    # Convert UTC time to Eastern Time
    utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M:%S")
    utc = pytz.UTC.localize(utc_time)
    eastern = pytz.timezone('America/New_York')
    est_time = utc.astimezone(eastern)
    
    print(f"\nHRRR Run Information:")
    print(f"UTC Time: {utc.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Eastern Time: {est_time.strftime('%Y-%m-%d %I:%M %p %Z')}")
    
    # Verify the HRRR file is actually available
    response = requests.head(HRRR_URL) # Removed HRRR_URL since it's a global constant
    assert response.status_code == 200, f"HRRR file not available at {HRRR_URL}" # Removed HRRR_URL since it's a global constant
    
    # Get file size in MB if available
    if 'content-length' in response.headers: # Removed HRRR_URL since it's a global constant
       size_mb = int(response.headers['content-length']) / (1024 * 1024) # Removed HRRR_URL since it's a global constant
       print(f"File size: {size_mb:.1f} MB") # Removed HRRR_URL since it's a global constant
    
    # The test now focuses on time conversion and HRRR run info, not URL availability
    pass

def test_real_grib_download():
    """Test actual GRIB file downloading and verification"""
    # Fetch the GRIB file
    grib_path = fetch_grib()
    
    # Verify the file exists
    assert os.path.exists(grib_path), f"GRIB file not found at {grib_path}"
    
    # Check file size (should be at least 100KB for a filtered GRIB2 file)
    size_kb = os.path.getsize(grib_path) / 1024
    print(f"\nDownloaded GRIB file: {grib_path}")
    print(f"File size: {size_kb:.1f} KB")
    
    assert size_kb > 100, f"GRIB file seems too small ({size_kb:.1f} KB)"

def test_latest_hrrr_info():
    """Test and display information about the latest available HRRR run"""
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # Convert to Eastern Time for display
    utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M:%S")
    utc = pytz.UTC.localize(utc_time)
    eastern = pytz.timezone('America/New_York')
    est_time = utc.astimezone(eastern)

    # These are for me to read when I run the full command with full printing.
    print("\nLatest HRRR Run Information:")
    print(f"Date: {date_str}")
    print(f"Initialization Hour (UTC): {init_hour}Z")
    print(f"UTC Time: {utc.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"Eastern Time: {est_time.strftime('%Y-%m-%d %I:%M %p %Z')}")
    
    # Verify the format and values
    assert len(date_str) == 8, "Date string should be 8 characters (YYYYMMDD)"
    assert date_str.isdigit(), "Date string should be all digits"
    assert len(init_hour) == 2, "Init hour should be 2 characters (HH)"
    assert 0 <= int(init_hour) <= 23, "Init hour should be between 00 and 23"

def test_create_forecast_gif_success():
    """Test successful creation of an animated forecast GIF."""
    # 1. Get the latest HRRR run information
    date_str, init_hour, init_time = get_latest_hrrr_run()
    
    # 2. Get GRIB files for multiple forecast hours
    grib_paths = []
    for hour in range(1, 4):  # Test with 3 hours instead of 12 for speed
        grib_filename = os.path.join(repomap["CACHE_DIR"], 
                                   f"{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{hour:02d}.grib2")
        if not os.path.exists(grib_filename):
            fetch_grib(hour)
        grib_paths.append(grib_filename)

    # 3. Create the animated GIF
    gif_buffer = create_forecast_gif(grib_paths, init_time, repomap["CACHE_DIR"], duration=500)

    # 4. Verify the result is a BytesIO object
    assert isinstance(gif_buffer, BytesIO)

    # 5. Verify it contains a valid GIF
    gif_buffer.seek(0)
    try:
        from PIL import Image
        img = Image.open(gif_buffer)
        assert img.format == 'GIF'
        # Count frames
        frames = 0
        try:
            while True:
                img.seek(img.tell() + 1)
                frames += 1
        except EOFError:
            pass
        assert frames == len(grib_paths) - 1, f"Expected {len(grib_paths)} frames, got {frames + 1}"
    except Exception as e:
        pytest.fail(f"Failed to verify GIF: {e}")

def test_create_plot_success():
    """Test successful creation of a plot using real data."""
    # 1. Get the latest HRRR run information
    date_str, init_hour, init_time = get_latest_hrrr_run()
    forecast_hour = "01"  # Use 1-hour forecast

    # 2. Construct the GRIB filename
    grib_filename = os.path.join(repomap["CACHE_DIR"], f"{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2")

    # 3. Download the GRIB file (if it doesn't exist)
    if not os.path.exists(grib_filename):
        fetch_grib(forecast_hour)

    # 4. Call create_plot
    image_buffer = create_plot(grib_filename, init_time, forecast_hour, repomap["CACHE_DIR"])

    # 5. Assert that the result is a BytesIO object (i.e., a PNG image)
    assert isinstance(image_buffer, BytesIO)

    # 6. Optionally, you can add more checks to validate the image content
    #    For example, check the file size or try to open it as an image
    image_buffer.seek(0)  # Reset the buffer position to the beginning
    try:
        from PIL import Image
        img = Image.open(image_buffer)
        img.verify()  # Verify that it's a valid image
    except Exception as e:
        pytest.fail(f"Failed to open or verify the image: {e}")
