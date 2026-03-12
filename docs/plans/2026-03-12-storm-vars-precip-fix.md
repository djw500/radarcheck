# Storm Intensity Variables + Precip Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add wind speed, gusts, and radar reflectivity to the qualitative pipeline and raw HRRR table; fix the precip accumulation/rounding bug.

**Architecture:** The variables `wind_10m`, `gust`, and `refc` already exist in `config.py` with full GRIB search strings and tile pipeline support. The work is: (1) wire them into the qualitative fetch loop, (2) de-accumulate precip in `build_raw_hrrr` so the table shows per-hour amounts instead of cumulative totals, (3) fix over-rounding (precip needs 2 decimals), and (4) add columns to the frontend tables.

**Tech Stack:** Python (qualitative pipeline), vanilla JS + Tailwind (frontend in Jinja template)

**Key context:**
- `refc` has `model_exclusions: ["gfs", "nbm", "ecmwf_hres"]` → only HRRR/NAM tiles exist. The API returns no data for excluded models, so `extract_latest_runs` gracefully returns `[]`.
- `wind_10m` and `gust` have no exclusions → available on all models.
- Precip (`apcp`) is marked `is_accumulation: True`. The API's `_accumulate_timeseries()` converts raw GRIB values to monotonic cumulative totals. The main chart (index.html:568) diffs these to per-hour rates, but `build_raw_hrrr` passes cumulative values through raw — causing "spurious" precip in the table, especially across stitch boundaries.

---

### Task 1: Fix precip de-accumulation and rounding

**Files:**
- Modify: `scripts/qualitative.py:91` (rounding), `scripts/qualitative.py:618-631` (build_raw_hrrr)
- Modify: `tests/test_qualitative_raw_hrrr.py` (add/update tests)

**Step 1: Write failing tests for de-accumulation**

Add to `tests/test_qualitative_raw_hrrr.py`:

```python
def test_build_raw_hrrr_deaccumulates_precip():
    """apcp/asnow should be per-hour increments, not cumulative totals."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [53.2, 51.8, 49.5, 48.0],
                "apcp": [0.0, 0.1, 0.3, 0.5],
                "asnow": [0.0, 0.0, 0.1, 0.3],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # Per-hour increments, not cumulative
    assert raw["hours"][0]["apcp"] == 0.0
    assert raw["hours"][1]["apcp"] == 0.1
    assert raw["hours"][2]["apcp"] == 0.2
    assert raw["hours"][3]["apcp"] == 0.2

    assert raw["hours"][2]["asnow"] == 0.1
    assert raw["hours"][3]["asnow"] == 0.2


def test_build_raw_hrrr_deaccum_resets_at_stitch_boundary():
    """De-accumulation must NOT diff across the latest→stitched boundary."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [53.2, 51.8, None, None],
                "apcp": [0.0, 0.1, None, None],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T12:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm", "8pm"],
            "data": {
                "t2m": [52.0, 50.5, 48.0, 46.0],
                "apcp": [0.0, 0.2, 0.5, 0.9],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # Latest hours: de-accumulated normally
    assert raw["hours"][0]["apcp"] == 0.0
    assert raw["hours"][1]["apcp"] == 0.1

    # Stitched hours: de-accumulated within their own sequence (NOT diffed against latest)
    assert raw["hours"][2]["apcp"] == 0.5   # First stitched value: keep as-is
    assert raw["hours"][3]["apcp"] == 0.4   # 0.9 - 0.5 = 0.4


def test_build_raw_hrrr_precip_2_decimals():
    """Precip should preserve 2 decimal places, not round to 1."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm"],
            "data": {
                "t2m": [53.2, 51.8],
                "apcp": [0.0, 0.04],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    # 0.04 should NOT round to 0.0
    assert raw["hours"][1]["apcp"] == 0.04
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_qualitative_raw_hrrr.py -v`
Expected: 3 new tests FAIL (apcp values are still cumulative/rounded)

**Step 3: Fix rounding in extract_latest_runs**

In `scripts/qualitative.py:91`, change:
```python
# OLD
values_by_time[vt] = round(v, 1)
# NEW
values_by_time[vt] = round(v, 2)
```

**Step 4: Add de-accumulation to build_raw_hrrr**

In `scripts/qualitative.py`, after the `per_hour` list is built (after line 631), add de-accumulation:

