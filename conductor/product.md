# Product Definition

## Vision
Transform Radarcheck from a location-based PNG image viewer into a **tile-based multi-model forecast table** that enables users to compare predictions from multiple weather models side-by-side, track forecast evolution, and receive AI-driven summaries.

## Core Value Proposition
- **Multi-Model Comparison:** View forecasts from HRRR, NAM, and GFS side-by-side to identify consensus or divergence.
- **Historical Context:** Track how a forecast has changed over previous model runs to gauge consistency.
- **Performance:** deliver fast, mobile-friendly tabular data without the bandwidth cost of heavy map layers.
- **AI Insight:** (Future) Automated summaries of forecast trends and risks.

## Target Audience
- Weather enthusiasts and professionals who need precise, raw model data.
- Users who want to answer: "GFS is saying 12 inches of snow, but keeps changing its mind. What do other models say?"

## Key Features
1.  **Multi-Model Table:** Unified view of multiple weather models.
2.  **Run Comparison:** "Show History" feature to see previous model outputs.
3.  **Tile-Based Architecture:** backend generation of statistical grids for fast querying.
4.  **AI Summaries:** Intelligent synthesis of complex model data.
5.  **System Health Dashboard:** `/status` page for monitoring cache completeness, disk usage, and scheduler status.
6.  **Optimized Build Pipeline:** Cache-first tile generation with metadata validation to eliminate redundant processing.

## Current State & Limitations
- **Active UI:** `index.html` (served at `/forecast` or `/`) is the **ONLY** active and supported frontend endpoint.
- **Defunct Endpoints:** Legacy endpoints (e.g., `/table/geo`, `/location/...` without the new React/Plotly UI) are deprecated and should be ignored or removed.
- **Data Source:** ECMWF integration via Herbie is active for the "ne" region.
