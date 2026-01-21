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
