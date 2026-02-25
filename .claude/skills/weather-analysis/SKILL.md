---
name: weather-analysis
description: Analyze weather forecast data at a location using cross-model, cross-run overlap analysis. Use when the user asks about weather forecasts, snow totals, precipitation, or temperature at a specific location.
argument-hint: [lat] [lon] [variable (optional)]
---

# Weather Forecast Analysis

Analyze forecast data for the location at coordinates `$0` lat, `$1` lon.
Variable focus (if specified): `$2`

## Step 1: Fetch All Available Data

### Current Conditions (ALWAYS DO THIS FIRST)

Pull current conditions from the nearest NWS observation station before anything else. This grounds the entire analysis in reality.

```bash
# Find nearest stations
curl -s -H 'User-Agent: RadarCheck/1.0' 'https://api.weather.gov/points/{lat},{lon}'
# Get station list from the observationStations URL in the response
# Then fetch latest observation
curl -s -H 'User-Agent: RadarCheck/1.0' 'https://api.weather.gov/stations/{STATION_ID}/observations/latest'
```

Key fields: `temperature`, `textDescription` (e.g., "Light Rain", "Snow"), `windSpeed`, `windDirection`, `visibility`, `precipitationLastHour`.

**Why this matters**: Current conditions are the anchor. If it's already snowing but the model says rain until 7 PM, the changeover is ahead of schedule and totals go up. If the forecast says snow but it's 40F and dry, explain the timeline. Always compare what models predicted for RIGHT NOW vs what's actually happening.

### NWS Forecaster Commentary

Fetch the latest Area Forecast Discussion (AFD) and Hazardous Weather Outlook (HWO) from the local NWS office. The AFD contains the forecaster's own analysis, confidence level, and storm narrative -- valuable context beyond raw model data.

```bash
# Find the forecast office from the points endpoint (e.g., PHI for Mount Holly)
# Then fetch the latest AFD and HWO
curl -s -H 'User-Agent: RadarCheck/1.0' 'https://api.weather.gov/products?type=AFD&location={OFFICE}&limit=1'
curl -s -H 'User-Agent: RadarCheck/1.0' 'https://api.weather.gov/products?type=HWO&location={OFFICE}&limit=1'
# Fetch the product text from the returned product ID URL
```

Cross-reference the forecaster's narrative with your model analysis. If the NWS highlights something the models miss (or vice versa), call it out.

### Fetch Model Data

The API endpoint is `/api/timeseries/multirun`. Query it for every model and variable you need.

**API pattern:**
```
http://localhost:5001/api/timeseries/multirun?lat={lat}&lon={lon}&model={model}&variable={var}&days={days}
```

**Models:** `hrrr`, `nam_nest`, `gfs`, `nbm`, `ecmwf_hres`

**Variables by model:**
- `asnow` (snowfall accumulation) — `hrrr`, `nbm` only (not gfs, nam_nest, ecmwf_hres)
- `snod` (snow depth) — `hrrr`, `nam_nest`, `gfs`, `ecmwf_hres` (not nbm)
- `t2m` (temperature) — all models
- `apcp` (total precip) — all models

**CRITICAL: Use `days=2` or `days=3`** for all queries. The default is `days=1` which is too narrow for synoptic models that cycle every 6 hours. With `days=2` you get 3-4 synoptic runs for trend analysis. For extended range queries, use `days=1` (latest run is sufficient).

**Query each model individually** — do NOT use `model=all`. Query each model+variable combo one at a time. This ensures you get data even if some models have no tiles yet.

For precipitation/snow questions, fetch ALL of these:
- `asnow` — HRRR, NBM
- `snod` — HRRR, NAM Nest, GFS, ECMWF
- `t2m` — all models
- `apcp` — all models

For temperature questions, fetch `t2m` and optionally `dpt` (dew point).

**If a query returns empty runs or an error, note it and move on** — don't skip the entire model. Some models may not have tiles built for the latest cycle but will have older runs.

### Geographic Probing

If the forecast signal is ambiguous or you suspect strong local gradients (elevation, coast, rain/snow line), probe nearby points to understand spatial structure:
- Fetch the same variable at 2-4 nearby locations offset by ~0.2-0.5 degrees
- Compare values to see if there's a sharp gradient across the target
- Elevation matters: a point in a valley vs. a ridge 10 miles away can differ by several inches of snow
- This is especially valuable near rain/snow transition zones — the line may be right on top of the location


## Step 2: Read the Data in the Right Order