```python
    # De-accumulate precipitation variables (cumulative → per-hour)
    ACCUM_VARS = ["apcp", "asnow", "nbm_apcp"]
    for var in ACCUM_VARS:
        prev_val = None
        prev_stitched = None
        for entry in per_hour:
            val = entry.get(var)
            if val is None:
                prev_val = None
                prev_stitched = None
                continue
            is_stitched = entry.get("_stitched", False)
            # Reset at stitch boundary (don't diff across different runs)
            if prev_val is not None and is_stitched == prev_stitched:
                increment = round(max(0, val - prev_val), 2)
                prev_val = val
                entry[var] = increment
            else:
                prev_val = val
                entry[var] = round(val, 2)
            prev_stitched = is_stitched
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_qualitative_raw_hrrr.py -v`
Expected: ALL tests PASS (including the 3 existing ones — check that `test_build_raw_hrrr_from_model_data` still passes since apcp [0.0, 0.0, 0.01] de-accumulates to [0.0, 0.0, 0.01])

**Step 6: Commit**

```bash
git add scripts/qualitative.py tests/test_qualitative_raw_hrrr.py
git commit -m "fix(qualitative): de-accumulate precip in raw HRRR table, preserve 2 decimal places"
```

---

### Task 2: Add wind_10m, gust, refc to qualitative pipeline

**Files:**
- Modify: `scripts/qualitative.py:32` (VARIABLES list)
- Modify: `scripts/qualitative.py:388` (variable key in prompt)
- Modify: `scripts/qualitative.py:466` (prompt note about accum — update to mention new vars)
- Modify: `tests/test_qualitative_raw_hrrr.py` (add wind/refc to test fixtures)

**Step 1: Write failing test**

Add to `tests/test_qualitative_raw_hrrr.py`:

```python
def test_build_raw_hrrr_includes_storm_vars():
    """Wind, gust, and refc should appear in raw HRRR output."""
    from scripts.qualitative import build_raw_hrrr

    model_data = {
        "hrrr_latest": {
            "init": "2026-03-11T15:00:00+00:00",
            "hours": ["5pm", "6pm", "7pm"],
            "data": {
                "t2m": [53.2, 51.8, 49.5],
                "wind_10m": [8.0, 12.0, 15.0],
                "gust": [15.0, 22.0, 28.0],
                "refc": [0.0, 25.0, 40.0],
                "apcp": [0.0, 0.0, 0.01],
            },
        },
    }
    raw = build_raw_hrrr(model_data)

    assert raw["hours"][0]["wind_10m"] == 8.0
    assert raw["hours"][1]["gust"] == 22.0
    assert raw["hours"][2]["refc"] == 40.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_qualitative_raw_hrrr.py::test_build_raw_hrrr_includes_storm_vars -v`
Expected: PASS actually — `build_raw_hrrr` already passes through all vars in `data`. This test just confirms the vars propagate correctly. If it passes immediately, that's fine — it's a regression guard for the pipeline change.

**Step 3: Add variables to VARIABLES list**

In `scripts/qualitative.py:32`, change:
```python
# OLD
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod"]
# NEW
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod", "wind_10m", "gust", "refc"]
```

**Step 4: Update variable key in LLM prompt**

In `scripts/qualitative.py:388`, change:
```python
# OLD
sections.append("Variable key: t2m=temp(°F), dpt=dewpoint(°F), cloud_cover=clouds(%), dswrf=solar(W/m²), apcp=rain(in), asnow=snow(in), snod=snow_depth(in)")
# NEW
sections.append("Variable key: t2m=temp(°F), dpt=dewpoint(°F), cloud_cover=clouds(%), dswrf=solar(W/m²), apcp=rain(in), asnow=snow(in), snod=snow_depth(in), wind_10m=wind(mph), gust=gusts(mph), refc=radar_reflectivity(dBZ, HRRR-only)")
```

**Step 5: Update prompt accumulation note**

In `scripts/qualitative.py:466`, change:
```python
# OLD
  - Note: apcp values are CUMULATIVE from forecast start. To get period amounts, subtract consecutive values.
# NEW
  - Note: apcp values are CUMULATIVE from forecast start. To get period amounts, subtract consecutive values.
  - Wind/gust in mph. refc (composite reflectivity) is dBZ — 0=clear, 20-35=light rain, 35-50=moderate, 50+=severe. refc is HRRR-only (not available in GFS/NBM/ECMWF).
```

**Step 6: Run all tests**

Run: `pytest tests/test_qualitative_raw_hrrr.py -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add scripts/qualitative.py tests/test_qualitative_raw_hrrr.py
git commit -m "feat(qualitative): add wind_10m, gust, refc to qualitative pipeline"
```

---

### Task 3: Update frontend raw HRRR tables

**Files:**
- Modify: `templates/index.html:1010-1018` (VARIABLE_META dict)
- Modify: `templates/index.html:1056` (full table display formatting)
- Modify: `templates/index.html:1146` (per-bucket table display formatting)

**Step 1: Add new columns to VARIABLE_META**

