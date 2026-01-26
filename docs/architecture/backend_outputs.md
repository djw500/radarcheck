# Backend Outputs & System Definition

## 1. Core Philosophy: Output-Driven Design
This document defines the expected behavior and artifacts of the backend system. We work backwards from these definitions to implement the logic.

## 2. Dream Dashboard (Status UI)
The `/status` page should be the single pane of glass for system health.

### 2.1 Job Queue Section (New)
*Goal: Visualize the pulse of the background workers.*
- **Summary Metrics:**
    - `Active Workers`: [Number] / [Total Configured]
    - `Queue Depth`: [Number of pending jobs]
    - `Failure Rate (1h)`: [%]
- **Job Table (Recent/Active):**
    - Columns: `ID`, `Type`, `Args` (e.g., model=HRRR, run=12z), `Status` (Running, Pending, Failed, Success), `Duration`, `Worker ID`.
    - Action: `Retry` button for failed jobs.

### 2.2 Data Inventory Section (Enhanced)
*Goal: Precise understanding of data gaps.*
- **Run Matrix:**
    - For each Model/Run:
        - `Completeness`: [X]/[Y] hours.
        - `Missing Hours`: List specific hours (e.g., "h04, h05 missing").
        - `Source Status`: (e.g., "NOAA has 48h, we have 40h").
- **Asset Verification:**
    - Tiles vs GRIBs: "We have the GRIB but failed to generate tiles" indicator.

### 2.3 System Resources
*Goal: Prevent resource exhaustion.*
- **Disk Usage:** Broken down by Model/Run (to spot runaways).
- **Cache Age:** Oldest file vs Newest file.

## 3. API Response Schemas
### 3.1 Status Endpoint: `/api/status/deep`
```json
{
  "queue": {
    "pending": 5,
    "active": 2,
    "failed_1h": 0,
    "workers_online": 4
  },
  "inventory": [
    {
      "model": "hrrr",
      "run": "20230125_12",
      "status": "partial",
      "missing_hours": [4, 5],
      "reason": "GRIB download failed" 
    }
  ],
  "alerts": []
}
```

## 4. Physical Artifacts (Filesystem)
### 4.1 Tiles (`cache/tiles/`)
Structure: `{region}/{res}deg/{model}/{run}/{variable}.npz`
- **Metadata**: Sidecar JSON `{variable}.meta.json` containing:
    - `source_grib_md5`: For change detection.
    - `generated_at`: Timestamp.
    - `stats`: Min/Max/Mean of the tile data.

### 4.2 GRIBs (`cache/gribs/`)
Structure: `{model}/{run}/{variable}/grib_{hour}.grib2`
- **Retention**: Deleted after tile generation (unless configured to keep).

## 5. Job Definitions (Task Types)
The queue will support these atomic tasks:
1.  `ScanSource`: Check NOAA/NOMADS for new runs.
2.  `IngestGrib`: Download a specific GRIB file.
3.  `BuildTile`: Generate NPZ from GRIB.
4.  `PruneCache`: Remove old runs/files.
