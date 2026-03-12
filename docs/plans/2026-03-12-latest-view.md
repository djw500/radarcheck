# Latest Unified Forecast View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Latest" default view showing a unified best-available forecast table — 48h hourly data sourced by priority (HRRR latest > HRRR previous > GFS), plus 5-day daily summaries with min/max/avg aggregation.

**Architecture:** A new `build_latest_table(model_data)` function in `qualitative.py` iterates each hour and picks the highest-priority source with data. Source attribution (e.g. "HRRR 7pm") is shown per row with subtle background tints. Daily rows aggregate GFS extended data (hours 49-168) by day. The result is added to the qualitative JSON as `latest_table` and rendered by a new "Latest" tab in the frontend (default view).

**Tech Stack:** Python (qualitative pipeline), vanilla JS + Tailwind (frontend in Jinja template)

**Key context:**
- `model_data` from `build_model_data()` already contains `hrrr_latest`, `hrrr_previous`, `gfs`, `nbm`, and `gfs_extended` dicts. Each has `init` (ISO string), `hours` (label list), and `data` (var → values list).
- `gfs_extended` has every-6h data from hour 49 to 240 but does NOT currently store ISO timestamps — only labels. We need to also store ISOs for daily grouping.
- Precip vars (`apcp`, `asnow`) come from the API as cumulative totals. Must de-accumulate to per-period increments.
- The existing `VARIABLE_META` and color-coding functions in the frontend can be reused.
- Source display format: `HRRR 7pm` / `GFS 6pm` (model name + init time in Eastern).

---

### Task 1: Store extended ISOs in gfs_extended

**Files:**
- Modify: `scripts/qualitative.py:215-228`

**Step 1: Add isos to gfs_extended dict**

In `scripts/qualitative.py`, find the block that builds `gfs_extended` (around line 215-228). The first variable that populates `gfs_extended` should also store the ISO times. Change:

```python
    # GFS extended outlook: every 6h from hour 48 to hour 240 (days 3-10)
    for var in VARIABLES:
        gfs_runs = extract_latest_runs(all_data[var], "gfs", count=1)
        if gfs_runs:
            values, labels, _ = extract_extended(gfs_runs, hours_ahead + 1, 240, step=6)
            if any(v is not None for v in values):
                if "gfs_extended" not in model_data:
                    model_data["gfs_extended"] = {
                        "init": gfs_runs[0][0],
                        "hours": labels,
                        "data": {},
                        "note": "Every 6h, days 3-10 — for daily outlook buckets"
                    }
                model_data["gfs_extended"]["data"][var] = values
```

To:

```python
    # GFS extended outlook: every 6h from hour 48 to hour 240 (days 3-10)
    for var in VARIABLES:
        gfs_runs = extract_latest_runs(all_data[var], "gfs", count=1)
        if gfs_runs:
            values, labels, ext_isos = extract_extended(gfs_runs, hours_ahead + 1, 240, step=6)
            if any(v is not None for v in values):
                if "gfs_extended" not in model_data:
                    model_data["gfs_extended"] = {
                        "init": gfs_runs[0][0],
                        "hours": labels,
                        "isos": ext_isos,
                        "data": {},
                        "note": "Every 6h, days 3-10 — for daily outlook buckets"
                    }
                model_data["gfs_extended"]["data"][var] = values
```

**Step 2: Run existing tests**

Run: `pytest tests/test_qualitative_raw_hrrr.py -v`
Expected: ALL PASS (no behavioral change)

**Step 3: Commit**

```bash
git add scripts/qualitative.py
git commit -m "refactor(qualitative): store ext_isos in gfs_extended for daily grouping"
```

---

### Task 2: Backend — build_latest_table function

**Files:**
- Modify: `scripts/qualitative.py` (add function after `build_raw_hrrr`)
- Create: `tests/test_latest_table.py`

**Step 1: Write failing tests**

Create `tests/test_latest_table.py`:

