# Radarcheck Improvement Plan

A comprehensive plan to enhance the Radarcheck weather visualization application across code quality, features, performance, reliability, and user experience.

---

## Executive Summary

Radarcheck is a well-structured Flask application for HRRR weather radar visualization. Recent commits have added multi-variable and multi-model support. This plan identifies improvements across 8 key areas organized into 5 phases, prioritized by impact and effort.

**Highlight: Interactive Map Visualization (Phase 3B)**
The highest-impact improvement is replacing static PNG images with interactive Leaflet/Mapbox maps. This enables pan/zoom, click-for-values, layer toggling, and base map switching - transforming the app from a simple image viewer into a modern weather visualization platform.

**Current State:**
- 2,374 lines of Python across 5 main modules
- 2 locations (Philadelphia, NYC)
- 15 weather variables, 3 models (HRRR, NAM 3km, NAM 12km)
- File-based caching with 24-hour retention
- Deployed on Fly.io with GitHub Actions CI/CD

---

## Phase 1: Code Quality & Reliability (Foundation)

### 1.1 Exception Handling Improvements

**Problem:** 6 broad `except Exception:` clauses mask specific errors and make debugging difficult.

**Changes:**

| File | Line | Current | Proposed |
|------|------|---------|----------|
| `cache_builder.py` | 115 | `except Exception` | `except (requests.RequestException, requests.Timeout)` |
| `cache_builder.py` | 225-226 | `except Exception` | `except (OSError, ValueError, RuntimeError, xr.backends.cfgrib_.DatasetBuildError)` |
| `cache_builder.py` | 423-424 | `except Exception` | `except (OSError, ValueError, RuntimeError)` |
| `app.py` | 391 | `except Exception` | `except (OSError, IOError, ValueError)` |
| `plotting.py` | 148 | bare `except` | `except (RuntimeError, cfgrib.exceptions.DatasetBuildError)` |
| `plotting.py` | 290-293 | `except Exception` | `except (RuntimeError, ValueError, KeyError)` |

**Add custom exception classes in `utils.py`:**
```python
class GribDownloadError(Exception):
    """Failed to download GRIB file from NOMADS."""

class GribValidationError(Exception):
    """GRIB file is corrupted or invalid."""

class PlotGenerationError(Exception):
    """Failed to generate forecast plot."""
```

### 1.2 Add Type Hints

**Problem:** No type hints make the codebase harder to maintain and prevents static analysis.

**Changes:** Add type hints to all public functions across all modules.

```python
# Example transformation for app.py
def get_available_locations(model_id: str | None = None) -> list[dict[str, Any]]:
    ...

def get_location_runs(location_id: str, model_id: str | None = None) -> list[dict[str, Any]]:
    ...

def get_run_metadata(location_id: str, run_id: str, model_id: str | None = None) -> dict[str, str] | None:
    ...
```

**Create `py.typed` marker and add `mypy` to dev dependencies.**

### 1.3 Structured Metadata Format

**Problem:** `metadata.txt` uses simple `key=value` format which is fragile and non-standard.

**Changes:**
- Migrate to JSON format for all metadata files
- Add backward compatibility reader that handles both formats
- Update `cache_builder.py` to write `metadata.json`
- Update `app.py` to read `metadata.json` with fallback to `metadata.txt`

```python
# New metadata format (metadata.json)
{
    "version": 1,
    "date_str": "20240115",
    "init_hour": "12",
    "init_time": "2024-01-15 12:00:00",
    "run_id": "run_20240115_12",
    "model_id": "hrrr",
    "model_name": "HRRR",
    "location": {
        "name": "Philadelphia",
        "center_lat": 40.04877,
        "center_lon": -75.38903,
        "zoom": 1.5
    },
    "generated_at": "2024-01-15T12:30:45Z"
}
```

### 1.4 Constants & Configuration Cleanup

**Problem:** Magic numbers scattered throughout code (timeouts, buffer sizes, retry counts).

**Changes in `config.py`:**
```python
# Network settings
DOWNLOAD_TIMEOUT_SECONDS = 60
HEAD_REQUEST_TIMEOUT_SECONDS = 10
MAX_DOWNLOAD_RETRIES = 3
RETRY_DELAY_SECONDS = 2

# File validation
MIN_GRIB_FILE_SIZE_BYTES = 1000
MIN_PNG_FILE_SIZE_BYTES = 1000

# Cache settings
MAX_RUNS_TO_KEEP = 24
CACHE_REFRESH_INTERVAL_MINUTES = 15

# Model discovery
HOURS_TO_CHECK_FOR_RUNS = 27
```

