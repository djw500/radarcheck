# Radarcheck Architecture

## System Overview

Radarcheck fetches weather forecast data from NOAA NOMADS, processes it into statistical tiles, and serves it through a Flask web application.

```
NOMADS (HRRR, NAM Nest, GFS, NBM)
        │
        ▼
   scheduler (enqueues jobs to SQLite)
        │
        ▼
   job_worker.py
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
   Flask app
        │
        ├──► / (forecast UI)
        ├──► /api/timeseries/multirun (JSON)
        └──► /status (dashboard)
```

## Key Components

### Data Layer

| File | Purpose |
|------|---------|
| `tiles.py` | Tile generation logic, point queries |
| `config.py` | Model definitions, variable configs, regions |
| `jobs.py` | SQLite job queue |
| `job_worker.py` | Background worker that processes queued jobs |

### Web Layer

| File | Purpose |
|------|---------|
| `app.py` | Flask app factory, global auth, index/health/metrics |
| `routes/forecast.py` | `/api/timeseries/multirun` + snow derivation helpers |
| `routes/status.py` | `/status` dashboard + `/api/status/*` + `/api/jobs/*` |
| `templates/` | Jinja2 HTML templates |
| `static/` | JavaScript, CSS |

### Cache Structure

```
cache/
├── gribs/                    # Raw GRIB files (CONUS extent)
│   └── <model>/<run>/<var>/grib_XX.grib2
├── tiles/                    # Statistical tiles (regional)
│   └── <region>/<resolution>/<model>/<run>/<var>.npz
├── jobs.db                   # Job queue (SQLite)
└── tiles.db                  # Tile metadata (SQLite)
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
- **Models**: HRRR (48h), NAM Nest (60h), GFS (168h), NBM (168h), ECMWF HRES (disabled)
- **Built variables**: apcp, prate, asnow, csnow, snod, t2m
- **Tiling region**: `ne` (Northeast US)

## Deployment

- **Platform**: Fly.io (ewr region)
- **Container**: Python 3.11-slim + eccodes
- **Storage**: 1GB persistent volume at `/app/cache`
- **Processes**: gunicorn (web) + scheduler + worker via supervisord
- **CI/CD**: Push to main → fly deploy
