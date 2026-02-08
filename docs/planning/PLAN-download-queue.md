# Download Queue Overhaul Plan

> SQLite-backed job queue to replace the ad-hoc scheduler/subprocess architecture.
> TDD approach: tests first for every module, then implementation.

---

## Table of Contents

1. [Current State Audit: Bugs & Issues](#1-current-state-audit-bugs--issues)
2. [Design Decisions (Resolved)](#2-design-decisions-resolved)
3. [Architecture](#3-architecture)
4. [Schema](#4-schema)
5. [Job Types & Granularity](#5-job-types--granularity)
6. [Implementation Phases](#6-implementation-phases)
7. [Legacy Code Removal](#7-legacy-code-removal)
8. [File Inventory](#8-file-inventory)
9. [Open Questions](#9-open-questions)

---

## 1. Current State Audit: Bugs & Issues

These are the known bugs and problems in the existing download backend that motivate this work.

### 1.1 `audit_stats` shadowing — GRIB counts always zero

- `utils.py:19` defines a module-level `audit_stats` dict. `cache_builder.py:40` imports it and increments `grib_hits` / `grib_misses`.
- `build_tiles.py:35` defines its **own** `audit_stats` with the same keys, shadowing the one from `utils`. The audit summary printed at the end of every build always shows `GRIB Cache Hits: 0` / `GRIB Cache Misses: 0`.
- **Fix**: Delete the shadow dict in `build_tiles.py:35`, import `audit_stats` from `utils` instead.

### 1.2 `audit_stats` thread-safety — data race under parallelism

- `audit_stats` is a plain dict incremented from threads via `download_all_hours_parallel`. With `PARALLEL_DOWNLOAD_WORKERS > 1`, concurrent `+= 1` on a dict value is a data race.
- **Fix**: Use `threading.Lock` around increments, or switch to `collections.Counter` with a lock, or just use atomic counters.

### 1.3 `process_model` double-iterates runs (post-pass normalization)

- `build_tiles_scheduled.py:370-382` re-iterates `runs_to_process[1:]` after the main loop. If the first pass succeeded, this is pure waste — `tiles_exist()` catches it, but each check opens an NPZ from disk. If the first pass failed for a transient reason, this is an accidental retry with no backoff.
- **Fix**: Remove the normalization pass entirely. The next 15-minute cycle handles retries properly.

### 1.4 `get_required_runs` hammers NOMADS with HEAD requests

- `build_tiles_scheduled.py:156-185` calls `check_run_available()` for up to 72 hours of candidate runs per model. That's up to **72 HEAD requests per model per cycle** (360 total for 5 models), including runs we already have complete tiles for.
- **Fix**: Short-circuit with `tiles_exist()` before calling `check_run_available()`. Or better: with the job queue, we only scan for *new* runs, not re-verify old ones.

### 1.5 `scan_cache_status` uses wrong `expected_hours`

- `status_utils.py:66` uses `model_config.get("max_forecast_hours", 24)` as the expected count. But HRRR non-synoptic runs are built with only 18 hours. The dashboard marks complete runs as "partial."
- **Fix**: Use `_get_max_hours_for_run()` (which already exists in the same file) instead of the model-global max.

### 1.6 `_get_expected_runs` doesn't check availability

- `status_utils.py:121-152` generates expected runs from pure time math without checking NOMADS. Runs that were never published (outages, too-new) show as "missing" on the dashboard.
- **Fix**: With the job queue, the dashboard queries actual job status — not filesystem guesses.

### 1.7 No guard against overlapping build cycles

- The scheduler sleeps 15 minutes between cycles (`build_tiles_scheduled.py:565`). If a cycle takes >15 minutes, nothing prevents the next cycle from starting and spawning duplicate `build_tiles.py` subprocesses for the same model/run. `FileLock` prevents file corruption, but wastes bandwidth and CPU.
- **Fix**: The job queue's atomic `claim` mechanism makes this impossible by design.

### 1.8 Dead code: `--once` mode variable set but never read

- `build_tiles_scheduled.py:519` sets `once_mode = "--once" in sys.argv` but it's never referenced. The actual `--once` handling is in the `if __name__` block at line 570.
- **Fix**: Delete the unused variable.

### 1.9 Dead code: `generate_forecast_images` and legacy `main()`

- `cache_builder.py:718-958` (`generate_forecast_images`) is the old per-location PNG path. It imports `geopandas`, `matplotlib` via `create_plot`, and is never called by the tile-building path.
- `cache_builder.py:1069-1168` (`main()`) is the legacy entry point that calls `generate_forecast_images`.
- These functions pull in heavy dependencies (`geopandas`, `psutil`, `matplotlib`) at import time for no reason.
- **Decision**: Remove. (See [Section 7](#7-legacy-code-removal).)

### 1.10 `log_memory_usage` is a no-op

- `cache_builder.py:57-63` has the function body `pass`. `psutil` is still imported at line 25 for nothing.
- **Fix**: Delete function and the `psutil` import.

### 1.11 Bare `except:` clauses swallow SystemExit/KeyboardInterrupt

- `build_tiles_scheduled.py:322` (`run_is_ready`), `cache_builder.py:1001`, `cache_builder.py:1055` use bare `except:`.
- **Fix**: Change to `except Exception:`.

---

## 2. Design Decisions (Resolved)

Answers to the open design questions, based on project requirements:

| # | Question | Decision |
|---|----------|----------|
| 1 | Single process or multi-process workers? | **TBD** — leaving open for now. Start with threads in same process for simplicity. |
| 2 | Concurrent download count? | **TBD** — start with 1, test with 2. |
| 3 | `scan_source` in DB or timer? | **TBD** — leaving open. |
| 4 | Job retention policy? | **TBD** — lean toward pruning completed jobs after 72h. |
| 5 | Flask enqueue on demand? | **TBD** — not needed initially. |
| 6 | Priority scheme? | **TBD** — start with recency-based. |
| 7 | Remove legacy image generation? | **Yes.** Remove `generate_forecast_images`, `extract_center_value`, `tiered_cleanup_runs`, `tiered_cleanup_gribs`, `main()`, and dead imports. |
| 8 | Volume persistence on Fly.io? | **Yes.** `cache/jobs.db` lives on the persistent volume and survives restarts/deploys. Stale `processing` jobs get reset on startup. |
| 9 | Job granularity? | **Per forecast timestep.** One job = one `(model, run, variable, forecast_hour)`. Maximum concurrency control. Maximum resumability. A partial download resumes at the exact hour it left off. |
| 10 | Monitoring/alerting? | **Dashboard only** for now. No webhooks/Slack. The `/status` page reads from `jobs.db`. |

---

## 3. Architecture

### 3.1 Current Architecture (what we're replacing)

```
Scheduler (build_tiles_scheduled.py)
  │  polls NOMADS every 15 min
  │  up to 360 HEAD requests per cycle
  │
  ├─ ThreadPoolExecutor(5 models in parallel)
  │   └─ process_model()
  │       └─ subprocess.Popen(build_tiles.py)  ← full subprocess per run
  │           └─ download_all_hours_parallel()
  │               └─ ThreadPoolExecutor(1 worker)
  │                   └─ fetch_grib() × N hours
  │
  └─ State tracking: JSON file + filesystem scanning
```

Problems: no dedup, no resumability, no visibility, wasteful NOMADS polling.

### 3.2 Target Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Scheduler Thread                        │
│  Runs every 15 min (or configurable)                        │
│                                                              │
│  1. Poll NOMADS for new runs (only recent, not 72h sweep)   │
│  2. For each new run: INSERT jobs for every                  │
│     (variable, forecast_hour) → idempotent via UNIQUE        │
│  3. Enqueue a build_tile job per (run, variable)             │
│     with dependency on its ingest_grib jobs                  │
└──────────────────┬───────────────────────────────────────────┘
                   │ writes
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                    jobs.db (SQLite WAL)                       │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ id │ type        │ args_json           │ status         │ │
│  │  1 │ ingest_grib │ {hrrr,run_...,t2m,1}│ completed     │ │
│  │  2 │ ingest_grib │ {hrrr,run_...,t2m,2}│ processing    │ │
│  │  3 │ ingest_grib │ {hrrr,run_...,t2m,3}│ pending       │ │
│  │  4 │ build_tile  │ {hrrr,run_...,t2m}  │ pending       │ │
│  │ .. │ ...         │ ...                  │ ...           │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────┬──────────────┬────────────────────────────┘
                   │ claims       │ reads
                   ▼              ▼
┌───────────────────────┐  ┌───────────────────────────────────┐
│    Worker Thread(s)   │  │   Flask App (read-only)            │
│                       │  │                                    │
│  while True:          │  │  /api/status/scheduled → SQL query │
│    job = claim_next() │  │  /status → dashboard page          │
│    execute(job)       │  │                                    │
│    mark_done(job)     │  │  No filesystem scanning needed     │
│                       │  │                                    │
└───────────────────────┘  └───────────────────────────────────┘
```

### 3.3 What stays the same

These modules are solid and don't need rewriting:

- **`fetch_grib()`** — download logic, retry/backoff, validation, FileLock. Workers call this directly.
- **`build_tiles_for_variable()`** / **`save_tiles_npz()`** — tile statistics and persistence.
- **`open_dataset_robust()`** — GRIB opening with cfgrib fallbacks.
- **`get_valid_forecast_hours()`** / **`get_run_forecast_hours()`** — schedule calculations.
- **`config.py`** — model/variable/region definitions.

---

## 4. Schema

Refined from the existing `docs/architecture/job_queue_spec.md`.

### 4.1 `jobs` table

```sql
CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT    NOT NULL,           -- ingest_grib | build_tile | cleanup
    args_json       TEXT    NOT NULL,           -- see Section 5 for shapes
    args_hash       TEXT    NOT NULL,           -- SHA256(type + args_json) for dedup
    priority        INTEGER NOT NULL DEFAULT 0, -- higher = picked first
    status          TEXT    NOT NULL DEFAULT 'pending',
                                               -- pending | processing | completed | failed
    worker_id       TEXT,                       -- hostname-pid-thread of claimer
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    started_at      TEXT,
    completed_at    TEXT,
    retry_after     TEXT,                       -- NULL or ISO timestamp; don't claim before this
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    parent_job_id   INTEGER,                    -- optional FK: build_tile points to its run context

    UNIQUE(type, args_hash)                     -- idempotent enqueue
);

CREATE INDEX idx_jobs_claimable
    ON jobs(status, retry_after, priority DESC, created_at ASC)
    WHERE status = 'pending';

CREATE INDEX idx_jobs_by_type_status
    ON jobs(type, status);
```

### 4.2 Claim query (atomic)

```sql
UPDATE jobs
SET    status     = 'processing',
       worker_id  = :worker_id,
       started_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE  id = (
    SELECT id FROM jobs
    WHERE  status = 'pending'
    AND    (retry_after IS NULL OR retry_after <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
)
RETURNING *;
```

### 4.3 Stale recovery (on startup)

```sql
UPDATE jobs
SET    status = 'pending', worker_id = NULL, started_at = NULL
WHERE  status = 'processing';
```

### 4.4 Retention pruning

```sql
DELETE FROM jobs
WHERE  status = 'completed'
AND    completed_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-72 hours');
```

---

## 5. Job Types & Granularity

### 5.1 `ingest_grib` — one per forecast timestep

This is the most granular unit. One job = one GRIB file.

```json
{
  "model_id": "hrrr",
  "run_id": "run_20260208_12",
  "variable_id": "t2m",
  "forecast_hour": 5,
  "date_str": "20260208",
  "init_hour": "12"
}
```

**Worker action**: Call existing `fetch_grib()`. Mark complete on success.

**Volume estimate**: HRRR 48h × 15 vars = 720 jobs per run. One HRRR run per hour = ~17,000 jobs/day. Completed jobs pruned after 72h. DB stays small.

### 5.2 `build_tile` — one per (run, variable, region)

Depends on all `ingest_grib` jobs for the same (model, run, variable) being complete.

```json
{
  "model_id": "hrrr",
  "run_id": "run_20260208_12",
  "variable_id": "t2m",
  "region_id": "ne"
}
```

**Worker action**: Collect all downloaded GRIB paths for this variable, call `build_tiles_for_variable()` + `save_tiles_npz()`. Mark complete on success.

**Dependency check**: Before executing, verify all `ingest_grib` siblings are `completed`. If not, re-queue as pending with a short `retry_after`.

### 5.3 `cleanup` — periodic maintenance

```json
{
  "target": "gribs",
  "model_id": "hrrr",
  "max_runs_to_keep": 2
}
```

**Worker action**: Delete old GRIB directories. Prune completed jobs from DB.

---

## 6. Implementation Phases

All phases follow TDD: **write tests first**, then implement to make them pass.

### Phase 1: `jobs.py` — Core Queue Module

The foundation. Pure Python + SQLite, no dependencies on weather code.

#### Tests to write first (`tests/test_jobs.py`)

```
test_init_db_creates_tables
test_init_db_enables_wal_mode
test_init_db_is_idempotent

test_enqueue_returns_job_id
test_enqueue_sets_pending_status
test_enqueue_duplicate_is_noop (same type + args → no error, no duplicate)
test_enqueue_different_args_creates_separate_jobs
test_enqueue_with_priority

test_claim_returns_highest_priority_first
test_claim_returns_oldest_first_at_same_priority
test_claim_sets_processing_status
test_claim_sets_worker_id_and_started_at
test_claim_returns_none_when_empty
test_claim_skips_processing_jobs (no double-claim)
test_claim_skips_jobs_before_retry_after
test_claim_returns_jobs_past_retry_after

test_complete_sets_completed_status
test_complete_sets_completed_at_timestamp
test_fail_sets_failed_status
test_fail_stores_error_message
test_fail_requeues_if_under_max_retries
test_fail_stays_failed_if_at_max_retries
test_fail_increments_retry_count
test_fail_requeue_sets_retry_after_with_backoff

test_recover_stale_resets_processing_to_pending
test_recover_stale_clears_worker_id

test_prune_completed_deletes_old_jobs
test_prune_completed_keeps_recent_jobs

test_count_by_status_returns_correct_counts
test_count_by_type_and_status

test_concurrent_claims_no_duplicates (two threads claim → different jobs)
```

#### Implementation: `jobs.py`

Public API:

```python
def init_db(db_path: str = "cache/jobs.db") -> sqlite3.Connection
def enqueue(conn, type: str, args: dict, priority: int = 0) -> int | None
def claim(conn, worker_id: str) -> dict | None
def complete(conn, job_id: int) -> None
def fail(conn, job_id: int, error: str, max_retries: int = 3) -> None
def recover_stale(conn) -> int  # returns count recovered
def prune_completed(conn, older_than_hours: int = 72) -> int
def count_by_status(conn) -> dict[str, int]
def get_jobs(conn, type: str = None, status: str = None, limit: int = 100) -> list[dict]
```

### Phase 2: Legacy Code Removal

Remove dead code from `cache_builder.py` before building on top of it.

#### What to delete from `cache_builder.py`

| Lines | Function/Code | Reason |
|-------|--------------|--------|
| 4 | `import gc` | Only used in legacy `main()` |
| 20-23 | `geopandas` import block | Only used in legacy `generate_forecast_images` and `main()` |
| 25 | `import psutil` | Only used in no-op `log_memory_usage` |
| 29 | `create_plot` from plotting import | Only used in legacy `generate_forecast_images` |
| 35-41 | `PlotGenerationError`, `convert_units`, `fetch_county_shapefile` from utils import | Only used in legacy functions |
| 57-63 | `log_memory_usage()` | No-op (body is `pass`) |
| 658-716 | `extract_center_value()` | Only called from `generate_forecast_images` |
| 718-958 | `generate_forecast_images()` | Legacy PNG generation path. Never called by tile building. |
| 960-1013 | `tiered_cleanup_runs()` | Location-based cleanup for legacy path. Only called from legacy `main()` |
| 1016-1066 | `tiered_cleanup_gribs()` | Only called from legacy `main()`. Scheduler has its own `cleanup_old_gribs()`. |
| 1069-1168 | `main()` + `if __name__` block | Legacy entry point |

#### What stays in `cache_builder.py`

- `get_valid_forecast_hours()`
- `detect_hourly_support()` / `get_run_forecast_hours()`
- `build_variable_query()` / `build_model_url()`
- `get_available_model_runs()` / `get_latest_model_run()` + backward-compat wrappers
- `fetch_grib()`
- `download_all_hours_parallel()`

#### Tests to verify before removing

Run existing test suite to establish baseline:

```
pytest tests/test_cache_builder.py
pytest tests/test_forecast_hours_schedule.py
pytest tests/test_hourly_override_schedule.py
pytest tests/test_downloader_schedule.py
pytest tests/test_hrrr.py
```

All must pass before AND after removal. None of these tests reference the legacy functions (verified via grep).

#### Other cleanup in this phase

- `build_tiles.py:35`: Delete shadow `audit_stats`, import from `utils` instead
- `build_tiles_scheduled.py:519`: Delete unused `once_mode` variable
- `build_tiles_scheduled.py:322`, `cache_builder.py:1001,1055`: Change bare `except:` to `except Exception:`
- (The bare excepts at 1001/1055 go away with the legacy removal, but fix line 322)

### Phase 3: Scheduler Refactor — Enqueue Instead of Subprocess

Replace `process_model()` → `Popen(build_tiles.py)` with `process_model()` → `enqueue(ingest_grib)` + `enqueue(build_tile)`.

#### Tests first (`tests/test_scheduler_enqueue.py`)

```
test_scan_model_enqueues_ingest_jobs_for_new_run
test_scan_model_skips_runs_with_existing_complete_jobs
test_scan_model_enqueues_all_variables_and_hours
test_scan_model_sets_correct_priority (newest run > older)
test_scan_model_enqueues_build_tile_after_ingest_jobs
test_scan_model_respects_max_hours_by_init (HRRR synoptic vs non-synoptic)
test_scan_model_respects_variable_model_exclusions
test_scan_model_idempotent (running twice doesn't duplicate)
test_scan_model_handles_nomads_unavailable
```

#### Implementation changes

1. `build_tiles_scheduled.py`: `process_model()` becomes:
   - Call `get_required_runs()` (keep existing logic, but short-circuit on DB state)
   - For each new run: loop `variables × forecast_hours`, call `enqueue(type="ingest_grib", ...)`
   - For each new (run, variable): call `enqueue(type="build_tile", ...)`
   - Delete the `subprocess.Popen` call to `build_tiles.py`
   - Delete the normalization pass (lines 370-382)

2. `build_tiles_scheduled.py`: `build_cycle()` becomes:
   - Run `process_model()` for each model (can keep parallel or go sequential — enqueueing is fast)
   - Enqueue `cleanup` jobs
   - No longer needs `ThreadPoolExecutor` for models

### Phase 4: Worker Loop

The thing that actually does the work.

#### Tests first (`tests/test_worker.py`)

```
test_worker_executes_ingest_grib_job
test_worker_calls_fetch_grib_with_correct_args
test_worker_marks_job_complete_on_success
test_worker_marks_job_failed_on_exception
test_worker_stores_traceback_on_failure
test_worker_skips_ingest_if_grib_already_cached
test_worker_executes_build_tile_job
test_worker_build_tile_waits_if_ingests_incomplete (re-queues with retry_after)
test_worker_build_tile_runs_when_all_ingests_complete
test_worker_sleeps_when_no_jobs
test_worker_recover_stale_on_startup
test_worker_handles_cleanup_job
```

#### Implementation: `worker.py`

```python
def run_worker(db_path: str, poll_interval: float = 5.0):
    conn = init_db(db_path)
    recover_stale(conn)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{threading.current_thread().name}"

    while True:
        job = claim(conn, worker_id)
        if not job:
            time.sleep(poll_interval)
            continue

        try:
            execute_job(job)
            complete(conn, job["id"])
        except Exception as e:
            fail(conn, job["id"], traceback.format_exc())

def execute_job(job: dict):
    args = json.loads(job["args_json"])
    if job["type"] == "ingest_grib":
        execute_ingest_grib(args)
    elif job["type"] == "build_tile":
        execute_build_tile(args)
    elif job["type"] == "cleanup":
        execute_cleanup(args)

def execute_ingest_grib(args: dict):
    """Calls existing fetch_grib(). Thin wrapper."""
    fetch_grib(
        model_id=args["model_id"],
        variable_id=args["variable_id"],
        date_str=args["date_str"],
        init_hour=args["init_hour"],
        forecast_hour=format_forecast_hour(args["forecast_hour"], args["model_id"]),
        run_id=args["run_id"],
    )

def execute_build_tile(args: dict):
    """Collects cached GRIBs, builds tile. Checks dependency completion first."""
    # Query DB: are all ingest_grib jobs for this (model, run, variable) completed?
    # If not: raise RetryLater("N ingest jobs still pending")
    # If yes: gather GRIB paths, call build_tiles_for_variable(), save_tiles_npz()
    ...
```

### Phase 5: Status Dashboard Refactor

Replace filesystem scanning with DB queries.

#### Tests first (`tests/test_status_db.py`)

```
test_status_summary_returns_counts_by_model_and_type
test_status_summary_includes_job_age_stats
test_status_scheduled_shows_pending_and_processing
test_status_scheduled_shows_recent_failures
test_status_shows_per_run_completion_percentage
test_disk_usage_still_works (keep existing filesystem-based disk usage)
```

#### Implementation changes

- `status_utils.py`:
  - `get_scheduled_runs_status()` → query `jobs` table instead of scanning NPZ files
  - `scan_cache_status()` → query `jobs` grouped by model/run/status
  - Keep `get_disk_usage()` as-is (filesystem-based, appropriate for that)
  - Keep `read_scheduler_logs()` as-is

- `app.py`:
  - `/api/status/scheduled` → calls refactored `get_scheduled_runs_status()`
  - `/api/status/summary` → adds job queue counts
  - `/status` template: add job queue section (pending/processing/completed/failed counts)

### Phase 6: Cleanup & Hardening

- Remove `build_tiles.py` as standalone CLI (it becomes unnecessary — workers do the work)
  - OR: keep it as a convenience tool that enqueues jobs and waits for completion
- Remove `download_all_hours_parallel()` from `cache_builder.py` (workers handle parallelism via multiple claimed jobs)
  - OR: keep it for the manual `build_tiles.py` CLI path
- Add `cleanup` job type: prune old GRIBs, prune old tile runs, prune completed DB jobs
- WAL checkpoint: periodic `PRAGMA wal_checkpoint(TRUNCATE)` to keep WAL file bounded

---

## 7. Legacy Code Removal (Detail)

Full list of removals for Phase 2. Every item is either dead code or only referenced by other dead code.

### From `cache_builder.py`

**Imports to remove:**
- `import gc` (line 4)
- `geopandas` try/except block (lines 20-23)
- `import psutil` (line 25)
- `create_plot` from `plotting` import (line 29) — keep `select_variable_from_dataset`
- `PlotGenerationError` from `utils` import (line 36)
- `convert_units` from `utils` import (line 36)
- `fetch_county_shapefile` from `utils` import (line 39)

**Functions to remove:**
- `log_memory_usage()` (lines 57-63) — no-op, body is `pass`
- `extract_center_value()` (lines 658-716) — only caller is `generate_forecast_images`
- `generate_forecast_images()` (lines 718-958) — legacy PNG path
- `tiered_cleanup_runs()` (lines 960-1013) — legacy location cleanup
- `tiered_cleanup_gribs()` (lines 1016-1066) — legacy, scheduler has own cleanup
- `main()` (lines 1069-1165) — legacy entry point
- `if __name__ == "__main__": main()` (lines 1167-1168)

### From `build_tiles.py`

- Line 35: Delete `audit_stats = {...}` (shadow), add `from utils import audit_stats`

### From `build_tiles_scheduled.py`

- Line 519: Delete `once_mode = "--once" in sys.argv`
- Line 322: Change `except:` to `except Exception:`
- Lines 370-382: Delete post-pass normalization loop

### Verification

Run **before** and **after** removal:
```bash
pytest tests/ -v
```

Expected: same pass/fail results. No existing test calls the removed functions.

---

## 8. File Inventory

### New files to create

| File | Purpose |
|------|---------|
| `jobs.py` | SQLite job queue: init, enqueue, claim, complete, fail, recover, prune |
| `worker.py` | Worker loop: claim jobs, dispatch to fetch_grib / build_tiles_for_variable |
| `tests/test_jobs.py` | Unit tests for job queue module |
| `tests/test_worker.py` | Unit tests for worker execution logic |
| `tests/test_scheduler_enqueue.py` | Tests for refactored scheduler |
| `tests/test_status_db.py` | Tests for DB-backed status queries |

### Files to modify

| File | Change |
|------|--------|
| `cache_builder.py` | Remove ~500 lines of legacy code and dead imports |
| `build_tiles.py` | Fix `audit_stats` shadow; optionally keep as CLI or convert to enqueue wrapper |
| `build_tiles_scheduled.py` | Replace subprocess spawning with job enqueue; remove normalization pass |
| `status_utils.py` | Query `jobs.db` instead of filesystem scanning |
| `app.py` | Update status API endpoints to use DB-backed queries |
| `config.py` | Add `JOBS_DB_PATH` setting (default: `cache/jobs.db`) |

### Files to eventually delete

| File | When |
|------|------|
| `plotting.py` | After confirming no remaining callers (app.py still uses `select_variable_from_dataset` and `get_colormap`) |

---

## 9. Open Questions

These were deferred and should be decided before or during implementation:

1. **Single process or multi-process workers?** Start with threads. Revisit if GIL becomes a bottleneck (unlikely — work is I/O bound).

2. **How many concurrent GRIB downloads?** Start with 1 worker thread (matches current `PARALLEL_DOWNLOAD_WORKERS=1`). The queue naturally supports scaling by adding threads.

3. **Should `scan_source` be a job type or a timer?** Leaning toward keeping it as a timer (the scheduler loop). It's a simple periodic action that doesn't benefit from job semantics.

4. **Job retention?** Lean toward pruning completed jobs after 72 hours. Failed jobs kept longer (7 days?) for debugging.

5. **Flask on-demand enqueue?** Not needed now. Can add a `/api/build` endpoint later if useful.

6. **Priority scheme?** Start simple: `priority = -hours_ago` (newest run = 0, older = negative). Model weight can be layered on later.
