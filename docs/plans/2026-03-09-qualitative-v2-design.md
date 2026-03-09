# Qualitative Forecast v2 — LLM-Driven Summary

## Goal

Replace the pre-derived hourly labels (sky condition, comfort, precip) with raw model data passed directly to the LLM. The LLM decides what to highlight per hour and produces both structured hourly columns and a meteorologist-style narrative with per-model attribution and trend callouts.

## Architecture

```
Python script (hourly cron)
  -> fetches multirun API for HRRR (latest 2 runs) + GFS (latest run)
  -> fetches trend snapshots from 1h/6h/24h ago
  -> builds compact data payload (~5K tokens)
  -> sends to Gemini Flash with structured output spec
  -> parses JSON response, validates, falls back to rule-based if needed
  -> caches result

Rust server
  -> serves cached JSON (unchanged)

UI
  -> renders LLM-chosen icons, freeform lines per hour, narrative
```

## Data Passed to LLM

| Source | Content | Purpose |
|--------|---------|---------|
| Latest HRRR run | 7 vars × 8 hours | Short-range ground truth |
| Previous HRRR run | 7 vars × 8 hours | Did the latest run just shift? |
| Latest GFS run | 7 vars × 8 hours | Long-range context |
| 1h-ago snapshot | median × 8 hours × 5 vars | Recent trend |
| 6h-ago snapshot | median × 8 hours × 5 vars | Medium trend |
| 24h-ago snapshot | median × 8 hours × 5 vars | Day-over-day trend |

Variables: t2m (°F), dpt (°F), cloud_cover (%), dswrf (W/m²), apcp (in), asnow (in), snod (in).

All values in human-readable units. Hours keyed by local time label ("2pm", "3pm"). Compact format — arrays, not verbose per-point objects.

Estimated input: ~5,000 tokens. At Gemini Flash pricing: ~$0.01/day at hourly generation.

## LLM Output Format

```json
{
  "hours": [
    {
      "time": "2pm",
      "icon": "sun",
      "lines": ["53°F", "Clearing out"],
      "temp": 53
    },
    {
      "time": "6pm",
      "icon": "cloud-rain",
      "lines": ["48°F", "Rain starting", "HRRR only, GFS dry"],
      "temp": 48,
      "precip": "0.1 in"
    }
  ],
  "narrative": "HRRR shows rain arriving by 6pm but GFS keeps it dry through the evening. Compared to 6 hours ago, temperatures have trended 3°F cooler for tonight."
}
```

### Field spec

- `hours`: array of 8 objects, one per forecast hour
- `icon`: one of `sun`, `moon`, `cloud`, `cloud-sun`, `cloud-moon`, `cloud-rain`, `snowflake`, `question`
- `lines`: 1-3 short strings. LLM decides what matters. Must always include temperature. Must include precip if non-zero.
- `temp`: numeric temperature in °F (for potential chart use)
- `precip`: string, only if non-zero
- `narrative`: 2-4 sentences. Meteorologist brief style. Per-model attribution. Trend callouts. Can note things like "storm approaching", "rain forecast disappeared", model disagreements.

### Freeform examples

The LLM can put whatever spin it thinks is important:
- "Start of big storm"
- "Rain forecast disappeared here"
- "HRRR and GFS disagree"
- "Wind picking up"
- "Fog likely"

## Error Handling

- Parse LLM JSON with `json.loads()`, validate `hours` is list of 8
- If parse fails: fall back to current rule-based derivation (keep `derive_*` functions)
- Validate `icon` against allowed set, default to `question` if unknown
- If `narrative` missing: join first hour's lines as fallback text

## Python Changes

- Keep `derive_*` functions as fallback only
- Replace `aggregate_hourly` (all-model median) with targeted fetches: HRRR latest 2 runs, GFS latest run
- Trend snapshot mechanism unchanged, but pass raw per-hour deltas to LLM
- New function to build compact data payload from raw API responses
- New function to parse/validate LLM JSON output
- Include raw prompt + full LLM output (stdout/stderr) in cached result for debugging

## UI Changes

- `renderSummary` reads `hours[].lines` and `hours[].icon` from LLM output
- Each column renders `lines` array as stacked `<div>`s (not fixed sky/comfort/precip slots)
- Narrative replaces current text display
- "Prompt & raw output" expandable stays for iteration

## Files Modified

| File | Change |
|------|--------|
| `scripts/qualitative.py` | Rewrite data fetching, prompt construction, output parsing |
| `templates/index.html` | Update `renderSummary` for freeform `lines[]` + narrative |

Server unchanged — still serves cached JSON file.
