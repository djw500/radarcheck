# 003: API authentication

## Status
Accepted

## Context
Public API endpoints should be protected in production environments.

## Decision
Use a shared API key via the `RADARCHECK_API_KEY` environment variable. The Flask decorator checks the key and blocks unauthorized requests.

## Consequences
- Production deployments must manage API secrets.
- Development remains frictionless without the key.
