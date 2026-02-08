# Download Queue Implementation Subtasks

This document tracks the implementation of the SQLite-backed job queue system.

## 1. Core Job Queue (`jobs.py`)
- [ ] Create `tests/test_jobs.py` for unit testing the queue.
- [ ] Create `jobs.py` with the following functions:
    - `init_db(db_path)`
    - `enqueue(conn, type, args, priority)`
    - `claim(conn, worker_id)`
    - `complete(conn, job_id)`
    - `fail(conn, job_id, error, max_retries)`
    - `recover_stale(conn, timeout)`
    - `prune_completed(conn, max_age_hours)`
    - `count_by_status(conn)`
    - `get_jobs(conn, type, status, limit)`
- [ ] Update `config.py` to include `JOBS_DB_PATH`.
- [ ] Verify all tests pass.

## 2. Legacy Code Removal
- [ ] Remove unused imports/functions in `cache_builder.py` (`generate_forecast_images`, `tiered_cleanup_runs`, `main`).
- [ ] Remove shadow `audit_stats` in `build_tiles.py`.
- [ ] Remove unused `once_mode` variable and fix bare `except:` clauses in `scripts/build_tiles_scheduled.py`.
- [ ] Verify existing tests pass.

## 3. Scheduler Refactor
- [ ] Create `tests/test_scheduler_enqueue.py`.
- [ ] Modify `scripts/build_tiles_scheduled.py`:
    - Replace `subprocess.Popen` with `enqueue(ingest_grib)` and `enqueue(build_tile)`.
    - Remove post-pass normalization loop in `process_model`.
    - Enqueue `cleanup` jobs in `build_cycle`.
- [ ] Verify new tests pass.

## 4. Worker Implementation (`worker.py`)
- [ ] Create `tests/test_worker.py`.
- [ ] Create `worker.py`:
    - Implement `run_worker` loop.
    - Implement `execute_ingest_grib` (wraps `fetch_grib`).
    - Implement `execute_build_tile` (wraps `build_tiles_for_variable`).
    - Implement `execute_cleanup`.
- [ ] Verify worker tests pass.

## 5. Status Dashboard Refactor
- [ ] Create `tests/test_status_db.py`.
- [ ] Modify `status_utils.py`:
    - Implement `get_scheduled_runs_status` using `jobs.db`.
    - Implement `scan_cache_status` using `jobs.db`.
- [ ] Modify `app.py`:
    - Update `/api/status/scheduled` and `/api/status/summary`.
    - Add queue stats to summary.
- [ ] Update `templates/status.html`.
- [ ] Verify status tests pass.

## 6. Cleanup & Hardening
- [ ] Run `scripts/test_tiles_e2e.py`.
- [ ] Run full `pytest` suite.
- [ ] Add WAL checkpointing.
