# Worklog: ECMWF Integration via Herbie (2026-01-24)

## Objective
Integrate ECMWF (IFS HRES) forecast data into Radarcheck using the `herbie-data` Python package, ensuring it appears in the main multi-model forecast UI (`index.html`).

## Implementation Steps

### 1. Backend Implementation
- **Tool Selection:** Selected `herbie-data` over `cdsapi` for lightweight, keyless access to ECMWF Open Data.
- **Fetcher:** Created `ecmwf.py` implementing `fetch_grib_herbie` to download specific variables (e.g., `:2t:` for t2m) via Herbie.
- **Integration:** Updated `cache_builder.py` to route `source="herbie"` models to the new fetcher.

### 2. Configuration (`config.py`)
- **Model Config:** Configured `ecmwf_hres` with:
  - `source`: `"herbie"`
  - `update_frequency_hours`: 6 (Synoptic)
  - `max_forecast_hours`: 240 (10 days)
  - `forecast_hour_schedule`: 3-hourly (0-144h), then 6-hourly. **CRITICAL FIX:** Without this, the system defaulted to hourly and failed to find files.
  - `availability_check_var`: `"t2m"` (Required by scheduler, though logic was bypassed).

### 3. Frontend Updates (`templates/index.html`)
- **Hardcoded Lists:** Discovered `index.html` used hardcoded lists for model toggles and configurations, ignoring the dynamic API for these UI elements.
- **Fixes:**
  - Added `ecmwf_hres` to `MODEL_CONFIG` with color `#10b981` (Emerald).
  - Added HTML toggle for ECMWF in the sticky footer.
  - Added `ecmwf_hres` to `modelOrder` and `modelColors` in `renderTable`.

### 4. Scheduler Fixes (`scripts/build_tiles_scheduled.py`)
- **Model List:** Added `ecmwf_hres` to the hardcoded `MODELS_CONFIG` list in the scheduler script.
- **Availability Check:** Updated `check_run_available` to return `True` immediately for `source="herbie"` models, skipping the NOMADS-specific URL check which would otherwise fail.

## Verification Strategy

### Manual Verification
Since the server and scheduler run persistently in a `tmux` session, we verified the pipeline by running a manual build command in the dev shell:

```bash
./.venv/bin/python build_tiles.py --region ne --model ecmwf_hres --variables t2m --max-hours 6 --run run_20260124_00
```

**Success Criteria:**
- Output confirmed "Saved t2m tiles to ..."
- No "404" or "Did not find" errors for valid 3-hourly steps.

### API Verification
Verified the frontend would see the new model using `curl`:

```bash
curl -s http://localhost:5001/api/tile_models
```
**Result:** JSON response included `ecmwf_hres` with the built run.

## Status
- **ECMWF HRES:** Fully integrated (Backend + Scheduler + UI).
- **Herbie:** Successfully caching GRIBs.
- **UI:** `index.html` updated to render ECMWF lines and table columns.
