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
- `grib_fetcher.py` - GRIB downloading, validation, URL building
- `config.py` - Models, variables, regions configuration
- `jobs.py` - SQLite job queue
- `job_worker.py` - Background worker that processes jobs
- `scripts/scheduler.py` - Scheduler that enqueues jobs + cleanup

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

## Sandbox Environment

This project runs inside a Docker container (the "innie"). Key context:

- **Container**: Python 3.11-slim on OrbStack (aarch64), user `dev` (uid 501)
- **Innie/Outie protocol**: You're inside Docker. The host Claude is the "outie."
  - Read from `~/.claude/inbox/` (messages from host)
  - Write to `~/.claude/outbox/` (messages to host)
  - File naming: `YYYY-MM-DD-HHMMSS-topic.md`
- **Rebuilds**: Edit `/workspace/Dockerfile.unified` or `/workspace/docker-compose.yml`, then drop a message in outbox asking the host to run `./sandbox.sh --rebuild`
- **No Docker socket** — you cannot rebuild yourself
- **RTK hook**: Bash commands are automatically rewritten by the RTK hook for token savings
- **Stagehand MCP**: Available globally for browser automation (local Playwright, `HEADLESS=false`, vision model `gemini-3-flash-preview`)
- **Gemini**: Always use `gemini-3-flash-preview` (never 2.5 Flash). Use `gemini-cli -p` or `llm -m gemini-3-flash-preview`
- **Fly.io CLI**: `~/.fly/bin/flyctl`

## Available Skills

Skills are symlinked from `/workspace/.claude/skills/`:
- **weather-analysis** — Cross-model forecast overlap analysis at a lat/lon
- **gemini-agent** — Dispatch research queries to Gemini CLI (free, fast)

## Self-Maintenance Rule

**Always update this CLAUDE.md when making significant changes to the project** — new endpoints, new files, architecture changes, new dependencies, workflow changes, or lessons learned. Keep it accurate so a fresh Claude session can be productive immediately.

## Commit Style

- Small, targeted commits
- Conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`
- Example: `feat(tiles): add GFS support for NE region`
