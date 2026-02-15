# Radarcheck Roadmap

**Last updated**: 2026-02-15

> Consolidated from STRATEGIC_PLAN.md, IMPROVEMENT_PLAN.md, WEATHER_FEATURES_PLAN.md, PLAN.md

## Vision

Transform Radarcheck from a location-based PNG image viewer into a **tile-based multi-model forecast table** that shows predictions from multiple weather models side-by-side, enabling users to quickly compare forecasts and track how they evolve.

**Target Use Case**: "GFS is saying 12 inches of snow this weekend, but keeps changing its mind."

---

## Current State (February 2026)

### Working
- **Tile system**: `build_tiles.py` generates 0.1° statistical grids (min/max/mean)
- **Tabular UI**: `/table/geo` displays forecast data by lat/lon
- **Models with tiles**: HRRR (48h), NAM Nest (60h), GFS (168h), NBM (168h) for NE region
- **Centralized GRIB cache**: No per-location duplication
- **Job queue scheduler**: `scripts/build_tiles_scheduled.py` enqueues + drains via SQLite `jobs.py`
- **Status API**: `/api/status/summary` includes job queue counts

### Broken/Deprecated
- **Old cache_builder**: PNG generation code removed (commit c39c6e3)
- **Location routes**: `/location/<id>` mostly non-functional

---

## Phase 1: Multi-Model Table (Current Priority)

### Backend
- [ ] `/api/table/multimodel` - Merge HRRR + NAM + GFS data by valid time
- [ ] Add more tiling regions (SE, MW, SW, NW)
- [ ] Region inference for all US coordinates

### Frontend
- [ ] Multi-column table: [Local Time | Precip | Snow | Temp] × [HRRR, NAM, GFS]
- [ ] Local time conversion (UTC → browser timezone)
- [ ] Location autocomplete (Nominatim geocoding)
- [ ] Color-coded cells by value thresholds

---

## Phase 2: Historical Run Comparison

- [ ] `/api/table/multirun` - Show last N runs of same model side-by-side
- [ ] "Show History" toggle in UI
- [ ] Highlight significant forecast changes

---

## Phase 3: Fly.io Automation — DONE

- [x] Scheduled tile building via SQLite job queue
- [x] `--clean-gribs` for disk space management
- [x] Tile/GRIB retention policies (tiered synoptic + hourly)
- [x] Job queue visibility in `/api/status/summary`
- [ ] Tile freshness check in `/health` endpoint

---

## Phase 4: Legacy Cleanup

- [x] Remove dead code from `cache_builder.py`
- [ ] Redirect `/location/<id>` to `/forecast?lat=...&lon=...`
- [ ] Remove `plotting.py` (only `select_variable_from_dataset` still used)
- [ ] Remove old templates
- [ ] Clean up legacy cache dirs on disk

---

## Phase 5: UX Polish

- [ ] Units in column headers
- [ ] Model init time display ("HRRR 12Z")
- [ ] CSV/JSON export
- [ ] Mobile-optimized layout

---

## Supported Models

| Model | Forecast | Update | Resolution | Status |
|-------|----------|--------|------------|--------|
| HRRR | 48h | Hourly | 3km | Active |
| NAM 3km | 60h | 6-hourly | 3km | Active |
| GFS | 168h | 6-hourly | 25km | Active |
| NBM | 168h | 6-hourly | 2.5km | Active |
| ECMWF | 240h | 12-hourly | 9km | Disabled (unstable) |

---

## Supported Variables

**Precipitation**: refc, apcp, prate, asnow, snod, csnow
**Temperature**: t2m, dpt, rh
**Wind**: wind_10m, gust
**Severe**: cape, hlcy, hail, vis

---

## Quick Commands

```bash
# Build HRRR tiles for NE region
python build_tiles.py --region ne --model hrrr --max-hours 24

# Build GFS tiles (7 days)
python build_tiles.py --region ne --model gfs --max-hours 168 --variables t2m apcp snod

# Run scheduler once (enqueue + drain)
python scripts/build_tiles_scheduled.py --once

# Run local server
python app.py -p 5001

# Run tests
pytest tests/
```

---

## Key Architecture

```
NOMADS → scheduler enqueues jobs → jobs.py (SQLite) → drain_queue() → job_worker → tiles.npz
                                                                        ↓
                                                                    tile_db.py (SQLite)
                                                                        ↓
                                                                    Flask API → /forecast UI
```

**DBs**: `cache/jobs.db` (job queue), `cache/tiles.db` (tile metadata)

---

## Related Documents

- `docs/planning/todos.md` - Detailed implementation checklist
- `docs/architecture/overview.md` - System design
- `docs/operations/flyio-guide.md` - Deployment guide