### 1.5 FileLock Timeout Handling

**Problem:** `FileLock` operations can hang indefinitely if a lock is not released.

**Changes:**
```python
# Add explicit timeout to all FileLock operations
from filelock import FileLock, Timeout

try:
    with FileLock(f"{filename}.lock", timeout=30):
        # file operations
except Timeout:
    logger.error(f"Could not acquire lock for {filename} within 30 seconds")
    raise GribValidationError(f"Lock timeout for {filename}")
```

---

## Phase 2: Testing & Quality Assurance

### 2.1 Unit Tests with Mocking

**Problem:** Only integration tests exist; no unit tests for individual functions.

**New test files:**

**`tests/test_utils.py`:**
```python
def test_convert_units_kelvin_to_fahrenheit():
    data = np.array([273.15, 300, 310])
    result = convert_units(data, "k_to_f")
    assert result[0] == pytest.approx(32.0)

def test_convert_units_unknown_returns_unchanged():
    data = np.array([1, 2, 3])
    result = convert_units(data, "unknown")
    np.testing.assert_array_equal(result, data)

def test_compute_wind_speed():
    u = np.array([3.0])
    v = np.array([4.0])
    result = compute_wind_speed(u, v)
    assert result[0] == pytest.approx(5.0)
```

**`tests/test_config.py`:**
```python
def test_all_variables_have_required_fields():
    required = ["nomads_params", "display_name", "units", "colormap", "vmin", "vmax", "category"]
    for var_id, var_config in WEATHER_VARIABLES.items():
        for field in required:
            assert field in var_config, f"{var_id} missing {field}"

def test_all_models_have_required_fields():
    required = ["name", "max_forecast_hours", "nomads_url", "dir_pattern", "file_pattern"]
    for model_id, model_config in MODELS.items():
        for field in required:
            assert field in model_config, f"{model_id} missing {field}"
```

**`tests/test_cache_builder.py`:**
```python
def test_build_variable_query_single_param():
    config = {"nomads_params": ["var_REFC"], "level_params": []}
    result = build_variable_query(config)
    assert result == "var_REFC=on&"

def test_build_variable_query_with_levels():
    config = {"nomads_params": ["var_TMP"], "level_params": ["lev_2_m_above_ground=on"]}
    result = build_variable_query(config)
    assert "var_TMP=on" in result
    assert "lev_2_m_above_ground=on" in result
```

**`tests/test_plotting.py`:**
```python
@pytest.fixture
def mock_grib_dataset():
    # Create synthetic xarray dataset for testing
    ...

def test_select_variable_from_dataset_prefers_short_name(mock_grib_dataset):
    ...

def test_get_colormap_returns_nws_for_reflectivity():
    config = {"colormap": "nws_reflectivity"}
    cmap = get_colormap(config)
    assert cmap.name == "radar"
```

### 2.2 Negative Test Cases

**New tests for error conditions:**

```python
# test_error_handling.py

def test_fetch_grib_handles_network_timeout(mocker):
    mocker.patch("requests.get", side_effect=requests.Timeout)
    with pytest.raises(ValueError, match="Failed to obtain valid GRIB"):
        fetch_grib(...)

def test_create_plot_handles_corrupted_grib(tmp_path):
    # Write invalid data to a file
    bad_grib = tmp_path / "bad.grib2"
    bad_grib.write_bytes(b"not a grib file")
    with pytest.raises(RuntimeError):
        create_plot(str(bad_grib), ...)

def test_api_handles_missing_cache_gracefully(client, monkeypatch):
    monkeypatch.setitem(repomap, "CACHE_DIR", "/nonexistent")
    response = client.get("/api/locations")
    assert response.status_code == 200
    assert response.get_json() == []
```

### 2.3 Test Coverage Target

**Goal:** Achieve 80% code coverage

**Add to CI pipeline:**
```yaml
- name: Run tests with coverage
  run: |
    pytest --cov=. --cov-report=xml --cov-fail-under=80
```

### 2.4 JavaScript Tests

**Problem:** 312 lines of JavaScript with no tests.

**Add Jest testing framework:**

```javascript
// tests/js/singleRunView.test.js
describe('SingleRunView', () => {
    test('preloads next image on slider change', () => {...});
    test('respects maxForecastHours boundary', () => {...});
});

// tests/js/timelineView.test.js
describe('TimelineView', () => {
    test('builds timeline grid from run data', () => {...});
    test('highlights selected cell', () => {...});
});
```

---

