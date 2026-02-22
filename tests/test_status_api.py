import pytest
from unittest.mock import patch
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client


@patch("routes.status.get_disk_usage")
@patch("routes.status.read_scheduler_status")
@patch("routes.status.get_job_queue_status")
def test_status_summary_endpoint(mock_jobs, mock_sched, mock_disk, client):
    mock_disk.return_value = {"total": 1000}
    mock_sched.return_value = {}
    mock_jobs.return_value = {}

    response = client.get("/api/status/summary")

    assert response.status_code == 200
    data = response.get_json()
    assert data["disk_usage"]["total"] == 1000


@patch("routes.status.read_scheduler_logs")
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
