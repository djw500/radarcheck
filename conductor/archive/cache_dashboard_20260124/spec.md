# Specification: Cache & Scheduler Dashboard

## Overview
Create a dedicated dashboard page reachable from the main site to monitor the health and status of the weather data cache and the background scheduler. This provides visibility into which model runs are available, how much disk space is being used, and when the next updates are expected.

## Functional Requirements
1.  **New Route:** Implement a new Flask route `/status` (accessible to all, as per user preference).
2.  **Run Status Grid:**
    -   Display a matrix of model runs (Date/Hour) vs. Models (HRRR, NAM, GFS, NBM, ECMWF).
    -   Color-code status: Fully Cached (Green), Partial (Yellow), Missing/Pending (Gray).
    -   Show at least the last 48 hours of expected runs.
3.  **Disk Usage Metrics:**
    -   Calculate and display total disk usage of the `cache/` directory.
    -   Break down usage by `gribs/` vs `tiles/` and by individual model.
4.  **Scheduler Monitoring:**
    -   Display the "Last Successful Build Cycle" timestamp.
    -   Display "Next Expected Build Cycle" (calculated based on `TILE_BUILD_INTERVAL_MINUTES`).
    -   **Log Viewer:**
        -   Show a summary of the most recent errors.
        -   Provide a "View Full Logs" interface (e.g., a collapsible section or separate sub-page `/status/logs`) to read `logs/scheduler_detailed.log` and `logs/app.log`.
5.  **Navigation:** Add a "System Status" link to the footer or header of `index.html`.

## Non-Functional Requirements
-   **Performance:** The disk usage calculation should be efficient (possibly cached for a few minutes) to avoid heavy I/O on every page load.
-   **Mobile-First:** The run matrix should scroll or stack gracefully on mobile devices.
-   **Style:** Match the existing "Forecast Analysis" dark theme and Tailwind UI.
-   **CLI Debuggability:** All data endpoints (e.g., status summary, logs) MUST be easily queryable via `curl` from the CLI to facilitate debugging without a browser.

## Acceptance Criteria
-   The dashboard is reachable via a link on the main site.
-   Users can see exactly which hours are missing for ECMWF vs GFS.
-   Disk usage matches the actual filesystem state.
-   Users can drill down into the full scheduler log to diagnose failures.

## Out of Scope
-   Interactive cache management (deleting or manually triggering builds) is deferred to a future "Admin Actions" track.
-   Real-time push updates via WebSockets; manual refresh or meta-refresh is sufficient.