## Phase 3: Features & User Experience

### 3.1 Additional Locations

**Problem:** Only 2 locations (Philadelphia, NYC) currently configured.

**Add to `config.py`:**

```python
"LOCATIONS": {
    # Existing
    "philly": {...},
    "nyc": {...},

    # New locations
    "boston": {
        "name": "Boston",
        "center_lat": 42.3601,
        "center_lon": -71.0589,
        "zoom": 1.5,
        "lat_min": 41.0,
        "lat_max": 43.5,
        "lon_min": -72.5,
        "lon_max": -69.5,
    },
    "dc": {
        "name": "Washington DC",
        "center_lat": 38.9072,
        "center_lon": -77.0369,
        "zoom": 1.5,
        "lat_min": 37.5,
        "lat_max": 40.0,
        "lon_min": -78.5,
        "lon_max": -75.5,
    },
    "chicago": {
        "name": "Chicago",
        "center_lat": 41.8781,
        "center_lon": -87.6298,
        "zoom": 1.5,
        "lat_min": 40.5,
        "lat_max": 43.0,
        "lon_min": -89.5,
        "lon_max": -86.0,
    },
    "denver": {
        "name": "Denver",
        "center_lat": 39.7392,
        "center_lon": -104.9903,
        "zoom": 1.5,
        "lat_min": 38.5,
        "lat_max": 41.0,
        "lon_min": -106.5,
        "lon_max": -103.5,
    },
    "la": {
        "name": "Los Angeles",
        "center_lat": 34.0522,
        "center_lon": -118.2437,
        "zoom": 1.5,
        "lat_min": 32.5,
        "lat_max": 35.5,
        "lon_min": -120.0,
        "lon_max": -116.5,
    },
    "seattle": {
        "name": "Seattle",
        "center_lat": 47.6062,
        "center_lon": -122.3321,
        "zoom": 1.5,
        "lat_min": 46.0,
        "lat_max": 49.0,
        "lon_min": -124.0,
        "lon_max": -121.0,
    },
}
```

### 3.2 User Preferences (Local Storage)

**Problem:** Users must re-select location/model/variable on each visit.

**Add to JavaScript:**

```javascript
// utils/preferences.js
const STORAGE_KEY = 'radarcheck_preferences';

function savePreferences(prefs) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

function loadPreferences() {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : {
        location: 'philly',
        model: 'hrrr',
        variable: 'refc',
        playbackSpeed: 500
    };
}
```

**Update `index.html` to redirect to last-used location.**

### 3.3 Custom Region Selection

**Problem:** Users are limited to predefined locations.

**New endpoint:**
```python
@app.route("/custom")
def custom_region():
    """Allow users to specify a custom center point and zoom."""
    lat = request.args.get('lat', type=float)
    lon = request.args.get('lon', type=float)
    zoom = request.args.get('zoom', default=1.5, type=float)
    # Validate bounds
    # Generate on-demand (or redirect to nearest cached location)
```

**UI component:** Leaflet map picker for selecting custom regions.

### 3.4 Weather Alerts Integration

**Problem:** No severe weather notifications.

**New module `alerts.py`:**
```python
import requests

NWS_ALERTS_API = "https://api.weather.gov/alerts/active"

def get_alerts_for_location(lat: float, lon: float) -> list[dict]:
    """Fetch active NWS alerts for a location."""
    params = {"point": f"{lat},{lon}"}
    response = requests.get(NWS_ALERTS_API, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("features", [])
```

**New API endpoint:**
```python
@app.route("/api/alerts/<location_id>")
def api_alerts(location_id):
    location = repomap["LOCATIONS"].get(location_id)
    if not location:
        return jsonify({"error": "Invalid location"}), 400
    alerts = get_alerts_for_location(location["center_lat"], location["center_lon"])
    return jsonify(alerts)
```

### 3.5 Enhanced Spaghetti Plot

**Problem:** Current spaghetti plot is basic; no zoom, no data point hover.

**Improvements:**
- Add Chart.js zoom plugin for time-range selection
- Add hover tooltips showing exact values
- Add ability to toggle individual runs on/off
- Add statistical envelope (mean + std deviation bands)
- Color-code by model age (older runs more faded)

### 3.6 Mobile Responsiveness

**Problem:** UI not optimized for mobile devices.

**CSS improvements in `static/css/style.css`:**
```css
@media (max-width: 768px) {
    .run-selector {
        flex-direction: column;
        gap: 10px;
    }

    .view-selector button {
        padding: 8px 12px;
        font-size: 14px;
    }

    .timeline-container {
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }

    .controls {
        flex-wrap: wrap;
    }
}
```

