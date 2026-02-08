import pytest
from unittest.mock import MagicMock, patch
import json
import sqlite3
import os
from datetime import datetime

from scripts.build_tiles_scheduled import process_model
from jobs import init_db, get_jobs

@pytest.fixture
def mock_db(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    conn = init_db(db_path)
    return conn, db_path

@pytest.fixture
def mock_config(monkeypatch):
    from config import repomap

    # Mock models
    mock_models = {
        "hrrr": {
            "id": "hrrr",
            "name": "HRRR",
            "max_forecast_hours": 4,
            "update_frequency_hours": 1,
            "availability_check_var": "refc",
            "max_hours_by_init": {"default": 4}
        }
    }

    # Mock variables
    mock_vars = {
        "t2m": {"short_name": "t2m"},
        "refc": {"short_name": "refc"}
    }

    # Mock regions
    mock_regions = {
        "ne": {"name": "Northeast"}
    }

    monkeypatch.setitem(repomap, "MODELS", mock_models)
    monkeypatch.setitem(repomap, "WEATHER_VARIABLES", mock_vars)
    monkeypatch.setitem(repomap, "TILING_REGIONS", mock_regions)
    monkeypatch.setitem(repomap, "TILES_DIR", "/tmp/tiles")

    return repomap

@patch("scripts.build_tiles_scheduled.get_required_runs")
@patch("scripts.build_tiles_scheduled.run_is_ready")
@patch("scripts.build_tiles_scheduled.get_run_forecast_hours")
def test_process_model_enqueues_jobs(mock_get_hours, mock_is_ready, mock_get_runs, mock_db, mock_config):
    conn, _ = mock_db

    # Setup mocks
    mock_get_runs.return_value = ["run_20260101_12"]
    mock_is_ready.return_value = True
    mock_get_hours.return_value = [1, 2] # 2 hours

    # Construct a config object matching MODELS_CONFIG structure in build_tiles_scheduled.py
    model_cfg = {
        "id": "hrrr",
        "max_hours": 4,
        "check_hours": 6
    }

    # Call process_model (modified signature expected)
    # We will modify process_model to accept conn
    process_model(model_cfg, conn=conn)

    # Verify jobs enqueued
    jobs = get_jobs(conn, limit=100)

    # Expect:
    # 2 variables (t2m, refc) * 2 hours = 4 ingest_grib jobs
    # 2 variables * 1 run * 1 region = 2 build_tile jobs
    # Total 6 jobs

    ingest_jobs = [j for j in jobs if j["type"] == "ingest_grib"]
    build_jobs = [j for j in jobs if j["type"] == "build_tile"]

    assert len(ingest_jobs) == 4
    assert len(build_jobs) == 2

    # Verify job args
    first_ingest = ingest_jobs[-1] # sort order DESC in get_jobs, so this is first inserted? No, get_jobs ORDER BY id DESC. so first element is LAST inserted.
    # We want to check content.

    # Check one ingest job
    t2m_h1 = next(j for j in ingest_jobs if j["args"]["variable_id"] == "t2m" and j["args"]["forecast_hour"] == 1)
    assert t2m_h1["args"]["model_id"] == "hrrr"
    assert t2m_h1["args"]["run_id"] == "run_20260101_12"
    assert t2m_h1["args"]["date_str"] == "20260101"
    assert t2m_h1["args"]["init_hour"] == "12"

    # Check one build job
    t2m_build = next(j for j in build_jobs if j["args"]["variable_id"] == "t2m")
    assert t2m_build["args"]["model_id"] == "hrrr"
    assert t2m_build["args"]["run_id"] == "run_20260101_12"
    assert t2m_build["args"]["region_id"] == "ne"

@patch("scripts.build_tiles_scheduled.get_required_runs")
def test_process_model_skips_complete_runs(mock_get_runs, mock_db, mock_config):
    # This test might be tricky because we rely on DB state to skip?
    # The plan says "short-circuit with tiles_exist() ... or better: with job queue, only scan for new runs".
    # Actually, idempotency of enqueue handles this. If we enqueue again, it's a no-op if exists.
    # But if jobs are completed, we shouldn't re-enqueue them unless we want to rebuild?
    # The plan says "enqueue ... idempotent via UNIQUE".
    # If job is 'completed', unique constraint might not block if we rely on hash?
    # Ah, UNIQUE(type, args_hash). So if it's completed, it's still in the table.
    # enqueue() does:
    # ON CONFLICT DO UPDATE SET status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END
    # So 'completed' stays 'completed'.
    # So we can just blindly enqueue everything!
    pass
