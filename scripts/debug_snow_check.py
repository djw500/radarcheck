import sys
import os
import json
import numpy as np
from datetime import datetime, timedelta

# Add current dir to path to import app/tiles
sys.path.append(os.getcwd())

from app import _derive_asnow_timeseries_from_tiles, _accumulate_timeseries
from tiles import load_timeseries_for_point

# Config
REGION = "ne"
RES = 0.1
LAT = 42.3601 # Boston
LON = -71.0589
GFS_RUN = "run_20260121_18" # Has apcp, csnow, snod
NBM_RUN = "run_20260122_01" # Has asnow

def get_valid_time(run_id, hour):
    # run_id format: run_YYYYMMDD_HH
    try:
        parts = run_id.split('_')
        date_str = parts[1]
        cycle_str = parts[2]
        base_dt = datetime.strptime(f"{date_str}{cycle_str}", "%Y%m%d%H")
        return base_dt + timedelta(hours=int(hour))
    except:
        return None

def main():
    print(f"Checking snow for Lat: {LAT}, Lon: {LON}")
    
    # 1. GFS Derived
    print(f"\n--- GFS Derived (Run {GFS_RUN}) ---")
    gfs_hours, gfs_asnow = _derive_asnow_timeseries_from_tiles(
        REGION, RES, "gfs", GFS_RUN, LAT, LON
    )
    
    if gfs_hours is None:
        print("Could not derive GFS asnow (missing data)")
        gfs_data = {}
    else:
        gfs_data = {get_valid_time(GFS_RUN, h): v for h, v in zip(gfs_hours, gfs_asnow)}

    # 2. GFS SNOD (Native)
    print(f"--- GFS SNOD (Run {GFS_RUN}) ---")
    try:
        h_snod, v_snod = load_timeseries_for_point(
            "cache/tiles", REGION, RES, "gfs", GFS_RUN, "snod", LAT, LON
        )
        gfs_snod_data = {get_valid_time(GFS_RUN, h): v for h, v in zip(h_snod, v_snod)}
    except FileNotFoundError:
        print("GFS SNOD not found")
        gfs_snod_data = {}

    # 3. NBM Native ASNOW
    print(f"\n--- NBM Native ASNOW (Run {NBM_RUN}) ---")
    try:
        h_nbm, v_nbm = load_timeseries_for_point(
            "cache/tiles", REGION, RES, "nbm", NBM_RUN, "asnow", LAT, LON
        )
        # APPLY ACCUMULATION FIX
        v_nbm_acc = _accumulate_timeseries(v_nbm)
        nbm_data = {get_valid_time(NBM_RUN, h): v for h, v in zip(h_nbm, v_nbm_acc)}
    except FileNotFoundError:
        print("NBM ASNOW not found")
        nbm_data = {}

    # 4. NBM Derived ASNOW (for sanity check)
    print(f"\n--- NBM Derived ASNOW (Run {NBM_RUN}) ---")
    # Note: NBM might not have csnow/t2m in tiles, but we check APCP
    try:
        # Check if we have apcp
        h_nbm_apcp, v_nbm_apcp = load_timeseries_for_point(
            "cache/tiles", REGION, RES, "nbm", NBM_RUN, "apcp", LAT, LON
        )
        # APPLY ACCUMULATION FIX TO APCP TOO
        v_nbm_apcp_acc = _accumulate_timeseries(v_nbm_apcp)
        nbm_apcp_data = {get_valid_time(NBM_RUN, h): v for h, v in zip(h_nbm_apcp, v_nbm_apcp_acc)}
    except FileNotFoundError:
        print("NBM APCP not found")
        nbm_apcp_data = {}


    # Align and Print
    all_times = sorted(list(set(gfs_data.keys()) | set(gfs_snod_data.keys()) | set(nbm_data.keys()) | set(nbm_apcp_data.keys())))
    
    print(f"\n{'Valid Time':<20} | {'GFS Der':<8} | {'GFS SNOD':<8} | {'GFS dSNOD':<8} | {'NBM Nat(Acc)':<12} | {'NBM APCP(Acc)':<12}")
    print("-" * 90)
    
    first_snod = None
    
    # Find first valid snod for baseline
    for t in all_times:
        if t in gfs_snod_data:
            first_snod = gfs_snod_data[t]
            break
            
    for t in all_times:
        if t is None: continue
        
        val_gfs_asnow = gfs_data.get(t, float('nan'))
        val_gfs_snod = gfs_snod_data.get(t, float('nan'))
        val_nbm_asnow = nbm_data.get(t, float('nan'))
        val_nbm_apcp = nbm_apcp_data.get(t, float('nan'))
        
        delta_snod = float('nan')
        if not np.isnan(val_gfs_snod) and first_snod is not None:
            delta_snod = val_gfs_snod - first_snod
                
        t_str = t.strftime("%Y-%m-%d %H:%M")
        # Filter rows where all are NaN
        if np.isnan(val_gfs_asnow) and np.isnan(val_gfs_snod) and np.isnan(val_nbm_asnow) and np.isnan(val_nbm_apcp):
            continue
            
        print(f"{t_str:<20} | {val_gfs_asnow:<8.2f} | {val_gfs_snod:<8.2f} | {delta_snod:<8.2f} | {val_nbm_asnow:<8.2f} | {val_nbm_apcp:<8.2f}")

if __name__ == "__main__":
    main()
