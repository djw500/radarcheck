import requests
import datetime
from config import repomap

def check_vars(model_id):
    print(f"Checking variables for {model_id}...")
    model_config = repomap["MODELS"][model_id]
    
    # Construct a valid date/time
    now = datetime.datetime.now(datetime.timezone.utc)
    # Check a few hours back to find a valid run
    valid_run = None
    for h in range(6):
        t = now - datetime.timedelta(hours=h)
        date_str = t.strftime("%Y%m%d")
        init_hour = t.strftime("%H")
        
        # Check basic availability
        fhour = model_config.get("forecast_hour_digits", 2)
        fhour_str = f"0{1 if fhour==2 else '01'}"
        
        file_name = model_config["file_pattern"].format(init_hour=init_hour, forecast_hour=fhour_str)
        dir_path = model_config["dir_pattern"].format(date_str=date_str, init_hour=init_hour)
        
        url = f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&var_TMP=on"
        try:
            r = requests.head(url, timeout=5)
            if r.status_code == 200:
                valid_run = (date_str, init_hour, file_name, dir_path)
                break
        except:
            continue
            
    if not valid_run:
        print("  No recent run found to test against.")
        return

    date_str, init_hour, file_name, dir_path = valid_run
    print(f"  Using run: {date_str} {init_hour}z")

    # Check each variable
    for var_id, var_config in repomap["WEATHER_VARIABLES"].items():
        # Build query params
        params = [f"{p}=on" for p in var_config["nomads_params"]]
        levels = var_config["level_params"]
        query = "&".join(params + levels)
        
        url = f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&{query}"
        
        try:
            r = requests.head(url, timeout=5)
            status = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
            # GFS specific check: REFC is known to fail
            if model_id == 'gfs' and var_id == 'refc' and r.status_code != 200:
                status = "FAIL (Expected for GFS?)"
            print(f"  {var_id:<10} {status}")
        except Exception as e:
            print(f"  {var_id:<10} ERROR: {e}")

    # Check specific potential snow variables for NAM/GFS
    if model_id in ["nam_nest", "gfs"]:
        extra_vars = ["var_CSNOW", "var_WEASD"]
        for v in extra_vars:
            url = f"{model_config['nomads_url']}?file={file_name}&dir={dir_path}&{v}=on"
            try:
                r = requests.head(url, timeout=5)
                status = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
                print(f"  {v:<10} {status}")
            except Exception as e:
                print(f"  {v:<10} ERROR: {e}")

if __name__ == "__main__":
    check_vars("nam_nest")
    check_vars("gfs")
