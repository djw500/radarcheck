# Specification: Tile Builder Logic Audit & Optimization

## Overview
Audit and refactor the tile building pipeline (`build_tiles.py` and `cache_builder.py`) to eliminate redundant GRIB downloads and tile reprocessing. The goal is to ensure the system is "cache-first" and only performs heavy I/O or computation when data is truly missing or metadata mismatch is detected.

## Goals
1.  **Eliminate Redundancy:** Stop downloading GRIBs and rebuilding `.npz` files for data already correctly cached.
2.  **Robust Validation:** Ensure cached data is valid and matches the current configuration (Geometry, Resolution, Units) before reuse.
3.  **Performance Visibility:** Add detailed logs and profiling to measure build efficiency and identify bottlenecks.

## Functional Requirements
1.  **Pre-check Refactor:** Move the check for existing `.npz` tiles to the very beginning of the build loop, *before* initializing thread pools or checking GRIB availability.
2.  **Metadata Validation Logic:**
    -   Read the `.meta.json` file associated with each tile run.
    -   Compare `lat_min`, `lat_max`, `lon_min`, `lon_max`, and `resolution_deg` against the current `repomap["TILING_REGIONS"]` configuration.
    -   Invalidate the cache and trigger a rebuild if a mismatch is detected (e.g., after a region expansion).
3.  **GRIB Reuse Audit:** Ensure `fetch_grib` correctly uses the central `cache/gribs` directory and skips the download if a valid file exists, regardless of which "task" or "region" requested it.
4.  **Audit Logging:**
    -   Log `[SKIP]` or `[CACHE HIT]` for every hour/variable that is already complete.
    -   Log `[PROCESS]` or `[MISS]` for data being actively built.
    -   Summarize "Efficiency Ratio" at the end of each build cycle.

## Non-Functional Requirements
-   **Execution Speed:** The "Pre-check" phase must be near-instant (filesystem check only).
-   **CLI Debuggability:** Users should be able to run `build_tiles.py` with a `--dry-run` or `--audit` flag to see what *would* be built.

## Acceptance Criteria
-   Running `build_tiles.py` twice for the same run results in 0 downloads and 0 tile generations on the second pass.
-   Expanding a region in `config.py` correctly triggers a rebuild of affected tiles.
-   Logs clearly show the ratio of cached vs. processed data.

## Out of Scope
-   Automated cache cleaning (handled by tiered cleanup tasks).
-   Migrating to a formal database for cache tracking (filesystem metadata is sufficient for now).
