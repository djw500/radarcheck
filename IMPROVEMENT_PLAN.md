# Radarcheck Improvement Plan

A comprehensive plan to enhance the Radarcheck weather visualization application across code quality, features, performance, reliability, and user experience.

---

## Executive Summary

Radarcheck is a well-structured Flask application for HRRR weather radar visualization. Recent commits have added multi-variable and multi-model support. This plan identifies improvements across 8 key areas organized into 4 phases, prioritized by impact and effort.

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
| 4.1 Parallel Downloads | Medium | Medium | High |
| 4.2 Cache Headers | High | Low | Medium |
| 4.4 Metrics | Medium | Medium | High |

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

# requirements.txt (new)
prometheus-client
flask-limiter
flasgger
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
