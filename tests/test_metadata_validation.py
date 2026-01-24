import pytest
import os
import json
from tiles import is_tile_valid

def test_is_tile_valid_success(tmp_path):
    meta_path = tmp_path / "test.meta.json"
    region_config = {
        "lat_min": 40.0, "lat_max": 45.0,
        "lon_min": -75.0, "lon_max": -70.0
    }
    meta = {
        "lat_min": 40.0, "lat_max": 45.0,
        "lon_min": -75.0, "lon_max": -70.0,
        "resolution_deg": 0.1
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    
    assert is_tile_valid(str(meta_path), region_config, 0.1) is True

def test_is_tile_valid_bounds_mismatch(tmp_path):
    meta_path = tmp_path / "test.meta.json"
    region_config = {
        "lat_min": 40.0, "lat_max": 45.0,
        "lon_min": -75.0, "lon_max": -70.0
    }
    meta = {
        "lat_min": 30.0, "lat_max": 45.0, # Mismatch
        "lon_min": -75.0, "lon_max": -70.0,
        "resolution_deg": 0.1
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    
    assert is_tile_valid(str(meta_path), region_config, 0.1) is False

def test_is_tile_valid_res_mismatch(tmp_path):
    meta_path = tmp_path / "test.meta.json"
    region_config = {
        "lat_min": 40.0, "lat_max": 45.0,
        "lon_min": -75.0, "lon_max": -70.0
    }
    meta = {
        "lat_min": 40.0, "lat_max": 45.0,
        "lon_min": -75.0, "lon_max": -70.0,
        "resolution_deg": 0.2 # Mismatch
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)
    
    assert is_tile_valid(str(meta_path), region_config, 0.1) is False
