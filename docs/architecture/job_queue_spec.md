# Job Queue Specification

## 1. Overview
This document specifies the schema and behavior of the SQLite-based task queue (`jobs.db`) that drives the backend.

## 2. Database Schema

### 2.1 Table: `jobs`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Unique Job ID |
| `type` | TEXT | Job type (e.g., `ingest_grib`, `build_tile`) |
| `args` | TEXT | JSON string of arguments (e.g., `{"model": "hrrr", ...}`) |
| `priority` | INTEGER | Higher number = higher priority (default: 0) |
| `status` | TEXT | `pending`, `processing`, `completed`, `failed` |
| `worker_id` | TEXT | ID of the worker currently processing (or NULL) |
| `created_at` | TIMESTAMP | UTC timestamp of creation |
| `started_at` | TIMESTAMP | UTC timestamp when processing began |
| `completed_at` | TIMESTAMP | UTC timestamp when processing ended |
| `error_message` | TEXT | Error trace if status is `failed` |
| `retry_count` | INTEGER | Number of times this job has been retried |

### 2.2 Table: `workers` (Optional, for heartbeat)
| Column | Type | Description |
|---|---|---|
| `id` | TEXT PRIMARY KEY | Unique Worker ID (hostname-pid) |
| `last_heartbeat` | TIMESTAMP | Last time the worker checked in |
| `current_job_id` | INTEGER | Currently processing job ID (Foreign Key) |

## 3. Job Types & Arguments

### 3.1 `scan_source`
*Checks NOAA/ECMWF for new runs.*
- **Args:** `{"model_id": "hrrr"}`

### 3.2 `ingest_grib`
*Downloads a GRIB file.*
- **Args:** `{"model_id": "hrrr", "run_id": "20230125_12", "hour": 5}`

### 3.3 `build_tile`
*Generates NPZ tiles from a GRIB.*
- **Args:** `{"model_id": "hrrr", "run_id": "20230125_12", "hour": 5, "region": "ne"}`

## 4. State Transitions

1.  **Enqueue:**
    - Insert `status='pending'`, `created_at=NOW`, `priority=X`.
2.  **Claim (Worker Loop):**
    - `BEGIN TRANSACTION`
    - Select one job where `status='pending'` ordered by `priority DESC, created_at ASC` limit 1.
    - Update job set `status='processing'`, `worker_id=ME`, `started_at=NOW`.
    - `COMMIT`
3.  **Complete:**
    - Update job set `status='completed'`, `completed_at=NOW`.
4.  **Fail:**
    - Update job set `status='failed'`, `completed_at=NOW`, `error_message=TRACE`.
    - (Optional) If `retry_count < MAX`, reset status to `pending`, increment `retry_count`.

## 5. Concurrency Control
- SQLite `WAL` mode (Write-Ahead Logging) MUST be enabled to allow concurrent readers/writers.
- Workers use atomic `UPDATE ... RETURNING` (or explicit transactions) to claim jobs to avoid race conditions.
