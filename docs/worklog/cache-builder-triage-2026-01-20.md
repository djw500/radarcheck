# Cache Builder Triage — 2026-01-20

This worklog documents issues observed while the cache builder was running locally on 2026-01-20. It summarizes evidence from `logs/cache_builder.log`, likely causes, and proposed remediation steps.

- Model: HRRR (`hrrr`)
- Run ID: `run_20260121_00`
- Locations observed in logs: Denver, Los Angeles
- Parallel downloads: enabled (configured workers: 4)

## Observations

- Denver — 2m Dew Point (`dpt`)
  - Multiple hours reported as successfully downloaded and verified (e.g., `grib_11`–`grib_24`).
  - Immediately afterward, processing for hours `01`–`24` fails with `No variables found in GRIB dataset.`
  - Example:
    ```text
    20:39:38 INFO: Successfully downloaded and verified GRIB file: cache/denver/hrrr/run_20260121_00/dpt/grib_14.grib2
    ...
    20:39:41 INFO: Processing dpt hour 01 for Denver (run run_20260121_00)
    20:39:41 ERROR: Error processing hour 01: No variables found in GRIB dataset.
    ```

- Los Angeles — Wind Gusts (`gust`) and 10m Wind (`wind_10m`)
  - Gust downloads repeatedly fail with undersized temporary files; retries exhausted.
  - Consequent processing logs for `wind_10m` show `Missing GRIB for hour` for many hours (13–24 in sample).
  - Examples:
    ```text
    20:43:08 INFO: Downloading GRIB ... var_GUST=on&lev_10_m_above_ground=on ... (attempt 1/3)
    20:43:08 ERROR: Download attempt 1 failed: Downloaded file is missing or too small: .../gust/grib_03.grib2.tmp
    ...
    20:43:08 INFO: Processing wind_10m hour 13 for Los Angeles (run run_20260121_00)
    20:43:08 ERROR: Error processing hour 13: Missing GRIB for hour 13
    ```

- Other variables (Denver) — `rh`, `hail`, `vis`
  - “Processing … hour …” lines appear for hours 01–24, but no corresponding “Saved forecast image …” lines were observed in this excerpt.

- General patterns
  - Interleaved log lines indicate parallel execution (multiple hours downloading concurrently).
  - Two primary failure modes:
    1) Network/download: undersized `.tmp` files, retries, then “Failed to obtain valid GRIB file after retries”.
    2) Parsing/plotting: GRIB opened later but `xarray/cfgrib` reports zero data variables during plotting (`No variables found in GRIB dataset`).

## Hypotheses

- NOMADS throttling/partial responses
  - HRRR filter endpoints occasionally return small/empty bodies (or HTML error pages), triggering the size check and retries.
  - Parallel fetches may exacerbate throttling or transient failures.

- GRIB parsing mismatch/environment sensitivity for `dpt`
  - Verification step loads the dataset and appears to find a variable, but the later plotting step sees no variables for the same hour path.
  - Possibilities: message selection differences between verification and plotting, subtle cfgrib index issues, or hour coverage gaps (e.g., hours 01–10 not truly present even though files exist).

- Over-aggressive success criteria in verification
  - The verification currently treats any dataset with at least one data var as valid; in case of wrong variable selection or mixed-content responses, this could pass while later lookups fail.

## Immediate Actions (Proposed)

1) Narrowed reproduction (single case)
- Run a single location/variable to isolate behavior:
  - `python cache_builder.py --location philly --model hrrr --variables refc --latest-only`

2) Reduce parallelism (stability vs throughput)
- Temporarily set `PARALLEL_DOWNLOAD_WORKERS` to 1–2 to reduce server load and test if failures abate.

3) Harden download validation
- Check response `Content-Type` and `Content-Length` before writing.
- If content-type looks like `text/html` or length is anomalously small, log first bytes and treat as failure (do not move temp file into place).

4) Improve retry/backoff
- Add exponential backoff with jitter; respect HTTP 429 and retry-after if present.

5) Enhance failure logging
- On download failure: log HTTP status code, content-type, and a short prefix of body bytes for diagnostics.

6) Verify cfgrib variable mapping
- For `dpt`: print available `ds.data_vars` during plotting failures to confirm what (if anything) cfgrib sees.
- Revisit `WEATHER_VARIABLES['dpt']` short/alt names; confirm HRRR shortName/keys for 2m dew point in the specific product.

## Follow-ups

- If failures persist after reducing parallelism, test alternative hours/models (e.g., NAM) to compare endpoint behavior.
- Consider caching HEAD checks of availability per hour before full GET.
- Optionally raise `MIN_GRIB_FILE_SIZE_BYTES` if we observe characteristic sizes for valid subsets.

## References

- Log file: `logs/cache_builder.log`
- Config: `config.py` (`WEATHER_VARIABLES`, `MODELS`, parallel and timeout settings)
- Code paths: `cache_builder.py` (download/verify/process), `plotting.py` (cfgrib/xarray loading)

---

## Verification Snapshot (2026-01-20)

Checked local cache contents for HRRR run `run_20260121_00` across multiple locations.

- Good (24/24 frames and GRIBs observed)
  - `refc` (Composite Reflectivity) — e.g., Chicago, Boston, Seattle
  - `prate` (Precipitation Rate) — e.g., Chicago, Seattle
  - `cape` (CAPE) — e.g., Chicago, Seattle
  - `vis` (Visibility) — e.g., Chicago

