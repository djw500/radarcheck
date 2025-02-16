import os
import requests
import zipfile
import logging

logger = logging.getLogger(__name__)

def download_file(url, local_path):
    """Download a file if it doesn't exist in cache."""
    if not os.path.exists(local_path):
        logger.info(f"Downloading from: {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Downloaded: {local_path}")
    else:
        logger.info(f"Using cached file: {local_path}")

def fetch_county_shapefile(cache_dir):
    """Download and extract the county shapefile if needed."""
    county_zip = os.path.join(cache_dir, "cb_2018_us_county_20m.zip")
    county_dir = os.path.join(cache_dir, "county_shapefile")
    county_shp = os.path.join(county_dir, "cb_2018_us_county_20m.shp")
    
    url_county = "https://www2.census.gov/geo/tiger/GENZ2018/shp/cb_2018_us_county_20m.zip"
    download_file(url_county, county_zip)
    if not os.path.exists(county_dir):
        with zipfile.ZipFile(county_zip, "r") as zip_ref:
            zip_ref.extractall(county_dir)
        logger.info("Extracted county shapefile.")
    else:
        logger.info("County shapefile already extracted.")
    return county_shp
