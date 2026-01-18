# Radarcheck API Reference

## Authentication
All API endpoints require the `X-API-Key` header when `RADARCHECK_API_KEY` is set.

## Endpoints

### GET /api/locations
Returns list of available locations with their latest run info.

**Response**
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
Returns available runs for a location.

### GET /api/valid_times/{location_id}/{model_id}/{run_id}/{variable_id}
Returns valid times and frame paths for a run.

### GET /api/center_values/{location_id}/{model_id}
Returns center-point values for each run.

### GET /api/alerts/{location_id}
Returns active NWS alerts for a location.

### GET /api/models
Returns model metadata.

### GET /api/variables
Returns available weather variable metadata.

### GET /metrics
Returns Prometheus metrics for the application.
