# Radarcheck Architecture

## System Overview

Radarcheck fetches weather forecast data from NOAA NOMADS, processes it into statistical tiles, and serves it through a Flask web application.

```
NOMADS (HRRR, NAM, GFS)
        │
        ▼
   build_tiles.py
        │
        ▼
   GRIB files ──────────────────► cache/gribs/<model>/<run>/<var>/
        │
        ▼
   tiles.py (stats extraction)
        │
        ▼
   NPZ tile files ──────────────► cache/tiles/<region>/<res>/<model>/<run>/<var>.npz
        │
        ▼
   Flask app (app.py)
        │
        ├──► /forecast (HTML table)
        ├──► /api/forecast (JSON)
        └──► /api/tile_* (tile discovery)
```

## Key Components

### Data Layer

| File | Purpose |
|------|---------|
| `build_tiles.py` | CLI tool to fetch GRIBs and generate tile statistics |
| `tiles.py` | Tile generation logic, point queries, grid slicing |
| `config.py` | Model definitions, variable configs, regions |

### Web Layer

| File | Purpose |
|------|---------|
| `app.py` | Flask routes, API endpoints |
| `templates/` | Jinja2 HTML templates |
| `static/` | JavaScript, CSS |

### Cache Structure

```
cache/
├── gribs/                    # Raw GRIB files (CONUS extent)
│   └── <model>/<run>/<var>/grib_XX.grib2
├── tiles/                    # Statistical tiles (regional)
│   └── <region>/<resolution>/<model>/<run>/<var>.npz
└── county_shapefile/         # US county boundaries
```

## Tile System

Each tile NPZ file contains:
- `mins`: Minimum value per cell per hour
- `maxs`: Maximum value per cell per hour
- `means`: Mean value per cell per hour
- `hours`: Array of forecast hours
- `meta`: JSON metadata (region, resolution, units, etc.)

Default resolution: **0.1° (~10km cells)**

## Models and Variables

See `config.py` for full definitions:
- **MODELS**: HRRR, NAM Nest, NAM 12km, GFS, RAP, ECMWF (scaffolded)
- **WEATHER_VARIABLES**: 15+ variables across precipitation, temperature, wind, severe categories
- **TILING_REGIONS**: Currently only "ne" (Northeast US)

## Deployment

- **Platform**: Fly.io (ewr region)
- **Container**: Python 3.11-slim + eccodes
- **Storage**: 1GB persistent volume at `/app/cache`
- **Process**: gunicorn serving Flask app
- **CI/CD**: GitHub Actions → fly deploy on push to main

See `docs/operations/flyio-guide.md` for deployment details.
