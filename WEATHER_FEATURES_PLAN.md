# Weather Features Enhancement Plan for Radarcheck

## Executive Summary

This document outlines a comprehensive plan to expand radarcheck from a single-variable (radar reflectivity) viewer into a multi-variable weather visualization platform. The plan includes adding snow accumulation, wind data, temperature, and other weather parameters, plus cumulative calculations and alternative data sources.

---

## Current State Analysis

### What We Have Now
- **Single Variable**: Composite Reflectivity (REFC) only
- **Data Source**: NOAA NOMADS HRRR model
- **Temporal Resolution**: Hourly forecasts, 24 hours ahead
- **Spatial Resolution**: 3km (HRRR native)
- **Update Frequency**: Hourly model runs
- **Geography**: Configurable regions (currently Philly, NYC, Denver)

### Technical Architecture
- Flask web app serving cached forecast images
- GRIB2 data fetching via NOMADS filter API
- matplotlib/cartopy for visualization
- File-based cache with symlinks for latest runs
- xarray/cfgrib for GRIB processing

---

## Phase 1: Multi-Variable Support (Core Infrastructure)

### 1.1 Architecture Changes

**Goal**: Refactor to support multiple weather variables with different visualizations

**Key Changes:**

1. **Config System** (`config.py`)
   - Replace `HRRR_VARS = "var_REFC=on&"` with structured dict:
   ```python
   WEATHER_VARIABLES = {
       "refc": {
           "nomads_param": "var_REFC",
           "display_name": "Radar Reflectivity",
           "units": "dBZ",
           "short_name": "refc",
           "colormap": "nws_reflectivity",
           "vmin": 5, "vmax": 75,
           "category": "precipitation"
       },
       "asnow": {
           "nomads_param": "var_ASNOW",
           "display_name": "Accumulated Snowfall",
           "units": "kg/m²",  # or inches after conversion
           "short_name": "asnow",
           "colormap": "snow_accumulation",
           "vmin": 0, "vmax": 50,
           "category": "winter",
           "is_accumulation": True
       },
       # ... more variables
   }
   ```

2. **Cache Structure** - Add variable dimension:
   ```
   cache/
   └── philly/
       └── run_20260118_12/
           ├── metadata.txt
           ├── refc/              # NEW: per-variable dirs
           │   ├── frame_01.png
           │   └── grib_01.grib2
           ├── asnow/
           │   └── ...
           └── wind/
               └── ...
   ```

3. **Data Fetching** (`cache_builder.py`)
   - Modify `fetch_grib()` to download multiple variables
   - Store variables in separate GRIB files or combined files
   - Build NOMADS URL with multiple var parameters: `var_REFC=on&var_ASNOW=on&var_TMP=on&...`

4. **Plotting System** (`plotting.py`)
   - Add `create_plot(variable_config, ...)` parameter
   - Implement variable-specific colormaps
   - Add colorbar configuration per variable type
   - Support different value ranges and scales

5. **Web Interface** (`app.py`)
   - Add variable selector to UI
   - New routes: `/frame/<location>/<run>/<variable>/<hour>`
   - API endpoint: `/api/variables/<location>/<run>` - list available variables

### 1.2 Variable-Specific Colormaps

Create matplotlib colormaps for each variable type:

- **Snow Accumulation**: White → Light Blue → Blue → Purple (0-24+ inches)
- **Wind Speed**: Green → Yellow → Orange → Red → Magenta (0-100+ mph)
- **Temperature**: Blue → Cyan → Green → Yellow → Orange → Red (-40°F to 120°F)
- **Precipitation Rate**: Light Blue → Green → Yellow → Red (0-3+ in/hr)

---

## Phase 2: Priority Weather Variables

### 2.1 Snow & Winter Weather

**Variables to Add:**

1. **ASNOW - Accumulated Snowfall** ⭐ HIGH PRIORITY
   - NOMADS param: `var_ASNOW`
   - GRIB shortName: `asnow`
   - Units: kg/m² (convert to inches: `inches = kg_m2 * 0.0393701`)
   - Display: Cumulative snowfall since model initialization
   - Use case: "How much snow will fall in the next 24 hours?"

