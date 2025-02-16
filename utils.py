import os
import zipfile
import requests

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
        print("Extracted county shapefile.")
    else:
        print("County shapefile already extracted.")
    return county_shp
