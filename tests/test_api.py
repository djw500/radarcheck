"""
Tests for Flask API endpoints.
"""

import os
import pytest
from unittest.mock import patch
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

# --- API Key Authentication Tests ---

def test_api_no_auth_required_when_key_not_set(client):
    """Test API requests work when RADARCHECK_API_KEY is not set."""
    response = client.get('/api/status/summary')
    assert response.status_code == 200

def test_api_requires_key_when_set(client_with_api_key):
    """Test API returns 401 when key is set but not provided."""
    response = client_with_api_key.get('/api/status/summary')
    assert response.status_code == 401
    data = response.get_json()
    assert 'error' in data

def test_api_accepts_valid_key(client_with_api_key):
    """Test API accepts request with valid X-API-Key header."""
    response = client_with_api_key.get(
        '/api/status/summary',
        headers={'X-API-Key': 'test-secret-key'}
    )
    assert response.status_code == 200

def test_api_rejects_invalid_key(client_with_api_key):
    """Test API returns 401 with wrong X-API-Key header."""
    response = client_with_api_key.get(
        '/api/status/summary',
        headers={'X-API-Key': 'wrong-key'}
    )
    assert response.status_code == 401

def test_health_no_auth_required(client_with_api_key):
    """Test /health is accessible without API key."""
    response = client_with_api_key.get('/health')
    assert response.status_code == 200