**DO NOT** just dump numbers. Follow this analytical sequence:

### 2a. Establish the Synoptic Baseline (GFS, NAM, ECMWF)

Start with the **longest-range models** that cover the full event:
- GFS (384h range, 6h cycles)
- NAM (60h range, 6h cycles)
- ECMWF HRES (240h range, 6h cycles)

For each, look at the **run-to-run trend** (e.g., last 3-4 synoptic runs):
- Is the signal strengthening or weakening?
- Is the event timing shifting earlier or later?
- Are the peak values converging or diverging?

**Key**: State the trend direction explicitly. "GFS trending DOWN: 9.3 -> 6.9 -> 5.3 inches" not just "GFS shows 5.3 inches."

### 2b. Check Short-Range Confirmation (HRRR)

HRRR runs hourly with 48h range. Look at the **most recent 3-4 hourly runs**:
- Do they align with the synoptic models' latest runs?
- Where the HRRR valid times overlap with GFS/NAM, do the values agree?

**Critical technique**: Find the overlap window where HRRR's forecast hours and a synoptic model's forecast hours cover the same valid time. If HRRR 18Z fhr=6 and GFS 12Z fhr=12 both verify at 00Z tomorrow and show similar values, that's confirmation.

### 2c. Check NBM (Statistical Blend)

NBM is a blend of all models. Look for:
- **Flips**: A sudden jump between consecutive runs (e.g., 4.7" -> 12.2") signals the underlying model consensus shifted
- **Stability**: If NBM has been consistent across many runs, model consensus is strong
- NBM tends to lag — it smooths out model noise but also delays sharp signals

### 2d. Temperature Cross-Check

Always check T2M for precipitation type questions:
- Surface temps above 33F mean mixed precipitation or rain
- The rain/snow line matters more than total QPF
- Check if temps are trending colder or warmer through the event
- A 1-2 degree shift can flip inches of snow to rain

### 2e. Extended Range Storm Outlook (Day 7-16)

Scan the extended range for upcoming storm signals using the longest-range models:
- **GFS**: out to 384h (16 days)
- **ECMWF HRES**: out to 240h (10 days)

**You MUST query GFS and ECMWF specifically** for extended range — NOT NBM (NBM only goes to 264h/11 days and lacks the physics resolution for extended outlooks).

```
# Extended range queries — use days=1 (latest synoptic run is enough)
curl 'http://localhost:5001/api/timeseries/multirun?lat={lat}&lon={lon}&model=gfs&variable=apcp&days=1'
curl 'http://localhost:5001/api/timeseries/multirun?lat={lat}&lon={lon}&model=gfs&variable=t2m&days=1'
curl 'http://localhost:5001/api/timeseries/multirun?lat={lat}&lon={lon}&model=ecmwf_hres&variable=apcp&days=1'
curl 'http://localhost:5001/api/timeseries/multirun?lat={lat}&lon={lon}&model=ecmwf_hres&variable=t2m&days=1'
```

Look at forecast hours beyond 168 (day 7) in the response `series` arrays:

1. **Precipitation signals**: Any APCP accumulation > 0.25 inches liquid in a 24-hour window
2. **Snow potential**: T2M below freezing during those precipitation periods
3. **Model agreement**: If both GFS and ECMWF show a signal in a similar timeframe, it's worth flagging. Single-model signals beyond day 10 are speculative.

Present as a brief section in the forecast:
> **Extended Outlook (Day 7-16):** GFS and ECMWF both show a potential precipitation event around [date]. With temps near [X]F, this could be [rain/snow/mix]. Worth watching as models converge.

Or if nothing:
> **Extended Outlook:** No significant precipitation signals in the day 7-16 window.

## Step 3: Synthesis

### Cross-Model Agreement Matrix

Build a mental matrix:

| Signal | GFS | NAM | HRRR | NBM | ECMWF |
|--------|-----|-----|------|-----|-------|
| Total  |     |     |      |     |       |
| Trend  |     |     |      |     |       |
| Timing |     |     |      |     |       |

### Embrace Multi-Modal Predictions — Do NOT Average

**Averaging is wrong.** Averaging says "both models are wrong, truth is in the middle." That's rarely the case. Instead, recognize when models are telling you **different stories** and present those scenarios honestly.

If GFS says 5" and HRRR says 12", don't say "8.5 inches." Say:

