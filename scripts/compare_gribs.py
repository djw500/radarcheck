import xarray as xr
import sys

paths = [
    "cache/seattle/hrrr/run_20260121_00/refc/grib_14.grib2",
    "cache/boston/hrrr/run_20260121_00/refc/grib_14.grib2"
]

for p in paths:
    print(f"--- {p} ---")
    try:
        ds = xr.open_dataset(p, engine="cfgrib")
        print("Lat range:", ds.latitude.min().values, ds.latitude.max().values)
        print("Lon range:", ds.longitude.min().values, ds.longitude.max().values)
        print("Shape:", ds.latitude.shape)
        ds.close()
    except Exception as e:
        print(f"Error: {e}")