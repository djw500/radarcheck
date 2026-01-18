"""
Tests for Flask API endpoints.

Tests the API contract for the iOS client, including:
- /api/locations, /api/runs/<location>, /api/valid_times/<location>/<run>
- /frame/<location>/<run>/<hour> image serving
- /health endpoint
- API key authentication
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import app


@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def client_with_api_key():
    """Create a test client with API key authentication enabled."""
    app.config['TESTING'] = True
    with patch.dict(os.environ, {'RADARCHECK_API_KEY': 'test-secret-key'}):
        # Need to reimport to pick up the new env var
        import importlib
        import app as app_module
        importlib.reload(app_module)
        with app_module.app.test_client() as client:
            yield client
        # Reload again to reset
        importlib.reload(app_module)


# --- Health Check Tests ---

def test_health_check(client):
    """Test /health endpoint returns status ok."""
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
    assert 'timestamp' in data
    assert 'locations_count' in data


# --- API Locations Tests ---

def test_api_locations_returns_list(client):
    """Test /api/locations returns a list."""
    response = client.get('/api/locations')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_locations_structure(client):
    """Test location objects have required fields."""
    response = client.get('/api/locations')
    assert response.status_code == 200
    data = response.get_json()
    
    # Skip if no locations are available
    if len(data) == 0:
        pytest.skip("No locations available in cache")
    
    location = data[0]
    assert 'id' in location
    assert 'name' in location
    assert 'init_time' in location
    assert 'run_id' in location


# --- API Variables Tests ---

def test_api_variables_lists_reflectivity(client):
    """Test /api/variables returns available variable categories."""
    response = client.get('/api/variables')
    assert response.status_code == 200
    data = response.get_json()
    assert "categories" in data
    assert "variables" in data

    all_variables = []
    for category in data["categories"].values():
        all_variables.extend(category.get("variables", []))

    assert "refc" in all_variables


def test_api_models_lists_hrrr(client):
    """Test /api/models returns available models."""
    response = client.get('/api/models')
    assert response.status_code == 200
    data = response.get_json()
    assert "models" in data
    assert "hrrr" in data["models"]


# --- API Runs Tests ---

def test_api_runs_for_valid_location(client):
    """Test /api/runs/<location> returns run list for valid location."""
    # Get a valid location first
    locations_response = client.get('/api/locations')
    locations = locations_response.get_json()
    
    if len(locations) == 0:
        pytest.skip("No locations available in cache")
    
    location_id = locations[0]['id']
    response = client.get(f'/api/runs/{location_id}')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    
    if len(data) > 0:
        run = data[0]
        assert 'run_id' in run
        assert 'init_time' in run


def test_api_runs_for_invalid_location(client):
    """Test /api/runs/<invalid> returns empty list."""
    response = client.get('/api/runs/nonexistent_location_xyz')
    assert response.status_code == 200
    data = response.get_json()
    assert data == []


# --- API Valid Times Tests ---

def test_api_valid_times_structure(client):
    """Test valid_times response has required fields."""
    # Get a valid location and run first
    locations_response = client.get('/api/locations')
    locations = locations_response.get_json()
    
    if len(locations) == 0:
        pytest.skip("No locations available in cache")
    
    location_id = locations[0]['id']
    runs_response = client.get(f'/api/runs/{location_id}')
    runs = runs_response.get_json()
    
    if len(runs) == 0:
        pytest.skip("No runs available for location")
    
    run_id = runs[0]['run_id']
    response = client.get(f'/api/valid_times/{location_id}/{run_id}')
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    
    if len(data) > 0:
        valid_time = data[0]
        assert 'forecast_hour' in valid_time
        assert 'valid_time' in valid_time
        assert 'frame_path' in valid_time


# --- Frame Endpoint Tests ---

def test_frame_returns_png(client):
    """Test /frame/<location>/<run>/<hour> returns image/png."""
    # Get a valid location first
    locations_response = client.get('/api/locations')
    locations = locations_response.get_json()
    
    if len(locations) == 0:
        pytest.skip("No locations available in cache")
    
    location_id = locations[0]['id']
    
    response = client.get(f'/frame/{location_id}/latest/1')
    
    # Could be 200 (success) or 404 (no frames yet)
    if response.status_code == 200:
        assert response.content_type == 'image/png'
    else:
        assert response.status_code == 404


def test_frame_invalid_location(client):
    """Test /frame with invalid location returns 400."""
    response = client.get('/frame/nonexistent_xyz/latest/1')
    assert response.status_code == 400


def test_frame_invalid_hour_too_low(client):
    """Test /frame with hour < 1 returns 400."""
    # Get a valid location first
    locations_response = client.get('/api/locations')
    locations = locations_response.get_json()
    
    if len(locations) == 0:
        pytest.skip("No locations available in cache")
    
    location_id = locations[0]['id']
    response = client.get(f'/frame/{location_id}/latest/0')
    assert response.status_code == 400


def test_frame_invalid_hour_too_high(client):
    """Test /frame with hour > 24 returns 400."""
    # Get a valid location first
    locations_response = client.get('/api/locations')
    locations = locations_response.get_json()
    
    if len(locations) == 0:
        pytest.skip("No locations available in cache")
    
    location_id = locations[0]['id']
    response = client.get(f'/frame/{location_id}/latest/25')
    assert response.status_code == 400


# --- API Key Authentication Tests ---

def test_api_no_auth_required_when_key_not_set(client):
    """Test API requests work when RADARCHECK_API_KEY is not set."""
    # The client fixture doesn't set the API key, so auth should be skipped
    response = client.get('/api/locations')
    assert response.status_code == 200


def test_api_requires_key_when_set(client_with_api_key):
    """Test API returns 401 when key is set but not provided."""
    response = client_with_api_key.get('/api/locations')
    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data


def test_api_accepts_valid_key(client_with_api_key):
    """Test API accepts request with valid X-API-Key header."""
    response = client_with_api_key.get(
        '/api/locations',
        headers={'X-API-Key': 'test-secret-key'}
    )
    assert response.status_code == 200


def test_api_rejects_invalid_key(client_with_api_key):
    """Test API returns 401 with wrong X-API-Key header."""
    response = client_with_api_key.get(
        '/api/locations',
        headers={'X-API-Key': 'wrong-key'}
    )
    assert response.status_code == 401


def test_health_no_auth_required(client_with_api_key):
    """Test /health is accessible without API key (for monitoring)."""
    response = client_with_api_key.get('/health')
    assert response.status_code == 200


def _build_center_values_cache(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    run_dir = cache_dir / "test-location" / "run_20240101_00"
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "metadata.txt").write_text(
        "date_str=20240101\n"
        "init_hour=00\n"
        "init_time=2024-01-01 00:00:00\n"
        "run_id=run_20240101_00\n"
        "location_name=Test Location\n"
        "center_lat=40.0\n"
        "center_lon=-75.0\n"
        "zoom=1.5\n"
    )
    (run_dir / "frame_01.png").write_bytes(b"fakepng")

    center_values = {
        "location_id": "test-location",
        "run_id": "run_20240101_00",
        "init_time": "2024-01-01 00:00:00",
        "center_lat": 40.0,
        "center_lon": -75.0,
        "units": "dBZ",
        "values": [
            {"forecast_hour": 1, "valid_time": "2024-01-01 01:00:00", "value": 12.3},
            {"forecast_hour": 2, "valid_time": "2024-01-01 02:00:00", "value": 15.7},
        ],
    }
    (run_dir / "center_values.json").write_text(json.dumps(center_values))
    return cache_dir


def test_api_center_values_for_run(client, tmp_path, monkeypatch):
    """Test /api/center_values/<location>/<run> returns center values."""
    cache_dir = _build_center_values_cache(tmp_path)
    monkeypatch.setitem(app_module.repomap, "CACHE_DIR", str(cache_dir))
    monkeypatch.setitem(
        app_module.repomap,
        "LOCATIONS",
        {
            "test-location": {
                "name": "Test Location",
                "center_lat": 40.0,
                "center_lon": -75.0,
                "zoom": 1.5,
                "lat_min": 39.0,
                "lat_max": 41.0,
                "lon_min": -76.0,
                "lon_max": -74.0,
            }
        },
    )
    monkeypatch.setattr(app_module, "API_KEY", None)

    response = client.get("/api/center_values/test-location/run_20240101_00")
    assert response.status_code == 200
    data = response.get_json()
    assert data["run_id"] == "run_20240101_00"
    assert data["values"][0]["forecast_hour"] == 1


def test_api_center_values_for_location(client, tmp_path, monkeypatch):
    """Test /api/center_values/<location> returns a list of run payloads."""
    cache_dir = _build_center_values_cache(tmp_path)
    monkeypatch.setitem(app_module.repomap, "CACHE_DIR", str(cache_dir))
    monkeypatch.setitem(
        app_module.repomap,
        "LOCATIONS",
        {
            "test-location": {
                "name": "Test Location",
                "center_lat": 40.0,
                "center_lon": -75.0,
                "zoom": 1.5,
                "lat_min": 39.0,
                "lat_max": 41.0,
                "lon_min": -76.0,
                "lon_max": -74.0,
            }
        },
    )
    monkeypatch.setattr(app_module, "API_KEY", None)

    response = client.get("/api/center_values/test-location")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert data[0]["run_id"] == "run_20240101_00"
