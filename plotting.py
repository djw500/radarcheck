import os
import logging
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

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

def create_plot(grib_path, init_time, forecast_hour, cache_dir, center_lat=None, center_lon=None, zoom=None):
    """Create a plot from HRRR GRIB data."""
    logger.info("Starting plot creation...")
    
    try:
        # --- Step 1: Open GRIB file ---
        logger.info("Attempting to open dataset with shortName filter...")
        try:
            ds = xr.open_dataset(grib_path, engine="cfgrib", filter_by_keys={'shortName': 'refc'})
            logger.info("Successfully loaded dataset with filter_by_keys={'shortName': 'refc'}")
        except Exception as e:
            logger.warning(f"Error with shortName filter: {str(e)}")
            logger.info("Trying parameter filter...")
            ds = xr.open_dataset(grib_path, engine="cfgrib", 
                               backend_kwargs={'filter_by_keys': {'paramId': '132'}})
            logger.info("Successfully loaded dataset with parameter filter")

        logger.info(f"Available variables in dataset: {list(ds.data_vars.keys())}")

        # --- Step 2: Determine which variable to plot ---
        if not ds.data_vars:
            raise ValueError("No variables found in the GRIB file after filtering.")

        if "refc" in ds.data_vars:
            logger.info("Found refc variable")
            data_to_plot = ds["refc"]
            var_label = "Composite Reflectivity (dBZ)"
        else:
            logger.info("Using first available variable")
            var_label = list(ds.data_vars.keys())[0]
            data_to_plot = ds[var_label]

        # --- Step 3: Define region center and zoom ---
        center_point = {
            'lat': center_lat if center_lat is not None else 40.04877,
            'lon': center_lon if center_lon is not None else -75.38903
        }
        zoom_degrees = zoom if zoom is not None else 1.5  # Controls the size of the view (smaller = more zoomed in)
        
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
            logger.info(f"Adjusted longitude bounds to 0-360: {region_bounds['lon_min']}, {region_bounds['lon_max']}")

        # Create the subset with a small buffer for interpolation
        buffer = 0.1
        lat_mask = (data_to_plot.latitude >= region_bounds['lat_min'] - buffer) & \
                  (data_to_plot.latitude <= region_bounds['lat_max'] + buffer)
        lon_mask = (data_to_plot.longitude >= region_bounds['lon_min'] - buffer) & \
                  (data_to_plot.longitude <= region_bounds['lon_max'] + buffer)
        
        subset = data_to_plot.where(lat_mask & lon_mask, drop=True)
        logger.info(f"Subset shape: {subset.shape}")

        # Create the plot
        fig = plt.figure(figsize=(8, 6))
        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_extent([
            region_bounds['lon_min'], 
            region_bounds['lon_max'],
            region_bounds['lat_min'], 
            region_bounds['lat_max']
        ], crs=ccrs.PlateCarree())
        
        logger.info("Creating plot...")
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
        
        # Get model initialization time
        utc_time = datetime.strptime(init_time, "%Y-%m-%d %H:%M:%S")
        utc = pytz.UTC.localize(utc_time)
        eastern = pytz.timezone('America/New_York')
        est_init_time = utc.astimezone(eastern)
        
        # Get actual valid time from the GRIB data
        if 'valid_time' in ds.coords:
            valid_time = ds.valid_time.values
            if isinstance(valid_time, np.datetime64):
                valid_time = valid_time.astype('datetime64[s]').tolist()
                valid_time = pytz.UTC.localize(valid_time)
                est_valid_time = valid_time.astimezone(eastern)
            else:
                # Fallback to calculated time if valid_time not available
                forecast_delta = timedelta(hours=int(forecast_hour))
                est_valid_time = est_init_time + forecast_delta
        else:
            # Fallback to calculated time if valid_time not available
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
        logger.info("Adding county boundaries...")
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
        logger.info(f"Plot bounding box: {bbox}")
        
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

        # Convert figure to BytesIO buffer
        buf = BytesIO()
        fig.savefig(buf, format='PNG', bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return buf

    except Exception as e:
        import traceback
        logger.error("Error in create_plot:", exc_info=True)
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
        logger.info(f"Processing forecast hour {i}...")
        
        # Create the plot for this forecast hour and get the buffer
        plot_buffer = create_plot(grib_path, init_time, str(i+1), cache_dir)
            
        # Convert buffer to PIL Image
        plot_buffer.seek(0)
        img = Image.open(plot_buffer)
        frames.append(img.copy())
        plot_buffer.close()
    
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
