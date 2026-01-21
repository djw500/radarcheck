import xarray as xr
import numpy as np

path = "cache/region_ne/hrrr/run_20260121_03/t2m/grib_01.grib2"

print(f"Opening {path}")
ds = xr.open_dataset(path, engine="cfgrib")
print(ds)

for var in ds.data_vars:
    print(f"\nVariable: {var}")
    print(ds[var].attrs)
    vals = ds[var].values
    print(f"Min: {np.nanmin(vals)}")
    print(f"Max: {np.nanmax(vals)}")
    print(f"Mean: {np.nanmean(vals)}")
