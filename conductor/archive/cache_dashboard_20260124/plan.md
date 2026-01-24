# Implementation Plan - Cache & Scheduler Dashboard

## Phase 1: Backend Data Collection & Utilities
- [x] Task: Implement Cache Discovery Logic. a181c05
    - [x] Write Tests: Create `tests/test_status_utils.py` to verify logic for identifying missing/partial runs.
    - [x] Implement: Create a utility in `utils.py` (or a new `status_utils.py`) to scan `cache/tiles` and return a structured run matrix.
- [x] Task: Implement Disk Usage Calculation. a181c05
    - [x] Write Tests: Verify disk usage calculation with mock directories in `tests/test_status_utils.py`.
    - [x] Implement: Add a function to calculate size of `cache/gribs` and `cache/tiles` with per-model breakdown.
- [x] Task: Implement Log Reading Utility. a181c05
    - [x] Write Tests: Verify tail-like log reading logic.
    - [x] Implement: Add a helper to read the last N lines of `logs/scheduler_detailed.log`.
- [x] Task: Conductor - User Manual Verification 'Backend Data Collection & Utilities' (Protocol in workflow.md) a181c05

## Phase 2: API Endpoints
- [x] Task: Create Status API Endpoints. ea0db13
    - [x] Write Tests: Create `tests/test_status_api.py` to verify `/api/status/summary` and `/api/status/logs`.
    - [x] Implement: Add routes to `app.py` that serve the data collected in Phase 1.
    - [x] Verification: Verify endpoints return valid JSON using `curl` against the local dev server.
- [x] Task: Conductor - User Manual Verification 'API Endpoints' (Protocol in workflow.md) ea0db13

## Phase 3: Frontend Dashboard
- [x] Task: Create Dashboard Template. ddda967
    - [x] Write Tests: Add Jest tests in `tests/js/statusView.test.js` for data rendering logic.
    - [x] Implement: Create `templates/status.html` with Tailwind CSS, including the Run Status Grid and Disk Usage charts (if applicable) or cards.
- [x] Task: Implement Log Deep-Dive View. ddda967
    - [x] Implement: Add a searchable/filterable log viewer component to `status.html` or a separate `templates/logs.html`.
- [x] Task: Conductor - User Manual Verification 'Frontend Dashboard' (Protocol in workflow.md) ddda967

## Phase 4: Final Integration & Navigation
- [x] Task: Add Navigation Links. 45c8e88
    - [x] Implement: Add "System Status" link to the footer of `templates/index.html`.
- [x] Task: End-to-End Verification. 45c8e88
    - [x] Sub-task: Verify dashboard correctly identifies a manually triggered partial run.
    - [x] Sub-task: Confirm all status data is accessible via `curl` for headless monitoring.
- [x] Task: Conductor - User Manual Verification 'Final Integration & Navigation' (Protocol in workflow.md) 45c8e88

## Phase 5: Refinement (UX & Verification)
- [x] Task: Unify UX Style. 8ba70cc
    - [x] Implement: Update `templates/status.html` to match `templates/index.html` structure (nav bar, fonts, colors).
- [x] Task: Fix Data Loading. 8ba70cc
    - [x] Investigate: Debug why data loading might be failing (browser console logs simulation/review logic).
    - [x] Fix: Update `static/js/statusView.js` to ensure correct API calls and error handling.
- [x] Task: Enable CLI Verification. 8ba70cc
    - [x] Verification: Ensure `curl http://localhost:5001/status` returns full HTML with expected structure (not just empty div placeholders).
- [x] Task: Scheduler Status Reporting. 8ba70cc
    - [x] Implement: Update `scripts/build_tiles_scheduled.py` to write a status file (e.g., `cache/scheduler_status.json`) with last run time, next run time, last error, and list of target runs/slots being monitored.
    - [x] Implement: Update `api_status_summary` in `app.py` to read this status file.
- [x] Task: Conductor - User Manual Verification 'Refinement' (Protocol in workflow.md) 8ba70cc
