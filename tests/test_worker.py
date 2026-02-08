import pytest
from unittest.mock import MagicMock, patch
import json
import sqlite3
import os
import threading
import time

from jobs import init_db, enqueue, JobStatus, get_jobs
from worker import run_worker, execute_job

@pytest.fixture
def mock_db(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    conn = init_db(db_path)
    yield conn, db_path
    conn.close()

@patch("worker.fetch_grib")
def test_worker_executes_ingest_grib_job(mock_fetch, mock_db):
    conn, db_path = mock_db

    # Enqueue a job
    args = {
        "model_id": "hrrr",
        "variable_id": "t2m",
        "date_str": "20260101",
        "init_hour": "12",
        "forecast_hour": 1,
        "run_id": "run_20260101_12"
    }
    enqueue(conn, "ingest_grib", args)

    # Run worker in a separate thread (or just call execute_job directly if we want to test logic)
    # Testing execute_job directly is simpler.

    job = get_jobs(conn)[0]
    execute_job(job, conn) # pass conn for logging or updates if needed?

    mock_fetch.assert_called_with(
        model_id="hrrr",
        variable_id="t2m",
        date_str="20260101",
        init_hour="12",
        forecast_hour="01", # format_forecast_hour logic
        run_id="run_20260101_12",
        use_hourly=False
    )

@patch("worker.build_region_tiles")
def test_worker_executes_build_tile_job(mock_build_region, mock_db):
    conn, db_path = mock_db

    args = {
        "model_id": "hrrr",
        "run_id": "run_20260101_12",
        "variable_id": "t2m",
        "region_id": "ne"
    }

    job = {"id": 1, "type": "build_tile", "args_json": json.dumps(args), "args": args}

    # execute_job checks for ingest jobs. Since we have none in DB, it should raise RetryLater and fail()
    # BUT wait, expected_hours = get_run_forecast_hours which uses real logic.
    # We should probably mock get_run_forecast_hours to return empty list or match existing jobs.

    with patch("worker.get_run_forecast_hours", return_value=[]):
        execute_job(job, conn)

    mock_build_region.assert_called_with(
        region_id="ne",
        model_id="hrrr",
        run_id="run_20260101_12",
        variables=["t2m"],
        resolution_deg=None,
        max_hours=48, # default for hrrr from config? Mock config used real config?
        clean_gribs=False,
        audit_only=False
    )

def test_worker_handles_failure(mock_db):
    conn, db_path = mock_db

    # Mock a failing function
    with patch("worker.execute_ingest_grib", side_effect=Exception("Boom")):
        job = {"id": 1, "type": "ingest_grib", "args_json": "{}", "args": {}}

        # execute_job raises exception, wrapper handles it
        with pytest.raises(Exception):
            execute_job(job, conn)
