# Radarcheck Roadmap

> Consolidated from STRATEGIC_PLAN.md, IMPROVEMENT_PLAN.md, WEATHER_FEATURES_PLAN.md, PLAN.md

## Vision

Transform Radarcheck from a location-based PNG image viewer into a **tile-based multi-model forecast table** that shows predictions from multiple weather models side-by-side, enabling users to quickly compare forecasts and track how they evolve.

**Target Use Case**: "GFS is saying 12 inches of snow this weekend, but keeps changing its mind."

---

## Current State (January 2026)

### Working
- **Tile system**: `build_tiles.py` generates 0.1° statistical grids (min/max/mean)
- **Tabular UI**: `/table/geo` displays forecast data by lat/lon
- **Models with tiles**: HRRR (24h), NAM Nest (60h) for NE region
- **Centralized GRIB cache**: No per-location duplication

### Broken/Deprecated
- **Old cache_builder**: PNG generation fails for many variables
- **Location routes**: `/location/<id>` mostly non-functional

---

## Phase 1: Multi-Model Table (Current Priority)

### Backend
- [ ] `/api/table/multimodel` - Merge HRRR + NAM + GFS data by valid time
- [ ] Build GFS tiles for NE region (7-day forecast)
- [ ] Add more tiling regions (SE, MW, SW, NW)

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

## Phase 3: Fly.io Automation

- [ ] Scheduled tile building (HRRR hourly, NAM/GFS every 6h)
- [ ] `--clean-gribs` for disk space management
- [ ] Tile freshness check in `/health` endpoint

---

## Phase 4: Deprecate Legacy System

### Remove Old Endpoints
- [ ] Redirect `/location/<id>` to `/forecast?lat=...&lon=...`
- [ ] Remove `/frame/` PNG serving routes
- [ ] Remove `cache_builder.py` image generation code
- [ ] Remove `plotting.py` (or keep minimal for future use)
- [ ] Delete `templates/location.html`, `templates/index.html` (old location picker)

### Cleanup
- [ ] Remove legacy cache structure: `cache/<location>/<model>/<run>/<var>/frame_*.png`
- [ ] Keep only: `cache/gribs/` and `cache/tiles/`
- [ ] Update `supervisord.conf` to remove cache_builder loop
- [ ] Update Dockerfile to remove matplotlib/cartopy (optional, saves image size)

### New Primary Routes
| Old Route | New Route | Notes |
|-----------|-----------|-------|
| `/` | `/forecast` | Main entry point |
| `/location/<id>` | `/forecast?lat=...&lon=...` | Redirect with geocoded coords |
| `/table/geo` | `/forecast` | Rename for clarity |
| `/api/table/bylatlon` | `/api/forecast` | Cleaner API name |

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
| HRRR | 24h | Hourly | 3km | Active |
| NAM 3km | 60h | 6-hourly | 3km | Active |
| NAM 12km | 84h | 6-hourly | 12km | Configured |
| GFS | 384h | 6-hourly | 25km | Configured |
| RAP | 21h | Hourly | 13km | Configured |
| ECMWF | 240h | 12-hourly | 9km | Scaffolded (needs CDS credentials) |

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

# Run local server
python app.py -p 5001

# Run tests
pytest tests/
```

---

## Related Documents

- `docs/planning/todos.md` - Detailed implementation checklist
- `docs/architecture/tile-system.md` - How tiles work
- `docs/ux-ideas.md` - UX improvement brainstorm
- `docs/operations/flyio-guide.md` - Deployment guide