2. **SNOD - Snow Depth** ⭐ HIGH PRIORITY
   - NOMADS param: `var_SNOD`
   - GRIB shortName: `snod`
   - Units: meters (convert to inches)
   - Display: Total snow depth on ground
   - Use case: "How deep is the snow pack?"

3. **CSNOW - Categorical Snow**
   - NOMADS param: `var_CSNOW`
   - Binary indicator: Yes/No snow
   - Use case: Rain/snow line visualization

### 2.2 Wind Data

**Variables to Add:**

1. **10m Wind Speed & Direction** ⭐ HIGH PRIORITY
   - NOMADS params: `var_UGRD=on&var_VGRD=on&lev_10_m_above_ground=on`
   - Calculate: `speed = sqrt(u² + v²)`, `direction = atan2(v, u)`
   - Display options:
     - **Wind speed heatmap** with colormap
     - **Wind barbs** overlaid on map
     - **Streamlines** showing wind flow patterns
   - Units: m/s (convert to mph: `mph = m_s * 2.23694`)

2. **GUST - Wind Gusts**
   - NOMADS param: `var_GUST`
   - Show maximum expected gusts
   - Use case: Severe weather warnings

3. **80m Wind Speed** (for wind energy applications)
   - Same as 10m but at `lev_80_m_above_ground=on`

### 2.3 Temperature & Moisture

**Variables to Add:**

1. **TMP - 2m Temperature** ⭐ MEDIUM PRIORITY
   - NOMADS param: `var_TMP=on&lev_2_m_above_ground=on`
   - GRIB shortName: `t2m`
   - Units: Kelvin (convert to °F: `F = (K - 273.15) * 9/5 + 32`)
   - Use case: Temperature forecasts

2. **DPT - 2m Dew Point**
   - NOMADS param: `var_DPT=on&lev_2_m_above_ground=on`
   - Calculate apparent temperature / heat index
   - Use case: Humidity comfort

3. **RH - Relative Humidity**
   - NOMADS param: `var_RH=on`
   - Units: %
   - Use case: Comfort, fire weather

### 2.4 Precipitation

**Variables to Add:**

1. **APCP - Accumulated Precipitation** ⭐ HIGH PRIORITY
   - NOMADS param: `var_APCP`
   - Units: kg/m² (same as mm, convert to inches)
   - Display: Total liquid-equivalent precipitation
   - Use case: Rainfall totals

2. **PRATE - Precipitation Rate**
   - NOMADS param: `var_PRATE`
   - Units: kg/m²/s
   - Use case: Instantaneous rainfall intensity

3. **CRAIN - Categorical Rain**
   - Binary yes/no
   - Use case: Precipitation type discrimination

### 2.5 Severe Weather

**Variables to Add:** (Lower priority but valuable)

1. **CAPE - Convective Available Potential Energy**
   - NOMADS param: `var_CAPE`
   - Units: J/kg
   - Use case: Thunderstorm potential

2. **HLCY - Storm Relative Helicity**
   - NOMADS param: `var_HLCY`
   - Use case: Tornado potential

3. **HAIL - Hail**
   - NOMADS param: `var_HAIL`
   - Use case: Severe weather

4. **VIS - Visibility**
   - NOMADS param: `var_VIS`
   - Units: meters
   - Use case: Fog, low visibility conditions

---

## Phase 3: Cumulative Calculations

### 3.1 Total Snowfall Over Forecast Period

**Problem**: ASNOW in HRRR is the accumulated snowfall since model initialization, so each forecast hour shows cumulative total.

**Solution**: Provide multiple views:

1. **Cumulative Snow (24-hour total)**
   - Use the final forecast hour (f24) ASNOW value
   - This shows total expected snowfall from now until +24 hours
   - Display as single summary map

2. **Hourly Snowfall Rate**
   - Calculate: `hourly_rate[hour] = ASNOW[hour] - ASNOW[hour-1]`
   - Show as animation (like current radar)
   - Helps identify when snow will be heaviest

