# Radarcheck Feature Reference

Complete inventory of all features, behaviors, and configuration. **Check this before any refactor to avoid accidentally breaking or deleting functionality.**

## Table of Contents
- [System Architecture](#system-architecture)
- [Models & Variables](#models--variables)
- [Tile Pipeline (Rust v2)](#tile-pipeline-rust-v2)
- [Retention & Cleanup](#retention--cleanup)
- [Server & API](#server--api)
- [Frontend](#frontend)
- [Forecast Auto-Trigger](#forecast-auto-trigger)
- [Production vs Dev](#production-vs-dev)
- [Database Schema](#database-schema)

---

## System Architecture

```
Python Scheduler (scripts/scheduler.py)
  └─ enqueues build_tile_hour jobs → SQLite jobs table

Rust Workers (one per model in dev, generic in prod)
  └─ claim jobs → fetch GRIB → decode → BucketMapping → RunAccumulator → finalize to .rctile v2

Rust Server (Axum, port 5001)
  └─ reads .rctile v2 files via mmap → serves API + static files + templates

Forecast Auto-Trigger (scripts/run-forecast.sh)
  └─ fired when all 3 synoptic models complete a cycle
  └─ prefetches data → Claude Code headless analysis → POST to /api/writeup
```

---

## Models & Variables

### Models

| ID | Name | Source | Grid | Hours | Update Freq | Tile Resolution |
|---|---|---|---|---|---|---|
| `hrrr` | HRRR | S3 (`noaa-hrrr-bdp-pds`) | Lambert Conformal | 48 (synoptic), 18 (hourly) | 1h | 0.03 deg |
| `nam_nest` | NAM 3km CONUS | NOMADS | Lambert Conformal | 60 | 6h | 0.1 deg |
| `gfs` | GFS | S3 (`noaa-gfs-bdp-pds`) | Regular lat/lon | 384 (3h to 240, 6h to 384) | 6h | 0.25 deg |
| `nbm` | National Blend | NOMADS | Regular lat/lon | 264 (synoptic), 36 (hourly) | 1h | 0.1 deg |
| `ecmwf_hres` | ECMWF HRES | data.ecmwf.int | Regular lat/lon (wrapped 180-360-180) | 240 (00/12Z), 144 (06/18Z) | 6h | 0.1 deg |

**NOMADS-backed models** (nam_nest, nbm): Subject to 500ms throttle between requests, 5000ms on 302 rate limit.

**GFS forecast hour schedule**: 3,6,9...240 (step 3), then 246,252...384 (step 6).

**NBM forecast hour schedule**: 1,2,3...36 (step 1), then 42,48...264 (step 6). Synoptic runs get full 264h; hourly runs only 36h.

### Tile Build Variables

`TILE_BUILD_VARIABLES = apcp,asnow,snod,t2m` (env var configurable)

| Variable | Units | Conversion | Snap Threshold | Excluded Models |
|---|---|---|---|---|
| `t2m` | degF | K→F (default), C→F | 0.0 | none |
| `apcp` | in | kg/m2→in (default), m→in | 0.005 in |  none |
| `asnow` | in | m→in | 0.005 in | gfs, nam_nest, ecmwf_hres |
| `snod` | in | m→in | 0.01 in | nbm |

### All Config Variables (Python, not all tiled)

`t2m, dpt, rh, wind_10m, gust, apcp, prate, asnow, snod, refc, cape, msl, hlcy, hail, snowlr, snowlvl`

### Region

Only one region: **`ne` (Northeast US Expanded)**
- Bounds: lat 33.0-47.0, lon -88.0 to -66.0
- Default resolution: 0.1 deg

---

## Tile Pipeline (Rust v2)

### rctile v2 Binary Format

One file per (region, model, variable, **run**). Files stored at:
`tiles/{region}/{res}/{model}/{variable}/run_YYYYMMDD_HH.rctile`

Each file contains exactly 1 run (runs table has 1 entry). Same binary format — no version bump needed.

```
Header (128 bytes):
  magic "RCT2", version 2, ny, nx, n_cells, lat/lon bounds, resolution,
  n_runs (=1), total_values_per_cell, runs_table_offset, index_offset, data_offset

Runs Table (variable size):
  Per run: run_id (16 bytes null-padded), init_unix (i64), n_hours (u16), hours[] (i32[])

Cell Index ((n_cells+1) * 8 bytes):
  u64 offsets into data section. Equal adjacent offsets = zero-elided cell.

Data (variable size):
  Per-cell gzip chunks. Each decompresses to total_values_per_cell * 4 bytes (f32).
```

### BucketMapping (gather-based)

- **Regular grids** (GFS, ECMWF, NBM): Binary search on sorted 1D lat/lon arrays. ECMWF lon array wraps 180→360→180, needs sorted index with reverse mapping.
- **Projected grids** (HRRR, NAM Lambert): SpatialHash with 0.5 deg resolution. GRIB lons normalized from 0-360 to -180..180 for hash lookup.
- **NN fill**: Empty edge cells (Lambert projection gaps) filled from nearest non-empty neighbor, spiral walk up to radius 20.

### RunAccumulator

- Keyed by `(run_id, variable_id)`.
- `cell_values: Vec<Vec<f32>>` — cell_values[cell_idx][hour_slot].
- `add_hour()` pushes one value per cell.
- Run change detection: when run_id changes, all accumulators finalize before starting new run.
- No jobs in queue: accumulators finalize before sleeping.

### Finalize (write path)

1. Build single `RunData` from accumulator (no file read, no merge)
2. **Atomic write**: write single-run rctile to `{variable}/run_YYYYMMDD_HH.rctile` via temp + rename
3. **Record in DB**: tile_runs + tile_variables tables
4. **File-based retention**: scan sibling `run_*.rctile` files, separate synoptic/hourly, delete excess + DB records
5. **Legacy cleanup**: delete old multi-run `{variable}.rctile` file if present

### Sibling Cancellation

When a GRIB fetch fails with "not found" (404), ALL pending jobs for that model+run are cancelled. NOT triggered on rate limits (302).

---

## Retention & Cleanup

### Python Scheduler (enqueue + cleanup)

| Setting | Env Var | Default | Purpose |
|---|---|---|---|
| Synoptic runs | `TILE_BUILD_SYNOPTIC_RUNS` | 8 | Max synoptic runs to keep |
| Hourly runs | `TILE_BUILD_HOURLY_RUNS` | 12 | Max hourly runs to keep |
| Per-model override | `TILE_BUILD_SYNOPTIC_RUNS_{MODEL}` | — | Override per model |
| Per-model override | `TILE_BUILD_HOURLY_RUNS_{MODEL}` | — | Override per model |

**`cleanup_old_runs()`**: Scans tile directories for both v1 run directories and v2 per-run rctile files in variable subdirectories. Separates synoptic/hourly, keeps newest N from each tier, deletes expired runs/files + DB records. Safety net for Rust worker's primary retention.

**`cleanup_herbie_cache()`**: Deletes GRIB cache date dirs older than 2 days.

### Rust Worker (file-based retention)

```rust
const MAX_SYNOPTIC_RUNS: usize = 8;
const MAX_HOURLY_RUNS: usize = 12;
```

Applied during `finalize()` via `apply_retention()` — scans sibling `run_*.rctile` files, separates synoptic/hourly by init_hour, deletes excess files + DB records. **Must match Python scheduler's defaults.**

### Production Fly.io Overrides

| Model | Synoptic | Hourly |
|---|---|---|
| HRRR | 2 | 2 |
| NBM | 2 | 2 |
| GFS | 4 | (default) |
| ECMWF | 4 | (default) |
| NAM | 3 | (default) |

### Synoptic Classification

`init_hour % 6 == 0` → synoptic (00, 06, 12, 18Z). All others → hourly.

---

## Server & API

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Main forecast UI (index.html) |
| GET | `/health` | Health check JSON (version, build_time, git_sha, tile_runs) |
| GET | `/metrics` | Prometheus stub |
| GET | `/status` | Status dashboard (status.html) |
| GET | `/writeup` | Forecast writeup page (writeup.html) |
| GET | `/api/timeseries/multirun` | Multi-run forecast timeseries |
| GET | `/api/timeseries/stitched` | Stitched/integrated timeseries |
| GET | `/api/status/summary` | Job queue, disk, memory, scheduler status |
| GET | `/api/status/run-grid` | Per-model run completeness grid |
| GET | `/api/status/logs` | Scheduler log tail |
| GET | `/api/jobs/list` | Job listing with filters |
| POST | `/api/jobs/retry-failed` | Retry failed jobs |
| POST | `/api/jobs/cancel` | Cancel jobs by ID or status |
| POST | `/api/jobs/enqueue-run` | Manually enqueue a model run |
| GET | `/api/writeup` | Read forecast writeup JSON |
| POST | `/api/writeup` | Save forecast writeup JSON |
| GET | `/api/writeup/audio` | Serve forecast audio MP3 |
| GET | `/api/writeup/audio/status` | Check audio availability |
| POST | `/api/writeup/audio/generate` | Generate audio (returns 501 in Rust server) |

### Multirun API

`GET /api/timeseries/multirun?lat=X&lon=X&model=all&variable=asnow&days=1`

- `model=all` queries all 5 models
- `days` filters runs by init_unix cutoff
- For accumulation vars (apcp, asnow): applies `accumulate_timeseries()` (handles NBM bucket data, cumulative resets, forward-fill NaN)
- NaN values skipped in output
- Response keyed by `"model_id/run_id"`

### Stitched API

`GET /api/timeseries/stitched?lat=X&lon=X&model=hrrr&variable=asnow&days=2`

- Single model only (not `model=all`)
- Chains verified segments from older runs with latest run
- Returns `event_total`, `baseline_accumulated`, `latest_run`, `runs_in_baseline`

### Mmap Cache

- `RwLock<HashMap<PathBuf, (SystemTime, Mmap)>>`
- Invalidation: checks file mtime on every request
- No eviction (~100 files for model=all with 5 models × 20 runs each; each mmap is ~300 bytes actual I/O via page cache)
- `unsafe { Mmap::map(&file) }` for zero-copy reads

### Server Read Path

For each (model, variable), the server scans `tiles/{region}/{res}/{model}/{variable}/` for `run_*.rctile` files, querying each via mmap cache. Falls back to legacy single-file path `{variable}.rctile` for backwards compatibility during migration.

### Authentication

- `RADARCHECK_API_KEY` env var — if set, all routes except `/health`, `/metrics`, `/static/*` require key
- Key checked in `x-api-key` header or `api_key` query param

### Point Query (cell index computation)

```
iy = floor((lat - lat_min) / resolution_deg), clamped to [0, ny-1]
ix = floor((lon - lon_min) / resolution_deg), clamped to [0, nx-1]
cell_idx = iy * nx + ix
```

No nearest-neighbor search — direct cell assignment via floor + clamp.

---

## Frontend

### index.html (Forecast UI)

- **Variables shown**: asnow (default), snod, t2m, wind_10m, apcp
- **Time periods**: 1 day, 3 days, 7 days (default)
- **Model toggles**: hrrr, nam_nest, gfs, nbm, ecmwf_hres (all on by default)
- **Chart**: Plotly.js, latest run full opacity, older runs fade by age
- **APCP display**: Converted to per-hour rate (delta/dt), step-line shape
- **Table**: Up to 3 runs per model, latest synoptic always included, synoptic marked with `*`
- **Stitch/integration bar**: asnow and apcp only, HRRR stitched endpoint
- **Location**: Default Philadelphia (40.0488, -75.3890), Nominatim geocoding, browser geolocation
- **Caching**: In-memory 5min TTL, localStorage 30min TTL
- **Background preloading**: After loading selected variable, preloads all other variables

### status.html (Dashboard)

- **Auto-refresh**: 10-second countdown
- **Job queue**: Pending/processing/failed/completed counts + rebuild ETA
- **Actions**: Retry failed, cancel pending, enqueue run (with dropdown of available runs)
- **Run grid**: Per-model tables with per-variable completion fractions, color-coded
- **Disk/memory**: Total cache, GRIB, tiles, memory usage with progress bar
- **Scheduler**: State, last run, next run, targets
- **Recent jobs**: Filterable list with error messages

### writeup.html (Forecast Writeup)

- **Auto-refresh**: 30-second interval
- **Markdown rendering**: marked.js with GFM, custom renderer (no strikethrough)
- **Audio player**: Play/pause, seek, volume, generates via TTS (501 on Rust server)
- **Copy panel**: Selectable raw markdown
- **Supporting data**: Collapsible detail section

---

## Forecast Auto-Trigger

### Trigger Conditions (Rust worker)

All must be true:
1. Completed model is in SYNOPTIC_MODELS (`gfs`, `nam_nest`, `ecmwf_hres`)
2. Zero remaining pending/processing jobs for this model+run
3. All 3 synoptic models have complete runs at the same init hour (checked via `latest_complete_run_at_hour`, looks back 3 days)
4. Cycle not already triggered (file-based dedup: `cache/last_forecast_trigger.txt`)

### Dedup

`cycle_id = "{init_hour}Z_{sorted_run_ids_joined}"`

Written BEFORE spawning to prevent double-trigger.

### Forecast Script

`scripts/run-forecast.sh`:
1. Prefetches data via `scripts/prefetch_forecast_data.py` (parallel API + NWS calls)
2. Data: Latest 4 synoptic runs for HRRR/NBM, latest 4 runs for GFS/ECMWF (NAM excluded from forecast)
3. Claude Code headless: `claude -p ... --allowedTools "Bash(curl*),Bash(python3*),Read" --max-budget-usd 2.00`
4. POSTs writeup to `/api/writeup`
5. Env: strips CLAUDECODE for nested invocation

### Analysis Methodology (SKILL.md)

1. Synoptic baseline (GFS, NAM, ECMWF) — run-to-run trends
2. Short-range confirmation (HRRR) — overlap windows
3. NBM stability — flip detection
4. Temperature cross-check — 33F threshold
5. Extended range (Day 7-16) — APCP > 0.25 + sub-freezing T2M
6. Multi-modal: scenarios not averages when models diverge
7. Implied snow ratio test: SNOD_or_ASNOW / APCP

---

## Production vs Dev

| Aspect | Production (Fly.io) | Dev (dev-services.sh) |
|---|---|---|
| Server | Gunicorn (Flask) port 5000 | Rust Axum port 5001 |
| Workers | 2 generic Python workers | 5 Rust workers (one per model) |
| Worker restart | supervisord autorestart | Bash loop with --max-jobs 50 |
| Auth | FLY_API_KEY secret | None |
| Volume | 1GB at /app/cache | Local cache/ |
| Memory | 1GB shared VM | Unconstrained |
| Max hours | HRRR=18, NAM=36, GFS=72, NBM=48, ECMWF=72 | Code defaults |
| Retention | Tight (2-4 per model) | Default (8 syn + 12 hourly) |
| Process mgr | supervisord | PID files + nohup |

**NOTE**: Production still uses Python Flask + Python workers. Rust server and workers are dev-only currently.

---

## Database Schema

### jobs table (created by Python scheduler)

```sql
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/completed/failed
    priority INTEGER DEFAULT 0,
    worker_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    retry_count INTEGER DEFAULT 0,
    retry_after TEXT,
    error_message TEXT,
    UNIQUE(type, args_hash)
);
```

**Priority**: `max(0, 100000 - minutes_old)` — newer runs get higher priority.
**Claim order**: `priority DESC, created_at ASC, id ASC`.
**No retries**: Jobs fail permanently. Scheduler re-enqueues in next cycle if needed.

### tile_runs table

```sql
CREATE TABLE tile_runs (
    region_id TEXT NOT NULL,
    resolution_deg REAL NOT NULL,
    model_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    init_time_utc TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (region_id, resolution_deg, model_id, run_id)
);
```

### tile_variables table

```sql
CREATE TABLE tile_variables (
    region_id TEXT, resolution_deg REAL, model_id TEXT, run_id TEXT,
    variable_id TEXT, job_id INTEGER, npz_path TEXT, meta_path TEXT,
    hours_json TEXT, size_bytes INTEGER, updated_at TEXT,
    PRIMARY KEY (region_id, resolution_deg, model_id, run_id, variable_id)
);
```

### tile_hours table

```sql
CREATE TABLE tile_hours (
    region_id TEXT, resolution_deg REAL, model_id TEXT, run_id TEXT,
    variable_id TEXT, forecast_hour INTEGER, job_id INTEGER, npz_path TEXT,
    updated_at TEXT,
    PRIMARY KEY (region_id, resolution_deg, model_id, run_id, variable_id, forecast_hour)
);
```

---

## Key Invariants (DO NOT BREAK)

1. **Tiered retention**: Synoptic and hourly runs kept separately. Frequent hourly runs must NOT push out long-range synoptic forecasts. Both scheduler and worker must use matching retention logic.
2. **Synoptic classification**: `init_hour % 6 == 0`. Used everywhere: retention, forecast trigger, prefetch filtering.
3. **No retries**: Jobs fail permanently. Scheduler re-enqueues if needed.
4. **GRIB lon convention**: Lambert grids (HRRR/NAM) return 0-360 lons. Must normalize to -180..180 for BucketMapping hash lookup.
5. **ECMWF lon wrapping**: GRIB lon array starts at 180, wraps through 360 to 180. Needs sorted index with reverse mapping for binary search.
6. **Accumulation handling**: NBM APCP is per-step buckets (not cumulative). `is_bucket_data()` detects this. Other models use cumulative/resetting.
7. **Snap thresholds**: apcp/asnow → 0.005 in, snod → 0.01 in, t2m → no snap. Applied during BucketMapping.apply() to improve compression.
8. **Zero-chunk elision**: Cells where ALL values across ALL runs are exactly 0.0 store no data in rctile. Saves ~50% for precip/snow variables.
9. **Atomic file writes**: rctile v2 writes to .tmp then renames. Prevents corruption from partial writes.
10. **Sibling cancellation**: On GRIB 404, cancel all pending jobs for same model+run. NOT on 302 (rate limit).
11. **Forecast trigger dedup**: File-based (`cache/last_forecast_trigger.txt`). Written BEFORE spawning to prevent double-trigger.
12. **Model exclusions**: asnow excluded from gfs/nam/ecmwf. snod excluded from nbm. Must be respected in both Python scheduler and Rust worker.