In `templates/index.html:1010-1019`, change:
```javascript
// OLD
const VARIABLE_META = {
    t2m:         {label: 'Temp',    unit: '°F'},
    dpt:         {label: 'Dew Pt',  unit: '°F'},
    cloud_cover: {label: 'Clouds',  unit: '%'},
    dswrf:       {label: 'Solar',   unit: 'W/m²'},
    apcp:        {label: 'Precip',  unit: 'in'},
    nbm_apcp:    {label: 'NBM Pcp', unit: 'in'},
    asnow:       {label: 'Snow',    unit: 'in'},
    snod:        {label: 'Depth',   unit: 'in'},
};
// NEW
const VARIABLE_META = {
    t2m:         {label: 'Temp',    unit: '°F',   dec: 0},
    dpt:         {label: 'Dew Pt',  unit: '°F',   dec: 0},
    wind_10m:    {label: 'Wind',    unit: 'mph',  dec: 0},
    gust:        {label: 'Gust',    unit: 'mph',  dec: 0},
    cloud_cover: {label: 'Clouds',  unit: '%',    dec: 0},
    dswrf:       {label: 'Solar',   unit: 'W/m²', dec: 0},
    refc:        {label: 'Refl',    unit: 'dBZ',  dec: 0},
    apcp:        {label: 'Precip',  unit: 'in',   dec: 2},
    nbm_apcp:    {label: 'NBM Pcp', unit: 'in',   dec: 2},
    asnow:       {label: 'Snow',    unit: 'in',   dec: 2},
    snod:        {label: 'Depth',   unit: 'in',   dec: 1},
};
```

**Step 2: Update display formatting in full HRRR table**

In `templates/index.html:1054-1057`, change the value formatting to use per-variable decimals:
```javascript
// OLD
for (const [varKey] of Object.entries(VARIABLE_META)) {
    const val = hourData[varKey];
    const display = val != null ? (Number.isInteger(val) ? val : val.toFixed(1)) : '—';
    fullTable += `<td class="px-2 py-0.5 text-right font-mono">${display}</td>`;
}
// NEW
for (const [varKey, meta] of Object.entries(VARIABLE_META)) {
    const val = hourData[varKey];
    const display = val != null ? val.toFixed(meta.dec) : '—';
    fullTable += `<td class="px-2 py-0.5 text-right font-mono">${display}</td>`;
}
```

**Step 3: Update display formatting in per-bucket table**

In `templates/index.html:1144-1147`, same change:
```javascript
// OLD
for (const [varKey, meta] of Object.entries(VARIABLE_META)) {
    const val = hourData[varKey];
    const display = val != null ? (Number.isInteger(val) ? val : val.toFixed(1)) : '—';
    tableHtml += `<td class="px-2 py-0.5 text-right font-mono">${display}</td>`;
}
// NEW
for (const [varKey, meta] of Object.entries(VARIABLE_META)) {
    const val = hourData[varKey];
    const display = val != null ? val.toFixed(meta.dec) : '—';
    tableHtml += `<td class="px-2 py-0.5 text-right font-mono">${display}</td>`;
}
```

**Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): add wind/gust/reflectivity columns to raw HRRR tables, fix precip decimals"
```

---

### Task 4: Restart qualitative + verify

**Step 1: Kill existing qualitative process and restart**

```bash
pkill -f "qualitative.py" || true
cd /workspace/radarcheck
nohup python scripts/qualitative.py --loop > logs/qualitative.log 2>&1 &
echo $! > /tmp/qualitative.pid
```

**Step 2: Force regeneration**

```bash
cd /workspace/radarcheck
python scripts/qualitative.py --once
```

**Step 3: Verify raw HRRR data includes new vars**

```bash
rtk proxy curl -s http://localhost:5001/api/qualitative?lat=40.0\&lon=-75.4 | python3 -c "
import sys, json
d = json.load(sys.stdin)
h = d.get('raw_hrrr', {}).get('hours', [])
if h:
    print('Vars in hour 0:', sorted(h[0].keys()))
    print('Sample:', {k: h[0][k] for k in ['wind_10m','gust','refc','apcp'] if k in h[0]})
else:
    print('ERROR: no raw_hrrr hours')
"
```

Expected output should include `wind_10m`, `gust`, `refc`, and `apcp` with per-hour (not cumulative) values.

**Step 4: Visual test with Stagehand**

Open `http://localhost:5001` in browser, verify:
- Raw HRRR Data table has Wind, Gust, Refl columns
- Precip shows small per-hour values (not cumulative), 2 decimal places
- Per-bucket expanded tables also show new columns

**Step 5: Commit any fixes**

If anything needs adjustment, fix and commit.