---

## Phase 3B: Interactive Map Visualization (Major Feature)

This is a significant architectural enhancement that replaces static PNG images with interactive web map overlays. This enables pan/zoom, click-for-values, layer toggling, and a much richer user experience.

### Current Architecture (Static Images)

```
GRIB2 → matplotlib/cartopy → PNG → Flask serves image → <img> tag displays
```

**Limitations:**
- Fixed zoom level per location
- No pan/zoom interaction
- No click-to-query values
- No layer toggling
- Large file sizes for high-resolution images
- New image required for any view change

### Proposed Architecture (Interactive Maps)

```
GRIB2 → Data Processing → Tile/Vector Generation → Map Library renders in browser
```

**Benefits:**
- Infinite pan/zoom within data bounds
- Click anywhere for exact forecast values
- Toggle multiple variables as layers
- Smooth animations between forecast hours
- Smaller data transfer (tiles loaded on demand)
- Base map customization (satellite, terrain, streets)

### 3B.1 Technology Selection

| Option | Pros | Cons | Recommendation |
|--------|------|------|----------------|
| **Leaflet + GeoTIFF tiles** | Simple, lightweight, good plugin ecosystem | Limited WebGL support | Good for MVP |
| **Mapbox GL JS** | Beautiful, fast WebGL rendering, great mobile | Requires API key, usage limits | Best UX |
| **OpenLayers** | Most powerful, no vendor lock-in | Steeper learning curve | Enterprise choice |
| **Deck.gl** | Excellent for large datasets, WebGL | Complex setup, React-focused | Best performance |

**Recommended: Leaflet for MVP, migrate to Mapbox GL JS for production**

### 3B.2 Data Pipeline Changes

**New module `tile_generator.py`:**

```python
import numpy as np
import xarray as xr
from PIL import Image
import mercantile
from rio_tiler.io import XarrayReader

def grib_to_geotiff(grib_path: str, output_path: str, variable_config: dict) -> str:
    """Convert GRIB2 to Cloud-Optimized GeoTIFF for efficient tiling."""
    ds = xr.open_dataset(grib_path, engine="cfgrib")
    data = select_variable_from_dataset(ds, variable_config)

    # Apply unit conversion
    if variable_config.get("conversion"):
        data = convert_units(data, variable_config["conversion"])

    # Write as COG (Cloud Optimized GeoTIFF)
    data.rio.to_raster(
        output_path,
        driver="COG",
        compress="deflate"
    )
    return output_path


def generate_tiles(geotiff_path: str, output_dir: str,
                   min_zoom: int = 4, max_zoom: int = 10) -> None:
    """Generate XYZ tile pyramid from GeoTIFF."""
    with XarrayReader(geotiff_path) as src:
        for zoom in range(min_zoom, max_zoom + 1):
            tiles = list(mercantile.tiles(*src.bounds, zooms=zoom))
            for tile in tiles:
                img, mask = src.tile(tile.x, tile.y, tile.z)
                tile_path = f"{output_dir}/{zoom}/{tile.x}/{tile.y}.png"
                save_tile(img, mask, tile_path, variable_config)


def generate_vector_contours(grib_path: str, variable_config: dict) -> dict:
    """Generate GeoJSON contours for vector rendering."""
    import rasterio
    from rasterio import features

    ds = xr.open_dataset(grib_path, engine="cfgrib")
    data = select_variable_from_dataset(ds, variable_config)

    # Generate contour levels
    vmin, vmax = variable_config["vmin"], variable_config["vmax"]
    levels = np.linspace(vmin, vmax, 10)

    contours = []
    for level in levels:
        mask = data.values >= level
        shapes = features.shapes(mask.astype(np.uint8), transform=data.rio.transform())
        for shape, value in shapes:
            if value == 1:
                contours.append({
                    "type": "Feature",
                    "properties": {"value": float(level)},
                    "geometry": shape
                })

    return {"type": "FeatureCollection", "features": contours}
```

### 3B.3 New API Endpoints

