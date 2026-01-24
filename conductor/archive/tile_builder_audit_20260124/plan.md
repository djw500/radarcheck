# Implementation Plan - Tile Builder Logic Audit & Optimization

## Phase 1: Audit & Profiling [checkpoint: 92c7b51]
- [x] Task: Baseline Profiling. 92c7b51
    - [x] Implement: Create a temporary profiling script or add timing decorators to `build_tiles.py` to measure time spent in downloads vs. tiling vs. indexing.
    - [x] Verification: Run `build_tiles.py` for a known run and record the time distribution.
- [x] Task: Add Audit Logging. 92c7b51
    - [x] Write Tests: Create `tests/test_audit_logs.py` to ensure log messages correctly identify [SKIP] vs [PROCESS] states.
    - [x] Implement: Update `build_tiles.py` and `cache_builder.py` with high-visibility audit logs for GRIB and Tile cache hits.
- [x] Task: Conductor - User Manual Verification 'Audit & Profiling' (Protocol in workflow.md) 92c7b51

## Phase 2: Metadata-Aware Pre-checks [checkpoint: 92c7b51]
- [x] Task: Implement Metadata Validation Utility. 92c7b51
    - [x] Write Tests: Create `tests/test_metadata_validation.py` to verify logic for comparing config bounds against `.meta.json`.
    - [x] Implement: Add `is_tile_valid(meta_path, region_id)` utility to `tiles.py` or `status_utils.py`.
- [x] Task: Refactor Build Loop for "Tile-First" Check. 92c7b51
    - [x] Write Tests: Update `tests/test_tiles_build.py` to verify that `build_region_tiles` exits early if valid tiles exist.
    - [x] Implement: Modify `build_region_tiles` in `build_tiles.py` to perform metadata validation *before* calling `download_all_hours_parallel`.
- [x] Task: Conductor - User Manual Verification 'Metadata-Aware Pre-checks' (Protocol in workflow.md) 92c7b51

## Phase 3: GRIB Reuse Optimization [checkpoint: 92c7b51]
- [x] Task: Audit `fetch_grib` for Redundancy. 92c7b51
    - [x] Implement: Ensure `fetch_grib` performs a robust file size and validity check on existing GRIBs before any network request or `herbie` initialization.
    - [x] Verification: Run `curl` or manual CLI builds to confirm identical GRIBs are never re-downloaded.
- [x] Task: Conductor - User Manual Verification 'GRIB Reuse Optimization' (Protocol in workflow.md) 92c7b51

## Phase 4: Final Integration & Dry Run Mode [checkpoint: 92c7b51]
- [x] Task: Implement Audit Summary. 92c7b51
    - [x] Implement: Add a summary table at the end of `build_tiles.py` execution showing "Efficiency Ratio" (Cache Hits / Total Requests).
- [x] Task: Add `--audit` (Dry Run) Flag. 92c7b51
    - [x] Implement: Add `--audit` flag to `build_tiles.py` that performs all checks but skips actual downloads and tiling.
    - [x] Verification: Confirm `python build_tiles.py --audit` correctly reports what *would* happen.
- [x] Task: Conductor - User Manual Verification 'Final Integration & Navigation' (Protocol in workflow.md) 92c7b51
