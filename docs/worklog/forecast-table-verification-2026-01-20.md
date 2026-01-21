# Forecast Table Verification — 2026-01-20

Purpose: Validate that `forecast_table.py` generates a correct table from the current cache.

## Procedure

- Ran: `python forecast_table.py --location philly --model hrrr --format json`
- Source cache: `cache/philly/hrrr/run_20260121_00/*/center_values.json`

## Expected Behavior

- Metadata: includes `location_id`, `model_id`, `run_id`, `init_time`.
- Columns: `hour`, `valid_time`, plus one column per available variable (only those with center_values present).
- Rows: one per forecast hour available across variables (HRRR → 24 hours), each with formatted values.
- Value formatting: includes units (e.g., `dBZ`, `in/hr`, `J/kg`, `mi`), numeric formatting per units.

## Observations

- Metadata present and correct:
  - `location_id = philly`, `model_id = hrrr`, `run_id = run_20260121_00`, `init_time = 2026-01-21 00:00:00`.
- Columns returned (for this dataset): `hour`, `valid_time`, `refc`, `prate`, `cape`, `vis`.
  - Note: variables with center_values missing (e.g., `t2m`, `dpt`, `apcp`, `asnow`, `snod`, `rh`, `gust`, `wind_10m`) are correctly omitted from the table.
- Rows: 24 entries (hours 1–24), each with a `valid_time` and values for the available variables.
- Formatting examples (from sample rows):
  - `refc`: "-10 dBZ" (reflectivity floor seen at the center point for this run).
  - `prate`: "0.00 in/hr" (no precip at center point for sampled hours).
  - `cape`: "0 J/kg" (stable conditions at center point).
  - `vis`: values like "32.31 mi", "29.89 mi" (varying by hour).

## Conclusion

- Table generation is correct for the available data:
  - Proper metadata is included.
  - Columns reflect only variables with `center_values.json` present.
  - 24 hourly rows are produced; `valid_time` values are populated.
  - Values are formatted with appropriate units.

## Notes / Gaps

- Missing variables from the table reflect underlying cache gaps (no center_values produced for those variables due to earlier download/parse issues). Once those are resolved, columns will appear automatically.
- "-10 dBZ" for `refc` appears as a consistent floor at the center point for early hours; this is plausible given no returns at the exact center.

## Next Steps

- After the cache builder fixes land (wind component mapping, shortName fallback, improved retries), re-run the builder and re-check this table:
  - Expect additional columns to appear (`t2m`, `dpt`, `apcp`, `asnow`, `snod`, `rh`, `wind_10m`, `gust`) once their center_values are generated.
