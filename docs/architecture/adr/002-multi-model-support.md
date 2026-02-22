# 002: Multi-model support

## Status
Accepted

## Context
Radarcheck supports multiple weather models: HRRR, NAM Nest, GFS, NBM, and ECMWF HRES. Each model has different run cadences, forecast horizons, and variable availability.

## Decision
Represent model metadata in `config.py` `MODELS` dict. The scheduler, job worker, and API all key off model ID.

## Consequences
- Per-model workers filter jobs by model ID via `--model` flag.
- Cache structure grows per model: `cache/tiles/<region>/<res>/<model>/<run>/<var>.npz`.
- Model exclusions (e.g., NBM cannot serve `csnow`, `prate`, `snod`) are handled in config.
