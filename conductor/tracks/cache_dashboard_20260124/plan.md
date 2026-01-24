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
- [ ] Task: Create Dashboard Template.
    - [ ] Write Tests: Add Jest tests in `tests/js/statusView.test.js` for data rendering logic.
    - [ ] Implement: Create `templates/status.html` with Tailwind CSS, including the Run Status Grid and Disk Usage charts (if applicable) or cards.
- [ ] Task: Implement Log Deep-Dive View.
    - [ ] Implement: Add a searchable/filterable log viewer component to `status.html` or a separate `templates/logs.html`.
- [ ] Task: Conductor - User Manual Verification 'Frontend Dashboard' (Protocol in workflow.md)

## Phase 4: Final Integration & Navigation
- [ ] Task: Add Navigation Links.
    - [ ] Implement: Add "System Status" link to the footer of `templates/index.html`.
- [ ] Task: End-to-End Verification.
    - [ ] Sub-task: Verify dashboard correctly identifies a manually triggered partial run.
    - [ ] Sub-task: Confirm all status data is accessible via `curl` for headless monitoring.
- [ ] Task: Conductor - User Manual Verification 'Final Integration & Navigation' (Protocol in workflow.md)
