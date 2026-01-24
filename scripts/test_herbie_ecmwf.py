from herbie import Herbie
import os
from datetime import datetime

# Test script for Herbie with ECMWF Open Data
# Target: 2-meter temperature for the 00Z run today

today = datetime.utcnow().strftime("%Y-%m-%d")
print(f"Testing Herbie for ECMWF on {today} 00Z...")

try:
    H = Herbie(
        today,
        model="ifs",
        product="oper",
        fxx=0
    )
    
    print(f"Herbie object created. Searching for :2t:...")
    
    # Download subset
    H.download(":2t:")
    
    print(f"Download path: {H.get_localFilePath(':2t:')}")
    
    # Try reading with xarray
    ds = H.xarray(":2t:")
    print("Xarray dataset loaded successfully!")
    print(ds)
    
except Exception as e:
    print(f"Error during Herbie test: {e}")
    import traceback
    traceback.print_exc()
