import pytest
from unittest.mock import patch, MagicMock
import app as app_module
from app import app as flask_app

@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    app_module.API_KEY = None
    with flask_app.test_client() as client:
        yield client

@patch("app.get_disk_usage")
def test_status_summary_endpoint(mock_disk, client):
    mock_disk.return_value = {"total": 1000}

    response = client.get("/api/status/summary")

    assert response.status_code == 200
    data = response.get_json()
    assert "cache_status" not in data
    assert data["disk_usage"]["total"] == 1000

@patch("app.read_scheduler_logs")
def test_status_logs_endpoint(mock_read, client):
    mock_read.return_value = ["Log 1", "Log 2"]
    
    response = client.get("/api/status/logs?lines=10")
    
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["lines"]) == 2
    assert data["lines"][0] == "Log 1"
    mock_read.assert_called_with(lines=10)

def test_status_page_route(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert b"System Status" in response.data