3. **Storm Total** (for multi-day events)
   - If storm spans multiple model runs, sum across runs
   - Requires tracking state between runs

**Implementation**:
```python
def calculate_cumulative_snow(location_id, run_id):
    """Calculate 24-hour total snowfall"""
    frame_24_grib = load_grib(location_id, run_id, hour=24, variable='asnow')
    snow_accumulation = frame_24_grib['asnow'].values
    # Convert kg/m² to inches
    snow_inches = snow_accumulation * 0.0393701
    return create_summary_plot(snow_inches, colormap='snow_total',
                              title="24-Hour Snowfall Total")
```

### 3.2 Total Precipitation

Same approach as snow:
- Use APCP at forecast hour 24 for total liquid accumulation
- Calculate hourly rates for animation
- Convert to inches for display

### 3.3 Wind Analysis

**Maximum Wind Gusts**:
- Track maximum wind speed across all 24 forecast hours at each grid point
- Display as "Maximum Expected Wind Gusts" map

**Sustained Winds**:
- Calculate average wind speed over 24 hours
- Useful for wind energy forecasting

---

## Phase 4: Alternative Data Sources & Multi-Model Integration

### 4.1 NAM Model Integration ⭐ HIGH PRIORITY

**NAM (North American Mesoscale Forecast System)**

The NAM model is a critical addition that complements HRRR by providing longer forecast ranges and alternative model guidance. NAM should be implemented alongside HRRR as a primary data source.

**NAM Variants Available:**

