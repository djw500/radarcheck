
import numpy as np
import os
from config import repomap
from app import _derive_asnow_timeseries_from_tiles
from tiles import load_timeseries_for_point

def compare_gfs():
    # Settings
    lat = 40.0
    lon = -75.0
    model_id = "gfs"
    
    # Find latest run
    res = 0.1
    res_dir = "0.100deg"
    base_dir = f"cache/tiles/ne/{res_dir}/{model_id}"
    if not os.path.exists(base_dir):
        print("No GFS runs found")
        return
        
    runs = sorted([r for r in os.listdir(base_dir) if r.startswith("run_")], reverse=True)
    if not runs:
        print("No GFS runs found")
        return
        
    run_id = runs[0]
    print(f"Analyzing GFS run: {run_id} at {lat}, {lon}")
    
    # 1. Get Derived ASNOW
    try:
        common_hours, snow_cum = _derive_asnow_timeseries_from_tiles(
            "ne", res, model_id, run_id, lat, lon
        )
    except Exception as e:
        print(f"Derivation failed: {e}")
        return

    if common_hours is None:
        print("Derivation returned None (missing source tiles?)")
        return

    # 2. Get Native SNOD
    try:
        h_snod, v_snod = load_timeseries_for_point(
            repomap["TILES_DIR"], "ne", res, model_id, run_id, "snod", lat, lon
        )
    except FileNotFoundError:
        print("SNOD not found")
        h_snod, v_snod = [], []

    # 3. Get Source inputs for context
    try:
        h_apcp, v_apcp = load_timeseries_for_point(
            repomap["TILES_DIR"], "ne", res, model_id, run_id, "apcp", lat, lon
        )
        h_t2m, v_t2m = load_timeseries_for_point(
            repomap["TILES_DIR"], "ne", res, model_id, run_id, "t2m", lat, lon
        )
        h_csnow, v_csnow = load_timeseries_for_point(
            repomap["TILES_DIR"], "ne", res, model_id, run_id, "csnow", lat, lon
        )
    except:
        pass

    # Align for print
    print(f"{'Hour':<4} | {'T2M':<6} | {'CSNOW':<5} | {'APCP(tot)':<10} | {'IncCalc':<8} | {'DerSnowCum':<12} | {'SNOD':<8}")
    print("-" * 70)
    
    # Create dictionary lookup
    def get_val(h, hours, values):
        if hours is None: return np.nan
        idx = np.where(hours == h)[0]
        return values[idx[0]] if len(idx) > 0 else np.nan

    prev_apcp = 0.0
    
    for h in common_hours[:48]: # First 48 hours
        t = get_val(h, h_t2m, v_t2m)
        c = get_val(h, h_csnow, v_csnow)
        a = get_val(h, h_apcp, v_apcp)
        s_cum = get_val(h, common_hours, snow_cum)
        d = get_val(h, h_snod, v_snod)
        
        # Re-calc increment locally to verify logic
        diff = a - prev_apcp
        # Logic from app.py: if diff < 0, use a (reset). if diff >= 0, use diff.
        inc = a if diff < 0 else diff
        prev_apcp = a # Update prev
        
        print(f"{h:<4} | {t:<6.1f} | {c:<5.0f} | {a:<10.4f} | {inc:<8.4f} | {s_cum:<12.4f} | {d:<8.4f}")

if __name__ == "__main__":
    compare_gfs()
