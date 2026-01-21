# Radarcheck Implementation TODOs

## Priority Legend
- **P0**: Must-have for MVP multi-model table
- **P1**: Important for good UX
- **P2**: Nice-to-have enhancements

---

## Phase 1: Multi-Model Table (P0)

### 1.1 Backend: Multi-Model API

- [ ] **P0** Create `/api/table/multimodel` endpoint in `app.py`
  - Input: `lat`, `lon`, `stat` (mean/min/max)
  - Output: `{ rows: [...], models: {...}, metadata: {...} }`
  - Merge data from all models with tiles for inferred region
  - Handle different forecast lengths (pad with null for shorter models)

- [ ] **P0** Add model init time to tile metadata
  - Update `build_tiles.py` to include `init_time_utc` in NPZ metadata
  - Surface in API response for freshness display

- [ ] **P0** Ensure region inference works for all US coordinates
  - Current `inferRegion()` in JS only has "ne" region
  - Add backend `/api/infer_region?lat=...&lon=...` or expand JS logic

### 1.2 Backend: Expand Tile Coverage

- [ ] **P0** Build GFS tiles for NE region
  ```bash
  python build_tiles.py --region ne --model gfs --max-hours 168 --variables t2m apcp snod
  ```

- [ ] **P1** Add more tiling regions to `config.py`
  - `se` (Southeast): lat 24-39, lon -92 to -75
  - `mw` (Midwest): lat 36-49, lon -104 to -80
  - `sw` (Southwest): lat 31-42, lon -125 to -102
  - `nw` (Northwest): lat 41-49.5, lon -125 to -110

- [ ] **P1** Update `build_tiles.py` to accept `--region all`
  - Loop through all configured regions

### 1.3 Frontend: Multi-Model Table View

- [ ] **P0** Create `templates/forecast.html`
  - Multi-column layout: [Local Time | Precip (models) | Snow (models) | Temp (models)]
  - JavaScript to fetch `/api/table/multimodel`
  - Merge and render rows by valid time

- [ ] **P0** Implement local time conversion in JavaScript
  ```javascript
  const localTime = new Date(utcString).toLocaleString('en-US', {
    weekday: 'short', hour: 'numeric', minute: '2-digit'
  });
  ```

- [ ] **P0** Add `/forecast` route in `app.py`
  - Render `forecast.html` template
  - Pass default region info

- [ ] **P1** Color-code cells by value thresholds
  - Temperature: blue (<32°F) to red (>80°F)
  - Precipitation: white (0) to blue (>1 inch)
  - Snow: white (0) to purple (>12 inches)

### 1.4 Frontend: Location Input

- [ ] **P0** Add location autocomplete
  - Integrate Nominatim (free, OSM-based) or Mapbox geocoding
  - Show suggestions as user types
  - Populate lat/lon on selection

- [ ] **P1** Save recent locations to localStorage
  - Store last 5 locations with name + lat/lon
  - Show as quick-select buttons

- [ ] **P2** Add map picker modal
  - Leaflet map with click-to-select
  - Show current location marker

---

## Phase 2: Historical Run Comparison (P1)

### 2.1 Backend: Multi-Run Support

- [ ] **P1** Implement `/api/table/multirun` endpoint
  - Input: `lat`, `lon`, `model`, `num_runs` (default 3)
  - Output: Side-by-side data from last N runs of same model
  - Useful for "How has the forecast changed?"

- [ ] **P1** Verify `scripts/build_history.py` works for all models
  - Currently builds 3-day history
  - Test with HRRR, NAM, GFS

### 2.2 Frontend: Run Comparison

- [ ] **P1** Add "Show History" toggle to forecast.html
  - Switch between "Latest" and "Last 3 runs" views
  - Highlight cells where values changed significantly (>20%)

---

## Phase 3: Fly.io Automation (P0)

### 3.1 Scheduled Tile Building

- [ ] **P0** Create `scripts/build_tiles_scheduled.py`
  - Wrapper that builds tiles for configured schedule
  - Logs progress and errors
  - Uses `--clean-gribs` to manage disk space

- [ ] **P0** Add fly.io scheduled task to `fly.toml`
  ```toml
  [[services]]
    # ... existing http service ...

  [processes]
    app = "gunicorn app:app"
    tile_builder = "python scripts/build_tiles_scheduled.py"

  # Or use fly.io Machines scheduled scaling
  ```

- [ ] **P0** Verify tile persistence across deploys
  - Current `fly.toml` mounts `radar_cache` volume at `/app/cache`
  - Ensure tiles are in `/app/cache/tiles/`

### 3.2 Health and Monitoring

- [ ] **P1** Add tile freshness check to `/health` endpoint
  - Check age of latest tile files
  - Return warning if tiles are >2 hours old for HRRR

- [ ] **P2** Add Prometheus metrics for tile building
  - `radarcheck_tile_build_seconds` (histogram)
  - `radarcheck_tile_build_failures_total` (counter)

---

## Phase 4: UX Polish (P1/P2)

### 4.1 Data Presentation

- [ ] **P1** Add units to column headers
  - "Precip (in)", "Temp (°F)", "Wind (mph)"

- [ ] **P1** Show model init times in header
  - "HRRR (12Z)", "NAM (06Z)", "GFS (00Z)"

- [ ] **P1** Handle missing data gracefully
  - Show "—" for missing hours instead of 0
  - Gray out cells with no data

- [ ] **P2** Add variable category tabs
  - "Precipitation" | "Temperature" | "Wind" | "All"

### 4.2 Export and Sharing

- [ ] **P1** Add "Download CSV" button
  - Export visible table as CSV

- [ ] **P2** Add shareable URL with encoded location
  - `/forecast?lat=40.05&lon=-75.39&name=Philadelphia`

### 4.3 Mobile Optimization

- [ ] **P1** Responsive table with horizontal scroll
- [ ] **P2** Condensed mobile layout (fewer columns visible by default)

---

## Technical Debt and Cleanup

### Code Quality

- [ ] **P2** Deprecate old `/location/<id>` routes
  - Add redirect to `/forecast` with appropriate lat/lon

- [ ] **P2** Remove unused code from `cache_builder.py`
  - PNG generation code if fully migrated to tiles

- [ ] **P2** Add type hints to `tiles.py` and `build_tiles.py`

### Testing

- [ ] **P1** Add tests for `/api/table/multimodel` endpoint
- [ ] **P1** Add tests for local time conversion logic
- [ ] **P2** Add end-to-end test for forecast.html rendering

---

## Quick Wins (Can Do Now)

1. **Build GFS tiles**: `python build_tiles.py --region ne --model gfs --max-hours 168 --variables t2m apcp`
2. **Add init_time to API**: Update `/api/table/bylatlon` to include model init time
3. **Improve region inference**: Add fallback to "ne" region for any US coordinate (temporary)

---

## Notes

- Disk space on fly.io: 1GB volume, tiles are ~50KB each, need to manage with `--clean-gribs`
- NOMADS rate limits: Keep `PARALLEL_DOWNLOAD_WORKERS=1` for reliability
- GFS forecast hours: Use every 3 hours after hour 120 to reduce data volume