```python
# Tile serving endpoint
@app.route("/tiles/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>/<int:z>/<int:x>/<int:y>.png")
def get_tile(location_id, model_id, run_id, variable_id, hour, z, x, y):
    """Serve map tiles for interactive display."""
    tile_path = os.path.join(
        repomap["CACHE_DIR"], location_id, model_id, run_id,
        variable_id, "tiles", str(hour), str(z), str(x), f"{y}.png"
    )
    if not os.path.exists(tile_path):
        return "", 204  # No content for missing tiles
    return send_file(tile_path, mimetype="image/png")


# GeoJSON endpoint for vector overlays
@app.route("/api/geojson/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
def get_geojson(location_id, model_id, run_id, variable_id, hour):
    """Serve GeoJSON contours for vector rendering."""
    geojson_path = os.path.join(
        repomap["CACHE_DIR"], location_id, model_id, run_id,
        variable_id, f"contours_{hour:02d}.geojson"
    )
    if not os.path.exists(geojson_path):
        return jsonify({"error": "Data not available"}), 404
    return send_file(geojson_path, mimetype="application/geo+json")


# Point query endpoint
@app.route("/api/value/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
def get_point_value(location_id, model_id, run_id, variable_id, hour):
    """Get forecast value at a specific lat/lon point."""
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    grib_path = get_grib_path(location_id, model_id, run_id, variable_id, hour)
    value, units = extract_point_value(grib_path, lat, lon, variable_config)

    return jsonify({
        "lat": lat,
        "lon": lon,
        "value": value,
        "units": units,
        "variable": variable_id,
        "forecast_hour": hour
    })
```

### 3B.4 Frontend Implementation

**New file `static/js/interactiveMap.js`:**

```javascript
class WeatherMap {
    constructor(containerId, options = {}) {
        this.map = L.map(containerId, {
            center: [options.centerLat || 40.0, options.centerLon || -75.0],
            zoom: options.zoom || 8,
            maxBounds: options.bounds
        });

        // Base layer options
        this.baseLayers = {
            'Streets': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'),
            'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'),
            'Terrain': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png')
        };
        this.baseLayers['Streets'].addTo(this.map);

        // Weather overlay layer
        this.weatherLayer = null;
        this.currentHour = 1;

        // County boundaries
        this.countyLayer = null;

        // Click handler for point queries
        this.map.on('click', (e) => this.onMapClick(e));

        // Layer control
        L.control.layers(this.baseLayers, {}, {position: 'topright'}).addTo(this.map);
    }

    setWeatherLayer(locationId, modelId, runId, variableId, hour) {
        // Remove existing weather layer
        if (this.weatherLayer) {
            this.map.removeLayer(this.weatherLayer);
        }

        // Add new tile layer
        const tileUrl = `/tiles/${locationId}/${modelId}/${runId}/${variableId}/${hour}/{z}/{x}/{y}.png`;
        this.weatherLayer = L.tileLayer(tileUrl, {
            opacity: 0.7,
            maxZoom: 12,
            minZoom: 4
        }).addTo(this.map);

        this.currentHour = hour;
    }

    async onMapClick(e) {
        const {lat, lng} = e.latlng;

        // Query the forecast value at this point
        const response = await fetch(
            `/api/value/${this.locationId}/${this.modelId}/${this.runId}/${this.variableId}/${this.currentHour}?lat=${lat}&lon=${lng}`
        );

        if (response.ok) {
            const data = await response.json();
            L.popup()
                .setLatLng(e.latlng)
                .setContent(`
                    <strong>${data.variable}</strong><br>
                    Value: ${data.value?.toFixed(1) ?? 'N/A'} ${data.units}<br>
                    Hour +${data.forecast_hour}
                `)
                .openOn(this.map);
        }
    }

    animateHours(startHour, endHour, interval = 500) {
        let hour = startHour;
        this.animationTimer = setInterval(() => {
            this.setWeatherLayer(this.locationId, this.modelId, this.runId, this.variableId, hour);
            hour++;
            if (hour > endHour) hour = startHour;
        }, interval);
    }

    stopAnimation() {
        if (this.animationTimer) {
            clearInterval(this.animationTimer);
            this.animationTimer = null;
        }
    }

    addCountyBoundaries(geojsonUrl) {
        fetch(geojsonUrl)
            .then(r => r.json())
            .then(data => {
                this.countyLayer = L.geoJSON(data, {
                    style: {
                        color: '#666',
                        weight: 1,
                        fillOpacity: 0
                    }
                }).addTo(this.map);
            });
    }

    setOpacity(opacity) {
        if (this.weatherLayer) {
            this.weatherLayer.setOpacity(opacity);
        }
    }
}

// Color legend control
L.Control.Legend = L.Control.extend({
    onAdd: function(map) {
        const div = L.DomUtil.create('div', 'legend');
        div.innerHTML = this.options.content;
        return div;
    }
});

function createLegend(variableConfig) {
    const {vmin, vmax, units, display_name} = variableConfig;
    // Generate gradient legend HTML
    return `
        <div class="legend-title">${display_name}</div>
        <div class="legend-gradient"></div>
        <div class="legend-labels">
            <span>${vmin}</span>
            <span>${vmax} ${units}</span>
        </div>
    `;
}
```