> **Scenario A (GFS solution, 5"):** The low tracks further south, precipitation shield stays offshore. Trend has been going this direction.
> **Scenario B (HRRR/NBM solution, 12"):** The low tracks closer, full wraparound snow band hits. Short-range models locked onto this.

Then assess which scenario has more support based on model agreement, trends, and overlap analysis.

**When to present a single range vs. scenarios:**
- **Single range**: Models cluster tightly (e.g., 8-11" across all models) — give "8-11 inches, high confidence"
- **Two scenarios**: Clear bimodal split (e.g., GFS at 5" vs HRRR/NBM at 12") — present both with likelihood assessment
- **Never**: Just average divergent models into a meaningless middle number

### Weighting

- **Highest weight**: Where short-range (HRRR) and synoptic (GFS/NAM) runs agree in the overlap window
- **Strong signal**: NBM flip in the same direction as HRRR/synoptic agreement
- **Caution signal**: Divergence between GFS trend and NBM/HRRR (one going up, other going down)
- **Watch**: ECMWF for independent confirmation (different model physics)

### For Snow Forecasts: The Implied Ratio Test

**Always compute implied snow ratios.** This is the single most important diagnostic for catching bad snow forecasts. When models appear to disagree on snow totals, check whether they actually disagree on *precipitation* or just on *snow conversion*.

Compute: `implied_ratio = SNOD_or_ASNOW / APCP`

You already have both SNOD and APCP for every model — no extra fields needed. This ratio *is* the density diagnostic. For ECMWF, `rsn` (snow density) is already baked into SNOD via `sd * 1000 / rsn` in `grib_fetcher.py`. If the implied ratio comes out unrealistic, you know the model's snow density assumption is wrong without needing to see `rsn` directly.

Build a table like this:

| Model | APCP (liquid) | SNOD or ASNOW | Implied Ratio | Physically Reasonable? |
|-------|---------------|---------------|---------------|------------------------|
| GFS   | 2.4"          | 5.3" SNOD     | 2.2:1         | NO — sleet ratio, not snow |
| ECMWF | 1.8"          | 5.3" SNOD     | 2.9:1         | NO — same problem |
| NAM   | 1.6"          | 7.6" SNOD     | 4.8:1         | Marginal |
| HRRR  | 2.0"          | 17.5" ASNOW   | 8.8:1         | YES — normal cold snow |
| NBM   | 1.4"          | 12.2" ASNOW   | 8.7:1         | YES |

**If models agree on QPF (~2") and temps (~28F) but show wildly different snow totals, the disagreement is in snow physics, not the storm.** A 2:1 ratio at 28F is physically impossible for snow — that's a sleet or freezing rain ratio. At 20-30F you expect 10:1 to 15:1. At 15-20F you expect 15:1 to 20:1.

**Why coarse models produce unrealistically low SNOD:**

SNOD is "snow depth on the ground" — not snowfall. Models compute it by running snow physics: accumulation, compaction under its own weight, settling over time, wind packing, partial melt from ground heat, and sublimation. The problems compound in coarse models:

1. **Grid-cell averaging**: GFS runs at ~13km, ECMWF at ~9km. A grid cell averages over mountains, valleys, urban and rural. Snow processes that are hyperlocal (drifting, elevation-dependent accumulation, urban heat) get smeared out. The cell "sees" an average surface that may be warmer or more mixed than any real point.

2. **Parameterized snow physics**: Coarse models can't resolve individual snow bands or convective snow showers. They parameterize snowfall rates from bulk moisture fields. This misses mesoscale banding that can dump 3"/hr in a 20-mile-wide band — HRRR resolves these, GFS cannot.

3. **Aggressive compaction/settling**: The snow pack models in GFS/ECMWF apply time-integrated compaction. A 12-hour storm's snow gets settled as it accumulates. The longer the model timestep and coarser the vertical resolution, the more aggressively this compresses the pack. The result is "depth after 24 hours of settling" not "how much fell."

4. **Ground heat flux**: Coarse models tend to have warmer soil temperatures (grid-cell averaging again). Warmer soil = more basal melt = less depth on the ground even when snowfall was heavy.

5. **ECMWF specifically**: ECMWF outputs `sd` (snow water equivalent) and `rsn` (snow density in kg/m3). We compute physical depth as `sd * 1000 / rsn`. The `rsn` field is model-derived density — if the model's snow physics over-densify the pack, depth comes out too low even with correct water equivalent. Check `grib_fetcher.py` `_fetch_ecmwf_snod()` for the implementation.

**The bottom line**: When temps are solidly cold and APCP broadly agrees across models, trust ASNOW from convective-scale models (HRRR, NBM) over SNOD from coarse models (GFS, ECMWF). SNOD from coarse models tells you depth-on-ground after physics — it's a different question than "how much snow fell."

**ASNOW vs SNOD cheat sheet:**
- ASNOW = accumulated snowfall (what fell). Only HRRR and NBM have it natively.
- SNOD = snow depth on ground (what's there after compaction/melt). All models have it.
- ASNOW > SNOD for the same event. Always.
- For "how much snow are we getting?" → prefer ASNOW
- For "how deep will the snow be tomorrow morning?" → SNOD is the right question, but validate the ratio against temperature
- GFS/NAM have no native ASNOW — you're stuck with SNOD. Flag the ratio and caveat accordingly.
- NBM ASNOW may decode as `unknown` variable name (cfgrib quirk) — data is still valid.

## Step 4: Present the Forecast

Structure your response as:

1. **Bottom line up front**: "Expecting X-Y inches of snow" OR present distinct scenarios if models are bimodal
2. **Confidence level**: High/Medium/Low based on model agreement
3. **Key supporting evidence**: 2-3 bullet points on what drives the range or each scenario
4. **Risk factors**: What could push it higher or lower (geographic gradients, temperature swings, track shifts)
5. **What to watch for**: Specific signals in upcoming data that would resolve uncertainty or shift the forecast. Be concrete:
   - "If GFS 18Z reverses its downtrend and jumps back above 8", that confirms the HRRR/NBM solution"
   - "Watch HRRR runs overnight — if they start dropping below 10", the high-end scenario is dying"
   - "If temps at hour 12 come in above 34F on the next HRRR, expect a rain/snow mix cutting totals"
   - "NBM held at 12" for 3 straight runs — if it stays there through 00Z, high confidence in that number"
   - Tell the user WHEN those runs drop (approx UTC and local) so they know when to check back
6. **Next update to watch**: When the next important model run drops and why it matters

## Step 5: Push Forecast to Writeup Page

After presenting your forecast analysis to the user, push it to the writeup page so it can be viewed and copied at `/writeup`.

Build a Python script to POST cleanly (avoids shell escaping issues with quotes/inches symbols):

```python
python3 -c "
import json, requests
data = {
    'title': '<Location> Forecast',
    'body': '''<2-paragraph summary from Step 4>''',
    'detail': '''<full supporting data with tables, timeline, what to watch>''',
    'location': {'lat': <lat>, 'lon': <lon>, 'name': '<location name>'}
}
r = requests.post('http://localhost:5001/api/writeup', json=data)
print(r.json())
"
```

**Important formatting notes:**
- Use `in` or `inches` instead of the `"` symbol for inches -- the quote character causes JSON escaping issues that break markdown rendering
- Use `--` instead of em dashes for the same reason
- `body` = the 2-paragraph summary (what gets copied via the Copy button)
- `detail` = full supporting analysis with markdown tables: model totals, QPF agreement, implied SLR ratios, hour-by-hour changeover timeline, current conditions table, what to watch for
- The detail section appears in a collapsible "Supporting Data & Analysis" panel below the summary
- Derive the location name from context or coordinates (e.g., "Radnor, PA")
- The server timestamps it automatically

After pushing the writeup, trigger audio generation so the user can listen:

```python
requests.post('http://localhost:5001/api/writeup/audio/generate', json={})
```

This runs asynchronously -- the audio will be available on the writeup page when done. Requires TTS dependencies installed via `install-tts.sh`.

## Common Mistakes to Avoid

- **Don't average divergent models**: If models disagree, present scenarios. Averaging says "everyone is wrong" — that's almost never the right read. Embrace multi-modal distributions.
- **Don't ignore trends**: A model's latest run is less meaningful than its trajectory across 3-4 runs.
- **Don't read GFS ASNOW**: GFS doesn't have native ASNOW. Use SNOD for GFS.
- **Don't confuse SNOD and ASNOW**: SNOD (depth on ground) < ASNOW (total fallen) due to compaction.
- **Don't trust a single outlier**: One model run showing 20" when everything else shows 8" is noise, not signal.
- **Don't ignore NBM flips**: A sudden NBM jump means the statistical blend's inputs shifted — investigate why.
- **Don't forget temperature**: All snow forecasts are conditional on temps staying cold enough.
- **Don't ignore geography**: A single point forecast can miss sharp local gradients. Probe nearby points when the signal is ambiguous or near a rain/snow line.
