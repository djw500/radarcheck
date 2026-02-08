import pytest
from unittest.mock import patch, MagicMock
from app import app
import json
import numpy as np
from datetime import datetime
import pytz

@pytest.fixture
def client():
    app.config['TESTING'] = True
    # Ensure API key auth is disabled for these tests
    with patch("app.API_KEY", None):
        with app.test_client() as client:
            yield client

@pytest.fixture
def mock_tiles():
    with patch("app.list_tile_models") as mock_list_models, \
         patch("app.list_tile_runs") as mock_list_runs, \
         patch("app.list_tile_variables") as mock_list_vars, \
         patch("app.load_timeseries_for_point") as mock_load_ts, \
         patch("app.infer_region_for_latlon") as mock_infer:

        yield {
            "list_models": mock_list_models,
            "list_runs": mock_list_runs,
            "list_vars": mock_list_vars,
            "load_ts": mock_load_ts,
            "infer": mock_infer
        }

def test_api_infer_region(client, mock_tiles):
    mock_tiles["infer"].return_value = "ne"
    with patch.dict("app.repomap", {"TILING_REGIONS": {"ne": {"name": "Northeast", "lat_min": 30, "lat_max": 50, "lon_min": -80, "lon_max": -60}}}):
        resp = client.get("/api/infer_region?lat=40&lon=-75")
        assert resp.status_code == 200
        data = resp.json
        assert data["region_id"] == "ne"

def test_api_table_multimodel(client, mock_tiles):
    mock_tiles["infer"].return_value = "ne"
    mock_tiles["list_models"].return_value = {"hrrr": ["run_20230101_00"]}
    mock_tiles["list_vars"].return_value = {"t2m": {}}
    mock_tiles["load_ts"].return_value = (np.array([1]), np.array([20.0]))

    fixed_now = datetime(2023, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

    # We need to ensure strptime returns a real datetime because app.py uses it
    real_datetime = datetime

    with patch.dict("app.repomap", {
        "TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}},
        "MODELS": {"hrrr": {"name": "HRRR", "max_forecast_hours": 10}},
        "WEATHER_VARIABLES": {"t2m": {}}
    }), patch("app.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        # Forward strptime to real datetime
        mock_dt.strptime.side_effect = real_datetime.strptime

        resp = client.get("/api/table/multimodel?lat=40&lon=-75")
        assert resp.status_code == 200
        data = resp.json
        assert "hrrr" in data["models"]
        assert len(data["rows"]) >= 1
        assert data["rows"][0]["hrrr_t2m"] == 20.0

def test_api_table_multirun(client, mock_tiles):
    mock_tiles["infer"].return_value = "ne"
    mock_tiles["list_runs"].return_value = ["run_20230101_00", "run_20230101_01"]
    mock_tiles["list_vars"].return_value = {"t2m": {}}
    mock_tiles["load_ts"].return_value = (np.array([1]), np.array([20.0]))

    with patch.dict("app.repomap", {
        "TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}},
        "MODELS": {"hrrr": {"name": "HRRR", "max_forecast_hours": 10}},
        "WEATHER_VARIABLES": {"t2m": {}}
    }):
        resp = client.get("/api/table/multirun?lat=40&lon=-75&model=hrrr")
        assert resp.status_code == 200
        data = resp.json
        assert "run_20230101_00" in data["runs"]
        assert len(data["rows"]) >= 1

def test_api_table_bylatlon(client, mock_tiles):
    mock_tiles["infer"].return_value = "ne"
    mock_tiles["list_runs"].return_value = ["run_20230101_00"]
    # We must mock list_models too because api_table_bylatlon uses it for diagnostics
    mock_tiles["list_models"].return_value = {"hrrr": ["run_20230101_00"]}
    mock_tiles["list_vars"].return_value = {"t2m": {}}
    mock_tiles["load_ts"].return_value = (np.array([1]), np.array([20.0]))

    with patch.dict("app.repomap", {
        "TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}},
        "MODELS": {"hrrr": {"name": "HRRR"}},
        "WEATHER_VARIABLES": {"t2m": {}}
    }):
        resp = client.get("/api/table/bylatlon?lat=40&lon=-75&model=hrrr")
        assert resp.status_code == 200
        data = resp.json
        assert data["metadata"]["run_id"] == "run_20230101_00"
        assert data["rows"][0]["t2m"] == 20.0

def test_api_tile_runs(client, mock_tiles):
    mock_tiles["list_runs"].return_value = ["run_20230101_00"]
    with patch.dict("app.repomap", {"TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}}}):
        resp = client.get("/api/tile_runs/hrrr?region=ne")
        assert resp.status_code == 200
        assert resp.json["runs"] == ["run_20230101_00"]

def test_api_tile_run_detail(client, mock_tiles):
    mock_tiles["list_vars"].return_value = {"t2m": {"hours": [1]}}
    with patch.dict("app.repomap", {"TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}}}):
        resp = client.get("/api/tile_run_detail/hrrr/run_20230101_00?region=ne")
        assert resp.status_code == 200
        assert "t2m" in resp.json["variables"]

def test_status_endpoints(client):
    with patch("app.scan_cache_status", return_value={}), \
         patch("app.get_disk_usage", return_value={}), \
         patch("app.read_scheduler_status", return_value={}), \
         patch("app.get_scheduled_runs_status", return_value=[]), \
         patch("app.read_scheduler_logs", return_value=[]):

        assert client.get("/api/status/summary").status_code == 200
        assert client.get("/api/status/scheduled").status_code == 200
        assert client.get("/api/status/logs").status_code == 200

def test_api_tile_models(client, mock_tiles):
    mock_tiles["list_models"].return_value = {"hrrr": ["run_1"]}
    with patch.dict("app.repomap", {"TILING_REGIONS": {"ne": {"default_resolution_deg": 0.1}}}):
        resp = client.get("/api/tile_models?region=ne")
        assert resp.status_code == 200
        assert "hrrr" in resp.json
