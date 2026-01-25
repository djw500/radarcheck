# Findings — GFS Missing Hours and ASNOW Derivation (2026-01-25)

## Summary
- New GFS tile runs have fewer forecast hours than older runs. Two factors explain this:
  1) A deployment env cap now limits GFS tiles to 72h on Fly (policy, not code).
  2) The downloader added a short‑circuit optimization but GFS lacked an explicit forecast schedule, causing valid 6‑hourly hours beyond 240h to be skipped.
- ASNOW derivation logic did not regress; behavior remains consistent and conservative.

## Impact
- GFS: Missing later hours past hourly/3‑hourly ranges. In prod, additional reduction to 72h due to env cap.
- ASNOW: No functional break, but fewer GFS hours can truncate derived snowfall timelines when using GFS tiles.

## Investigation
- Reviewed conductor docs and recent commits for the last 48h.
- Traced hour generation: cache_builder.get_valid_forecast_hours() → download_all_hours_parallel() → build_tiles_for_variable() → save_tiles_npz(hours=...).
- Compared scheduler/env policy (Fly) with local defaults.
- Audited ASNOW derivation paths: _derive_asnow_timeseries_from_tiles(), _accumulate_timeseries(), API paths using accumulation logic.

## Root Causes
- Policy: fly.toml sets TILE_BUILD_MAX_HOURS_GFS="72" (commit e14d5b1), reducing built hours in production to save disk.
- Logic gap: download_all_hours_parallel() introduced a “short‑circuit after 3 consecutive misses” mechanism, which depends on model forecast cadence being defined. GFS had no forecast_hour_schedule in config; the downloader attempted f241,f242,f243 (404) and aborted before reaching valid 6‑hourly hours like f246, f252, leading to missing hours even when max_hours allowed more.

## Remediation
- Added GFS forecast hour schedule to config to match NOMADS 0.25° cadence:
  - 3‑hourly 3–240; 6‑hourly 246–384.
- Added tests to lock forecast hour schedules and catch regressions (GFS and a reference check for NBM):
  - tests/test_forecast_hours_schedule.py

## Validation
- Unit tests ensure get_valid_forecast_hours('gfs', ...) returns expected sets (excludes 241–245, includes 246).
- Manual sanity: After next build, GFS tiles should include 246, 252, ... when max_hours permits (dev). In prod, still capped at 72h unless env is adjusted.

## ASNOW Derivation Review
- _derive_asnow_timeseries_from_tiles():
  - APCP increments handle resets; NaNs forward‑fill; noise floor <1e‑3 zeroed.
  - CSNOW gates (fractional or binary) and T2M veto (≥33°F) with conservative SLR.
  - Optional SNOD capping remains disabled to avoid non‑monotonic totals.
- API endpoints also enforce monotonic accumulation via _accumulate_timeseries for accumulation vars.
- Conclusion: No regression found in ASNOW computation.

## Recommendations
- Keep the GFS schedule in config; extend similar explicit schedules where applicable (e.g., NAM 12km) to prevent future short‑circuit gaps.
- Decide desired GFS horizon per environment:
  - Dev/local: raise TILE_BUILD_MAX_HOURS_GFS (e.g., 240 or 384) for full timelines.
  - Prod (Fly): adjust TILE_BUILD_MAX_HOURS_GFS if more hours are required; current is 72 for storage constraints.
- Consider adding a small integration test that asserts GFS tiles include 246 when max_hours ≥ 246 (behind a mock downloader).

## Notes on Recent Non‑ASNOW Changes
- Region tiles now support means‑only persistence; does not affect hours.
- Tile loader has nearest‑neighbor fallback for sparse grids; affects values, not hours.
- Status endpoints and scheduler reporting added; no impact on hour generation.

## Appendix
- Changed files:
  - config.py — add MODELS["gfs"]["forecast_hour_schedule"].
  - tests/test_forecast_hours_schedule.py — new tests.
- Related commits (last ~2 days):
  - e14d5b1 feat(deploy): aggressive cache cleanup; Fly env caps including TILE_BUILD_MAX_HOURS_GFS=72.
  - 92c7b51/311fa40 scheduler/tiles audit and retention (not directly changing hour selection but affects availability/cleanup).

