import os
import glob
import numpy as np
from datetime import datetime
from collections import deque
from config import repomap

def scan_cache_status(region="ne"):
    """
    Scans the tile cache for the given region and returns the status of model runs.
    
    Returns:
        dict: {
            "model_id": {
                "name": "Model Name",
                "runs": {
                    "run_id": {
                        "status": "complete" | "partial",
                        "hours_present": int,
                        "expected_hours": int,
                        "last_modified": float (timestamp)
                    }
                }
            }
        }
    """
    tiles_dir = repomap["TILES_DIR"]
    region_config = repomap["TILING_REGIONS"].get(region)
    if not region_config:
        return {}
        
    res = region_config.get("default_resolution_deg", 0.1)
    # Directory structure: cache/tiles/{region}/{res}deg/{model}/{run}
    res_dir = f"{res:.3f}deg".rstrip("0").rstrip(".")
    base_dir = os.path.join(tiles_dir, region, res_dir)
    
    status = {}
    
    if not os.path.exists(base_dir):
        return status
        
    models = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    
    for model_id in models:
        model_config = repomap["MODELS"].get(model_id, {})
        status[model_id] = {
            "name": model_config.get("name", model_id),
            "runs": {}
        }
        
        model_path = os.path.join(base_dir, model_id)
        runs = [d for d in os.listdir(model_path) if d.startswith("run_")]
        
        for run_id in runs:
            run_path = os.path.join(model_path, run_id)
            
            # Check for a proxy variable (t2m) to determine completeness
            # This logic mirrors tiles_exist in build_tiles_scheduled.py
            npz_path = os.path.join(run_path, "t2m.npz")
            
            hours_present = 0
            expected_hours = model_config.get("max_forecast_hours", 24)
            run_status = "partial"
            last_modified = 0
            
            if os.path.exists(npz_path):
                last_modified = os.path.getmtime(npz_path)
                try:
                    with np.load(npz_path) as data:
                        if 'hours' in data:
                            hours_present = len(data['hours'])
                except Exception:
                    pass
            
            # Status determination
            # Allow some tolerance or specific logic? 
            # For now: >= 90% is complete
            if hours_present >= expected_hours * 0.9:
                run_status = "complete"
            elif hours_present == 0:
                run_status = "empty" # Or just don't list it? Better to list.
            
            status[model_id]["runs"][run_id] = {
                "status": run_status,
                "hours_present": hours_present,
                "expected_hours": expected_hours,
                "last_modified": last_modified
            }
            
    return status

def _get_dir_size(path):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += _get_dir_size(entry.path)
    return total

def get_disk_usage():
    """
    Calculates disk usage for cache directories.
    Returns:
        dict: {
            "total": int,
            "gribs": { "total": int, "model_id": int, ... },
            "tiles": { "total": int, "models": { "model_id": int } }
        }
    """
    grib_dir = repomap["GRIB_CACHE_DIR"]
    tiles_dir = repomap["TILES_DIR"]
    
    usage = {
        "total": 0,
        "gribs": {"total": 0},
        "tiles": {"total": 0, "models": {}}
    }
    
    # GRIBS
    if os.path.exists(grib_dir):
        usage["gribs"]["total"] = _get_dir_size(grib_dir)
        for model_id in os.listdir(grib_dir):
            model_path = os.path.join(grib_dir, model_id)
            if os.path.isdir(model_path):
                size = _get_dir_size(model_path)
                usage["gribs"][model_id] = size
    
    # TILES
    # Tiles structure is complex: tiles/{region}/{res}/{model}
    # We want to aggregate by model across all regions/resolutions
    if os.path.exists(tiles_dir):
        usage["tiles"]["total"] = _get_dir_size(tiles_dir)
        
        # Walk to find model directories
        # We assume model IDs are known from config to avoid scanning too deep blindly
        # Or we can iterate regions -> res -> models
        
        # Iterate known models and sum up their usage across all regions
        known_models = repomap["MODELS"].keys()
        
        for region in os.listdir(tiles_dir):
            region_path = os.path.join(tiles_dir, region)
            if not os.path.isdir(region_path): continue
            
            for res in os.listdir(region_path):
                res_path = os.path.join(region_path, res)
                if not os.path.isdir(res_path): continue
                
                for model_id in os.listdir(res_path):
                    if model_id in known_models:
                        model_path = os.path.join(res_path, model_id)
                        size = _get_dir_size(model_path)
                        usage["tiles"]["models"][model_id] = usage["tiles"]["models"].get(model_id, 0) + size

    usage["total"] = usage["gribs"]["total"] + usage["tiles"]["total"]
    return usage

def read_scheduler_logs(lines=100, log_path='logs/scheduler_detailed.log'):
    """Reads the last N lines from the scheduler log."""
    if not os.path.exists(log_path):
        return []
    
    try:
        with open(log_path, 'r') as f:
            return [line.rstrip('\n') for line in deque(f, lines)]
    except Exception:
        return []
