# Forecast Table Implementation Plan

## Goal
Create a simple table-based interface to display forecast values from HRRR models for a given location, showing all predicted variables across all future time steps.

## Problem
The current interface is complex with images, animations, and multiple controls. For debugging and quick data inspection, a simple table showing raw values is more useful.

## Solution Overview

### 1. forecast_table.py - Core Table Generator
A standalone script that:
- Reads cached `center_values.json` files from the cache directory
- Aggregates data across all available variables for a location/model/run
- Outputs a formatted table (terminal, HTML, or JSON)

**Usage:**
```bash
# Terminal table output
python forecast_table.py --location philly

# HTML output
python forecast_table.py --location philly --format html --output table.html

# JSON output
python forecast_table.py --location philly --format json
```

### 2. Data Structure
The table structure:
- **Rows**: Forecast hours (1 to max_forecast_hours, e.g., 24 for HRRR)
- **Columns**: Weather variables (refc, t2m, dpt, wind_10m, gust, apcp, etc.)
- **Cells**: Value + units (e.g., "35 dBZ", "45 °F", "0.5 in")

### 3. Files to Create

| File | Purpose |
|------|---------|
| `forecast_table.py` | Main script for generating tables |
| `tests/test_forecast_table.py` | Programmatic integrity tests |

### 4. Implementation Steps

**Step 1: Create forecast_table.py**
- Function `load_all_center_values(cache_dir, location_id, model_id, run_id)`
  - Scans variable subdirectories for center_values.json files
  - Returns dict: `{variable_id: {hour: value, ...}, ...}`
- Function `build_table(values_dict, variables_config)`
  - Creates tabular structure with proper headers
- Function `format_table_terminal(table)` - Pretty print for CLI
- Function `format_table_html(table)` - HTML table output
- Function `format_table_json(table)` - JSON output
- CLI argument parsing for location, model, run, format, output

**Step 2: Create integrity tests**
- Test that table has correct number of rows (forecast hours)
- Test that table has correct number of columns (variables)
- Test that values are properly formatted with units
- Test that missing data is handled gracefully
- Test HTML/JSON output structure validity

**Step 3: Add Flask route (optional)**
- `/table/<location_id>` - HTML table view
- `/api/table/<location_id>` - JSON table data

## Key Functions

```python
def load_all_center_values(cache_dir: str, location_id: str,
                           model_id: str = "hrrr",
                           run_id: str = None) -> dict:
    """Load center values for all available variables."""

def build_forecast_table(values: dict,
                         variables_config: dict) -> list[dict]:
    """Build table structure: [{hour, valid_time, var1, var2, ...}, ...]"""

def format_value(value: float, variable_config: dict) -> str:
    """Format value with units: '35.2 dBZ'"""
```

## Testing Strategy

1. **Unit tests** - Test individual functions with mock data
2. **Integration tests** - Test with real cache structure (if available)
3. **Validation tests** - Verify table structure matches expectations

## Dependencies
- Uses existing `config.py` for WEATHER_VARIABLES definitions
- Reads existing `center_values.json` files from cache
- No new external dependencies required

## Implemented Features

### Flask Integration
- `/table/<location_id>` - HTML table view with model/run selectors
- `/table/<location_id>/<model_id>` - Table for specific model
- `/table/<location_id>/<model_id>/<run_id>` - Table for specific run
- `/api/table/<location_id>` - JSON API endpoint

### Template
- `templates/table.html` - Responsive table with sticky headers

---

## Additional Models for Extended Forecasts

### Currently Supported (in config.py)

| Model | Max Hours | Update | Resolution | Source |
|-------|-----------|--------|------------|--------|
| HRRR | 24h | Hourly | 3km | NOMADS |
| NAM 3km | 60h | 6-hourly | 3km | NOMADS |
| NAM 12km | 84h | 6-hourly | 12km | NOMADS |
| RAP | 21h | Hourly | 13km | NOMADS |
| GFS | 384h (16 days) | 6-hourly | 25km | NOMADS |

### Total Accumulated Precipitation/Snowfall

These variables accumulate from model initialization time:

- **APCP** (Accumulated Precipitation): Total liquid-equivalent precip since init
  - Final hour value = total forecast precipitation
  - Hourly rate = APCP[hour] - APCP[hour-1]

- **ASNOW** (Accumulated Snowfall): Total snow accumulation since init
  - Final hour value = total forecast snowfall
  - Convert: `inches = kg_m2 * 0.0393701`

**To get 24-hour totals**: Read the final forecast hour's APCP/ASNOW value

### Models NOT Yet Integrated

#### ECMWF (European Centre)
- **Resolution**: 9km (HRES) / 18km (ENS)
- **Forecast Range**: 10-15 days
- **Access**: Requires ECMWF account (free for research)
- **API**: `data.ecmwf.int` or Copernicus CDS
- **Variables**: All standard + many derived products
- **Note**: Generally considered most accurate global model

**Integration effort**: HIGH - requires separate account, different API

#### EPS (Ensemble Prediction Systems)

1. **GEFS (Global Ensemble Forecast System)**
   - NOAA's ensemble system
   - 31 members (1 control + 30 perturbed)
   - Available on NOMADS: `nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p25s.pl`
   - Useful for uncertainty quantification
   - Variables: Same as GFS

2. **ECMWF ENS**
   - 51 members
   - Higher quality but requires account
   - Available via Copernicus CDS

**Integration effort**: MEDIUM for GEFS, HIGH for ECMWF ENS

### Recommended Priority for Long-Range Forecasts

1. **GFS** (already configured) - Use for 3-16 day outlooks
   - Access: `/api/table/<location>/gfs/<run_id>`
   - Best free option for extended forecasts

2. **GEFS ensemble** - For uncertainty/probability
   - Shows range of possible outcomes
   - "Spaghetti plots" for storm tracks

3. **ECMWF HRES** - For most accurate 1-10 day forecasts
   - Requires account setup
   - Worth it for serious meteorological use

### Example: Getting 7-Day Precipitation Total

```bash
# Using GFS (already supported)
python forecast_table.py --location philly --model gfs

# The APCP column at hour 168 (7 days) shows total accumulation
```

### To Add ECMWF Support (Future Work)

1. Register at `cds.climate.copernicus.eu`
2. Install `cdsapi` package
3. Create new fetcher module `ecmwf_fetcher.py`
4. Add ECMWF config to `config.py`:
   ```python
   "ecmwf": {
       "name": "ECMWF HRES",
       "max_forecast_hours": 240,
       "update_frequency_hours": 12,
       "source": "copernicus_cds",
       "requires_auth": True,
   }
   ```

---

## Future Enhancements (Out of Scope for Now)
- Multi-model comparison table (side-by-side HRRR vs GFS vs NAM)
- Multi-run time series (track forecast evolution)
- Interactive filtering by variable category
- ECMWF integration
- GEFS ensemble spread visualization

## Related Worklogs

- Cache builder triage and remediation notes (2026-01-20):
  - docs/worklog/cache-builder-triage-2026-01-20.md
