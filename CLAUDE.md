# CLAUDE.md

This file provides guidance to Claude Code and other AI agents working with this codebase.

## Project Overview

Radarcheck is a weather forecast visualization app that fetches GRIB2 data from NOAA NOMADS and ECMWF, generates statistical tiles, and serves forecast tables through a Flask web interface.

**Target Use Case**: "GFS is saying 12 inches of snow this weekend, but keeps changing its mind."

**Current Focus**: Transitioning from legacy PNG-based location views to a tile-based multi-model tabular interface.

## Architecture

```
NOMADS/ECMWF → build_tiles.py → GRIB → tiles.py → NPZ tiles → Flask → /table/geo UI
```

### Data Flow

1. **Fetch**: `build_tiles.py` downloads GRIBs from NOMADS (HRRR, NAM, GFS, NBM) or ECMWF (via Herbie)
2. **Process**: `tiles.py` computes grid statistics (min/max/mean) over 0.1° cells
3. **Store**: Results saved as NPZ files in `cache/tiles/`
4. **Serve**: Flask API queries tiles by lat/lon, returns JSON
5. **Display**: `table_geo.html` renders forecast tables

### Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask routes, API endpoints, weather derivations (SLR, snowfall) |
| `build_tiles.py` | CLI to fetch GRIBs and generate tiles |
| `tiles.py` | Tile statistics, point queries, NPZ serialization |
| `config.py` | Models, variables, regions configuration |
| `forecast_table.py` | Tabular forecast data generation |
| `cache_builder.py` | Legacy NOMADS downloader (deprecated for tiles) |
| `ecmwf.py` | ECMWF Herbie data fetcher |
| `utils.py` | Unit conversions (K→°F, m→in, etc.) |

### Cache Structure

```
cache/
├── gribs/<model>/<run>/<var>/grib_XX.grib2   # Raw GRIB files
├── tiles/<region>/<res>/<model>/<run>/<var>.npz  # Statistical tiles
└── scheduler_status.json                     # Background builder state
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
python build_tiles.py --region ne --model gfs --max-hours 168 --variables t2m apcp snod

# Build with cleanup (saves disk space)
python build_tiles.py --region ne --model gfs --max-hours 168 --clean-gribs

# Run tests
pytest tests/
pytest tests/test_tiles_build.py -v
pytest --cov=. --cov-report=term  # With coverage (80% required)

# Linting
black .
isort .
flake8
mypy .
```

## Key Endpoints

### Primary (Tile-Based)

| Endpoint | Purpose |
|----------|---------|
| `/table/geo` | Main tabular forecast UI (tile-backed) |
| `/api/table/bylatlon` | JSON forecast data at lat/lon |
| `/api/tile_models` | Available models with tiles |
| `/api/tile_runs/<model>` | Available runs for a model |

### Infrastructure

| Endpoint | Purpose |
|----------|---------|
| `/health` | Health check |
| `/api/status` | System and cache status |
| `/metrics` | Prometheus metrics |

### Legacy (Deprecated)

| Endpoint | Status |
|----------|--------|
| `/location/<id>` | Broken - PNG-based views |
| `/frame/` | Deprecated - PNG serving |

## Supported Models

| Model | Forecast | Update | Source | Status |
|-------|----------|--------|--------|--------|
| HRRR | 18-48h | Hourly | NOMADS | Active |
| NAM 3km | 60h | 6-hourly | NOMADS | Active |
| NAM 12km | 84h | 6-hourly | NOMADS | Configured |
| GFS | 384h | 6-hourly | NOMADS | Active |
| NBM | 264h | Hourly | NOMADS | Active |
| RAP | 21h | Hourly | NOMADS | Configured |
| ICON | 180h | 6-hourly | DWD | Configured |
| ECMWF HRES | 240h | 6-hourly | Herbie | Active |
| ECMWF EPS | 360h | 6-hourly | Herbie | Active |

## Supported Variables

| Category | Variables |
|----------|-----------|
| **Precipitation** | refc, apcp, prate |
| **Winter** | asnow, snod, csnow |
| **Temperature** | t2m, dpt, rh |
| **Wind** | wind_10m, gust |
| **Severe** | cape, hlcy, hail |
| **Surface** | msl |

## Current State (January 2026)

**Working**:
- Tile building for HRRR, NAM, GFS, NBM, ECMWF (NE region)
- `/table/geo` UI with lat/lon input
- Centralized GRIB caching
- Background tile builder on Fly.io
- ECMWF integration via Herbie

**Deprecated/Broken**:
- `/location/<id>` PNG-based views
- `cache_builder.py` image generation

## Development Workflow

### Branch Strategy

Work directly on `main` branch. No PRs or feature branches—commit directly to main.

### Local Development

```bash
# Start dev server
source .venv/bin/activate
python app.py -p 5001

# Test tile building
python build_tiles.py --region ne --model hrrr --max-hours 6 --dry-run
```

