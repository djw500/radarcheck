Title: Snow Accumulation View – Bug + Fix (Note to Gemini)

Context
- User reported extreme overestimation in the snow accumulation view (e.g., 9" expected vs ~40" shown).
- The /snow page pulls data from `/api/timeseries/multirun?variable=asnow` and plots accumulated snowfall per model/run.

Root Causes
- GRIB stepType ambiguity: Our GRIB open default favored `stepType=instant` on ambiguity. For accumulated variables (APCP/ASNOW), this can corrupt the baseline (treating accumulated fields as instant or vice‑versa), inflating totals.
- Naive snowfall derivation: For models without native ASNOW, we computed `asnow ≈ apcp * 10` gated by CSNOW, which:
  - Didn’t always enforce accumulation (earlier code returned per‑step values directly in one path).
  - Overcounted in marginal temp regimes (near freezing) and mixed precip.

Changes Applied
1) GRIB loading fixes (tiles.py)
   - `open_dataset_robust(path, preferred_filter=None)` now accepts a preferred filter.
   - For accumulation variables (`variable_config["is_accumulation"]`), prefer `{'stepType': 'accum'}` when opening.
   - For PRATE, prefer `{'stepType': 'instant'}`.
   - Effect: APCP/ASNOW tiles are built from the correct step type.

2) Conservative derived snowfall (app.py)
   - New helper: `_derive_asnow_timeseries_from_tiles(...)` used by `/api/timeseries/multirun`.
   - Requirements and logic:
     - Require CSNOW to derive snowfall; otherwise skip derivation (prevents “all cold precip = snow”).
     - Load APCP, detect cumulative vs incremental; compute non‑negative per‑step deltas; drop tiny liquid noise (<0.001 in water).
     - Load T2M if available; apply a conservative SLR by temperature and veto warm periods:
       - ≥33°F → 0:1 (rain), 31–33°F → 6:1, 28–31°F → 8:1, 22–28°F → 10:1, <22°F → 12:1 (cap).
     - Accumulate over time (cumsum) to return true accumulated snowfall.
     - If SNOD is available, apply a soft cap: derived snowfall ≤ 1.5× (snow depth change during run) to avoid runaway totals due to compaction vs SWE.
   - Also applied temperature‑aware gating to `/api/table/multimodel`’s naïve per‑row derivation (kept simple but conservative).

Files touched
- `tiles.py`
  - `open_dataset_robust` (new parameter + fallback order uses accum for accumulations).
  - `build_tiles_for_variable` (passes preferred filter based on variable).
- `app.py`
  - `_derive_asnow_timeseries_from_tiles` (new).
  - `/api/timeseries/multirun` now uses the new helper for `asnow` when native tiles are missing.
  - `/api/table/multimodel` post‑merge derivation uses temp‑aware SLR for per‑row fallback.

How to Reproduce and Verify
1) Build fresh tiles (so stepType preferences apply):
   - HRRR: `python build_tiles.py --region ne --model hrrr --variables apcp csnow t2m snod asnow --max-hours 24`
   - NAM Nest: `python build_tiles.py --region ne --model nam_nest --variables apcp csnow t2m snod --max-hours 24`
   - GFS (optional): `python build_tiles.py --region ne --model gfs --variables apcp csnow t2m snod --max-hours 48`
2) Start server: `python app.py -p 5001`
3) Check:
   - JSON: `/api/timeseries/multirun?lat=40.05&lon=-75.4&model=all&variable=asnow&days=1`
   - Chart: `/snow` (same location). HRRR ASNOW (native) should be the reference line; derived lines should be in the same ballpark, not 4–5×.

Why this fixes the 40" issue
- Using `stepType=accum` for APCP removes a major source of inflation.
- Requiring CSNOW and vetoing ≥33°F removes mixed/rain contamination.
- Lower SLR near freezing (6–8:1) reduces wet‑snow overcount.
- Optional SNOD cap guards against remaining outliers.

Open Risks / Follow‑ups
- Alignment: ensure all aligned series strictly follow `common_hours` ordering when SNOD is used (currently using boolean masks; should switch to indexed alignment to be airtight).
- Cross‑variable availability:
  - If CSNOW is sparse/missing at a point, derivation now returns None (no derived snowfall).
  - Consider adding WEASD (SWE) and a SWE→snow conversion as a secondary path when CSNOW is absent.
- UI clarity: label derived lines as “Derived Snow (APCP×SLR)” in `/snow` legend when not native ASNOW.
- Tests: add unit tests for `_derive_asnow_timeseries_from_tiles` with synthetic APCP/CSNOW/T2M cases (cumulative vs incremental APCP; warm veto; SLR bands; SNOD cap).

Action Items for Gemini
- Verify stepType behavior by inspecting GRIB metadata on a few APCP files to confirm `stepType=accum` is being used in tiles.
- Add indexed alignment for SNOD in the derivation helper (map `common_hours` via search to snod hours rather than boolean mask).
- Add a config flag to disable SNOD cap if needed for certain regions.
- Consider exposing a strict mode: only chart native ASNOW; disable derivation unless explicitly requested.

References in code
- `tiles.py`: `open_dataset_robust`, `build_tiles_for_variable`.
- `app.py`: `_derive_asnow_timeseries_from_tiles`, `/api/timeseries/multirun`, `/api/table/multimodel`.

