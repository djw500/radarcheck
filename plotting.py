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

from utils import download_file, fetch_county_shapefile

def create_plot(grib_path, init_time, forecast_hour, cache_dir):
    """Create a plot from HRRR GRIB data."""
    print("Starting plot creation...")
    
    try:
        # --- Step 1: Open GRIB file ---
        print("Attempting to open dataset with shortName filter...")
        try:
            ds = xr.open_dataset(grib_path, engine="cfgrib", filter_by_keys={'shortName': 'refc'})
            print("Successfully loaded dataset with filter_by_keys={'shortName': 'refc'}")
        except Exception as e:
            print(f"Error with shortName filter: {str(e)}")
            print("Trying parameter filter...")
            ds = xr.open_dataset(grib_path, engine="cfgrib", 
                               backend_kwargs={'filter_by_keys': {'paramId': '132'}})
            print("Successfully loaded dataset with parameter filter")

        print("Available variables in dataset:", list(ds.data_vars.keys()))

        # --- Step 2: Determine which variable to plot ---
        if not ds.data_vars:
            raise ValueError("No variables found in the GRIB file after filtering.")

        if "refc" in ds.data_vars:
            print("Found refc variable")
            data_to_plot = ds["refc"]
            var_label = "Composite Reflectivity (dBZ)"
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
            cmap="gist_ncar",
            vmin=0,
            vmax=70,
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
