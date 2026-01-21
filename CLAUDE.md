# CLAUDE.md

This file provides guidance to Claude Code and other AI agents working with this codebase.

## Project Overview

Radarcheck is a weather forecast visualization app that fetches GRIB2 data from NOAA NOMADS, generates statistical tiles, and serves forecast tables through a Flask web interface.

**Current Focus**: Transitioning from legacy PNG-based location views to a tile-based multi-model tabular interface.

## Architecture

```
NOMADS → build_tiles.py → GRIB → tiles.py → NPZ tiles → Flask → /forecast UI
```

**Key files**:
- `build_tiles.py` - CLI to fetch GRIBs and generate tiles
- `tiles.py` - Tile statistics and point queries
- `app.py` - Flask routes and API
- `config.py` - Models, variables, regions configuration

**Cache structure**:
```
cache/
├── gribs/<model>/<run>/<var>/grib_XX.grib2   # Raw GRIB files
└── tiles/<region>/<res>/<model>/<run>/<var>.npz  # Statistical tiles
```

## Common Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run development server
python app.py -p 5001

# Build tiles for a region/model
python build_tiles.py --region ne --model hrrr --max-hours 24

# Build with cleanup (saves disk space)
python build_tiles.py --region ne --model gfs --max-hours 168 --clean-gribs

# Run tests
pytest tests/

# Run single test
pytest tests/test_tiles_build.py -v
```

## Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/table/geo` | Main tabular forecast UI (tile-backed) |
| `/api/table/bylatlon` | JSON forecast data at lat/lon |
| `/api/tile_models` | Available models with tiles |
| `/api/tile_runs/<model>` | Available runs for a model |
| `/health` | Health check |

## Current State (January 2026)

**Working**:
- Tile building for HRRR and NAM Nest (NE region)
- `/table/geo` UI with lat/lon input
- Centralized GRIB caching

**Deprecated/Broken**:
- `/location/<id>` PNG-based views
- `cache_builder.py` image generation

## Documentation

```
docs/
├── planning/roadmap.md      # Vision and phases
├── planning/todos.md        # Implementation checklist
├── architecture/overview.md # System design
├── operations/flyio-guide.md # Deployment
└── ux/ideas.md              # UX improvements
```

## Development Workflow

**Branch strategy**: Work directly on `main` branch. No PRs or feature branches—commit directly to main.

**Local development**:
- Run dev server on macbook: `python app.py -p 5001`
- Test tile building and caching locally before deploying
- No API key required for local dev server

**Production (Fly.io)**:
- Push to `main` triggers automatic deploy
- API key authentication is required (set via `FLY_API_KEY` secret)
- 1GB volume for tile/GRIB caches

## Development Notes

- Use `.venv` for virtualenv (not `venv`)
- Server runs on `localhost:5001` during development
- Keep `PARALLEL_DOWNLOAD_WORKERS=1` for NOMADS reliability

## Adding New Features

1. **New variable**: Add to `WEATHER_VARIABLES` in `config.py`
2. **New model**: Add to `MODELS` in `config.py`
3. **New region**: Add to `TILING_REGIONS` in `config.py`
4. **New API endpoint**: Add route in `app.py`

## Testing

```bash
pytest tests/                    # All tests
pytest tests/test_tiles_build.py # Tile tests
pytest --cov=. --cov-report=term # With coverage
```

## Commit Style

- Small, targeted commits
- Conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`
- Example: `feat(tiles): add GFS support for NE region`
