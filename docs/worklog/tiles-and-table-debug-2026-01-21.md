# Tiles + Table Debug — 2026-01-21

Context
- Goal: Move rendering/table to 0.1° tile cache (min/max/mean) and enable on-demand tables by lat/lon.
- Added: tile builder (build_tiles.py), tile-backed endpoints, /table/geo page with diagnostics, and HRRR tiles for NE region.

What changed today
- Built real HRRR tiles (NE, 0.1°, 6–8 hours): t2m, dpt, apcp, prate, refc, rh, vis, wind_10m.
- Fixed unit mapping and indexing:
  - t2m/dpt unit conversions now respect source units (K→°F, °C→°F) via unit_conversions_by_units.
  - Tile metadata now records 0–360 longitude indexing; lookups normalize longitudes correctly.
- Diagnostics:
  - /api/table/bylatlon includes diagnostics.tile_cell with indices and cell center lat/lon.
  - Added /api/tile_models, /api/tile_runs, /api/tile_run_detail, and richer failure diagnostics.
- New /table/geo UX:
  - Model/run dropdowns constrained by available tiles.
  - Manual lat/lon inputs and IP-based approximate location fallback.
  - “Copy Debug JSON” button to share full diagnostic payloads.

Issue discovered & resolved
- Symptom: t2m appeared ~70°F in January — implausibly warm.
- Root cause: Sampling used wrong tile x-index when GRIB/tiles used 0–360 longitudes; the lookup assumed −180..180 without wrap.
- Fix: Persist and use lon_0_360 + index_lon_min in tile meta; normalize lon on lookup. Verified raw GRIB at the point: 261.82 K (~9.2°F), confirming source was correct.

How to validate locally
- Runs and models with tiles:
  - GET /api/tile_models?region=ne&resolution=0.1
  - GET /api/tile_runs/hrrr?region=ne&resolution=0.1
- Variables for a run:
  - GET /api/tile_run_detail/hrrr/<run_id>?region=ne&resolution=0.1
- Table at point:
  - GET /api/table/bylatlon?lat=40.0574&lon=-75.4017&model=hrrr&stat=mean
  - Check diagnostics.tile_cell and confirm lat_center/lon_center near the input.

Next steps
- Build additional variables (gust) with improved retry/backoff.
- Extend HRRR coverage to 24h; consider NAM/GFS sets.
- Add unit labels to /table/geo headers; add quick CSV/JSON download.
- Optionally auto-build tiles post GRIB fetch in cache_builder (flag --build-tiles).

Operational note
- Agent runs in-process on the developer Mac and can access localhost (server expected at http://localhost:5001).

## Bug Fix 2 & Optimization (Session 2)

### Issue: T2M still warm (regression/subtlety)
- **Symptom:** Despite the longitude fix, temperatures were still reading high (e.g., ~68-70°F) for Philly in January, while the raw GRIB at the correct coordinate showed ~11°F.
- **Root Cause:** The `_reduce_stats` function in `tiles.py` was applying an index array (calculated from valid points) to the *full, flattened* value array. This caused it to pull data from the beginning of the array (lower latitudes, warmer temps) instead of the correct spatially mapped indices.
- **Fix:** Updated `_reduce_stats` to accept `valid_mask` and filter the value array *before* applying the sort order. Added a regression test (`scripts/test_tiles_e2e.py`) that uses a larger source grid than the target region to catch this specific indexing error.
- **Result:** T2M values now match the GRIB (~11°F).

### Optimization Discovery: GRIB Duplication
- **Observation:** `cache/seattle/...` and `cache/boston/...` contain identical GRIB files for the same model/run (checked hash/metadata).
- **Implication:** The current downloader fetches the full CONUS grid for every configured location, resulting in ~200MB of duplication per city.
- **Next Step:** Refactor `cache_builder.py` to store GRIBs in a central `cache/gribs/<model>/<run>/` directory, enabling multiple location/tile extractions from a single download.

## NAM Nest Enablement
- **Goal:** Enable NAM Nest (3km) model alongside HRRR.
- **Challenge:** NAM Nest does not support all variables available in HRRR (e.g., `asnow`, `hlcy` at 0-3km, `hail`).
- **Solution:** Added `model_exclusions` list to `WEATHER_VARIABLES` in `config.py`. Updated `build_tiles.py` and `cache_builder.py` to skip excluded variables.
- **Result:** Successfully built tiles for NAM Nest (run `20260121_00`) for `t2m`, `refc`, `dpt`, `wind_10m`, `apcp`, `snod`, `cape`.
- **Validation:** `/api/table/bylatlon` correctly serves NAM Nest data for Philly.