```python
"""Tests for build_latest_table — unified best-available forecast."""
import datetime


def _make_model_data():
    """Model data with HRRR latest (partial), HRRR previous, GFS, NBM, GFS extended."""
    return {
        "hrrr_latest": {
            "init": "2026-03-11T23:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [60.0, 58.0, None, None],
                "gust": [15.0, 12.0, None, None],
                "apcp": [0.0, 0.1, None, None],
            },
        },
        "hrrr_previous": {
            "init": "2026-03-11T22:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [59.0, 57.0, 55.0, None],
                "gust": [14.0, 11.0, 10.0, None],
                "apcp": [0.0, 0.15, 0.3, None],
            },
        },
        "gfs": {
            "init": "2026-03-11T18:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "t2m": [58.0, 56.0, 54.0, 52.0],
                "gust": [13.0, 10.0, 9.0, 8.0],
                "apcp": [0.0, 0.2, 0.5, 0.9],
            },
        },
        "nbm": {
            "init": "2026-03-11T22:00:00+00:00",
            "hours": ["1am", "2am", "3am", "4am"],
            "data": {
                "apcp": [0.0, 0.05, 0.12, 0.2],
            },
        },
        "gfs_extended": {
            "init": "2026-03-11T18:00:00+00:00",
            "hours": ["sat 6am", "sat 12pm", "sat 6pm", "sun 12am", "sun 6am", "sun 12pm"],
            "isos": [
                "2026-03-14T11:00:00", "2026-03-14T17:00:00", "2026-03-14T23:00:00",
                "2026-03-15T05:00:00", "2026-03-15T11:00:00", "2026-03-15T17:00:00",
            ],
            "data": {
                "t2m": [35.0, 45.0, 40.0, 30.0, 33.0, 44.0],
                "apcp": [0.1, 0.3, 0.5, 0.5, 0.6, 0.8],
                "gust": [20.0, 15.0, 18.0, 10.0, 12.0, 14.0],
            },
        },
    }


def test_latest_table_priority():
    """Hour 1-2 use HRRR latest, hour 3 uses HRRR previous, hour 4 uses GFS."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    assert len(hourly) == 4

    # Hours 1-2: HRRR latest has data
    assert hourly[0]["source"].startswith("HRRR")
    assert hourly[0]["t2m"] == 60.0
    assert hourly[1]["t2m"] == 58.0

    # Hour 3: HRRR latest is None, falls to previous
    assert hourly[2]["source"].startswith("HRRR")
    assert hourly[2]["t2m"] == 55.0

    # Hour 4: both HRRR runs are None, falls to GFS
    assert hourly[3]["source"].startswith("GFS")
    assert hourly[3]["t2m"] == 52.0


def test_latest_table_source_labels():
    """Source labels should be 'MODEL Xpm' format with Eastern time."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    # HRRR latest init 23:00 UTC = 6pm Eastern
    assert hourly[0]["source"] == "HRRR 6pm"
    # HRRR previous init 22:00 UTC = 5pm Eastern
    assert hourly[2]["source"] == "HRRR 5pm"
    # GFS init 18:00 UTC = 1pm Eastern
    assert hourly[3]["source"] == "GFS 1pm"


def test_latest_table_deaccumulates_precip():
    """Precip should be per-hour increments, resetting at source boundaries."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    # Hour 1: first value from HRRR latest
    assert hourly[0]["apcp"] == 0.0
    # Hour 2: diff within HRRR latest (0.1 - 0.0)
    assert hourly[1]["apcp"] == 0.1
    # Hour 3: first value from HRRR previous (source changed — no diff)
    assert hourly[2]["apcp"] == 0.3
    # Hour 4: first value from GFS (source changed — no diff)
    assert hourly[3]["apcp"] == 0.9


def test_latest_table_nbm_precip():
    """NBM precip should appear as separate nbm_apcp column, de-accumulated."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    assert hourly[0]["nbm_apcp"] == 0.0
    assert hourly[1]["nbm_apcp"] == 0.05
    assert hourly[2]["nbm_apcp"] == 0.07  # 0.12 - 0.05
    assert hourly[3]["nbm_apcp"] == 0.08  # 0.2 - 0.12


def test_latest_table_daily_aggregation():
    """Daily rows should have min/max/avg per variable."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    daily = result["daily"]

    assert len(daily) >= 1

    # First day (Sat) has 3 points: 35, 45, 40
    sat = daily[0]
    assert sat["day"].startswith("Sat")
    assert sat["source"].startswith("GFS")
    assert sat["t2m"]["min"] == 35.0
    assert sat["t2m"]["max"] == 45.0
    assert sat["t2m"]["avg"] == 40.0  # mean

    # Precip: sum of increments (0.1, 0.2, 0.2) = 0.5
    assert sat["apcp"]["avg"] == 0.5


def test_latest_table_no_data():
    """Should return empty hourly/daily if no model data."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table({})
    assert result["hourly"] == []
    assert result["daily"] == []
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latest_table.py -v`
Expected: FAIL with ImportError (function doesn't exist yet)

**Step 3: Implement build_latest_table**

Add this function to `scripts/qualitative.py` after `build_raw_hrrr` (after line 660):

```python
def build_latest_table(model_data):
    """Build unified best-available forecast table with source attribution.

    Priority per hour: hrrr_latest > hrrr_previous > gfs.
    Returns {"hourly": [...], "daily": [...]}.
    """
    if not model_data:
        return {"hourly": [], "daily": []}

    eastern = datetime.timezone(datetime.timedelta(hours=-5))

    def _source_label(display_name, init_iso):
        try:
            t = datetime.datetime.fromisoformat(init_iso).astimezone(eastern)
            return f"{display_name} {t.strftime('%-I%p').lower()}"
        except Exception:
            return display_name

    # Source priority: latest HRRR > previous HRRR > GFS
    source_keys = [
        ("hrrr_latest", "HRRR"),
        ("hrrr_previous", "HRRR"),
        ("gfs", "GFS"),
    ]
    sources = []
    for key, display in source_keys:
        mdata = model_data.get(key)
        if mdata:
            label = _source_label(display, mdata.get("init", ""))
            sources.append((key, label, mdata))

    # Determine hour count from first available source
    n_hours = 0
    hour_labels = []
    for _, _, mdata in sources:
        if mdata.get("hours"):
            hour_labels = mdata["hours"]
            n_hours = len(hour_labels)
            break

    # Collect all variable names across sources
    all_vars = set()
    for _, _, mdata in sources:
        all_vars.update(mdata.get("data", {}).keys())
    all_vars.discard("_stitched")

    # ---- Hourly section (48h) ----
    hourly = []
    for i in range(n_hours):
        entry = {"hour": hour_labels[i], "source": "—"}
        for key, source_label, mdata in sources:
            data = mdata.get("data", {})
            t2m_vals = data.get("t2m", [])
            if i < len(t2m_vals) and t2m_vals[i] is not None:
                entry["source"] = source_label
                for var in all_vars:
                    vals = data.get(var, [])
                    entry[var] = vals[i] if i < len(vals) else None
                break
        hourly.append(entry)

    # De-accumulate precip within each source run
    ACCUM_VARS = ["apcp", "asnow"]
    for var in ACCUM_VARS:
        prev_val = None
        prev_source = None
        for entry in hourly:
            val = entry.get(var)
            if val is None:
                prev_val = None
                prev_source = None
                continue
            source = entry.get("source")
            if prev_val is not None and source == prev_source:
                entry[var] = round(max(0, val - prev_val), 2)
                prev_val = val
            else:
                prev_val = val
                entry[var] = round(val, 2)
            prev_source = source

    # NBM precip overlay (de-accumulated separately)
    nbm = model_data.get("nbm")
    nbm_apcp = list(nbm["data"].get("apcp", [])) if nbm and nbm.get("data") else []
    prev_nbm = None
    for i, entry in enumerate(hourly):
        val = nbm_apcp[i] if i < len(nbm_apcp) else None
        if val is not None and prev_nbm is not None:
            entry["nbm_apcp"] = round(max(0, val - prev_nbm), 2)
        elif val is not None:
            entry["nbm_apcp"] = round(val, 2)
        else:
            entry.setdefault("nbm_apcp", None)
        if val is not None:
            prev_nbm = val

    # ---- Daily section (days 3-7 from gfs_extended) ----
    daily = []
    gfs_ext = model_data.get("gfs_extended")
    if gfs_ext and gfs_ext.get("isos"):
        ext_data = gfs_ext.get("data", {})
        ext_isos = gfs_ext.get("isos", [])
        gfs_source = _source_label("GFS", gfs_ext.get("init", ""))

        # De-accumulate extended precip first
        ext_precip = {}
        for var in ACCUM_VARS:
            raw = list(ext_data.get(var, []))
            increments = []
            prev = None
            for v in raw:
                if v is not None and prev is not None:
                    increments.append(round(max(0, v - prev), 2))
                elif v is not None:
                    increments.append(round(v, 2))
                else:
                    increments.append(None)
                if v is not None:
                    prev = v
            ext_precip[var] = increments

        # Group by Eastern date
        days = {}
        day_order = []
        for idx, iso in enumerate(ext_isos):
            try:
                dt = datetime.datetime.fromisoformat(iso + "+00:00").astimezone(eastern)
                day_key = dt.strftime("%a %b %-d")
            except Exception:
                continue
            if day_key not in days:
                days[day_key] = []
                day_order.append(day_key)
            point = {}
            for var in all_vars:
                if var in ACCUM_VARS:
                    vals = ext_precip.get(var, [])
                else:
                    vals = ext_data.get(var, [])
                point[var] = vals[idx] if idx < len(vals) else None
            days[day_key].append(point)

        for day_key in day_order[:5]:
            points = days[day_key]
            day_entry = {"day": day_key, "source": gfs_source}
            for var in all_vars:
                vals = [p[var] for p in points if p.get(var) is not None]
                if not vals:
                    day_entry[var] = {"min": None, "max": None, "avg": None}
                elif var in ACCUM_VARS:
                    day_entry[var] = {
                        "min": round(min(vals), 2),
                        "max": round(max(vals), 2),
                        "avg": round(sum(vals), 2),
                    }
                else:
                    day_entry[var] = {
                        "min": round(min(vals), 1),
                        "max": round(max(vals), 1),
                        "avg": round(sum(vals) / len(vals), 1),
                    }
            daily.append(day_entry)

    return {"hourly": hourly, "daily": daily}
```

**Step 4: Run tests**

Run: `pytest tests/test_latest_table.py -v`
Expected: ALL PASS (6 tests)

**Step 5: Commit**

```bash
git add scripts/qualitative.py tests/test_latest_table.py
git commit -m "feat(qualitative): add build_latest_table with source priority and daily aggregation"
```

---

### Task 3: Wire build_latest_table into generate_summary

**Files:**
- Modify: `scripts/qualitative.py:720-732` (generate_summary result dict)

**Step 1: Add latest_table to the result**

In `scripts/qualitative.py`, find the block that builds the final `result` dict in `generate_summary()` (around line 720). Change:

```python
    # Build final result
    raw_hrrr = build_raw_hrrr(model_data, nbm_apcp_prev=model_data.get("_nbm_apcp_prev"))

    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "buckets": llm_data.get("buckets", llm_data.get("hours", [])),
        "narrative": llm_data["narrative"],
        "raw_hrrr": raw_hrrr,
        "prompt": prompt,
        "llm_raw": raw_output,
    }
```

To:

```python
    # Build final result
    raw_hrrr = build_raw_hrrr(model_data, nbm_apcp_prev=model_data.get("_nbm_apcp_prev"))
    latest_table = build_latest_table(model_data)

    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "buckets": llm_data.get("buckets", llm_data.get("hours", [])),
        "narrative": llm_data["narrative"],
        "raw_hrrr": raw_hrrr,
        "latest_table": latest_table,
        "prompt": prompt,
        "llm_raw": raw_output,
    }
```

**Step 2: Run all tests**

Run: `pytest tests/test_qualitative_raw_hrrr.py tests/test_latest_table.py -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add scripts/qualitative.py
git commit -m "feat(qualitative): wire build_latest_table into generate_summary output"
```

---

### Task 4: Frontend — Latest tab and table rendering

**Files:**
- Modify: `templates/index.html:105` (add Latest button)
- Modify: `templates/index.html` (add renderLatest function and view container)

**Step 1: Add "Latest" button to variable switcher**

In `templates/index.html`, find the variable switcher buttons (line 105). Add a "Latest" button BEFORE the "Summary" button:

```html
<button data-var="latest" class="var-btn px-3 py-1.5 rounded-full bg-primary text-white shadow-lg shadow-primary/30 whitespace-nowrap transition font-semibold">Latest</button>
<button data-var="summary" class="var-btn px-3 py-1.5 rounded-full text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition whitespace-nowrap font-semibold">Summary</button>
```

And remove the `bg-primary text-white shadow-lg shadow-primary/30` active classes from whichever button currently has them (likely "Snow"). The "Latest" button should be the default active one.

**Step 2: Add latest view container**

Find the summary view container in the HTML. Add a sibling container for the latest view:

```html
<div id="latestView" class="hidden space-y-2">
    <p class="text-sm text-slate-500 dark:text-slate-400" id="latestMeta"></p>
    <div id="latestContent"></div>
</div>
```

**Step 3: Add renderLatest function**

Add this function near `renderSummary` in the JavaScript. It reuses the existing `VARIABLE_META` and color-coding functions from the summary view (these should be moved to outer scope or duplicated):

```javascript
function renderLatest(data) {
    const lt = data.latest_table;
    if (!lt) { document.getElementById('latestContent').innerHTML = '<p class="text-slate-400">No data</p>'; return; }

    // Reuse VARIABLE_META and color functions — these need to be accessible here.
    // Move VARIABLE_META and color helpers to module scope if not already.

    const LATEST_META = {
        t2m:         {label: 'Temp',    unit: '°F',   dec: 0, bg: _tempBg},
        dpt:         {label: 'Dew Pt',  unit: '°F',   dec: 0, bg: v => v >= 70 ? 'rgba(249,115,22,0.2)' : v >= 65 ? 'rgba(234,179,8,0.15)' : null},
        wind_10m:    {label: 'Wind',    unit: 'mph',  dec: 0, bg: _gustBg},
        gust:        {label: 'Gust',    unit: 'mph',  dec: 0, bg: _gustBg},
        cloud_cover: {label: 'Clouds',  unit: '%',    dec: 0, bg: null},
        dswrf:       {label: 'Solar',   unit: 'W/m²', dec: 0, bg: null},
        refc:        {label: 'Refl',    unit: 'dBZ',  dec: 0, bg: _refcBg},
        apcp:        {label: 'Precip',  unit: 'in',   dec: 2, bg: _precipBg},
        nbm_apcp:    {label: 'NBM Pcp', unit: 'in',   dec: 2, bg: _precipBg},
        asnow:       {label: 'Snow',    unit: 'in',   dec: 2, bg: _snowBg},
        snod:        {label: 'Depth',   unit: 'in',   dec: 1, bg: v => v > 0 ? `rgba(147,112,219,${Math.min(v/12,1)*0.3})` : null},
    };

    // Source tint colors (very subtle)
    const SOURCE_TINTS = {
        'HRRR': null,  // primary source — no tint
        'GFS': 'rgba(168,85,247,0.04)',  // barely-there purple
    };
    function getSourceTint(source) {
        if (!source) return null;
        for (const [key, tint] of Object.entries(SOURCE_TINTS)) {
            if (source.startsWith(key)) return tint;
        }
        return null;
    }

    let html = '<div class="bg-slate-50 dark:bg-slate-900/50 rounded-lg px-3 py-2 border border-slate-200 dark:border-slate-700 overflow-x-auto max-h-[70vh] overflow-y-auto">';
    html += '<table class="w-full text-xs">';

    // Header
    html += '<thead class="sticky top-0 bg-slate-50 dark:bg-slate-900 z-10"><tr class="text-slate-400 dark:text-slate-500">';
    html += '<td class="pr-2 py-1 font-medium">Hour</td>';
    html += '<td class="px-2 py-1">Source</td>';
    for (const [, meta] of Object.entries(LATEST_META)) {
        html += `<td class="px-2 py-1 text-right">${meta.label}</td>`;
    }
    html += '</tr></thead><tbody>';

    // Hourly rows
    for (const row of (lt.hourly || [])) {
        const tint = getSourceTint(row.source);
        const rowStyle = tint ? ` style="background:${tint}"` : '';
        html += `<tr class="text-slate-600 dark:text-slate-300 border-t border-slate-200/50 dark:border-slate-700/50"${rowStyle}>`;
        html += `<td class="pr-2 py-0.5 font-medium text-slate-500 dark:text-slate-400 whitespace-nowrap">${row.hour}</td>`;
        html += `<td class="px-2 py-0.5 text-xs text-slate-400 dark:text-slate-500 whitespace-nowrap">${row.source}</td>`;
        for (const [varKey, meta] of Object.entries(LATEST_META)) {
            const val = row[varKey];
            const display = val != null ? val.toFixed(meta.dec) : '—';
            const bg = (val != null && meta.bg) ? meta.bg(val) : null;
            const style = bg ? ` style="background:${bg}"` : '';
            html += `<td class="px-2 py-0.5 text-right font-mono"${style}>${display}</td>`;
        }
        html += '</tr>';
    }

    // Separator
    if (lt.daily && lt.daily.length > 0) {
        const colSpan = Object.keys(LATEST_META).length + 2;
        html += `<tr><td colspan="${colSpan}" class="py-2"><div class="border-t-2 border-slate-300 dark:border-slate-600"></div></td></tr>`;

        // Daily header
        html += '<tr class="text-slate-400 dark:text-slate-500 bg-slate-100 dark:bg-slate-800/50">';
        html += '<td class="pr-2 py-1 font-medium">Day</td>';
        html += '<td class="px-2 py-1">Source</td>';
        for (const [, meta] of Object.entries(LATEST_META)) {
            html += `<td class="px-2 py-1 text-right">${meta.label}</td>`;
        }
        html += '</tr>';

        // Daily rows: 3 sub-rows per day (min/max/avg)
        for (const day of lt.daily) {
            const tint = getSourceTint(day.source);
            const rowStyle = tint ? ` style="background:${tint}"` : '';
            for (const stat of ['min', 'max', 'avg']) {
                const isFirst = stat === 'min';
                const statLabel = stat === 'avg' ? (null) : stat;  // avg row shows the stat differently below
                html += `<tr class="text-slate-600 dark:text-slate-300 border-t border-slate-200/30 dark:border-slate-700/30"${rowStyle}>`;
                // Day column: only show on first sub-row
                html += `<td class="pr-2 py-0.5 font-medium text-slate-500 dark:text-slate-400 whitespace-nowrap">${isFirst ? day.day : ''}</td>`;
                // Source + stat
                html += `<td class="px-2 py-0.5 text-xs text-slate-400 dark:text-slate-500 whitespace-nowrap">${isFirst ? day.source : ''} <span class="text-slate-300 dark:text-slate-600">${stat}</span></td>`;
                for (const [varKey, meta] of Object.entries(LATEST_META)) {
                    const varData = day[varKey];
                    let val = null;
                    if (varData && typeof varData === 'object') {
                        val = varData[stat];
                    }
                    const display = val != null ? val.toFixed(meta.dec) : '—';
                    const bg = (val != null && meta.bg) ? meta.bg(val) : null;
                    const style = bg ? ` style="background:${bg}"` : '';
                    html += `<td class="px-2 py-0.5 text-right font-mono"${style}>${display}</td>`;
                }
                html += '</tr>';
            }
        }
    }

    html += '</tbody>';
    // Units footer
    html += '<tfoot><tr class="text-[10px] text-slate-400 dark:text-slate-600"><td></td><td></td>';
    for (const [, meta] of Object.entries(LATEST_META)) {
        html += `<td class="px-2 text-right">${meta.unit}</td>`;
    }
    html += '</tr></tfoot></table></div>';

    document.getElementById('latestContent').innerHTML = html;

    // Meta text
    if (data.generated_at) {
        const gen = new Date(data.generated_at);
        const ago = Math.round((Date.now() - gen.getTime()) / 60000);
        document.getElementById('latestMeta').textContent = ago < 60 ? `Updated ${ago}m ago` : `Updated ${Math.round(ago/60)}h ago`;
    }
}
```

**Step 4: Hook up view switching**

In the variable button click handler, add the `latest` case. Find the existing handler that shows/hides the summary view vs chart view. Add logic so when `data-var="latest"` is selected:
- Hide chart container, summary view
- Show `#latestView`
- Call `renderLatest(summaryData)` (reusing the cached summary API data)
- Hide period selector (not relevant for latest view)

When any other variable is selected, hide `#latestView`.

**Step 5: Make "Latest" the default on page load**

Find the initialization code that sets the default variable on page load. Change it so `latest` is selected by default instead of the current default (Snow). This means on initial load:
- The `latest` button should have active styling
- `renderLatest()` should be called once summary data loads

**Step 6: Move color helper functions to outer scope**

The color functions (`_refcBg`, `_precipBg`, `_snowBg`, `_gustBg`, `_tempBg`) are currently defined inside `renderSummary`. Move them to the outer scope (module level, above both `renderSummary` and `renderLatest`) so both functions can use them. Update `VARIABLE_META` in `renderSummary` to still reference them.

**Step 7: Sticky Hour/Day column**

The Hour and Day columns (first column) must remain visible when scrolling horizontally. Apply sticky positioning:

- On the header `<td>` for "Hour"/"Day": add `sticky left-0 z-20 bg-slate-50 dark:bg-slate-900`
- On each data row's first `<td>` (hour/day label): add `sticky left-0 z-10 bg-slate-50 dark:bg-slate-900/95`
- The container already has `overflow-x-auto` for horizontal scroll

This means the first column floats in place while the rest of the table scrolls.

**Step 8: Transpose toggle**

Add a small toggle button above the table:

```html
<div class="flex items-center gap-2 mb-2">
    <button id="latestTranspose" class="text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-600 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800">⇄ Transpose</button>
</div>
```

When toggled, re-render the table transposed:
- **Normal (default):** rows=hours, columns=variables (current layout)
- **Transposed:** rows=variables, columns=hours. First column is the variable name (sticky). Header row shows hour labels. Source row at top.

Store the transpose state in a variable (`let latestTransposed = false`). The toggle button calls `renderLatest(data)` again which checks the flag and renders accordingly.

For the transposed layout:
- First row after header: Source labels for each hour
- Then one row per variable, with the variable label in the sticky first column
- Color coding still applies to each cell
- Daily section: same transposition — columns are days, rows are variables, with min/max/avg sub-columns per day

**Step 9: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): add Latest unified forecast view as default tab"
```

---

### Task 5: Regenerate and verify

**Step 1: Restart qualitative and regenerate**

```bash
pkill -f "qualitative.py" || true
cd /workspace/radarcheck
nohup python scripts/qualitative.py --loop > /tmp/qualitative.log 2>&1 &
python scripts/qualitative.py --once
```

**Step 2: Verify API output**

```bash
rtk proxy curl -s 'http://localhost:5001/api/qualitative?lat=40.0&lon=-75.4' | python3 -c "
import sys, json
d = json.load(sys.stdin)
lt = d.get('latest_table', {})
hourly = lt.get('hourly', [])
daily = lt.get('daily', [])
if hourly:
    print(f'Hourly: {len(hourly)} rows')
    print(f'First: {hourly[0][\"hour\"]} source={hourly[0][\"source\"]}')
    sources = set(h['source'] for h in hourly)
    print(f'Sources used: {sources}')
else:
    print('ERROR: no hourly data')
if daily:
    print(f'Daily: {len(daily)} days')
    print(f'First day: {daily[0][\"day\"]} source={daily[0][\"source\"]}')
else:
    print('No daily data')
"
```

**Step 3: Visual test with Stagehand**

Open `http://localhost:5001` in browser. Verify:
- "Latest" tab is default (active on load)
- Table shows hourly rows with Source column
- Source labels are like "HRRR 7pm", "GFS 1pm"
- GFS rows have barely-visible purple tint
- Color coding works on all cells
- Daily section appears below separator with min/max/avg sub-rows
- Daily values are also color-coded
- Switching to Summary/Snow/etc tabs works, switching back to Latest works
