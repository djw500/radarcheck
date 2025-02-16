import os
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

import pytz
import xarray as xr
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import geopandas as gpd
from shapely.geometry import box
import requests
from PIL import Image

from utils import download_file, fetch_county_shapefile

def create_radar_colormap():
    """Create a colormap matching NWS radar reflectivity standards."""
    # Define colors for different dBZ ranges
    colors = [
        (0.6, 0.6, 0.6, 0.0),  # Transparent for < 5 dBZ
        (0.7, 0.7, 0.9, 1.0),  # Light blue for 5-15 dBZ
        (0.0, 0.8, 0.0, 1.0),  # Green for 15-25 dBZ
        (1.0, 1.0, 0.0, 1.0),  # Yellow for 25-35 dBZ
        (1.0, 0.5, 0.0, 1.0),  # Orange for 35-45 dBZ
        (1.0, 0.0, 0.0, 1.0),  # Red for 45-55 dBZ
        (0.6, 0.0, 0.6, 1.0),  # Purple for > 55 dBZ
    ]
    
    # Create positions for the color transitions
    positions = [0.0, 0.133, 0.267, 0.4, 0.533, 0.667, 1.0]
    
    return LinearSegmentedColormap.from_list('radar', list(zip(positions, colors)))

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

        # --- Step 3: Define region center and zoom ---
        center_point = {
            'lat': 40.04877,
            'lon': -75.38903
        }
        zoom_degrees = 1.5  # Controls the size of the view (smaller = more zoomed in)
        
        # Calculate region bounds from center and zoom
        region_bounds = {
            'lat_min': center_point['lat'] - zoom_degrees,
            'lat_max': center_point['lat'] + zoom_degrees,
            'lon_min': center_point['lon'] - zoom_degrees * 1.3,  # Adjust for longitude projection
            'lon_max': center_point['lon'] + zoom_degrees * 1.3
        }

        # --- Step 4: Subset and plot the data ---
        # Handle longitude wrapping if needed
        lon = data_to_plot.longitude
        if float(lon.min()) >= 0:
            # Convert negative longitudes to 0-360 range
            region_bounds['lon_min'] = 360 + region_bounds['lon_min']
            region_bounds['lon_max'] = 360 + region_bounds['lon_max']
            print(f"Adjusted longitude bounds to 0-360: {region_bounds['lon_min']}, {region_bounds['lon_max']}")

        # Create the subset with a small buffer for interpolation
        buffer = 0.1
        lat_mask = (data_to_plot.latitude >= region_bounds['lat_min'] - buffer) & \
                  (data_to_plot.latitude <= region_bounds['lat_max'] + buffer)
        lon_mask = (data_to_plot.longitude >= region_bounds['lon_min'] - buffer) & \
                  (data_to_plot.longitude <= region_bounds['lon_max'] + buffer)
        
        subset = data_to_plot.where(lat_mask & lon_mask, drop=True)
        print(f"Subset shape: {subset.shape}")

        # Create the plot
        fig = plt.figure(figsize=(8, 6))
        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_extent([
            region_bounds['lon_min'], 
            region_bounds['lon_max'],
            region_bounds['lat_min'], 
            region_bounds['lat_max']
        ], crs=ccrs.PlateCarree())
        
        print("Creating plot...")
        subset.plot.pcolormesh(
            ax=ax,
            x="longitude",
            y="latitude",
            cmap=create_radar_colormap(),
            vmin=5,
            vmax=75,
            add_colorbar=True,
            transform=ccrs.PlateCarree()
        )
        
        # Convert init_time to Eastern Time
        utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M")
        utc = pytz.UTC.localize(utc_time)
        eastern = pytz.timezone('America/New_York')
        est_init_time = utc.astimezone(eastern)
        
        # Calculate forecast valid time
        forecast_delta = timedelta(hours=int(forecast_hour))
        est_valid_time = est_init_time + forecast_delta
        
        ax.set_title(f"HRRR Forecast: {var_label}\n"
                    f"Model Run: {est_init_time.strftime('%I:%M %p %Z')}\n"
                    f"Valid: {est_valid_time.strftime('%I:%M %p %Z')}")
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

        # Mark region center point
        ax.plot(center_point['lon'], center_point['lat'], 
                marker='*', markersize=15, color='gold', 
                transform=ccrs.PlateCarree())

        return fig

    except Exception as e:
        import traceback
        print("Error in create_plot:")
        print(traceback.format_exc())
        raise
def create_forecast_gif(grib_paths, init_time, cache_dir, duration=500):
    """
    Create an animated GIF from multiple HRRR forecast hours.
    
    Args:
        grib_paths: List of paths to GRIB files for different forecast hours
        init_time: Initial model run time
        cache_dir: Directory for caching files
        duration: Duration for each frame in milliseconds (default 500ms)
    
    Returns:
        BytesIO object containing the animated GIF
    """
    frames = []
    
    for i, grib_path in enumerate(grib_paths):
        print(f"Processing forecast hour {i}...")
        
        # Create the plot for this forecast hour
        fig = create_plot(grib_path, init_time, i, cache_dir)
        
        # Convert matplotlib figure to PIL Image
        buf = BytesIO()
        fig.savefig(buf, format='PNG', bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        img = Image.open(buf)
        frames.append(img.copy())
        buf.close()
    
    # Save the animation to a buffer
    gif_buffer = BytesIO()
    frames[0].save(
        gif_buffer,
        format='GIF',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=False
    )
    gif_buffer.seek(0)
    
    return gif_buffer
