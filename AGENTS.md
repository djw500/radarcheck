# AGENTS.md

## Notes for automated agents

- Read `CLAUDE.md` for repository architecture and common commands before making changes.
- Keep API additions covered by tests in `tests/` when feasible.
- Prefer small, targeted commits that describe the change clearly.

## Execution Environment (Important)

- Host: Developer macOS (local MacBook), not a container sandbox.
- Filesystem: Full read/write access to this repo on disk.
- Server: Flask app commonly runs on `http://localhost:5001` during development.
- Process: Agent runs in-process on the Mac and can access the host network, including `http://localhost` endpoints (e.g., curl to the Flask server).
- Python: Use a local virtualenv at `.venv`.
  - Create: `python3 -m venv .venv && source .venv/bin/activate`
  - Install: `pip install -r requirements.txt`
- Network: Allowed. Real data fetches (e.g., NOAA NOMADS) are permitted for tile building and GRIB access.
- Approvals: Treat network installs/long-running fetches as “on-request” and log what you’re doing before running.

## Tile Cache Quick Facts

- Tiles directory: `cache/tiles/<region>/<resolution>/<model>/<run>/<variable>.npz`
- GRIB cache: `cache/gribs/<model>/<run>/<variable>/grib_XX.grib2` (shared across locations/regions)
- Default region: `ne` (Northeast US). Default resolution: `0.1deg`.
- Typical flow to build tiles without full plotting stack:
  - Minimal deps: `pip install numpy xarray requests cfgrib eccodes filelock psutil`
  - Build: `PYTHONPATH=. .venv/bin/python build_tiles.py --region ne --model hrrr --variables t2m dpt apcp prate --max-hours 6`
  - This fetches GRIBs to `cache/gribs/...` and generates tiles in `cache/tiles/...`.
- Helpful endpoints while the server runs on `localhost:5001`:
  - `/api/tile_models?region=ne&resolution=0.1` → models that have tiles
  - `/api/tile_runs/<model>?region=ne&resolution=0.1` → runs for a model
  - `/api/tile_run_detail/<model>/<run>?region=ne&resolution=0.1` → variables present for a run
  - `/api/table/bylatlon?lat=..&lon=..&model=hrrr` → tile-backed table at a point (server infers region)

## Conventions

- Prefer tile-backed endpoints/UI (/table/geo, /api/table/bylatlon) to avoid regenerating per-location caches while iterating on UX.
- When adding features that read tiles, include diagnostics in responses to aid local debugging.
