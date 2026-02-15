"""Tests for the scheduler's job enqueueing logic."""

import sqlite3
import sys
import os

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import repomap
from jobs import init_db, count_by_status, get_jobs


@pytest.fixture
def jobs_conn(tmp_path):
    """Create an in-memory jobs DB connection."""
    db_path = str(tmp_path / "test_jobs.db")
    conn = init_db(db_path)
    yield conn
    conn.close()


class TestEnqueueRunJobs:
    """Test enqueue_run_jobs from the scheduler."""

    def test_enqueue_creates_jobs(self, jobs_conn):
        """enqueue_run_jobs should create pending jobs for each variable × hour."""
        from scripts.build_tiles_scheduled import enqueue_run_jobs

        # Use a small max_hours so we get a manageable number of jobs
        region_id = list(repomap["TILING_REGIONS"].keys())[0]
        model_id = "hrrr"
        run_id = "run_20260215_12"

        n = enqueue_run_jobs(jobs_conn, region_id, model_id, run_id, max_hours=3)

        assert n > 0
        counts = count_by_status(jobs_conn)
        assert counts.get("pending", 0) == n

    def test_enqueue_is_idempotent(self, jobs_conn):
        """Calling enqueue_run_jobs twice should not create duplicates."""
        from scripts.build_tiles_scheduled import enqueue_run_jobs

        region_id = list(repomap["TILING_REGIONS"].keys())[0]
        model_id = "hrrr"
        run_id = "run_20260215_12"

        n1 = enqueue_run_jobs(jobs_conn, region_id, model_id, run_id, max_hours=3)
        n2 = enqueue_run_jobs(jobs_conn, region_id, model_id, run_id, max_hours=3)

        assert n1 > 0
        assert n2 == 0  # All duplicates ignored
        counts = count_by_status(jobs_conn)
        assert counts.get("pending", 0) == n1

    def test_enqueue_respects_model_exclusions(self, jobs_conn):
        """Jobs should not be created for variables excluded from the model."""
        from scripts.build_tiles_scheduled import enqueue_run_jobs

        region_id = list(repomap["TILING_REGIONS"].keys())[0]
        model_id = "hrrr"
        run_id = "run_20260215_12"

        n = enqueue_run_jobs(jobs_conn, region_id, model_id, run_id, max_hours=2)

        # Check that no jobs reference excluded variables
        jobs = get_jobs(jobs_conn, job_type="build_tile_hour", limit=1000)
        for job in jobs:
            import json
            args = json.loads(job["args_json"])
            var_config = repomap["WEATHER_VARIABLES"].get(args["variable_id"], {})
            assert model_id not in var_config.get("model_exclusions", [])

    def test_enqueue_respects_build_variables_env(self, jobs_conn, monkeypatch):
        """When TILE_BUILD_VARIABLES is set, only those variables should be enqueued."""
        # Reimport to pick up the monkeypatch
        monkeypatch.setenv("TILE_BUILD_VARIABLES", "t2m")

        # Need to reload the module to pick up the env var change
        import importlib
        import scripts.build_tiles_scheduled as sched_mod
        importlib.reload(sched_mod)

        region_id = list(repomap["TILING_REGIONS"].keys())[0]
        n = sched_mod.enqueue_run_jobs(jobs_conn, region_id, "hrrr", "run_20260215_12", max_hours=2)

        jobs = get_jobs(jobs_conn, job_type="build_tile_hour", limit=1000)
        var_ids = set()
        for job in jobs:
            import json
            args = json.loads(job["args_json"])
            var_ids.add(args["variable_id"])

        assert var_ids == {"t2m"}
        assert n > 0

        # Reload again to reset
        monkeypatch.delenv("TILE_BUILD_VARIABLES", raising=False)
        importlib.reload(sched_mod)


class TestDrainQueue:
    """Test the drain_queue function."""

    def test_drain_empty_queue(self, jobs_conn):
        """drain_queue on empty DB should return (0, 0)."""
        from scripts.build_tiles_scheduled import drain_queue

        processed, failed = drain_queue(jobs_conn)
        assert processed == 0
        assert failed == 0


class TestGetJobQueueStatus:
    """Test the status_utils job queue status function."""

    def test_get_job_queue_status_returns_dict(self, tmp_path, monkeypatch):
        """get_job_queue_status should return a dict of status counts."""
        db_path = str(tmp_path / "status_jobs.db")
        monkeypatch.setitem(repomap, "JOBS_DB_PATH", db_path)

        from status_utils import get_job_queue_status

        result = get_job_queue_status()
        # Empty DB should return empty dict (no rows)
        assert isinstance(result, dict)

    def test_get_job_queue_status_with_jobs(self, tmp_path, monkeypatch):
        """get_job_queue_status should count jobs by status."""
        db_path = str(tmp_path / "status_jobs2.db")
        monkeypatch.setitem(repomap, "JOBS_DB_PATH", db_path)

        from jobs import enqueue, init_db as init_jobs_db
        conn = init_jobs_db(db_path)
        enqueue(conn, "build_tile_hour", {"test": 1})
        enqueue(conn, "build_tile_hour", {"test": 2})
        conn.close()

        from status_utils import get_job_queue_status
        result = get_job_queue_status()
        assert result.get("pending", 0) == 2
