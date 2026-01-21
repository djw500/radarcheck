# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Radarcheck is a Flask web application that displays HRRR (High-Resolution Rapid Refresh) weather radar forecast data. It fetches GRIB2 files from NOAA's NOMADS server, generates forecast images with matplotlib/cartopy, and serves them through a web interface.

## Local Development Setup

```bash
# Create and activate virtual environment (required on macOS with Homebrew Python)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Common Commands

All commands below assume the virtual environment is activated (`source venv/bin/activate`).

```bash
# Run the Flask development server (default port 5000, use -p for alternate port)
python app.py
python app.py -p 5001  # if port 5000 is in use (e.g., AirPlay Receiver on macOS)

# Build/refresh the forecast cache (downloads HRRR data and generates images)
python cache_builder.py

# Build cache for a specific location only
python cache_builder.py --location philly

# Build only the latest model run
python cache_builder.py --latest-only

# Run all tests
pytest tests/

# Run a single test
pytest tests/test_hrrr.py::test_real_hrrr_availability -v

# Run tests with output displayed
pytest tests/test_hrrr.py -v -s
```

## Architecture

### Data Flow
1. `cache_builder.py` fetches HRRR GRIB2 data from NOAA NOMADS for configured locations
2. `plotting.py` generates PNG forecast images using matplotlib and cartopy
3. Images are stored in `cache/<location_id>/run_<date>_<hour>/` with a `latest` symlink
4. `app.py` serves these cached images through Flask endpoints

### Key Components

- **config.py**: Central configuration with `repomap` dict containing:
  - `LOCATIONS`: Geographic regions with lat/lon bounds and center points
  - `CACHE_DIR`: Where forecast data is stored
  - HRRR file naming patterns

- **cache_builder.py**: Background job that:
  - Discovers available HRRR model runs (checks last 24 hours)
  - Downloads subsetted GRIB2 files via NOMADS filter API
  - Generates 24-hour forecast frames per location
  - Manages cache cleanup (keeps last N runs per `MAX_RUNS_TO_KEEP`)

- **plotting.py**: Creates radar reflectivity plots with:
  - NWS-style colormap for dBZ values
  - County boundary overlays (from Census shapefiles)
  - Configurable center point and zoom level

- **app.py**: Flask routes including:
  - `/` - Location selection index
  - `/location/<id>` - Forecast viewer with run selector
  - `/frame/<location_id>/<run_id>/<hour>` - Individual frame images
  - `/api/*` - JSON endpoints for runs, valid times, locations
  - `/health` - Health check endpoint

### Cache Structure
```
cache/
├── <location_id>/
│   ├── latest -> run_YYYYMMDD_HH (symlink)
│   └── run_YYYYMMDD_HH/
│       ├── metadata.txt
│       ├── valid_times.txt
│       ├── frame_01.png through frame_24.png
│       └── grib_*.grib2
└── county_shapefile/
```

## Adding New Locations

Add entries to `repomap["LOCATIONS"]` in `config.py`:
```python
"location_id": {
    "name": "Display Name",
    "center_lat": 40.0,
    "center_lon": -75.0,
    "zoom": 1.5,  # degrees from center
    "lat_min": 38.5,
    "lat_max": 41.5,
    "lon_min": -77.0,
    "lon_max": -73.0
}
```

## External Dependencies

- NOAA NOMADS HRRR data: `nomads.ncep.noaa.gov`
- US Census county shapefiles: `www2.census.gov`
- Requires `cfgrib` engine for xarray (eccodes library)
