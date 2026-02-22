# Job Queue Specification

## 1. Overview
SQLite-based job queue (`cache/jobs.db`) that drives background tile building.

## 2. Database Schema

### Table: `jobs`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Unique Job ID |
| `type` | TEXT | Job type (currently `build_tile_hour`) |
| `args_json` | TEXT | JSON arguments (model, run, variable, hour, region) |
| `args_hash` | TEXT | SHA-256 hash for deduplication |
| `priority` | INTEGER | Higher = higher priority (default 0) |
| `status` | TEXT | `pending`, `processing`, `completed`, `failed` |
| `worker_id` | TEXT | ID of claiming worker (or NULL) |
| `created_at` | TEXT | UTC ISO timestamp |
| `started_at` | TEXT | When processing began |
| `completed_at` | TEXT | When processing ended |
| `retry_after` | TEXT | Backoff timestamp for failed retries |
| `error_message` | TEXT | Error trace if failed |
| `retry_count` | INTEGER | Times retried (default 0) |
| `parent_job_id` | INTEGER | Optional parent job reference |

**Constraints**: `UNIQUE(type, args_hash)` prevents duplicate jobs.

## 3. Job Types

### `build_tile_hour`
Downloads GRIB for one forecast hour and builds the NPZ tile.
- **Args**: `{"model": "hrrr", "run": "20260222_12", "variable": "apcp", "hour": 6, "region": "ne", "resolution": 0.1}`

## 4. State Transitions

1. **Enqueue**: `INSERT OR IGNORE` with `status='pending'`. Failed jobs are reset to pending by the scheduler's `enqueue()` logic.
2. **Claim**: Atomic `UPDATE ... RETURNING` selects one `pending` job where `retry_after` has passed, ordered by priority DESC, created_at ASC.
3. **Complete**: `status='completed'`, `completed_at=NOW`.
4. **Fail**: `status='failed'`, `error_message=TRACE`. No automatic retries — the scheduler's next cycle re-enqueues if needed.

## 5. Concurrency
- SQLite WAL mode enabled for concurrent readers/writers.
- Workers use atomic UPDATE to claim jobs without races.
- Per-model workers (`--model` flag) only claim jobs matching their model filter.
