# Implementation Plan - ECMWF & Herbie Investigation

## Phase 1: Research & Prototyping
- [x] Task: Research Herbie documentation and dependencies. 60102ac
    - [x] Sub-task: Check Herbie's support for ECMWF Open Data.
    - [x] Sub-task: Analyze Herbie's dependency weight (is it too heavy for a lightweight app?).
    - [x] Sub-task: Create a small script `scripts/test_herbie_ecmwf.py` to attempt a download of a single field (e.g., 2m temp).
- [x] Task: Evaluate Feasibility. 60102ac
    - [x] Sub-task: Compare `scripts/test_herbie_ecmwf.py` performance vs existing `build_tiles.py` method.
    - [x] Sub-task: Decision point: Use Herbie or direct HTTP for ECMWF?
- [x] Task: Conductor - User Manual Verification 'Research & Prototyping' (Protocol in workflow.md) 60102ac

## Phase 2: Implementation (Backend)
- [~] Task: Implement ECMWF Data Fetching.
    - [ ] Sub-task: Create/Update `ecmwf.py` with the chosen method (Herbie or Request-based).
    - [ ] Sub-task: Write tests for `ecmwf.py` to ensure it correctly identifies latest runs and URLs.
- [ ] Task: Integrate into Tile Builder.
    - [ ] Sub-task: Update `build_tiles.py` (or `config.py`) to enable ECMWF model processing.
    - [ ] Sub-task: Verify `grib2` to `npz` conversion works for ECMWF grids (projection handling).
    - [ ] Sub-task: Write test `tests/test_ecmwf_integration.py` to verify end-to-end tile creation.
- [ ] Task: Conductor - User Manual Verification 'Implementation (Backend)' (Protocol in workflow.md)

## Phase 3: Frontend & Deployment
- [ ] Task: Update Frontend.
    - [ ] Sub-task: Update `config.py` to expose ECMWF in `MODELS` list.
    - [ ] Sub-task: Verify `index.html` / `table.html` displays ECMWF column/option.
- [ ] Task: Deployment Prep.
    - [ ] Sub-task: Update `requirements.txt` (if Herbie is used).
    - [ ] Sub-task: Run full test suite.
- [ ] Task: Conductor - User Manual Verification 'Frontend & Deployment' (Protocol in workflow.md)