1. **NAM 3km CONUS Nest** ⭐ RECOMMENDED
   - **Resolution**: 3km (same quality as HRRR)
   - **Coverage**: Continental United States
   - **Forecast Range**: 60 hours (2.5 days vs HRRR's 18-48 hours)
   - **Update Frequency**: Every 6 hours (00z, 06z, 12z, 18z)
   - **Hourly Output**: 1-hour timesteps
   - **NOMADS Access**: `nomads.ncep.noaa.gov/cgi-bin/filter_nam_conusnest.pl`
   - **Filter Parameter**: `ds=nam_conusnest`

2. **NAM 12km CONUS**
   - **Resolution**: 12km
   - **Forecast Range**: 84 hours (3.5 days)
   - **NOMADS Access**: `nomads.ncep.noaa.gov/cgi-bin/filter_nam.pl`
   - **Use Case**: Extended forecasts beyond 60 hours

**NAM Variables Available** (similar to HRRR):
- Composite Reflectivity (REFC)
- 1km AGL Reflectivity
- Accumulated Precipitation (APCP) - 1h, 3h, 6h, 12h, 24h intervals
- Accumulated Snowfall (ASNOW)
- Snow Depth (SNOD)
- 10m Wind (UGRD, VGRD) and Wind Gusts (GUST)
- 2m Temperature (TMP) including 24h max/min
- Dew Point, Relative Humidity
- CAPE (Surface-Based, Mixed-Layer, Most Unstable)
- CIN (Convective Inhibition)
- Storm Relative Helicity (0-1km, 0-3km)
- Precipitable Water (PWAT)
- Cloud Cover (Total, Low, Mid, High)
- Simulated IR Satellite imagery
- Visibility (VIS)
- Temperature Advection (700mb, 850mb)
- Bulk Wind Shear (0-6km)
- Lapse Rates

**NAM Integration Strategy:**

1. **Multi-Model Comparison View**
   - Side-by-side HRRR vs NAM display
   - Toggle between models for same variable
   - Useful for identifying model agreement/disagreement

2. **Extended Forecasts with NAM**
   - Use HRRR for hours 1-18 (most accurate, highest frequency)
   - Switch to NAM 3km for hours 19-60 (extended range)
   - Seamless transition in UI

3. **NAM-Specific Features**
   - NAM produces better 24h max/min temperature forecasts
   - NAM's 60-hour range better for multi-day snow events
   - Access to multiple CAPE types (SB, ML, MU)

4. **Implementation Approach**
   ```python
   MODELS = {
       "hrrr": {
           "name": "HRRR",
           "resolution_km": 3,
           "max_forecast_hours": 48,
           "update_frequency_hours": 1,
           "nomads_url": "nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl",
           "grib_filter": "ds=hrrr_2d",
           "file_pattern": "hrrr.t{HH}z.wrfsfcf{FF}.grib2"
       },
       "nam_nest": {
           "name": "NAM 3km CONUS",
           "resolution_km": 3,
           "max_forecast_hours": 60,
           "update_frequency_hours": 6,
           "nomads_url": "nomads.ncep.noaa.gov/cgi-bin/filter_nam_conusnest.pl",
           "grib_filter": "ds=nam_conusnest",
           "file_pattern": "nam.t{HH}z.conusnest.hiresf{FF}.tm00.grib2"
       },
       "nam_12km": {
           "name": "NAM 12km",
           "resolution_km": 12,
           "max_forecast_hours": 84,
           "update_frequency_hours": 6,
           "nomads_url": "nomads.ncep.noaa.gov/cgi-bin/filter_nam.pl",
           "grib_filter": "ds=nam",
           "file_pattern": "nam.t{HH}z.awphys{FF}.tm00.grib2"
       }
   }
   ```

5. **Cache Structure with Multiple Models**
   ```
   cache/
   └── philly/
       ├── hrrr/
       │   └── run_20260118_12/
       │       ├── refc/
       │       ├── asnow/
       │       └── ...
       └── nam_nest/
           └── run_20260118_12/
               ├── refc/
               ├── asnow/
               └── ...
   ```

**NAM Priority in Roadmap**: Sprint 6 (originally), but should be considered for Sprint 3-4 given high value.

---

### 4.2 Other NOAA Models

**GFS (Global Forecast System)** - 0.25° resolution (~25km)
- Available on NOMADS: `nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl`
- Global coverage, 16-day forecasts
- Use case: Long-range outlook, locations outside HRRR/NAM domain
- Update frequency: 4 times daily (00z, 06z, 12z, 18z)

**RAP (Rapid Refresh)** - 13km resolution
- Available on NOMADS: `nomads.ncep.noaa.gov/cgi-bin/filter_rap.pl`
- Parent model to HRRR
- Update frequency: Hourly
- Forecast range: 21 hours
- Use case: Backup when HRRR unavailable, Alaska coverage

### 4.2 Radar Data (Real Observations)

**NEXRAD Level 3 Products** - Real-time radar observations
- Source: AWS S3 bucket `s3://noaa-nexrad-level2`
- Also available via NCEI: `www.ncei.noaa.gov/access/services/data/v1`
- Products: Base Reflectivity, Velocity, Spectrum Width
- Use case: Current conditions (vs model forecasts)
- **Integration**: Add "Observed Radar" toggle alongside HRRR forecast

**MRMS (Multi-Radar Multi-Sensor)** - Composite radar mosaics
- Source: Iowa State (Iowa Environmental Mesonet)
- Already processed/composited from multiple NEXRAD sites
- Products: Reflectivity, precipitation estimates, echo tops
- Easier to integrate than raw NEXRAD

### 4.3 Satellite Data

**GOES-16/18 Satellite Imagery**
- Source: AWS S3 `s3://noaa-goes16`, `s3://noaa-goes18`
- Products: Visible, IR, Water Vapor channels
- Resolution: 0.5-2km
- Use case: Cloud cover, storm structure
- **Integration**: Overlay satellite imagery as base layer

### 4.4 Gridded Analysis Products

**SNODAS (Snow Data Assimilation System)**
- Source: `www.nohrsc.noaa.gov/snowfall_v2/`
- Products: Snow depth, SWE (snow water equivalent)
- Resolution: 1km
- Use case: Current observed snow conditions

**NDFD (National Digital Forecast Database)**
- Source: `tgftp.nws.noaa.gov/SL.us008001/ST.opnl/`
- Products: Official NWS forecasts as grids
- Use case: Compare HRRR model to official forecast

### 4.5 International Models (Advanced)

**ECMWF (European Centre for Medium-Range Weather Forecasts)**
- Resolution: 9km
- Often more accurate than GFS
- Requires account: `data.ecmwf.int`

**Canadian GEM**
- Resolution: 10km
- Coverage: North America
- Source: `dd.weather.gc.ca`

---

## Phase 5: UI/UX Enhancements

### 5.1 Variable Selector

**Design**:
- Dropdown or tab interface for variable selection
- Categories: "Precipitation & Storms", "Winter Weather", "Temperature", "Wind", "Severe Weather"
- Persistent selection across forecast hours

### 5.2 Layer Compositing

**Allow multiple layers simultaneously**:
- Base layer: Radar reflectivity
- Overlay: Wind barbs
- Overlay: Temperature contours
- Overlay: County warnings

### 5.3 Summary Dashboard

**24-Hour Summary View**:
- Side-by-side panels showing:
  - Total snowfall
  - Maximum wind gusts
  - Temperature range
  - Total precipitation
- Downloadable summary image

### 5.4 Location-Specific Features

**Conditional variable display**:
- Winter variables (snow) only show during winter months or when relevant
- Tropical variables during hurricane season
- Fire weather during dry seasons

### 5.5 Comparison Mode ⭐ Critical for NAM Integration

**Multi-Model Comparison**:
- **HRRR vs NAM side-by-side** - Compare high-resolution models for same forecast hour
- **Model agreement highlighting** - Show where models agree/disagree on snowfall, wind, etc.
- **Ensemble-style view** - Display both models with transparency to see consensus

**Multi-Run Comparison**:
- Different model runs (00z vs 06z vs 12z)
- Track forecast consistency over time
- Identify forecast trends (is snow total increasing or decreasing?)

**Multi-Variable Comparison**:
- Different variables (snow vs rain, radar vs satellite)
- Stacked layers (temperature + wind barbs + radar)

---

## Implementation Roadmap

### Sprint 1: Multi-Variable & Multi-Model Infrastructure (2-3 weeks)
- [ ] Refactor config system for multi-variable support
- [ ] Refactor config system for multi-model support (HRRR, NAM, etc.)
- [ ] Update cache structure with variable subdirectories and model subdirectories
- [ ] Modify cache_builder to fetch multiple variables
- [ ] Update plotting.py to accept variable and model parameters
- [ ] Add variable-specific colormaps
- [ ] Update Flask routes to include variable and model parameters
- [ ] Add model selector to UI framework

### Sprint 2: Core Variables (2 weeks)
- [ ] Add ASNOW (accumulated snowfall)
- [ ] Add SNOD (snow depth)
- [ ] Add 10m wind (UGRD, VGRD) with wind barbs
- [ ] Add APCP (accumulated precipitation)
- [ ] Add TMP (temperature)

### Sprint 3: Cumulative Calculations (1 week)
- [ ] Implement 24-hour snowfall total summary
- [ ] Calculate hourly snowfall rates
- [ ] Add precipitation total summary
- [ ] Calculate max wind gusts across forecast period

### Sprint 4: NAM Model Integration ⭐ (1-2 weeks)
- [ ] Integrate NAM 3km CONUS Nest as primary alternative model
- [ ] Implement model selection toggle (HRRR vs NAM)
- [ ] Add extended forecast view using NAM's 60-hour range
- [ ] Implement HRRR→NAM seamless transition for long-range forecasts
- [ ] Add model comparison view (side-by-side HRRR vs NAM)

### Sprint 5: UI Enhancement (1-2 weeks)
- [ ] Build variable selector interface
- [ ] Add summary dashboard page
- [ ] Implement layer compositing (overlays)
- [ ] Add units conversion (metric/imperial toggle)
- [ ] Polish model comparison interface

### Sprint 6: Additional Variables (1 week)
- [ ] Add GUST (wind gusts)
- [ ] Add DPT (dew point) and RH (humidity)
- [ ] Add PRATE (precipitation rate)
- [ ] Add VIS (visibility)
- [ ] Add NAM-specific variables (multiple CAPE types, helicity)

### Sprint 7: Additional Data Sources (2-3 weeks)
- [ ] Add NAM 12km for 84-hour extended forecasts
- [ ] Add GFS for 16-day long-range forecasts
- [ ] Integrate MRMS real-time radar observations
- [ ] Add GOES satellite imagery overlay
- [ ] Add RAP model as HRRR backup

### Sprint 8: Advanced Features (2 weeks)
- [ ] Severe weather variables dashboard (CAPE, helicity, shear)
- [ ] Multi-run comparison (old vs new model runs)
- [ ] Custom location creator (user-defined regions)
- [ ] Export/download capabilities (images, data, GIFs)
- [ ] Mobile-responsive design improvements

---

## Technical Considerations

### Storage Requirements

**Current**: ~100MB per location per run (24 frames + GRIBs for REFC only)

**With All Variables**: ~1-2GB per location per run
- 10+ variables × 24 hours × ~5-10MB per variable-hour

**Mitigation**:
- Keep GRIBs only for latest 2-3 runs
- Delete older GRIBs, keep PNGs only
- Implement compression for PNG storage
- Use cloud storage (S3) for archive

### NOMADS Request Throttling

NOMADS limits:
- 5 concurrent connections per IP
- No rate limits per se, but courtesy limits apply

**Mitigation**:
- Download multiple variables in single request (combined GRIB)
- Implement exponential backoff on failures
- Cache aggressively
- Consider NOAA Big Data Program sources (AWS, Google Cloud) as alternatives

### Processing Performance

**Current**: ~30 seconds per location per run (24 frames)

**With Multiple Variables**: ~5-10 minutes per location per run

**Mitigation**:
- Parallel processing (multiprocessing)
- Pre-generate only key variables by default
- On-demand generation for less-used variables
- Use faster plotting (Pillow instead of matplotlib for simple plots)

### Unit Conversions

Implement consistent conversion utilities:
```python
def convert_temperature(kelvin, to='fahrenheit'):
    if to == 'fahrenheit':
        return (kelvin - 273.15) * 9/5 + 32
    elif to == 'celsius':
        return kelvin - 273.15

def convert_wind_speed(m_per_s, to='mph'):
    if to == 'mph':
        return m_per_s * 2.23694
    elif to == 'kts':
        return m_per_s * 1.94384

def convert_precipitation(kg_per_m2, to='inches'):
    # kg/m² = mm of liquid
    if to == 'inches':
        return kg_per_m2 * 0.0393701
    elif to == 'cm':
        return kg_per_m2 / 10
```

---

## Feature Priority Matrix

| Feature | Priority | Complexity | User Value | Sprint | Notes |
|---------|----------|------------|------------|--------|-------|
| ASNOW (Snowfall) | ⭐⭐⭐⭐⭐ | Low | Very High | 2 | Most requested winter feature |
| APCP (Rain) | ⭐⭐⭐⭐⭐ | Low | Very High | 2 | Core precipitation tracking |
| Wind Speed/Direction | ⭐⭐⭐⭐⭐ | Medium | Very High | 2 | Critical for safety, wind energy |
| **NAM 3km Model** | **⭐⭐⭐⭐⭐** | **Medium** | **Very High** | **4** | **Extended forecasts, model comparison** |
| Temperature | ⭐⭐⭐⭐ | Low | High | 2 | Fundamental weather variable |
| SNOD (Snow Depth) | ⭐⭐⭐⭐ | Low | High | 2 | Winter operations planning |
| Cumulative Calculations | ⭐⭐⭐⭐ | Low | High | 3 | 24h snow totals, storm totals |
| GUST (Wind Gusts) | ⭐⭐⭐⭐ | Low | High | 6 | Severe weather preparedness |
| Model Comparison UI | ⭐⭐⭐⭐ | Medium | High | 4 | HRRR vs NAM side-by-side |
| MRMS Radar (Obs) | ⭐⭐⭐⭐ | High | High | 7 | Real-time vs forecast |
| Dew Point/Humidity | ⭐⭐⭐ | Low | Medium | 6 | Comfort index |
| Visibility | ⭐⭐⭐ | Low | Medium | 6 | Aviation, driving safety |
| Satellite Imagery | ⭐⭐⭐ | High | Medium | 7 | Cloud visualization |
| NAM 12km (84h) | ⭐⭐⭐ | Low | Medium | 7 | Extended range |
| GFS Model | ⭐⭐ | Medium | Medium | 7 | Long-range outlook |
| CAPE/Severe Wx | ⭐⭐ | Medium | Medium | 8 | Advanced severe weather |

---

## Example API Responses (Future State)

### `/api/variables`
```json
{
  "categories": {
    "precipitation": {
      "name": "Precipitation & Storms",
      "variables": ["refc", "apcp", "prate"]
    },
    "winter": {
      "name": "Winter Weather",
      "variables": ["asnow", "snod", "csnow"]
    },
    "wind": {
      "name": "Wind",
      "variables": ["wind_10m", "gust"]
    },
    "temperature": {
      "name": "Temperature & Moisture",
      "variables": ["t2m", "dpt", "rh"]
    }
  }
}
```

### `/api/summary/<location>/<run>`
```json
{
  "location_id": "philly",
  "run_id": "run_20260118_12",
  "summary": {
    "total_snowfall_inches": 8.5,
    "max_wind_gust_mph": 45,
    "temperature_range_f": {
      "min": 18,
      "max": 32
    },
    "total_precipitation_inches": 0.9
  },
  "summary_image_url": "/api/summary_image/philly/run_20260118_12"
}
```

---

## Resources & References

### NOMADS Documentation
- [NOMADS Grib Filter - HRRR 2D](https://nomads.ncep.noaa.gov/gribfilter.php?ds=hrrr_2d)
- [NCEP HRRR Product Documentation](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/)
- [HRRR GRIB2 Variable Tables](https://home.chpc.utah.edu/~u0553130/Brian_Blaylock/HRRR_archive/hrrr_sfc_table_f00-f01.html)

### Model Information
- [High-Resolution Rapid Refresh (HRRR)](https://rapidrefresh.noaa.gov/hrrr/)
- [HRRR on AWS](https://registry.opendata.aws/noaa-hrrr-pds/)
- [University of Utah HRRR Archive](https://mesowest.utah.edu/html/hrrr/)
- [NCEP NAM Product Documentation](https://www.nco.ncep.noaa.gov/pmb/products/nam/)
- [NAM 3km CONUS Nest Inventory](https://www.nco.ncep.noaa.gov/pmb/products/nam/nomads/)
- [NAM on AWS](https://registry.opendata.aws/noaa-nam/)

### GRIB2 Specifications
- [GRIB2 Table 4.2-0-1: Moisture](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-1.shtml)
- [NCEP GRIB2 Documentation](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/)

### Python Libraries
- **Herbie**: Modern HRRR data fetching - [GitHub](https://github.com/blaylockbk/Herbie)
- **xarray**: Multi-dimensional arrays - [Docs](https://docs.xarray.dev/)
- **cfgrib**: GRIB reader - [GitHub](https://github.com/ecmwf/cfgrib)
- **cartopy**: Cartographic projections - [Docs](https://scitools.org.uk/cartopy/)

---

## Why NAM Integration is Critical

NAM (North American Mesoscale) integration represents one of the highest-value additions to radarcheck. Here's why:

### 1. Extended Forecast Range
- **HRRR**: 18-48 hours (varies by product)
- **NAM 3km**: 60 hours (2.5 days)
- **NAM 12km**: 84 hours (3.5 days)

**Impact**: Users can track multi-day snow events, plan for weekend weather, and see storm evolution beyond HRRR's range.

### 2. Model Comparison = Better Decisions

When HRRR and NAM disagree significantly on snowfall amounts or storm timing, it signals forecast uncertainty. When they agree, users can have higher confidence. This is especially valuable for:
- **Winter storms**: Is it 6 inches or 12 inches of snow?
- **Severe weather**: Will storms develop in my area?
- **Wind events**: How strong will gusts be?

### 3. Same Resolution as HRRR (3km CONUS Nest)

NAM's 3km nest matches HRRR's 3km resolution, making it a true peer comparison rather than a lower-quality backup. Users get:
- Similar detail in precipitation bands
- Similar accuracy in mesoscale features
- Comparable snow accumulation forecasts

### 4. Complementary Strengths

Each model has different physics and data assimilation:
- **HRRR**: Updates hourly, better for rapidly evolving situations
- **NAM**: 6-hourly updates, longer range, different boundary layer physics
- Together they provide an "ensemble-like" view of possible outcomes

### 5. Operational Value

For users making real decisions (DOT snow removal, event planning, construction, agriculture):
- **Risk Management**: If both models show heavy snow, prepare accordingly
- **Confidence Assessment**: Large model spread = low confidence, plan for uncertainty
- **Trend Analysis**: Track how NAM's longer-range forecast evolves with each update

### 6. Minimal Additional Complexity

Since NAM uses the same GRIB2 format, similar variables, and NOMADS distribution as HRRR:
- **Same code paths**: Reuse GRIB fetching, plotting, caching infrastructure
- **Same variables**: ASNOW, APCP, REFC, wind, temperature all available
- **Same API**: NOMADS filter service works identically
- **Low risk**: Well-understood, operational NOAA model

### Implementation Priority

Given these benefits, **NAM integration should be Sprint 4** (not Sprint 6) to deliver maximum value early. The architecture changes in Sprint 1 will support multi-model switching, making NAM integration straightforward once the foundation is in place.

**Quick Win**: Even implementing NAM for snow variables only (ASNOW, SNOD) would be tremendously valuable to winter weather users.

---

## Conclusion

This plan provides a clear roadmap for transforming radarcheck from a single-variable radar viewer into a comprehensive multi-variable, multi-model weather visualization platform. The phased approach allows for:

1. **Strong foundation** (Sprint 1): Architecture that scales to many variables AND multiple models
2. **Quick wins** (Sprint 2): Core variables users want most (snow, wind, rain)
3. **High-value calculations** (Sprint 3): Cumulative snowfall, total precipitation
4. **Multi-model capability** (Sprint 4): NAM integration for extended forecasts and model comparison
5. **Progressive enhancement**: Can stop at any sprint and have a functional product
6. **Future-proofing**: Designed to accommodate additional data sources and variables

### Key Deliverables by Sprint

**After Sprint 2** (6 weeks): Multi-variable HRRR viewer with snow, rain, wind, and temperature
**After Sprint 4** (10-12 weeks): Full HRRR + NAM multi-model system with 60-hour forecasts and model comparison
**After Sprint 8** (20+ weeks): Complete weather visualization platform with satellite, radar observations, and severe weather tools

### Estimated Effort

- **Sprints 1-2**: 5-6 weeks → Most impactful variables
- **Sprints 1-4**: 9-11 weeks → Multi-model system with NAM ⭐ RECOMMENDED INITIAL TARGET
- **Sprints 1-8**: 18-20 weeks → Full feature set

### Next Steps

1. **Immediate**: Review and confirm feature priorities
2. **Week 1**: Set up development environment with HRRR and NAM test data
3. **Week 2**: Begin Sprint 1 multi-variable/multi-model infrastructure
4. **Week 6**: User testing after Sprint 2 (core variables)
5. **Week 11**: Major milestone review after Sprint 4 (NAM integration complete)
6. **Ongoing**: Iterate based on user feedback and feature adoption

### Success Metrics

- **User Engagement**: Time spent comparing HRRR vs NAM forecasts
- **Forecast Accuracy Perception**: Do users report better decision-making with model comparison?
- **Feature Adoption**: Which variables get used most (snow, wind, temperature)?
- **Extended Range Usage**: How often do users view forecasts beyond 24 hours (enabled by NAM)?

This roadmap prioritizes **practical value** (snow totals, wind forecasts, model comparison) over novelty features, ensuring radarcheck becomes an essential tool for weather-dependent decision making.
