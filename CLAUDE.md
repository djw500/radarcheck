# CLAUDE.md

This file provides guidance to Claude Code and other AI agents working with this codebase.

## Project Overview

Radarcheck is a weather forecast visualization app that fetches GRIB2 data from NOAA NOMADS, generates statistical tiles, and serves forecast tables through a Flask web interface.

## Architecture

```
NOMADS → scheduler → job_worker.py → tiles.py → NPZ tiles
                                                      ↓
                                              Flask API → UI
```

**Key files**:
- `app.py` - Flask app factory, global auth, index/health/metrics routes
- `routes/forecast.py` - `/api/timeseries/multirun` endpoint + snow derivation
- `routes/status.py` - `/status` dashboard + `/api/status/*` + `/api/jobs/*`
- `tiles.py` - Tile generation, statistics, and point queries
- `cache_builder.py` - GRIB fetching and validation
- `config.py` - Models, variables, regions configuration
- `jobs.py` - SQLite job queue
- `job_worker.py` - Background worker that processes jobs
- `scripts/build_tiles_scheduled.py` - Scheduler that enqueues jobs

**Cache structure**:
```
cache/
├── gribs/<model>/<run>/<var>/grib_XX.grib2   # Raw GRIB files
├── tiles/<region>/<res>/<model>/<run>/<var>.npz  # Statistical tiles
├── jobs.db                                    # Job queue (SQLite)
└── tiles.db                                   # Tile metadata (SQLite)
```

## Common Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run development server
python app.py -p 5001

# Start all dev services (server + scheduler + per-model workers)
bash dev-services.sh start

# Run tests
pytest tests/
```

## Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/` | Main forecast UI |
| `/status` | System status dashboard |
| `/health` | Health check |
| `/metrics` | Prometheus metrics |
| `/api/timeseries/multirun` | Multi-run forecast timeseries at lat/lon |
| `/api/status/summary` | System status JSON |
| `/api/status/run-grid` | Model run completeness grid |
| `/api/status/logs` | Scheduler log tail |
| `/api/jobs/list` | Job queue listing |
| `/api/jobs/retry-failed` | Retry failed jobs |
| `/api/jobs/cancel` | Cancel a job |
| `/api/jobs/enqueue-run` | Manually enqueue a run |

## Development Workflow

**Branch strategy**: Work directly on `main` branch. No PRs or feature branches.

**Local development**:
- Run dev server: `python app.py -p 5001`
- Dev services (server + workers): `bash dev-services.sh start`
- No API key required for local dev server

**Production (Fly.io)**:
- Push to `main` triggers automatic deploy
- API key authentication via global `before_request` (set via `FLY_API_KEY` secret)
- 1GB volume for tile/GRIB caches
- Single generic worker (RAM constrained)

## Development Notes

- Use `.venv` for virtualenv (not `venv`)
- Server runs on `localhost:5001` during development
- Keep `PARALLEL_DOWNLOAD_WORKERS=1` for NOMADS reliability

## Adding New Features

1. **New variable**: Add to `WEATHER_VARIABLES` in `config.py`
2. **New model**: Add to `MODELS` in `config.py`
3. **New region**: Add to `TILING_REGIONS` in `config.py`
4. **New API endpoint**: Add route in `routes/forecast.py` or `routes/status.py`

## Testing

```bash
pytest tests/
```

## Commit Style

- Small, targeted commits
- Conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`
- Example: `feat(tiles): add GFS support for NE region`