### 3B.5 Updated HTML Template

**Replace image display in `location.html`:**

```html
<div id="singleView" class="view active">
    <p>Model initialized: {{ init_time }}</p>
    <div class="controls">
        <button id="playButton">Play</button>
        <input type="range" id="timeSlider" min="1" max="{{ models[model_id].max_forecast_hours }}" value="1">
        <span id="timeDisplay">Hour +1</span>
        <label>
            Opacity: <input type="range" id="opacitySlider" min="0" max="100" value="70">
        </label>
    </div>

    <!-- Interactive map container -->
    <div id="weatherMap" style="width: 100%; height: 500px;"></div>

    <!-- Fallback static image for older browsers -->
    <noscript>
        <img src="/frame/{{ location_id }}/{{ model_id }}/{{ run_id }}/{{ variable_id }}/1"
             alt="Forecast Plot" style="width: 100%;">
    </noscript>
</div>

<script>
    const weatherMap = new WeatherMap('weatherMap', {
        centerLat: {{ location.center_lat }},
        centerLon: {{ location.center_lon }},
        zoom: 8
    });

    weatherMap.locationId = '{{ location_id }}';
    weatherMap.modelId = '{{ model_id }}';
    weatherMap.runId = '{{ run_id }}';
    weatherMap.variableId = '{{ variable_id }}';

    // Initial layer
    weatherMap.setWeatherLayer(
        '{{ location_id }}', '{{ model_id }}', '{{ run_id }}',
        '{{ variable_id }}', 1
    );

    // Slider updates map
    document.getElementById('timeSlider').addEventListener('input', (e) => {
        weatherMap.setWeatherLayer(
            weatherMap.locationId, weatherMap.modelId,
            weatherMap.runId, weatherMap.variableId,
            parseInt(e.target.value)
        );
    });

    // Opacity control
    document.getElementById('opacitySlider').addEventListener('input', (e) => {
        weatherMap.setOpacity(e.target.value / 100);
    });
</script>
```

### 3B.6 Cache Builder Updates

**Update `cache_builder.py` to generate tiles:**

```python
def generate_forecast_images(location_config, counties, model_id, run_info=None, variable_ids=None):
    # ... existing code ...

    for variable_id in variable_ids:
        for hour in range(1, max_hours + 1):
            # Existing: Generate static PNG
            if GENERATE_STATIC_IMAGES:
                image_buffer = create_plot(...)
                save_image(image_buffer, image_path)

            # New: Generate tiles for interactive map
            if GENERATE_MAP_TILES:
                geotiff_path = grib_to_geotiff(grib_path, temp_geotiff, variable_config)
                tile_dir = os.path.join(variable_cache_dir, "tiles", f"{hour:02d}")
                generate_tiles(geotiff_path, tile_dir, min_zoom=4, max_zoom=10)

            # New: Generate GeoJSON contours (optional, for vector rendering)
            if GENERATE_VECTOR_CONTOURS:
                contours = generate_vector_contours(grib_path, variable_config)
                with open(contour_path, 'w') as f:
                    json.dump(contours, f)
```

### 3B.7 New Dependencies

```txt
# requirements.txt additions
leaflet  # via CDN, no pip package needed
rioxarray  # xarray + rasterio integration
rio-tiler  # Efficient tile generation
mercantile  # Tile math utilities
rio-cogeo  # Cloud Optimized GeoTIFF support
```

### 3B.8 Migration Strategy

**Phase A: Parallel Implementation**
1. Keep existing static image generation
2. Add tile generation as opt-in feature (`GENERATE_MAP_TILES=true`)
3. Create new `/map/<location_id>` route for interactive view
4. Users can switch between static and interactive views

**Phase B: Interactive as Default**
1. Make interactive map the default view
2. Keep static images as fallback/API option
3. Add "Download Image" button that uses existing PNG generation

**Phase C: Optimize**
1. Generate tiles on-demand instead of pre-generating
2. Add tile caching with Redis/CDN
3. Consider vector tiles (MVT) for even better performance

### 3B.9 Storage Considerations

**Tile Storage Estimate (per forecast hour, per variable):**
- Zoom 4-10 = ~1,400 tiles per location
- ~5KB average per tile (PNG with transparency)
- ~7MB per hour × 24 hours × 15 variables = ~2.5GB per run

