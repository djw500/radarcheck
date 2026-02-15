# Radarcheck Implementation TODOs

**Last updated**: 2026-02-15 (after job queue refactor)

## Priority Legend
- **P0**: Must-have / blocking
- **P1**: Important for good UX
- **P2**: Nice-to-have

---

## Just Completed: Scheduler → Job Queue Refactor

The scheduler (`scripts/build_tiles_scheduled.py`) now uses an SQLite job queue instead of spawning subprocesses. Key changes in commit `c39c6e3`:

- **`cache_builder.py`**: ~510 lines of dead legacy code removed (PNG generation, image plotting, old `main()`)
- **`build_tiles.py`**: Fixed shadow `audit_stats` bug (was hiding shared counter from `utils.py`)
- **`scripts/build_tiles_scheduled.py`**: Subprocess → enqueue + inline drain via `jobs.py`
- **`status_utils.py`**: Added `get_job_queue_status()` with counts by status
- **`app.py`**: `/api/status/summary` now includes `job_queue` field
- **`tests/test_scheduler_enqueue.py`**: 7 new tests for enqueue logic

### Remaining work from refactor

- [ ] **P0** Test `python scripts/build_tiles_scheduled.py --once` end-to-end against live NOMADS
- [x] **P1** Fix pre-existing ECMWF test (`test_build_region_tiles_ecmwf`) — mocked tile_db functions
- [x] **P1** `--clean-gribs` in `job_worker.py` — N/A, scheduler handles cleanup via `cleanup_old_gribs()`
- [x] **P2** Replace `datetime.utcnow()` deprecation warnings in `jobs.py` and tests with `datetime.now(timezone.utc)`
- [x] **P2** Remove unused `tiles_exist_any()` from scheduler
- [x] **BUG** Fix `job_worker.py` `run_worker()` never calling `complete()` on successful jobs

---

## Phase 1: Multi-Model Table (P0)

### Backend
- [ ] **P0** `/api/table/multimodel` endpoint — merge HRRR + NAM + GFS data by valid time
- [ ] **P1** Add more tiling regions to `config.py` (SE, MW, SW, NW)
- [ ] **P1** Region inference for coordinates outside NE

### Frontend
- [ ] **P0** Multi-column table view: [Local Time | Precip | Snow | Temp] × models
- [ ] **P0** Location autocomplete (Nominatim or similar)
- [ ] **P1** Color-coded cells by value thresholds
- [ ] **P1** Save recent locations to localStorage

---

## Phase 2: Historical Run Comparison (P1)

- [ ] `/api/table/multirun` — last N runs of same model side-by-side
- [ ] "Show History" toggle in UI
- [ ] Highlight significant forecast changes between runs

---

## Phase 3: Fly.io Automation — MOSTLY DONE

- [x] Scheduled tile building (scheduler + job queue)
- [x] `--clean-gribs` for disk space management
- [x] Tile/GRIB retention policies
- [x] Job queue visibility in `/api/status/summary`
- [ ] **P1** Tile freshness check in `/health` endpoint

---

## Phase 4: Legacy Cleanup (P1)

- [x] Remove dead code from `cache_builder.py` (PNG generation, extract_center_value, etc.)
- [ ] **P1** Redirect `/location/<id>` to `/forecast?lat=...&lon=...`
- [ ] **P2** Remove `plotting.py` (only `select_variable_from_dataset` is still used — could move to `tiles.py`)
- [ ] **P2** Remove old templates (`location.html`, old `index.html`)
- [ ] **P2** Remove legacy cache structure: `cache/<location>/<model>/<run>/` if present on disk

---

## Phase 5: UX Polish (P2)

- [ ] Units in column headers
- [ ] Model init time display ("HRRR 12Z")
- [ ] CSV/JSON export
- [ ] Mobile-optimized layout
- [ ] Shareable URL with encoded location

---

## Known Issues

- ECMWF model is disabled in scheduler config (was unstable)
- `PARALLEL_DOWNLOAD_WORKERS=1` is required for NOMADS reliability

---

## Notes

- Disk: 1GB Fly.io volume, tiles ~50KB each, GRIBs cleaned after each cycle
- All models tile for NE region currently: HRRR (48h), NAM Nest (60h), GFS (168h), NBM (168h)
- Job queue DB: `cache/jobs.db`, tile metadata DB: `cache/tiles.db`
