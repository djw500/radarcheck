#!/usr/bin/env python3
import datetime
import requests
import sys
import subprocess
import pytz
import os
import time
from config import repomap
from utils import format_forecast_hour

MODELS_TO_PROCESS = ["hrrr", "nam_nest", "gfs"]
DAYS_BACK = 3
REGION = "ne"

def get_runs_for_model(model_id, days_back):
    model_config = repomap["MODELS"][model_id]
    now = datetime.datetime.now(datetime.timezone.utc)
    found_runs = []
    
    # Check hourly for the last N days
    # HRRR is hourly, NAM/GFS are 6-hourly (00, 06, 12, 18)
    hours_range = days_back * 24
    
    print(f"Scanning {model_id} runs for past {days_back} days...")
    
    for h in range(hours_range):
        check_time = now - datetime.timedelta(hours=h)
        date_str = check_time.strftime("%Y%m%d")
        init_hour = check_time.strftime("%H")
        
        # Optimization: Skip non-synoptic hours for GFS/NAM
        if model_id in ["gfs", "nam_nest"] and int(init_hour) % 6 != 0:
            continue
            
        # Check NOMADS availability
        # We check forecast hour 1
        fhour_str = format_forecast_hour(1, model_id)
        file_name = model_config["file_pattern"].format(init_hour=init_hour, forecast_hour=fhour_str)
        dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
        url = f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&{model_config['availability_check_var']}=on"
        
        try:
            r = requests.head(url, timeout=2)
            if r.status_code == 200:
                run_id = f"run_{date_str}_{init_hour}"
                found_runs.append(run_id)
                print(f"  Found {run_id}")
        except:
            pass
            
    return found_runs

def main():
    for model in MODELS_TO_PROCESS:
        runs = get_runs_for_model(model, DAYS_BACK)
        print(f"\nProcessing {len(runs)} runs for {model}: {runs}")
        
        for run_id in runs:
            print(f"\n>>> Building tiles for {model} {run_id}...")
            # Check if tiles already exist (optimization)
            # Just verify one variable like t2m
            tile_path = f"cache/tiles/{REGION}/0.100deg/{model}/{run_id}/t2m.npz"
            if os.path.exists(tile_path):
                print(f"  Tiles already exist for {run_id}, skipping.")
                continue
                
            cmd = [
                sys.executable, "build_tiles.py",
                "--region", REGION,
                "--model", model,
                "--run", run_id,
                "--clean-gribs",
                # Default to all variables
            ]
            
            # For HRRR, limit max hours if needed, but 'full data' was requested.
            # HRRR default is 24.
            # NAM default 60.
            # GFS default 384 -> Cap at 168 (7 days) per request
            if model == "gfs":
                cmd.extend(["--max-hours", "168"])
            
            ret = subprocess.call(cmd)
            if ret != 0:
                print(f"  Failed to build tiles for {run_id}")
            
            # Rate limit politeness
            print("  Sleeping 5s...")
            time.sleep(5)

if __name__ == "__main__":
    main()
