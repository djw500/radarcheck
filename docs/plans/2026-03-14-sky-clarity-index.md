# Sky Clarity Index — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a derived "Sky" column to the Latest forecast table that shows perceived sunny/cloudy conditions using solar clearness, cloud layer breakdown, and radar reflectivity — matching what consumer weather apps (AccuWeather, MyRadar) display.

**Architecture:** Fetch HRRR cloud layer vars (LCDC/MCDC/HCDC) and visibility (VIS) at low resolution with aggressive retention (Latest-only, not timelines). Compute a `sky_clarity` (0-100, 100=clear) index in `build_latest_table()`: daytime uses existing solar clearness index as primary signal; nighttime uses low+mid cloud as opaque proxy; radar reflectivity overrides for precip. Display as a color-coded column (gray→yellow) next to temperature.

**Tech Stack:** Python (qualitative.py), Rust (config, worker), HTML/JS (index.html)

**Research context:** See `docs/research/hrrr-cloud-vars-full-accounting.md` and `docs/research/nbm-cloud-vars-analysis.md` for variable definitions and model biases.

---

## Background

Our current `cloud_cover` variable (`TCDC:entire atmosphere`) counts thin cirrus the same as thick overcast. On a day with high cirrus, it reads 100% cloud while the sun is fully shining through (DSWRF=100% clearness). AccuWeather and MyRadar both show this as "sunny" because they use opacity-aware metrics.

We already have:
- **Solar clearness index** (0-100%) — computed in Rust via `solar.rs`, served as `dswrf`. Returns `None` at night.
- **Composite reflectivity** (`refc`) — dBZ values, detects active precip.

We need to add:
- **LCDC** (low cloud layer) — opaque clouds (stratus, fog)
- **MCDC** (middle cloud layer) — opaque clouds (altostratus)
- **HCDC** (high cloud layer) — often thin/transparent (cirrus)
- **VIS** (visibility) — fog/haze signal

These are Latest-only variables — aggressive retention to save disk space.

---

### Task 1: Add Cloud Layer Variables to Config

**Files:**
- Modify: `/workspace/radarcheck/config.py` (WEATHER_VARIABLES dict, after `cloud_cover` entry ~line 283)

**Step 1: Add LCDC, MCDC, HCDC, VIS variable definitions**

Add these entries to the `WEATHER_VARIABLES` dict in `config.py`, after the `cloud_cover` entry:

```python
    "lcdc": {
        "display_name": "Low Cloud",
        "units": "%",
        "short_name": "lcdc",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 100,
        "category": "cloud",
        "herbie_search": {
            "default": ":LCDC:low cloud layer",
        },
        "model_exclusions": ["nbm", "ecmwf_hres"],
        "variable_resolution_override": 0.25,
    },
    "mcdc": {
        "display_name": "Mid Cloud",
        "units": "%",
        "short_name": "mcdc",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 100,
        "category": "cloud",
        "herbie_search": {
            "default": ":MCDC:middle cloud layer",
        },
        "model_exclusions": ["nbm", "ecmwf_hres"],
        "variable_resolution_override": 0.25,
    },
    "hcdc": {
        "display_name": "High Cloud",
        "units": "%",
        "short_name": "hcdc",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 100,
        "category": "cloud",
        "herbie_search": {
            "default": ":HCDC:high cloud layer",
        },
        "model_exclusions": ["nbm", "ecmwf_hres"],
        "variable_resolution_override": 0.25,
    },
    "vis": {
        "display_name": "Visibility",
        "units": "m",
        "short_name": "vis",
        "colormap": "viridis",
        "vmin": 0,
        "vmax": 20000,
        "category": "cloud",
        "herbie_search": {
            "default": ":VIS:surface",
        },
        "model_exclusions": ["ecmwf_hres"],
        "variable_resolution_override": 0.25,
    },
```

