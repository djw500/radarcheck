import xarray as xr
import numpy as np

path = "cache/region_ne/hrrr/run_20260121_03/t2m/grib_01.grib2"
target_lat = 40.05
target_lon = -75.40

print(f"Opening {path}")
ds = xr.open_dataset(path, engine="cfgrib")

# Convert target lon to 0-360 if needed
# Check GRIB longitude convention
lons = ds.longitude.values
lats = ds.latitude.values

print(f"Lon range: {np.nanmin(lons)} to {np.nanmax(lons)}")
if np.nanmin(lons) >= 0:
    target_lon_adj = target_lon + 360
else:
    target_lon_adj = target_lon

print(f"Target: {target_lat}, {target_lon} (adj: {target_lon_adj})")

# Find closest point
dist = (lats - target_lat)**2 + (lons - target_lon_adj)**2
min_idx = np.unravel_index(np.argmin(dist), dist.shape)

print(f"Closest index: {min_idx}")
print(f"Lat at index: {lats[min_idx]}")
print(f"Lon at index: {lons[min_idx]}")

val_k = ds.t2m.values[min_idx]
print(f"Value (K): {val_k}")
val_f = (val_k - 273.15) * 1.8 + 32
print(f"Value (F): {val_f}")
