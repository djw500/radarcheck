import pytest
from app import app
import json
from unittest.mock import patch, MagicMock

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@patch('app.get_scheduled_runs_status')
def test_api_status_scheduled(mock_get_status, client):
    # Mock the return value
    mock_get_status.return_value = [
        {
            "model_id": "hrrr",
            "run_id": "run_20260125_12",
            "status": "complete",
            "cached_hours": [1, 2, 3],
            "expected_hours": [1, 2, 3]
        }
    ]
    
    response = client.get('/api/status/scheduled')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "runs" in data
    assert len(data["runs"]) == 1
    assert data["runs"][0]["model_id"] == "hrrr"
