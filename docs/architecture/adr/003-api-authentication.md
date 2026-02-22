# 003: API authentication

## Status
Accepted

## Context
All API endpoints should be protected in production.

## Decision
Use a global `@app.before_request` hook that checks `FLY_API_KEY` environment variable. The key can be provided via `X-API-Key` header or `api_key` query parameter. `/health` and `/metrics` are exempt.

When no key is configured (local dev), all requests are allowed.

## Consequences
- Production deployments must set `FLY_API_KEY` as a Fly.io secret.
- Development remains frictionless without the key.
- All routes are protected by default — no per-route decorators needed.
