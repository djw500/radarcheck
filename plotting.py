import os
import zipfile
from io import BytesIO
from datetime import datetime

import xarray as xr
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import geopandas as gpd
from shapely.geometry import box
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

def create_plot(grib_path, init_time, forecast_hour, cache_dir):
    """Create a plot from HRRR GRIB data."""
    print("Starting plot creation...")
    
    try:
        # --- Step 1: Open GRIB file ---
        print("Attempting to open dataset with shortName filter...")
        try:
            ds = xr.open_dataset(grib_path, engine="cfgrib", filter_by_keys={'shortName': '2t'})
            print("Successfully loaded dataset with filter_by_keys={'shortName': '2t'}")
        except Exception as e:
            print(f"Error with shortName filter: {str(e)}")
            print("Trying typeOfLevel filter...")
            ds = xr.open_dataset(grib_path, engine="cfgrib", 
                               backend_kwargs={'filter_by_keys': {'typeOfLevel': 'surface', 'stepType': 'accum'}})
            print("Successfully loaded dataset with typeOfLevel filter")

        print("Available variables in dataset:", list(ds.data_vars.keys()))

        # --- Step 2: Determine which variable to plot ---
        if "t2m" in ds.data_vars:
            print("Found t2m variable")
            temp_var = "t2m"
            temp_celsius = ds[temp_var] - 273.15
            data_to_plot = temp_celsius
            var_label = "2-m Temperature (°C)"
        elif "TMP" in ds.data_vars:
            print("Found TMP variable")
            temp_var = "TMP"
            temp_celsius = ds[temp_var] - 273.15
            data_to_plot = temp_celsius
            var_label = "2-m Temperature (°C)"
        elif "sdwe" in ds.data_vars:
            print("Found sdwe variable")
            sdwe = ds["sdwe"]
            snowfall_cm = sdwe / 10.0
            snowfall_in = snowfall_cm / 2.54
            data_to_plot = snowfall_in
            var_label = "Snowfall (inches)"
        else:
            print("Using first available variable")
            var_label = list(ds.data_vars.keys())[0]
            data_to_plot = ds[var_label]

        # --- Step 3: Subset the data for Philadelphia region ---
        desired_lat_min, desired_lat_max = 39.0, 40.5
        desired_lon_min, desired_lon_max = -76, -74.0

        lon = data_to_plot.longitude
        if float(lon.min()) >= 0:
            philly_lon_min = 360 + desired_lon_min
            philly_lon_max = 360 + desired_lon_max
            print("Adjusted longitude bounds to 0-360:", philly_lon_min, philly_lon_max)
        else:
            philly_lon_min = desired_lon_min
            philly_lon_max = desired_lon_max

        print(f"Subsetting data with bounds: lat({desired_lat_min}, {desired_lat_max}), lon({philly_lon_min}, {philly_lon_max})")
        subset = data_to_plot.where(
            (data_to_plot.latitude >= desired_lat_min) & 
            (data_to_plot.latitude <= desired_lat_max) &
            (data_to_plot.longitude >= philly_lon_min) & 
            (data_to_plot.longitude <= philly_lon_max),
            drop=True
        )
        print("Subset shape:", subset.shape)

        # --- Step 4: Create the plot ---
        fig = plt.figure(figsize=(8, 6))
        ax = plt.axes(projection=ccrs.PlateCarree())
        
        print("Creating plot...")
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

        # --- Step 5: Overlay county boundaries ---
        print("Adding county boundaries...")
        shp_path = fetch_county_shapefile(cache_dir)
        counties = gpd.read_file(shp_path)
        
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

        # Mark center of Philadelphia
        roi_lat = 40.04877
        roi_lon = -75.38903
        ax.plot(roi_lon, roi_lat, marker='*', markersize=15, color='gold', 
                transform=ccrs.PlateCarree())

        # Save plot to buffer
        print("Saving plot to buffer...")
        buf = BytesIO()
        plt.savefig(buf, format="PNG", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        import traceback
        print("Error in create_plot:")
        print(traceback.format_exc())
        raise
