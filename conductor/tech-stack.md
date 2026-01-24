# Technology Stack

## Core
- **Backend:** Python 3 (Flask)
- **Frontend:** HTML5, CSS3, Vanilla JavaScript (with Jest for testing)

## Data Engineering
- **Processing:** NumPy, xarray, cfgrib, pandas, geopandas
- **Geospatial:** Rasterio, Shapely
- **Source:** NOAA NOMADS (GRIB2 format), ECMWF Open Data (via Herbie)

## Infrastructure
- **Virtual Environment:** `.venv` (already configured and required for development)
- **Server:** Gunicorn behind Nginx (via Fly.io default)
- **Background Tasks:** Supervisor (managing `build_tiles.py`)
- **Build Audit:** Metadata-aware pre-checks and dry-run monitoring in `build_tiles.py`
- **Deployment:** Docker on Fly.io
- **Caching:** File-based (GRIBs and NPZ tiles)

## Testing
- **Python:** pytest
- **JavaScript:** Jest