- GRIBs present but frames missing (24 GRIBs, 0 frames)
  - `dpt`, `t2m`, `rh`, `apcp`, `asnow`, `snod` across many locations
  - Interpretation: plotting failed after download; consistent with the `shortName`-filtered open returning an empty dataset. A fallback to open without filter has been implemented in `plotting.py`.

- Wind fields
  - `wind_10m` — partial GRIB coverage in some locations (e.g., Philly shows 12/24 GRIBs, 0 frames); others 0/0. Mapping for vector components now includes common shortName variants (`10u/10v`, `u10/v10`) and will be validated on next run.
  - `gust` — mostly 0/0 (downloads failing; prior logs indicate undersized responses). New logging will capture content-type/length for diagnosis.

- Not present in HRRR 2D fetch (0/0)
  - `hail`, `hlcy` consistently 0/0. Likely not available via the HRRR 2D surface endpoint used here and may require different datasets (pressure-level or diagnostic fields).

Notes on expected GRIB shortNames (common conventions)
- 2m Temperature → `2t` (also seen as `t2m`/`tmp`)
- 2m Dew Point → `2d`
- 10m Wind Components → `10u`, `10v` (also `UGRD`/`VGRD` in param naming)

Action: After the code fixes (fallback open and vector component candidates), re-run targeted variables to confirm frame generation catches up to GRIB availability.

---

## Follow-up Run (2026-01-21) — APCP across models

Goal: Verify that the rendered forecast table includes Accumulated Precipitation (APCP) with correct units across models, and that center_values are populated.

Scope
- Location: Boston
- Variables: `apcp`
- Models: `hrrr` (24h), `nam_nest` (60h)
- Command examples:
  - `python cache_builder.py --location boston --model hrrr --variables apcp --latest-only`
  - `python cache_builder.py --location boston --model nam_nest --variables apcp --latest-only`

Changes validated
- `apcp` now lists `source_short_names`: `tp`, `apcp` so GRIB shortName `tp` is accepted (observed: dataset variables show `['tp']`).
- Per-variable dynamic conversion now selects by GRIB units when present:
  - `m -> in` for total precip in meters (common for `tp`)
  - `kg m-2 -> in` for NOAA APCP
- `center_values.json` now records `units: "in"` and a `values` array for each processed hour.

Results
- HRRR (run `run_20260121_01`):
  - GRIB hours processed: 1–18 OK; 19–24 missing (not yet available at run time).
  - Table renders column “Accumulated Precipitation (in)” with 0.00 for 1–18.
  - Observed dataset variable: `tp` (shortName), confirming alias logic works.
- NAM CONUS Nest (run `run_20260121_00`):
  - GRIB hours processed: 1–48 OK; 49–60 missing.
  - Table renders APCP with small non-zero values starting around hr 27; units in inches correct.
  - Observed dataset variable: `tp`.

Rendered table checks
- HRRR: `python forecast_table.py --location boston --model hrrr --run run_20260121_01` shows APCP column (inches) with hours 1–18.
- NAM Nest: `python forecast_table.py --location boston --model nam_nest --run run_20260121_00` shows APCP column across 1–48 with expected non-zero increments.

Open items / Next steps
- GFS: Max forecast length (384h) makes ad‑hoc runs expensive. Consider:
  - Add CLI flag to limit max hours per run (e.g., `--max-hours`),
  - Per-model hour-step rules (e.g., process only 3‑hourly APCP for GFS).
- EPS/ECMWF: Reading is working via `tp`+`m`→`in`, but automated fetch requires CDS integration and credentials.
- Parallelism: Keep `PARALLEL_DOWNLOAD_WORKERS` at 1–2 during operational runs to reduce transient NOMADS failures.

---

## Status Update (2026-01-21) — Multi‑model builder, UI filtering, temp/precip verification

Summary of changes
- Cross‑model APCP: Added `source_short_names` (accepts `tp`) and dynamic unit conversions (`m→in`, `kg m-2→in`) in plotting and center value extraction.
- Single‑run multi‑model build: `cache_builder.py` now accepts `--models` (or `--model all`) and `--max-hours` so one invocation can fetch all desired models/variables for a location.
- Helper script: `scripts/run_model_matrix.py` runs a matrix of models/variables for one location, verifies center_values presence, and prints a status matrix. GFS capped by default.
- build_cache.sh: Runs a single `cache_builder` call for temp/precip variables across HRRR, NAM 3km, and GFS (GFS capped to 168h by default). Overridable via env vars.
- Server UI filtering: Model dropdowns now show only models with runs in cache for the location; variable dropdowns show only variables that have data for the selected run. Location selection added to map/table views.
- ECMWF/EPS scaffolding: Added `ecmwf_hres` and `ecmwf_eps` model entries with `source: cds` and `ecmwf.py` (cdsapi fetch scaffold). Requires cdsapi + credentials to activate.

Verification results
- Location: Boston
- Models/Vars: HRRR, NAM 3km (nam_nest), GFS; `t2m` and `apcp` verified end‑to‑end (frames, center_values, table render). GFS validated with small hour sample and supports capping to one week for broader runs.
- Server pages:
  - `/location/boston?model=hrrr` shows “2m Temperature” and “Accumulated Precipitation” in dropdown (filtered to available variables).
  - `/table/boston/hrrr/<run>` renders columns for temp/precip with values in expected units.

Operational notes
- NAM nest is NAM 3km CONUS (confirmed).
- For GFS APCP, consider per‑model step rules (e.g., 3‑hourly) if we see sparse hours; current flow tolerates missing hours and renders what’s available.
- To enable ECMWF/EPS downloads, install cdsapi and supply credentials (`~/.cdsapirc` or env) and expand variable/time step mapping as needed.
