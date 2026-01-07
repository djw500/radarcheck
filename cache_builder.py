import os
import logging
import argparse
import shutil
from datetime import datetime, timedelta, timezone
import pytz
import tempfile
from filelock import FileLock
import xarray as xr
import requests

from io import BytesIO
import geopandas as gpd
import psutil
import gc

from config import repomap
from utils import download_file, fetch_county_shapefile
from plotting import create_plot

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create logs directory if it doesn't exist
# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

def log_memory_usage(context=""):
    """Log current memory usage."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    # Convert to MB
    rss_mb = mem_info.rss / 1024 / 1024
    logger.info(f"Memory Usage [{context}]: {rss_mb:.2f} MB")

def get_available_hrrr_runs(max_runs=5):
    """Find multiple recent HRRR runs available, from newest to oldest."""
    now = datetime.now(timezone.utc)
    available_runs = []
    
    # Check the last 24 hours of potential runs
    for hours_ago in range(0, 27):
        # Stop once we have enough runs
        if len(available_runs) >= max_runs:
            break
            
        check_time = now - timedelta(hours=hours_ago)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        
        # Check if the file exists using the filter URL
        url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
               f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}z.wrfsfcf01.grib2&"
               f"dir=%2Fhrrr.{date_str}%2Fconus&"
               f"var_REFC=on")
        
        try:
            response = requests.head(url, timeout=10)
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
                
                run_info = {
                    'date_str': date_str,
                    'init_hour': init_hour,
                    'init_time': model_time.strftime("%Y-%m-%d %H:%M:%S"),
                    'run_id': f"run_{date_str}_{init_hour}"
                }
                
                available_runs.append(run_info)
                logger.info(f"Found available HRRR run: {run_info['run_id']}")
        except Exception as e:
            logger.warning(f"Error checking run from {hours_ago} hours ago: {str(e)}")
    
    if not available_runs:
        raise Exception("Could not find any recent HRRR runs")
        
    return available_runs

def get_latest_hrrr_run():
    """Find the most recent HRRR run available."""
    runs = get_available_hrrr_runs(max_runs=1)
    if runs:
        run = runs[0]
        return run['date_str'], run['init_hour'], run['init_time']
    raise Exception("Could not find a recent HRRR run")

def fetch_grib(date_str, init_hour, forecast_hour, location_config, run_id):
    """Download and cache the HRRR GRIB file for a specific forecast hour and location."""
    url = (f"https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl?"
           f"file={repomap['HRRR_FILE_PREFIX']}{init_hour}{repomap['HRRR_FILE_SUFFIX']}{forecast_hour}.grib2&"
           f"dir=%2Fhrrr.{date_str}%2Fconus&"
           f"{repomap['HRRR_VARS']}"
           f"leftlon={location_config['lon_min']}&rightlon={location_config['lon_max']}&"
           f"toplat={location_config['lat_max']}&bottomlat={location_config['lat_min']}&")
    
    location_id = location_config['id']
    run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
    os.makedirs(run_cache_dir, exist_ok=True)
    
    filename = os.path.join(run_cache_dir, f"grib_{forecast_hour}.grib2")
    
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
            ds = xr.open_dataset(temp_filename, engine="cfgrib")
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

def generate_forecast_images(location_config, counties, run_info=None):
    """Generate forecast images for a specific location and model run."""
    try:
        location_id = location_config['id']
        
        # If no specific run provided, get the latest
        if run_info is None:
            date_str, init_hour, init_time = get_latest_hrrr_run()
            run_id = f"run_{date_str}_{init_hour}"
        else:
            date_str = run_info['date_str']
            init_hour = run_info['init_hour']
            init_time = run_info['init_time']
            run_id = run_info['run_id']
            
        logger.info(f"Processing HRRR run {run_id} for {location_config['name']}")
        
        # Create run-specific cache directory
        run_cache_dir = os.path.join(repomap["CACHE_DIR"], location_id, run_id)
        os.makedirs(run_cache_dir, exist_ok=True)
        
        # Create metadata file with run information
        metadata_path = os.path.join(run_cache_dir, "metadata.txt")
        with open(metadata_path, "w") as f:
            f.write(f"date_str={date_str}\n")
            f.write(f"init_hour={init_hour}\n")
            f.write(f"init_time={init_time}\n")
            f.write(f"run_id={run_id}\n")
            f.write(f"location_name={location_config['name']}\n")
            f.write(f"center_lat={location_config['center_lat']}\n")
            f.write(f"center_lon={location_config['center_lon']}\n")
            f.write(f"zoom={location_config['zoom']}\n")
        
        # Download and process each forecast hour
        valid_times = []
        for hour in range(1, 25):
            hour_str = f"{hour:02d}"
            logger.info(f"Processing forecast hour {hour_str} for {location_config['name']} (run {run_id})")
            
            try:
                # Fetch GRIB file
                grib_path = fetch_grib(date_str, init_hour, hour_str, location_config, run_id)
                
                # Calculate valid time
                init_dt = datetime.strptime(init_time, "%Y-%m-%d %H:%M:%S")
                if not init_dt.tzinfo:
                    init_dt = pytz.UTC.localize(init_dt)
                valid_time = init_dt + timedelta(hours=hour)
                valid_time_str = valid_time.strftime("%Y-%m-%d %H:%M:%S")
                
                # Save image to cache
                image_path = os.path.join(run_cache_dir, f"frame_{hour_str}.png")
                
                # Check if image already exists and is valid
                if os.path.exists(image_path) and os.path.getsize(image_path) > 1000:
                    logger.info(f"Skipping existing frame: {image_path}")
                else:
                    # Generate plot
                    image_buffer = create_plot(
                        grib_path, 
                        init_time, 
                        hour_str, 
                        repomap["CACHE_DIR"],
                        center_lat=location_config['center_lat'],
                        center_lon=location_config['center_lon'],
                        zoom=location_config['zoom'],
                        counties=counties
                    )
                    
                    with open(image_path, "wb") as f:
                        f.write(image_buffer.getvalue())
                    
                    logger.info(f"Saved forecast image for hour {hour_str} to {image_path}")
                
                # Record valid time mapping
                valid_times.append({
                    "forecast_hour": hour,
                    "valid_time": valid_time_str,
                    "frame_path": f"frame_{hour_str}.png"
                })
                
                
            except Exception as e:
                logger.error(f"Error processing hour {hour_str}: {str(e)}")
                # Continue with next hour
        
        # Save valid time mapping
        valid_times_path = os.path.join(run_cache_dir, "valid_times.txt")
        with open(valid_times_path, "w") as f:
            for vt in valid_times:
                f.write(f"{vt['forecast_hour']}={vt['valid_time']}={vt['frame_path']}\n")
        
        # Create a symlink to the latest run (atomic replacement)
        latest_link = os.path.join(repomap["CACHE_DIR"], location_id, "latest")
        # Create temp symlink and atomically rename to avoid race conditions
        temp_link = os.path.join(repomap["CACHE_DIR"], location_id, f".latest_tmp_{os.getpid()}")
        try:
            os.symlink(run_id, temp_link)
            os.replace(temp_link, latest_link)
        except OSError:
            # Fallback for systems that don't support atomic replace of symlinks
            if os.path.exists(temp_link):
                os.unlink(temp_link)
            if os.path.exists(latest_link) or os.path.islink(latest_link):
                os.unlink(latest_link)
            os.symlink(run_id, latest_link)
        
        logger.info(f"Completed forecast image generation for {location_config['name']} (run {run_id})")
        return True
        
    except Exception as e:
        logger.error(f"Error generating forecast images for {location_config['name']}: {str(e)}", exc_info=True)
        return False

def cleanup_old_runs(location_id):
    """Remove old runs to save disk space, keeping only the most recent N runs."""
    try:
        location_dir = os.path.join(repomap["CACHE_DIR"], location_id)
        if not os.path.exists(location_dir):
            return
            
        # Get all run directories
        run_dirs = []
        for item in os.listdir(location_dir):
            if item.startswith("run_") and os.path.isdir(os.path.join(location_dir, item)):
                run_dirs.append(item)
        
        # Sort by run ID (which includes date and hour)
        run_dirs.sort(reverse=True)
        
        # Remove older runs beyond the limit
        max_runs = repomap.get("MAX_RUNS_TO_KEEP", 5)
        if len(run_dirs) > max_runs:
            for old_run in run_dirs[max_runs:]:
                old_run_path = os.path.join(location_dir, old_run)
                logger.info(f"Removing old run: {old_run_path}")
                shutil.rmtree(old_run_path)
    
    except Exception as e:
        logger.error(f"Error cleaning up old runs for {location_id}: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Build forecast cache for configured locations")
    parser.add_argument("--location", help="Specific location ID to process (default: all)")
    parser.add_argument("--runs", type=int, default=5, help="Number of model runs to process (default: 5)")
    parser.add_argument("--latest-only", action="store_true", help="Process only the latest run")
    args = parser.parse_args()
    
    # Ensure cache directory exists
    os.makedirs(repomap["CACHE_DIR"], exist_ok=True)
    
    # Get county shapefile (shared resource)
    shp_path = fetch_county_shapefile(repomap["CACHE_DIR"])
    logger.info("Loading county shapefile into memory...")
    counties = gpd.read_file(shp_path)
    log_memory_usage("after_shapefile_load")
    
    # Get available HRRR runs
    if args.latest_only:
        available_runs = get_available_hrrr_runs(max_runs=1)
    else:
        available_runs = get_available_hrrr_runs(max_runs=args.runs)
    
    logger.info(f"Found {len(available_runs)} available HRRR runs")
    
    # Process locations
    if args.location:
        # Process single location
        if args.location in repomap["LOCATIONS"]:
            # Create a copy to avoid mutating the global config
            location_config = repomap["LOCATIONS"][args.location].copy()
            location_config['id'] = args.location
            logger.info(f"Processing single location: {location_config['name']}")

            for run_info in available_runs:
                generate_forecast_images(location_config, counties, run_info)
                log_memory_usage(f"after_run_{run_info['run_id']}")
                gc.collect()

            # Clean up old runs
            cleanup_old_runs(args.location)
        else:
            logger.error(f"Location ID '{args.location}' not found in configuration")
    else:
        # Process all locations
        logger.info(f"Processing all {len(repomap['LOCATIONS'])} configured locations")
        for location_id, location_config_orig in repomap["LOCATIONS"].items():
            # Create a copy to avoid mutating the global config
            location_config = location_config_orig.copy()
            location_config['id'] = location_id

            for run_info in available_runs:
                generate_forecast_images(location_config, counties, run_info)
                log_memory_usage(f"after_run_{run_info['run_id']}")

            # Clean up old runs
            cleanup_old_runs(location_id)
    
    logger.info("Cache building complete")
    
    # In production (supervisord), sleep before exiting so we don't hammer NOAA
    # HRRR updates hourly, so checking every 15 minutes is sufficient
    import time
    sleep_minutes = int(os.environ.get("CACHE_REFRESH_INTERVAL", 15))
    logger.info(f"Sleeping for {sleep_minutes} minutes before next refresh...")
    time.sleep(sleep_minutes * 60)

if __name__ == "__main__":
    main()
