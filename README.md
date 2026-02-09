# radarcheck

Radarcheck is a weather forecast visualization app that fetches GRIB2 data from NOAA NOMADS, generates statistical tiles, and serves forecast tables through a Flask web interface.

## Getting Started

1.  Create a virtual environment:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

2.  Run the web server:
    ```bash
    python app.py
    ```
    The server listens on `http://localhost:5000` (or the PORT env var).

## Worker & Scheduler

The application relies on a background worker and scheduler to fetch data and build tiles.

-   **Scheduler:** Adds jobs to the queue based on model schedules.
    ```bash
    ./scripts/run_scheduler.sh
    ```

-   **Worker:** Processes jobs from the queue.
    ```bash
    ./scripts/run_worker.sh
    ```

## E2E Testing with Fake Data

To test the entire pipeline without downloading large GRIB files from NOAA, use the `RADARCHECK_FAKE_DATA` environment variable.

1.  Enable fake data mode:
    ```bash
    export RADARCHECK_FAKE_DATA=1
    ```

2.  Run the scheduler (it will "fetch" fake data):
    ```bash
    ./scripts/run_scheduler.sh --once
    ```

3.  Run the worker (it will process the fake data):
    ```bash
    ./scripts/run_worker.sh --once
    ```

This will copy the local `debug_gfs.grib2` instead of downloading files, allowing you to verify the tile generation and serving pipeline.
