# Short-Term Qualitative Forecast — Design

## Goal

Add a "Summary" mode to the UI that shows an 8-hour hourly timeline with derived weather conditions (sky condition, comfort level, precip) plus AI-generated qualitative text with trend indicators comparing to 1h/6h/24h prior forecasts.

## Architecture

```
Python script (hourly cron, like scheduler)
  -> fetches multirun API for 7 vars at lat/lon
  -> aggregates per-hour median across models for next 8h
  -> derives sky condition (cloud+solar) and comfort (temp+dpt)
  -> compares to cached snapshots from 1h/6h/24h ago for trends
  -> pipes summary JSON to `llm -m gemini-3-flash-preview`
  -> saves to cache/qualitative/{lat_lon_grid}.json

Rust server
  -> GET /api/qualitative?lat=X&lon=Y
  -> serves cached JSON

UI
  -> "Summary" tab in variable switcher
  -> 8 hourly columns + AI text + trend badges
```

## Derived Indicators

### Sky Condition (cloud_cover + solar clearness index)

| Cloud Cover | Solar Clearness | Label |
|-------------|----------------|-------|
| < 20% | > 80% | Bright sunny |
| 20-60% | > 60% | Bright cloudy |
| 20-60% | < 60% | Partly cloudy |
| 60-90% | > 40% | Mostly cloudy, some sun |
| 60-90% | < 40% | Overcast |
| > 90% | < 20% | Dark and heavy |

Nighttime: use cloud_cover only (solar is null). Labels become "Clear", "Partly cloudy", "Overcast", etc.

### Comfort Level (temp + dew point)

| Dew Point | Label |
|-----------|-------|
| < 40F | Dry/crisp |
| 40-55F | Comfortable |
| 55-65F | Sticky |
| 65-70F | Muggy |
| > 70F | Oppressive |

Combined with temp for display: "72F - Sticky", "55F - Crisp"

## Hourly Column Display

Each of 8 columns shows (stacked):
- **Hour**: "2pm"
- **Sky icon + label**: cloud-sun icon, "Bright cloudy"
- **Temp + comfort**: "72F - Sticky"
- **Precip**: "Rain 0.1 in" (only shown if > 0)

## Trend Indicators

Compare current median forecast for next 4h to the same future-time window from snapshots at 1h/6h/24h ago.

Thresholds:
- Temp: > 3F change
- Precip: > 0.05 in change
- Cloud: > 15% change
- Solar: > 15% change

Display as badges on AI text: "warmer than 6h ago", "drier than yesterday"

## AI Text Generation

Prompt template:
```
Given this 8-hour forecast for [location]: [JSON summary with derived conditions].
Trends vs 1h/6h/24h ago: [trend deltas].
Write 2-3 sentences. Be conversational, mention specific times if conditions shift.
```

Uses `llm -m gemini-3-flash-preview`. Cost: ~$0.15/month at hourly generation.

## Caching

- Keyed by lat/lon rounded to 0.1 degree grid
- Regenerated hourly by Python script
- Stale data served if generation fails (max age 3h)
- Snapshots retained for 1h/6h/24h comparison

## Data Sources

Reads from existing multirun API — all 7 variables (t2m, dpt, cloud_cover, dswrf, apcp, asnow, snod) across all 5 models. No new tile pipeline work.

## Files to Create/Modify

| File | Change |
|------|--------|
| `scripts/qualitative.py` | NEW: hourly cron script — fetch, aggregate, derive, LLM, cache |
| `rust_worker/crates/server/src/main.rs` | Add GET /api/qualitative endpoint (serve cached JSON) |
| `templates/index.html` | Add "Summary" tab + hourly column renderer |
| `dev-services.sh` | Add qualitative script to cron/scheduler |
| `cache/qualitative/` | Cache directory for generated summaries |
