# AGENTS.md

Instructions for automated AI agents (Codex, Gemini CLI, etc.) working on this codebase.

> **Important**: Read `CLAUDE.md` first for full project context.

## Quick Context

- **What**: Weather forecast app serving NOAA model data as tables
- **Stack**: Python 3.11, Flask, xarray/cfgrib, Fly.io
- **Focus**: Tile-based tabular forecasts (not PNG images)

## Environment

- **Host**: Developer macOS or Linux (not containerized during dev)
- **Virtualenv**: `.venv` (create with `python3 -m venv .venv`)
- **Server**: Flask on `http://localhost:5001`
- **Network**: Real NOAA NOMADS fetches allowed

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | **Primary instructions** - read this first |
| `config.py` | Models, variables, regions |
| `build_tiles.py` | Tile generation CLI |
| `tiles.py` | Tile logic |
| `app.py` | Flask routes |

## Tile Cache

```
cache/tiles/<region>/<resolution>/<model>/<run>/<variable>.npz
```

- Default region: `ne` (Northeast US)
- Default resolution: `0.1` degrees (~10km)
- Stats per cell: min, max, mean

## Useful Commands

```bash
# Build tiles (minimal deps)
pip install numpy xarray requests cfgrib eccodes filelock psutil
python build_tiles.py --region ne --model hrrr --variables t2m dpt --max-hours 6

# Test tile API
curl "http://localhost:5001/api/tile_models?region=ne&resolution=0.1"
curl "http://localhost:5001/api/table/bylatlon?lat=40.05&lon=-75.4&model=hrrr"
```

## Workflow

- **Branch strategy**: Commit directly to `main` branch (no PRs)
- **Local testing**: Dev server runs on macbook at `localhost:5001` (no API key)
- **Production**: Fly.io requires API key authentication

## Conventions

- Prefer tile-backed endpoints (`/table/geo`, `/api/table/bylatlon`)
- Avoid legacy per-location caches
- Include diagnostics in API responses for debugging
- Small, targeted commits with conventional prefixes

## Documentation

- `docs/planning/roadmap.md` - Current priorities
- `docs/planning/todos.md` - Task checklist
- `docs/architecture/overview.md` - System design
