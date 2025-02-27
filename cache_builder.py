import os
import logging
import argparse
from datetime import datetime, timedelta
import pytz
from filelock import FileLock
import xarray as xr
import requests
from io import BytesIO

from config import repomap
from utils import download_file, fetch_county_shapefile
from plotting import create_plot

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

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

def fetch_grib(date_str, init_hour, forecast_hour, location_config):
    """Download and cache the HRRR GRIB file for a specific forecast hour and location."""
    url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
           f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2&"
           f"dir=%2Fhrrr.{date_str}%2Fconus&"
           f"{repomap['HRRR_VARS']}"
           f"leftlon={location_config['lon_min']}&rightlon={location_config['lon_max']}&"
           f"toplat={location_config['lat_max']}&bottomlat={location_config['lat_min']}&")
    
    location_id = location_config['id']
    filename = os.path.join(repomap["CACHE_DIR"], f"{location_id}_{repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2")
    
    def try_load_grib(filename):
        """Try to load and validate a GRIB file"""
        if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
            return False
        try:
            with FileLock(f"{filename}.lock"):
                # Try to open the file without chunks first
                ds = xr.open_dataset(filename, engine="cfgrib")
                # Force load reflectivity to verify file integrity
                ds['refc'].values
                ds.close()
                return True
        except (OSError, ValueError, RuntimeError) as e:
            if "End of resource reached when reading message" in str(e):
                logger.error(f"GRIB file corrupted (premature EOF): {filename}")
            else:
                logger.warning(f"GRIB file invalid: {filename}, Error: {str(e)}")
            with FileLock(f"{filename}.lock"):
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                        logger.info(f"Deleted invalid file: {filename}")
                    # Also clean up any partial downloads
                    if os.path.exists(f"{filename}.tmp"):
                        os.remove(f"{filename}.tmp")
                except OSError as e:
                    logger.error(f"Error cleaning up invalid files: {str(e)}")
            return False

    # Try to use cached file
    if try_load_grib(filename):
        logger.info(f"Using cached valid GRIB file: {filename}")
        return filename

    # Try downloading up to 3 times
    for attempt in range(3):
        logger.info(f"Downloading GRIB file from: {url} (attempt {attempt + 1}/3)")
        try:
            temp_filename = f"{filename}.tmp"
            download_file(url, temp_filename)
            
            # Verify the temporary file
            if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) < 1000:
                raise ValueError(f"Downloaded file is missing or too small: {temp_filename}")
            
            # Try to open with xarray to verify it's valid
            ds = xr.open_dataset(temp_filename, engine="cfgrib", chunks={'time': 1})
            # Force load reflectivity to verify file integrity
            ds['refc'].load()
            ds.close()
            
            # If verification passed, move the file into place atomically
            with FileLock(f"{filename}.lock"):
                os.replace(temp_filename, filename)
                logger.info(f"Successfully downloaded and verified GRIB file: {filename}")
                return filename
                
        except Exception as e:
            logger.error(f"Download attempt {attempt + 1} failed: {str(e)}", exc_info=True)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except Exception:
                    pass
    
    raise ValueError("Failed to obtain valid GRIB file after 3 attempts")

def generate_forecast_images(location_config):
    """Generate forecast images for a specific location."""
    try:
        # Get the most recent HRRR run
        date_str, init_hour, init_time = get_latest_hrrr_run()
        logger.info(f"Using HRRR run from {init_time}")
        
        # Create location-specific cache directory
        location_id = location_config['id']
        location_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        os.makedirs(location_cache_dir, exist_ok=True)
        
        # Create metadata file with run information
        metadata_path = os.path.join(location_cache_dir, "metadata.txt")
        with open(metadata_path, "w") as f:
            f.write(f"date_str={date_str}\n")
            f.write(f"init_hour={init_hour}\n")
            f.write(f"init_time={init_time}\n")
            f.write(f"location_name={location_config['name']}\n")
            f.write(f"center_lat={location_config['center_lat']}\n")
            f.write(f"center_lon={location_config['center_lon']}\n")
            f.write(f"zoom={location_config['zoom']}\n")
        
        # Download and process each forecast hour
        for hour in range(1, 25):
            hour_str = f"{hour:02d}"
            logger.info(f"Processing forecast hour {hour_str} for {location_config['name']}")
            
            # Fetch GRIB file
            grib_path = fetch_grib(date_str, init_hour, hour_str, location_config)
            
            # Generate plot
            image_buffer = create_plot(
                grib_path, 
                init_time, 
                hour_str, 
                repomap["CACHE_DIR"],
                center_lat=location_config['center_lat'],
                center_lon=location_config['center_lon'],
                zoom=location_config['zoom']
            )
            
            # Save image to cache
            image_path = os.path.join(location_cache_dir, f"frame_{hour_str}.png")
            with open(image_path, "wb") as f:
                f.write(image_buffer.getvalue())
            
            logger.info(f"Saved forecast image for hour {hour_str} to {image_path}")
            
        logger.info(f"Completed forecast image generation for {location_config['name']}")
        return True
        
    except Exception as e:
        logger.error(f"Error generating forecast images for {location_config['name']}: {str(e)}", exc_info=True)
        return False

def main():
    parser = argparse.ArgumentParser(description="Build forecast cache for configured locations")
    parser.add_argument("--location", help="Specific location ID to process (default: all)")
    args = parser.parse_args()
    
    # Ensure cache directory exists
    os.makedirs(repomap["CACHE_DIR"], exist_ok=True)
    
    # Get county shapefile (shared resource)
    fetch_county_shapefile(repomap["CACHE_DIR"])
    
    # Process locations
    if args.location:
        # Process single location
        if args.location in repomap["LOCATIONS"]:
            location_config = repomap["LOCATIONS"][args.location]
            location_config['id'] = args.location
            logger.info(f"Processing single location: {location_config['name']}")
            generate_forecast_images(location_config)
        else:
            logger.error(f"Location ID '{args.location}' not found in configuration")
    else:
        # Process all locations
        logger.info(f"Processing all {len(repomap['LOCATIONS'])} configured locations")
        for location_id, location_config in repomap["LOCATIONS"].items():
            location_config['id'] = location_id
            generate_forecast_images(location_config)
    
    logger.info("Cache building complete")

if __name__ == "__main__":
    main()