- Server runs on `localhost:5001` during development
- No API key required for local dev server
- Use `.venv` for virtualenv (not `venv`)

### Production (Fly.io)

- Push to `main` triggers automatic deploy via GitHub Actions
- API key authentication required (set via `RADARCHECK_API_KEY` secret)
- 1GB persistent volume for tile/GRIB caches at `/app/cache`
- Two processes via supervisor: web (gunicorn) + tile_builder
- Region: `ewr` (New Jersey)

### Testing

```bash
pytest tests/                        # All tests
pytest tests/test_api.py -v          # API tests
pytest tests/test_tiles_build.py -v  # Tile tests
pytest --cov=. --cov-report=term     # With coverage
```

**CI Requirements**: 80% test coverage enforced in GitHub Actions.

## Adding New Features

1. **New variable**: Add to `WEATHER_VARIABLES` in `config.py`
2. **New model**: Add to `MODELS` in `config.py`
3. **New region**: Add to `TILING_REGIONS` in `config.py`
4. **New API endpoint**: Add route in `app.py`

## Code Style

### Python

- Follow [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- `snake_case` for functions/variables, `PascalCase` for classes, `ALL_CAPS` for constants
- 4 spaces indentation, 80 character line limit
- Type annotations encouraged for public APIs
- Docstrings with `Args:`, `Returns:`, `Raises:` sections

### JavaScript

- Vanilla JavaScript (no frameworks)
- ES6+ features allowed
- Jest for testing

### Commits

- Small, targeted commits
- Conventional prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`
- Include scope: `feat(tiles): add GFS support for NE region`

## Product Guidelines

- **Technical Precision**: Use meteorological terminology ("Reflectivity", "12Z Run", "Dew Point")
- **Data Density**: Maximize information per pixel, users want raw data
- **Speed**: Prioritize text and lightweight tables over heavy graphics
- **Professional Tone**: Objective, concise, fact-based

## Documentation

```
docs/
├── README.md                    # Doc index
├── API.md                       # API reference
├── architecture/
│   ├── overview.md              # System design
│   ├── backend_outputs.md       # Output format specs
│   └── adr/                     # Architecture Decision Records
├── planning/
│   ├── roadmap.md               # Vision and phases
│   └── todos.md                 # Implementation checklist
├── operations/
│   └── flyio-guide.md           # Deployment guide
├── ux/
│   └── ideas.md                 # UX improvements
└── worklog/                     # Development session logs

conductor/
├── product.md                   # Product definition
├── product-guidelines.md        # Design principles
├── tech-stack.md                # Technology choices
├── workflow.md                  # Development workflow
└── code_styleguides/            # Style guides
```

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `RADARCHECK_API_KEY` | API authentication (production) | None |
| `TILE_BUILD_INTERVAL_MINUTES` | Background builder frequency | 15 |
| `TILE_BUILD_MAX_HOURS_HRRR` | Max HRRR forecast hours | 18 |
| `TILE_BUILD_MAX_HOURS_GFS` | Max GFS forecast hours | 72 |
| `TILE_BUILD_VARIABLES` | Variables to build | asnow,t2m,wind_10m,... |
| `PARALLEL_DOWNLOAD_WORKERS` | NOMADS download parallelism | 1 |

## Key Configuration (config.py)

### Tiling Regions

```python
"TILING_REGIONS": {
    "ne": {  # Northeast US (expanded)
        "lat_min": 33.0, "lat_max": 47.0,
        "lon_min": -88.0, "lon_max": -66.0,
        "default_resolution_deg": 0.1,
        "stats": ["mean"],
    }
}
```

### Network Settings

- `DOWNLOAD_TIMEOUT_SECONDS`: 60
- `MAX_DOWNLOAD_RETRIES`: 3
- `PARALLEL_DOWNLOAD_WORKERS`: 1 (keep at 1 for NOMADS reliability)

## Troubleshooting

### Common Issues

1. **GRIB download failures**: NOMADS has rate limits, keep workers at 1
2. **Missing tiles**: Check `cache/tiles/` structure and run `build_tiles.py`
3. **ECMWF errors**: Ensure Herbie is configured correctly in `ecmwf.py`
4. **API 401**: Set `X-API-Key` header in production

### Debug Commands

```bash
# Check GRIB file contents
python scripts/debug_grib.py cache/gribs/hrrr/run_xxx/t2m/grib_01.grib2

# Verify tile coverage
python build_tiles.py --region ne --model hrrr --max-hours 6 --dry-run

# Check scheduler status
cat cache/scheduler_status.json
```

## iOS App

Located in `ios/RadarCheck/`:
- SwiftUI-based companion app
- Uses `/api/table/bylatlon` endpoint
- Debug: `http://localhost:5001`, Production: configurable

## Related Files

- `AGENTS.md` - General AI agent instructions (references this file)
- `GEMINI.md` - Gemini-specific instructions
- `CODEX.md` - Codex-specific instructions
- `CONTRIBUTING.md` - Contribution guidelines
