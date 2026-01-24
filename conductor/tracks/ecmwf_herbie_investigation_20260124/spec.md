# Specification: ECMWF Support via Herbie

## Context
The current app supports HRRR, NAM, and GFS. The user wants to add ECMWF support to the forecast table. The user also wants to investigate "Herbie" (a python tool for downloading weather model data) to see if it simplifies the data pipeline while keeping the app lightweight and fast.

## Goals
1.  **Research Herbie:** Determine if Herbie is a viable replacement or addition to the current `requests`-based GRIB fetching logic in `build_tiles.py` and `ecmwf.py`.
    -   *Constraint:* Must support partial file downloading (byte range requests) if possible to keep "lightweight".
    -   *Constraint:* Must allow specific variable selection (t2m, apcp, etc.).
2.  **Implement ECMWF:** Add ECMWF (IFS) model support to the backend.
    -   *Source:* ECMWF open data (likely via Herbie or direct AWS/Azure bucket access).
    -   *Output:* Generate NPZ tiles for ECMWF matching the existing schema.
3.  **Performance:** Ensure the new dependency or data source does not degrade tile build times or app startup.

## Requirements
-   **Herbie Evaluation:**
    -   Can it download ECMWF open data?
    -   Does it support filtering by variable/level (idx files)?
    -   Is it heavy? (Dependency tree check).
-   **Integration:**
    -   If Herbie is good: Refactor a small part of `build_tiles.py` or create a new `herbie_fetcher.py` to test it.
    -   If Herbie is heavy/slow: Stick to custom `requests` logic but implement ECMWF specific URL patterns.
-   **Frontend:**
    -   Add ECMWF to the model selector in `index.html` (or the new table view).

## Out of Scope
-   Replacing the entire GRIB processing pipeline (cfgrib/xarray) unless Herbie mandates it.
-   Full historical archive access (focus on real-time operational forecast).
