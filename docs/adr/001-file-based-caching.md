# 001: File-based caching

## Status
Accepted

## Context
The application generates forecast frames and metadata for each model run. Persisting these outputs allows the Flask app to serve forecasts without recomputation.

## Decision
Use the filesystem for cache storage organized by location, model, and run ID. Metadata is stored as JSON to simplify parsing and validation.

## Consequences
- Simple operational model and easy inspection.
- Requires disk cleanup and retention policies.
- Scaling storage requires additional tooling.