**Optimization Options:**
1. Generate tiles on-demand (lazy generation)
2. Use WebP instead of PNG (50% size reduction)
3. Limit zoom levels (4-8 instead of 4-10)
4. Store only most recent 2-3 runs with tiles

### 3B.10 Comparison: Static vs Interactive

| Feature | Static Images | Interactive Map |
|---------|--------------|-----------------|
| Pan/Zoom | Fixed | Unlimited |
| Click for values | No | Yes |
| Multiple base maps | No | Yes |
| Layer toggling | No | Yes |
| Storage per hour | ~200KB | ~7MB (tiles) |
| Initial load time | Fast | Medium |
| Mobile experience | Limited | Excellent |
| Offline support | Easy | Complex |
| Browser support | Universal | Modern only |

---

## Phase 4: Performance & Infrastructure

### 4.1 Parallel GRIB Downloads

**Problem:** GRIB files downloaded sequentially, slowing cache builds.

**Changes to `cache_builder.py`:**
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def download_all_hours_parallel(model_id, variable_id, date_str, init_hour,
                                 location_config, run_id, max_hours):
    """Download GRIB files in parallel using thread pool."""
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                fetch_grib, model_id, variable_id, date_str, init_hour,
                f"{hour:02d}", location_config, run_id
            ): hour
            for hour in range(1, max_hours + 1)
        }
        for future in as_completed(futures):
            hour = futures[future]
            try:
                results[hour] = future.result()
            except Exception as e:
                logger.error(f"Failed to download hour {hour}: {e}")
    return results
```

### 4.2 Image Caching Headers

**Problem:** Browser makes new requests for already-loaded images.

**Add to `app.py`:**
```python
@app.after_request
def add_cache_headers(response):
    if response.content_type == 'image/png':
        # Cache for 1 hour (images don't change once generated)
        response.headers['Cache-Control'] = 'public, max-age=3600'
        response.headers['ETag'] = hashlib.md5(response.data).hexdigest()
    return response
```

### 4.3 Database Backend (Optional)

**Problem:** File-based metadata becomes unwieldy at scale.

**Proposed schema (SQLite initially, PostgreSQL for production):**
```sql
CREATE TABLE model_runs (
    id INTEGER PRIMARY KEY,
    location_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    date_str TEXT NOT NULL,
    init_hour TEXT NOT NULL,
    init_time TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(location_id, model_id, run_id)
);

CREATE TABLE forecast_frames (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES model_runs(id),
    variable_id TEXT NOT NULL,
    forecast_hour INTEGER NOT NULL,
    valid_time TIMESTAMP NOT NULL,
    frame_path TEXT NOT NULL,
    center_value REAL,
    UNIQUE(run_id, variable_id, forecast_hour)
);

CREATE INDEX idx_runs_location_model ON model_runs(location_id, model_id);
CREATE INDEX idx_frames_run ON forecast_frames(run_id);
```

### 4.4 Metrics & Monitoring

**Problem:** Limited observability beyond health check.

**Add Prometheus metrics:**
```python
from prometheus_client import Counter, Histogram, generate_latest

REQUEST_COUNT = Counter('radarcheck_requests_total', 'Total requests', ['endpoint', 'status'])
REQUEST_LATENCY = Histogram('radarcheck_request_latency_seconds', 'Request latency', ['endpoint'])
CACHE_BUILD_DURATION = Histogram('radarcheck_cache_build_seconds', 'Cache build duration', ['location', 'model'])
GRIB_DOWNLOAD_FAILURES = Counter('radarcheck_grib_download_failures_total', 'GRIB download failures', ['model'])

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain'}
```

### 4.5 Rate Limiting

**Problem:** No protection against API abuse.

**Add Flask-Limiter:**
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

@app.route("/frame/<location_id>/<model_id>/<run_id>/<variable_id>/<int:hour>")
@limiter.limit("100 per minute")
@require_api_key
def get_frame(...):
    ...
```

### 4.6 CDN Integration

**Problem:** All image requests hit origin server.

**Options:**
1. **Cloudflare** (free tier): Add Cloudflare in front of Fly.io
2. **Fly.io edge caching**: Configure `fly.toml` with cache rules
3. **S3 + CloudFront**: Store generated images in S3

**Recommended: Cloudflare** (simplest, free, works with existing setup)

---

## Phase 5: Documentation & Developer Experience

### 5.1 API Documentation

