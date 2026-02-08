import pytest
from unittest.mock import MagicMock, patch
import sqlite3
import json
from datetime import datetime, timezone

from jobs import init_db, enqueue, complete, JobStatus
from status_utils import get_scheduled_runs_status, scan_cache_status

@pytest.fixture
def mock_db(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    conn = init_db(db_path)
    return conn, db_path

@pytest.fixture
def mock_config(monkeypatch):
    from config import repomap
    monkeypatch.setitem(repomap, "JOBS_DB_PATH", "cache/jobs.db")
    monkeypatch.setitem(repomap, "MODELS", {
        "hrrr": {"name": "HRRR", "max_forecast_hours": 2, "update_frequency_hours": 1, "max_hours_by_init": {"default": 2}}
    })
    return repomap

@patch("status_utils.init_db")
def test_get_scheduled_runs_status_from_db(mock_init_db, mock_db, mock_config):
    conn, db_path = mock_db
    mock_init_db.return_value = conn

    # Enqueue some jobs
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H')}"

    # Ingest jobs: 1 complete, 1 pending
    enqueue(conn, "ingest_grib", {
        "model_id": "hrrr", "run_id": run_id, "variable_id": "t2m", "forecast_hour": 1
    })
    complete(conn, 1) # id 1

    enqueue(conn, "ingest_grib", {
        "model_id": "hrrr", "run_id": run_id, "variable_id": "t2m", "forecast_hour": 2
    })

    # Build job: pending
    enqueue(conn, "build_tile", {
        "model_id": "hrrr", "run_id": run_id, "variable_id": "t2m", "region_id": "ne"
    })

    # Call status function
    status_list = get_scheduled_runs_status(region="ne")

    # Verify
    assert len(status_list) >= 1
    run_status = next(r for r in status_list if r["run_id"] == run_id)

    # Since 1/2 ingest done, and build pending
    # We should see partial status or progress
    # Legacy format: "cached_hours": [1] (since hour 1 is done? No, ingest done != tile built)
    # If using DB, 'cached_hours' implies tiles exist.
    # If build_tile is not done, cached_hours should be []?
    # Or we can repurpose cached_hours to mean "ingested hours"?
    # The dashboard uses cached_hours to show progress bar.
    # So "ingested" is a good proxy.

    assert run_status["status"] == "partial"
    assert len(run_status["cached_hours"]) == 1
    assert run_status["cached_hours"][0] == 1
