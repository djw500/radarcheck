import pytest
from unittest.mock import patch, MagicMock
from app import app
import json
from datetime import datetime
import pytz
import numpy as np

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with patch("app.API_KEY", None):
        with app.test_client() as client:
            yield client

# --- Mock Data ---
MOCK_MODELS = {
    "hrrr": {"name": "HRRR", "max_forecast_hours": 48, "update_frequency_hours": 1},
    "gfs": {"name": "GFS", "max_forecast_hours": 384, "update_frequency_hours": 6}
}
MOCK_REGIONS = {
    "ne": {"name": "Northeast", "lat_min": 30, "lat_max": 50, "lon_min": -80, "lon_max": -60, "default_resolution_deg": 0.1}
}
MOCK_VARS = {
    "t2m": {"display_name": "Temp", "units": "F"},
    "asnow": {"display_name": "Snow", "units": "in", "is_accumulation": True}
}

@patch.dict("app.repomap", {
    "MODELS": MOCK_MODELS,
    "TILING_REGIONS": MOCK_REGIONS,
    "WEATHER_VARIABLES": MOCK_VARS,
    "DEFAULT_MODEL": "hrrr",
    "DEFAULT_VARIABLE": "t2m",
    "CACHE_DIR": "/tmp/cache",
    "TILES_DIR": "/tmp/tiles",
    "LOCATIONS": {
        "philly": {"name": "Philly", "center_lat": 40.0, "center_lon": -75.0, "zoom": 10}
    }
})
class TestAppComprehensive:

    # --- Basic Routes ---
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json["status"] == "ok"

    def test_metrics(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_index(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_status_page(self, client):
        resp = client.get("/status")
        assert resp.status_code == 200

    def test_explainer(self, client):
        resp = client.get("/explainer")
        assert resp.status_code == 200

    def test_table_geo_view(self, client):
        resp = client.get("/table/geo")
        assert resp.status_code == 200

    def test_forecast_view(self, client):
        resp = client.get("/forecast")
        assert resp.status_code == 200

    # --- API Metadata ---
    def test_api_models(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert "hrrr" in resp.json["models"]

    def test_api_regions(self, client):
        resp = client.get("/api/regions")
        assert resp.status_code == 200
        assert "ne" in resp.json

    def test_api_layers(self, client):
        resp = client.get("/api/layers")
        assert resp.status_code == 200

    def test_api_variables(self, client):
        resp = client.get("/api/variables")
        assert resp.status_code == 200
        assert "t2m" in resp.json["variables"]

    def test_api_locations(self, client):
        with patch("app.get_available_locations", return_value=[{"id": "philly"}]):
            resp = client.get("/api/locations")
            assert resp.status_code == 200
            assert resp.json[0]["id"] == "philly"

    # --- Tile API Infer ---
    def test_api_infer_region(self, client):
        resp = client.get("/api/infer_region?lat=40&lon=-70")
        assert resp.status_code == 200
        assert resp.json["region_id"] == "ne"

        resp = client.get("/api/infer_region?lat=0&lon=0")
        assert resp.status_code == 404

    # --- Tile API Models/Runs ---
    @patch("app.list_tile_models")
    def test_api_tile_models(self, mock_list, client):
        mock_list.return_value = {"hrrr": ["run1"]}
        resp = client.get("/api/tile_models?region=ne")
        assert resp.status_code == 200
        assert "hrrr" in resp.json

    @patch("app.list_tile_runs")
    def test_api_tile_runs(self, mock_list, client):
        mock_list.return_value = ["run1", "run2"]
        resp = client.get("/api/tile_runs/hrrr?region=ne")
        assert resp.status_code == 200
        assert resp.json["runs"] == ["run1", "run2"]

    @patch("app.list_tile_variables")
    def test_api_tile_run_detail(self, mock_list, client):
        mock_list.return_value = {"t2m": {"hours": [1]}}
        resp = client.get("/api/tile_run_detail/hrrr/run1")
        assert resp.status_code == 200
        assert "t2m" in resp.json["variables"]

    # --- Table APIs ---
    @patch("app.list_tile_models")
    @patch("app.list_tile_variables")
    @patch("app.load_timeseries_for_point")
    def test_api_table_multimodel(self, mock_load, mock_vars, mock_models, client):
        # Let's use a valid run_id
        valid_run = "run_20230101_00"
        mock_models.return_value = {"hrrr": [valid_run]}
        mock_vars.return_value = {"t2m": {}}
        mock_load.return_value = (np.array([1]), np.array([32.0]))

        # We need to ensure datetime.now() inside api_table_multimodel
        # aligns with the run ID so the run isn't filtered out as "future" or "too old"?
        # Actually api_table_multimodel generates a timeline starting from NOW.
        # If NOW is far from run init, maybe no overlap?
        # Run init is 2023-01-01. If now is 2026, forecast hours (1) will be in 2023.
        # The table iterates from NOW to NOW+7days.
        # So we need to patch datetime.now to be close to 2023-01-01.

        fixed_now = datetime(2023, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)

        with patch("app.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.strptime.side_effect = datetime.strptime

            resp = client.get("/api/table/multimodel?lat=40&lon=-75")
            assert resp.status_code == 200
            # Check rows exist
            assert len(resp.json["rows"]) > 0

    @patch("app.list_tile_runs")
    @patch("app.list_tile_variables")
    @patch("app.load_timeseries_for_point")
    def test_api_table_multirun(self, mock_load, mock_vars, mock_runs, client):
        valid_run = "run_20230101_00"
        mock_runs.return_value = [valid_run]
        mock_vars.return_value = {"t2m": {}}
        mock_load.return_value = (np.array([1]), np.array([32.0]))

        resp = client.get("/api/table/multirun?lat=40&lon=-75&model=hrrr")
        assert resp.status_code == 200
        assert len(resp.json["rows"]) > 0

    @patch("app.list_tile_runs")
    @patch("app.list_tile_variables")
    @patch("app.load_timeseries_for_point")
    def test_api_table_bylatlon(self, mock_load, mock_vars, mock_runs, client):
        valid_run = "run_20230101_00"
        mock_runs.return_value = [valid_run]
        mock_vars.return_value = {"t2m": {}}
        mock_load.return_value = (np.array([1]), np.array([32.0]))

        resp = client.get("/api/table/bylatlon?lat=40&lon=-75&model=hrrr")
        assert resp.status_code == 200
        assert len(resp.json["rows"]) > 0

    # --- Frame/Tile Rendering ---
    @patch("app.load_grid_slice")
    def test_frame_tiles(self, mock_load, client):
        # Return 2x2 array
        mock_load.return_value = (
            np.zeros((2,2)),
            {"lat_min": 0, "lat_max": 1, "lon_min": 0, "lon_max": 1, "resolution_deg": 0.5}
        )
        resp = client.get("/frame/tiles/ne/hrrr/run1/t2m/1")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"

    # --- Status API ---
    @patch("app.scan_cache_status", return_value={})
    @patch("app.get_disk_usage", return_value={})
    @patch("app.read_scheduler_status", return_value={})
    def test_api_status_summary(self, mock_read, mock_du, mock_scan, client):
        # *mocks expands to (mock_scan, mock_du, mock_read, client) but client is fixture so it comes last?
        # No, pytests injects fixtures into arguments by name.
        # unittest.mock.patch as decorator appends mock args.
        # So signature is (self, mock_read, mock_du, mock_scan, client).
        # Order is reverse of decoration.
        resp = client.get("/api/status/summary")
        assert resp.status_code == 200

    @patch("app.get_scheduled_runs_status", return_value=[])
    def test_api_status_scheduled(self, mock_get, client):
        resp = client.get("/api/status/scheduled")
        assert resp.status_code == 200

    @patch("app.read_scheduler_logs", return_value=["log1"])
    def test_api_status_logs(self, mock_read, client):
        resp = client.get("/api/status/logs")
        assert resp.status_code == 200

    # --- Legacy/Other ---
    def test_api_alerts(self, client):
        with patch("app.get_alerts_for_location", return_value=[]):
            resp = client.get("/api/alerts/philly")
            assert resp.status_code == 200

    def test_custom_region(self, client):
        resp = client.get("/custom?lat=40&lon=-75&zoom=2")
        assert resp.status_code == 302 # Redirect

    # --- Error Cases ---
    def test_404_handlers(self, client):
        # infer_region_for_latlon returns None for 0,0 based on our mock region (30-50 lat)
        resp = client.get("/api/table/multimodel?lat=0&lon=0")
        assert resp.status_code == 400 # Actually app.py returns 400 "Point outside..." for inferred failure

    def test_location_view(self, client):
        with patch("app.get_available_models_for_location", return_value={"hrrr": {}}), \
             patch("app.get_location_runs", return_value=[{"run_id": "r1", "init_time": "t"}]), \
             patch("app.get_run_metadata", return_value={"init_time": "t"}), \
             patch("app.get_run_valid_times", return_value=[]), \
             patch("app.get_available_variables_for_run", return_value=["t2m"]):

            resp = client.get("/location/philly")
            assert resp.status_code == 200

    def test_summary_view(self, client):
        # Fix: Jinja template expects summary.temperature_range_f.min/max
        summary_data = {
            "summary": {
                "total_snowfall_inches": 1.0,
                "temperature_range_f": {"min": 20, "max": 30},
                "total_precipitation_inches": 0.1,
                "max_wind_gust_mph": 10
            },
            "units": {}
        }
        with patch("app.get_location_runs", return_value=[{"run_id": "r1"}]), \
             patch("app.summarize_run", return_value=summary_data):
            resp = client.get("/summary/philly")
            assert resp.status_code == 200

    def test_table_view(self, client):
        # Fix: build_forecast_table expects specific structure
        # variables -> { var_id: { values: { hour: { ... } }, config: {...} } }
        data = {
            "variables": {
                "t2m": {
                    "values": {1: {"value": 20.0, "valid_time": "t"}},
                    "config": {"units": "F"}
                }
            },
            "metadata": {"init_time": "t", "run_id": "r1"}
        }
        with patch("app.load_all_center_values", return_value=data):
             resp = client.get("/table/philly")
             assert resp.status_code == 200