**Create `docs/API.md`:**
```markdown
# Radarcheck API Reference

## Authentication
All API endpoints require `X-API-Key` header when `RADARCHECK_API_KEY` is set.

## Endpoints

### GET /api/locations
Returns list of available locations with their latest run info.

**Response:**
```json
[
  {
    "id": "philly",
    "name": "Philadelphia",
    "init_time": "2024-01-15 12:00:00",
    "run_id": "run_20240115_12",
    "model_id": "hrrr",
    "model_name": "HRRR"
  }
]
```

### GET /api/runs/{location_id}
### GET /api/runs/{location_id}/{model_id}
...
```

### 5.2 OpenAPI/Swagger Spec

**Add `flask-apispec` or `flasgger` for auto-generated API docs:**
```python
from flasgger import Swagger

swagger = Swagger(app)

@app.route("/api/locations")
@swag_from({
    'responses': {
        200: {
            'description': 'List of available locations',
            'schema': {'type': 'array', 'items': {'$ref': '#/definitions/Location'}}
        }
    }
})
def api_locations():
    ...
```

### 5.3 Contributing Guidelines

**Create `CONTRIBUTING.md`:**
- Development environment setup
- Code style (Black, isort, flake8)
- Testing requirements
- PR process
- Commit message format

### 5.4 Architecture Decision Records

**Create `docs/adr/` directory:**
- `001-file-based-caching.md`
- `002-multi-model-support.md`
- `003-api-authentication.md`

---

## Implementation Priority

| Phase | Priority | Effort | Impact |
|-------|----------|--------|--------|
| 1.1 Exception Handling | High | Low | High |
| 1.2 Type Hints | Medium | Medium | Medium |
| 1.4 Constants Cleanup | High | Low | Medium |
| 2.1 Unit Tests | High | Medium | High |
| 2.2 Negative Tests | High | Low | High |
| 3.1 More Locations | High | Low | High |
| 3.2 User Preferences | Medium | Low | Medium |
| 3.6 Mobile Responsive | Medium | Low | High |
| **3B Interactive Maps** | **High** | **High** | **Very High** |
| 4.1 Parallel Downloads | Medium | Medium | High |
| 4.2 Cache Headers | High | Low | Medium |
| 4.4 Metrics | Medium | Medium | High |

### Interactive Map Implementation Phases

| Sub-phase | Effort | Dependencies |
|-----------|--------|--------------|
| 3B.1 Leaflet MVP | Medium | None |
| 3B.2 Tile generation | High | rioxarray, rio-tiler |
| 3B.3 Point query API | Low | Existing GRIB reading |
| 3B.4 Animation controls | Low | 3B.1 |
| 3B.5 Multiple base maps | Low | 3B.1 |
| 3B.6 On-demand tiles | High | 3B.2 |

---

## Quick Wins (Can be done immediately)

1. **Add more locations** - Just config changes
2. **Add cache headers** - 5 lines of code
3. **Fix broad exceptions** - Find/replace with specific types
4. **Add constants** - Move magic numbers to config
5. **Mobile CSS** - Add media queries

---

## Dependencies to Add

```txt
# requirements-dev.txt
pytest
pytest-cov
pytest-mock
mypy
black
isort
flake8

# requirements.txt additions
prometheus-client
flask-limiter
flasgger

# Interactive maps (Phase 3B)
rioxarray          # xarray + rasterio integration
rio-tiler          # Efficient tile generation from raster data
mercantile         # XYZ tile coordinate utilities
rio-cogeo          # Cloud Optimized GeoTIFF support
rasterio           # Core raster I/O (dependency of above)
```

**Frontend dependencies (via CDN):**
```html
<!-- Leaflet CSS/JS -->
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<!-- Optional: Mapbox GL JS for WebGL rendering -->
<script src="https://api.mapbox.com/mapbox-gl-js/v3.0.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.0.0/mapbox-gl.css" rel="stylesheet" />
```

---

## Success Metrics

- **Code Quality:** 0 broad `except Exception:` clauses
- **Test Coverage:** >80% line coverage
- **Type Coverage:** 100% of public functions typed
- **Locations:** 8+ configured locations
- **Performance:** <2s average frame load time
- **Reliability:** 99.9% uptime (Fly.io monitoring)

---

## Timeline Suggestion

This plan is organized by priority and dependency. Phases can be worked on incrementally:

- **Phase 1** forms the foundation and should be addressed first
- **Phase 2** ensures changes don't break existing functionality
- **Phase 3** adds user-facing value
- **Phase 4** optimizes for scale
- **Phase 5** helps future contributors

Each section is independent enough to be tackled as separate PRs.