Key design decisions:
- `variable_resolution_override: 0.25` — coarse resolution to save disk (~10x less than 0.03deg). These are spatially smooth fields, 0.25 is fine for point queries.
- `model_exclusions` — only fetch from HRRR, GFS, NAM-NEST (NBM/ECMWF don't have layer breakdown).
- VIS is available from NBM too (good quality), so only exclude ecmwf_hres.

**Step 2: Add new vars to scheduler's BUILD_VARIABLES_ENV**

Modify `/workspace/radarcheck/scripts/scheduler.py` line 73:

```python
BUILD_VARIABLES_ENV = os.environ.get("TILE_BUILD_VARIABLES", "") or "apcp,asnow,snod,t2m,cloud_cover,dpt,dswrf,wind_10m,gust,refc,lcdc,mcdc,hcdc,vis"
```

**Step 3: Add aggressive retention for cloud layer vars**

These variables are Latest-only — we don't need 12 hourly runs of LCDC for the timeseries charts. Add per-variable retention config to `scheduler.py`.

After `_get_retention()` (line 84), add:

```python
# Variables that only need minimal retention (Latest view only, not timelines).
# Keep 2 synoptic + 3 hourly runs — enough for build_latest_table's per-hour stitching.
LATEST_ONLY_VARS = {"lcdc", "mcdc", "hcdc", "vis"}
LATEST_ONLY_SYNOPTIC = 2
LATEST_ONLY_HOURLY = 3
```

Then modify `_apply_tiered_retention_files()` to use tighter retention for these variables. The function already receives the variable dir path — extract the variable name from it and apply `LATEST_ONLY_*` limits if it's in the set.

**Step 4: Commit**

```bash
git add config.py scripts/scheduler.py
git commit -m "feat: add cloud layer vars (LCDC/MCDC/HCDC/VIS) with aggressive retention"
```

---

### Task 2: Wire Cloud Layers into Latest Table Backend

**Files:**
- Modify: `/workspace/radarcheck/scripts/qualitative.py` (~line 32 VARIABLES list, and `build_latest_table`)

**Step 1: Write the failing test**

Add to `/workspace/radarcheck/tests/test_latest_table.py`:

```python
def test_sky_clarity_index_daytime():
    """Sky clarity during daytime uses solar clearness index."""
    from scripts.qualitative import compute_sky_clarity

    # Daytime: solar available, thin cirrus (high cloud but sun through)
    result = compute_sky_clarity(
        solar=95.0,     # 95% clearness
        cloud_cover=100.0,  # total cloud says 100% (cirrus)
        lcdc=0.0,       # no low cloud
        mcdc=0.0,       # no mid cloud
        hcdc=100.0,     # all high cloud
        refc=-10.0,     # no radar echo
        vis=16000.0,    # good visibility
    )
    assert result >= 85, f"Thin cirrus + high solar should be clear, got {result}"


def test_sky_clarity_index_overcast():
    """Sky clarity for thick overcast blocking sun."""
    from scripts.qualitative import compute_sky_clarity

    result = compute_sky_clarity(
        solar=15.0,     # very little sun
        cloud_cover=100.0,
        lcdc=90.0,      # thick low cloud
        mcdc=80.0,      # thick mid cloud
        hcdc=50.0,
        refc=-5.0,      # no precip
        vis=8000.0,
    )
    assert result <= 25, f"Thick overcast should be cloudy, got {result}"


def test_sky_clarity_index_precip_override():
    """Radar reflectivity > 20 dBZ forces sky clarity to 0 (precip)."""
    from scripts.qualitative import compute_sky_clarity

    result = compute_sky_clarity(
        solar=50.0,
        cloud_cover=100.0,
        lcdc=50.0,
        mcdc=50.0,
        hcdc=50.0,
        refc=35.0,      # active precip
        vis=2000.0,
    )
    assert result == 0, f"Active precip should be 0, got {result}"


def test_sky_clarity_index_nighttime():
    """Nighttime (solar=None) falls back to opaque cloud estimate."""
    from scripts.qualitative import compute_sky_clarity

    # Clear night
    clear = compute_sky_clarity(
        solar=None,
        cloud_cover=5.0,
        lcdc=0.0,
        mcdc=0.0,
        hcdc=5.0,
        refc=-10.0,
        vis=16000.0,
    )
    assert clear >= 80, f"Clear night should be high clarity, got {clear}"

    # Overcast night
    overcast = compute_sky_clarity(
        solar=None,
        cloud_cover=100.0,
        lcdc=90.0,
        mcdc=80.0,
        hcdc=100.0,
        refc=-10.0,
        vis=5000.0,
    )
    assert overcast <= 30, f"Overcast night should be low clarity, got {overcast}"
```

**Step 2: Run tests, verify they fail**

```bash
pytest tests/test_latest_table.py -k sky_clarity -v
```

Expected: FAIL with `ImportError: cannot import name 'compute_sky_clarity'`

**Step 3: Implement `compute_sky_clarity` in qualitative.py**

Add after the VARIABLES list (~line 33):

```python
# Cloud layer variables — fetched at low resolution for Latest view only
CLOUD_LAYER_VARS = ["lcdc", "mcdc", "hcdc", "vis"]

def compute_sky_clarity(solar, cloud_cover, lcdc, mcdc, hcdc, refc, vis):
    """Compute sky clarity index (0-100, 100=clear).

    Combines solar clearness (daytime), cloud layers (all hours), and
    radar reflectivity into a single "is it sunny/cloudy" metric matching
    what consumer weather apps display.

    Args:
        solar: Clearness index 0-100 (None at night)
        cloud_cover: Total cloud fraction 0-100
        lcdc: Low cloud cover 0-100 (None if unavailable)
        mcdc: Mid cloud cover 0-100 (None if unavailable)
        hcdc: High cloud cover 0-100 (None if unavailable)
        refc: Composite reflectivity in dBZ (None if unavailable)
        vis: Visibility in meters (None if unavailable)
    Returns:
        int: 0 (precip/dense fog) to 100 (clear sky)
    """
    # --- Radar override: active precip trumps everything ---
    if refc is not None and refc > 20:
        return 0
    if refc is not None and refc > 5:
        return min(15, int(100 - refc * 3))

    # --- Visibility penalty: fog/mist ---
    vis_penalty = 0
    if vis is not None and vis < 1000:
        return 5  # dense fog
    elif vis is not None and vis < 5000:
        vis_penalty = int((5000 - vis) / 100)  # 0-40 penalty

    # --- Opaque cloud estimate (low + mid, ignoring thin high cloud) ---
    opaque = 0
    if lcdc is not None and mcdc is not None:
        opaque = max(lcdc, mcdc)  # worst of low/mid layers
    elif cloud_cover is not None:
        opaque = cloud_cover  # fallback to total if no layer data

    # --- Daytime: solar clearness is ground truth ---
    if solar is not None:
        clarity = int(solar)
        clarity = max(0, clarity - vis_penalty)
        return max(0, min(100, clarity))

    # --- Nighttime: use opaque cloud estimate ---
    clarity = int(100 - opaque)
    clarity = max(0, clarity - vis_penalty)
    return max(0, min(100, clarity))
```

**Step 4: Run tests, verify they pass**

```bash
pytest tests/test_latest_table.py -k sky_clarity -v
```

Expected: all 4 PASS

**Step 5: Commit**

```bash
git add scripts/qualitative.py tests/test_latest_table.py
git commit -m "feat: add compute_sky_clarity index algorithm"
```

---

### Task 3: Integrate Sky Clarity into build_latest_table

**Files:**
- Modify: `/workspace/radarcheck/scripts/qualitative.py` (VARIABLES list, `build_model_data`, `build_latest_table`)

**Step 1: Add cloud layer vars to VARIABLES and build_model_data**

In `qualitative.py`:

1. Add to the `VARIABLES` list (line 32):
```python
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod", "wind_10m", "gust", "refc"]
```
becomes:
```python
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod", "wind_10m", "gust", "refc"]
CLOUD_DETAIL_VARS = ["lcdc", "mcdc", "hcdc", "vis"]
ALL_LATEST_VARS = VARIABLES + CLOUD_DETAIL_VARS
```

2. In `build_model_data()`, after fetching VARIABLES (~line 132), also fetch cloud detail vars:
```python
    # Fetch cloud layer detail vars (low resolution, Latest-only)
    for var in CLOUD_DETAIL_VARS:
        all_data[var] = fetch_multirun(lat, lon, var, model="all", days=2)
```

**Step 2: Add sky_clarity computation to build_latest_table**

In `build_latest_table()`, after the main per-hour selection loop and de-accumulation, add a sky clarity computation pass. For each hourly entry:

```python
    # ---- Compute sky clarity index per hour ----
    for entry in hourly:
        entry["sky"] = compute_sky_clarity(
            solar=entry.get("dswrf"),
            cloud_cover=entry.get("cloud_cover"),
            lcdc=entry.get("lcdc"),
            mcdc=entry.get("mcdc"),
            hcdc=entry.get("hcdc"),
            refc=entry.get("refc"),
            vis=entry.get("vis"),
        )
```

Also collect cloud detail vars in the per-hour per-variable loop. Currently the loop iterates `VARIABLES` — it should also iterate `CLOUD_DETAIL_VARS` from `all_data`:

In the HRRR run collection section, scan `ALL_LATEST_VARS` instead of `VARIABLES` for run discovery and per-variable data extraction. Same for GFS fallback.

**Step 3: Write integration test**

Add to `tests/test_latest_table.py`:

```python
def test_latest_table_has_sky_clarity():
    """Latest table hourly rows should include sky clarity index."""
    from scripts.qualitative import build_latest_table

    result = build_latest_table(_make_model_data())
    hourly = result["hourly"]

    # All rows should have a 'sky' key
    for row in hourly:
        assert "sky" in row, f"Row {row['hour']} missing 'sky' key"
        assert 0 <= row["sky"] <= 100, f"Sky clarity out of range: {row['sky']}"
```

**Step 4: Run all tests**

```bash
pytest tests/test_latest_table.py -v
```

Expected: all pass (old tests + new sky clarity test)

**Step 5: Commit**

```bash
git add scripts/qualitative.py tests/test_latest_table.py
git commit -m "feat(latest): integrate sky clarity index into build_latest_table"
```

---

### Task 4: Frontend — Add Sky Column to Latest Table

**Files:**
- Modify: `/workspace/radarcheck/templates/index.html`

**Step 1: Add sky to VARIABLE_META**

Find the `VARIABLE_META` object used by `renderLatest` (~line 1030). It currently doesn't include `sky`. We need to add it as the FIRST entry so it appears right after Hour/Source, next to temperature:

Actually, `sky` is a derived field not in `VARIABLE_META`. We should render it specially — as a color-coded cell between Source and the first variable column (t2m).

In `renderLatest()`, for **both normal and transposed layouts**:

**Normal layout (rows = hours):**
- Add "Sky" column header after "Source"
- For each hourly row, render `row.sky` as a colored cell:
  - Background: gradient from `rgba(100,116,139, 0.4)` (gray, cloudy) at 0 to `rgba(250,204,21, 0.4)` (yellow, sunny) at 100
  - Text: sky value + "%" or a weather symbol

```javascript
// Sky clarity color: gray (cloudy) → yellow (sunny)
function skyBg(val) {
    if (val == null) return null;
    if (val === 0) return 'rgba(100,116,139,0.5)';  // precip gray
    const t = val / 100;
    const r = Math.round(100 + t * 150);  // 100→250
    const g = Math.round(116 + t * 88);   // 116→204
    const b = Math.round(139 - t * 118);  // 139→21
    return `rgba(${r},${g},${b},0.35)`;
}

function skyLabel(val) {
    if (val == null) return '\u2014';
    if (val === 0) return '🌧';
    if (val <= 15) return '🌧';
    if (val <= 30) return '☁️';
    if (val <= 60) return '⛅';
    if (val <= 85) return '🌤';
    return '☀️';
}
```

- Render cell: `skyLabel(row.sky)` with `skyBg(row.sky)` background

**Transposed layout (rows = variables):**
- Add a "Sky" row at the top (before other variable rows)
- For each column (hour), render `row.sky` with same coloring

**Step 2: Verify visually**

Load the page, switch to Latest tab, confirm:
- Sky column appears between Source and Temp
- Clear hours show ☀️ with yellow tint
- Overcast hours show ☁️ with gray tint
- Precip hours show 🌧

**Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat(ui): add sky clarity column to Latest table with emoji icons"
```

---

### Task 5: Rebuild Rust Worker and Verify End-to-End

**Files:**
- Build: `rust_worker/` (cargo build)
- Restart: workers + server

**Step 1: Rebuild Rust**

The Rust worker and server need to serve the new variables via the multirun endpoint. No Rust code changes needed — the tile reading is variable-agnostic. But we need to rebuild to pick up any pending changes.

```bash
export CMAKE_GENERATOR="Unix Makefiles" LIBCLANG_PATH="/usr/lib/llvm-19/lib" PATH="$HOME/.cargo/bin:$PATH"
cargo build --release --manifest-path rust_worker/Cargo.toml
```

**Step 2: Restart workers**

Kill and restart all workers so the scheduler can enqueue LCDC/MCDC/HCDC/VIS jobs:

```bash
# Kill existing workers (bash loops will restart with new binary)
pkill -f radarcheck-worker
pkill -f radarcheck-server
sleep 2
# Start server
nohup rust_worker/target/release/radarcheck-server --port 5001 --app-root . --db-path cache/jobs.db --tiles-dir cache/tiles --cache-dir cache > /tmp/radarcheck-server.log 2>&1 &
# Start workers
for model in hrrr gfs nbm nam_nest ecmwf_hres; do
    nohup bash -c "while true; do rust_worker/target/release/radarcheck-worker --model $model --poll-interval 10 --max-jobs 50 --db-path cache/jobs.db --tiles-dir cache/tiles 2>&1; sleep 2; done" > /tmp/radarcheck-worker-$model.log 2>&1 &
done
```

**Step 3: Force scheduler run to enqueue new variable jobs**

```bash
python3 scripts/scheduler.py --once
```

**Step 4: Wait for tiles to populate, then verify**

```bash
# Check if LCDC tiles are being created
ls cache/tiles/ne/0.250deg/hrrr/lcdc/ 2>/dev/null
# Test the API returns data
python3 -c "
from scripts.qualitative import build_model_data, build_latest_table
md, hl, hi, _, all_data = build_model_data(40.0, -75.4)
result = build_latest_table(md, all_data, hl, hi)
for row in result['hourly'][:8]:
    print(f'{row[\"hour\"]:12s} sky={row.get(\"sky\",\"?\")} src={row[\"source\"]}')
"
```

**Step 5: Clear latest cache and verify live endpoint**

```bash
rm -f cache/latest/*.json
curl -s 'http://localhost:5001/api/latest?lat=40.0&lon=-75.4' | python3 -m json.tool | head -20
```

**Step 6: Commit any adjustments**

```bash
git add -A && git commit -m "chore: verify sky clarity end-to-end"
```

---

### Task 6: Handle Missing Cloud Layer Data Gracefully

**Files:**
- Modify: `/workspace/radarcheck/scripts/qualitative.py` (`compute_sky_clarity`)

Cloud layer tiles won't exist for the first few hours after deployment. The algorithm must degrade gracefully:

**Step 1: Write test for missing data**

```python
def test_sky_clarity_all_none():
    """Sky clarity with no data at all returns reasonable default."""
    from scripts.qualitative import compute_sky_clarity

    result = compute_sky_clarity(
        solar=None, cloud_cover=None, lcdc=None, mcdc=None,
        hcdc=None, refc=None, vis=None,
    )
    assert result is None or (0 <= result <= 100)


def test_sky_clarity_solar_only():
    """With only solar available, still produces a good result."""
    from scripts.qualitative import compute_sky_clarity

    result = compute_sky_clarity(
        solar=90.0, cloud_cover=None, lcdc=None, mcdc=None,
        hcdc=None, refc=None, vis=None,
    )
    assert result == 90
```

**Step 2: Update compute_sky_clarity to handle all-None gracefully**

Add early return:
```python
    # If no data at all, return None
    if solar is None and cloud_cover is None and lcdc is None:
        return None
```

**Step 3: Run tests, commit**

```bash
pytest tests/test_latest_table.py -v
git add scripts/qualitative.py tests/test_latest_table.py
git commit -m "fix: handle missing cloud layer data in sky clarity"
```

---

## Summary of Changes

| Component | What changes | Disk impact |
|-----------|-------------|-------------|
| `config.py` | Add LCDC, MCDC, HCDC, VIS variable definitions | None |
| `scheduler.py` | Add vars to build list, aggressive retention (2 syn + 3 hr) | ~200MB at 0.25deg |
| `qualitative.py` | `compute_sky_clarity()` + wire into `build_latest_table` | None |
| `index.html` | Sky column with emoji icons + color gradient | None |
| `test_latest_table.py` | 6 new tests for sky clarity | None |

Estimated disk: 4 new vars × 0.25deg × ~5 runs ≈ 200MB (vs current 4.6GB total — minimal impact).
