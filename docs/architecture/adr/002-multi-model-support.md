# 002: Multi-model support

## Status
Accepted

## Context
Radarcheck now supports HRRR, NAM 3km, and NAM 12km. Each model has different run cadences and forecast horizons.

## Decision
Represent model metadata in `config.py` and allow the cache builder and API to select a model per request.

## Consequences
- API and UI can expose model selection.
- Cache structure grows with each additional model.
- Tests must validate required model metadata fields.
