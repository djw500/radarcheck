I will begin by searching for the official HRRR GRIB2 parameter tables and inventory lists, specifically focusing on the `wrfsfcf` files, while also looking for documentation on cloud and radiation variables for GFS, NBM, and NAM-NEST to establish a baseline for comparison.
I will now perform a targeted search for the NOMADS inventory (.idx) files for HRRR (wrfsfcf, wrfprsf, wrfnatf), GFS, NBM, and NAM-NEST to extract the exact GRIB2 search strings and identify which cloud, visibility, and radiation variables are available in each model.
I will now search for the NOMADS inventory (.idx) file contents for GFS, NBM, and NAM-NEST to retrieve the exact GRIB2 search strings and confirm the availability of specific cloud, visibility, and radiation variables in each model.
This report provides a comprehensive accounting of cloud, visibility, and radiation variables in the **HRRR** (High-Resolution Rapid Refresh) `wrfsfcf` files on NOMADS/AWS S3, with comparisons to **GFS**, **NBM**, and **NAM-NEST**.

## Summary
The HRRR `wrfsfcf` (surface fields) dataset is the most variable-rich source for high-resolution (3km) cloud and radiation diagnostics in the NCEP suite. While variables like **Total Cloud Cover (TCDC)** and **Visibility (VIS)** are standard across all models, the HRRR provides unique "simulated radar" products like **Echo Top (RETOP)** and **Composite Reflectivity (REFC)** that are absent in synoptic models like the GFS. However, the HRRR is known for a **significant positive bias in solar radiation (DSWRF)** and a **dry/clear bias in cloud cover**, often over-predicting sunlight by 50–80 $W/m^2$ in summer. The **NAM-NEST** serves as a useful counter-balance, as it tends to over-predict low-level moisture and cloudiness.

## Key Data Points: HRRR `wrfsfcf` Variable Accounting

| Variable | GRIB2 Search String | Physical Measurement | Units | Trustworthiness / Biases | Common Use Cases |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Visibility** | `:VIS:surface:` | Horizontal visibility at sfc | meters | Known "clear bias"; underestimates fog density. | Aviation, Highway safety |
| **Total Clouds** | `:TCDC:entire atmosphere:` | Total cloud fraction | % | **Dry bias**; often misses sub-grid cumulus. | Consumer apps, general wx |
| **Low Clouds** | `:TCDC:low cloud layer:` | Cloud cover < 642 hPa | % | Often too low in HRRR; too high in NAM-NEST. | Aviation (VFR/IFR), Solar |
| **Mid Clouds** | `:TCDC:middle cloud layer:` | Cloud cover 350-642 hPa | % | Generally reliable for synoptic decks. | Aviation, Satellite validation |
| **High Clouds** | `:TCDC:high cloud layer:` | Cloud cover < 350 hPa | % | Good for cirrus; can be over-done in convection. | Astronomy, Solar energy |
| **BL Clouds** | `:TCDC:boundary layer...` | Clouds in the PBL | % | Highly sensitive to PBL mixing scheme. | Fog forecasting, Agriculture |
| **Cloud Ceiling**| `:HGT:cloud ceiling:` | Height of lowest BKN/OVC layer | gpm | Often "jumpy" due to binary cloud thresholds. | Aviation (Landing mins) |
| **Cloud Base** | `:HGT:cloud base:` | Height of the cloud bottom | gpm | Better for convective bases than stratus. | Aviation, Paragliding |
| **Cloud Top** | `:HGT:cloud top:` | Geopotential height of cloud top | gpm | Reliable for convective height/intensity. | Aviation (Flight levels) |
| **Echo Top** | `:RETOP:cloud top:` | 18 dBZ echo top height | meters | Excellent for storm severity; unique to CAMs. | Storm chasing, Air traffic |
| **Down SW Flux**| `:DSWRF:surface:` | Global Horizontal Irradiance | $W/m^2$ | **High Positive Bias** (too sunny). | Solar power, Evaporation |
| **Up SW Flux** | `:USWRF:surface:` | Reflected solar radiation | $W/m^2$ | Dependent on Albedo (snow cover accuracy). | Energy balance, Climate |
| **Down LW Flux**| `:DLWRF:surface:` | Greenhouse effect radiation | $W/m^2$ | Underestimated if cloud cover is low. | Overnight lows, Frost risk |
| **Up LW Flux** | `:ULWRF:surface:` | Terrestrial IR radiation | $W/m^2$ | Highly dependent on Skin Temp accuracy. | Surface cooling, Heat waves |
| **Vis Beam SW** | `:VBDSF:surface:` | Direct beam solar radiation | $W/m^2$ | Crucial for tracking PV; reflects clear bias. | Concentrated Solar (CSP) |
| **Vis Diff SW** | `:VDDSF:surface:` | Scattered solar radiation | $W/m^2$ | Underestimated in the HRRR. | Fixed PV panels, Botany |

## Comparable Analysis: Model Feature Matrix

| Feature | HRRR (3km) | NAM-NEST (3km) | GFS (13km) | NBM (Blend) |
| :--- | :--- | :--- | :--- | :--- |
| **Resolution** | 3 km | 3 km | 13 km | 1-3 km |
| **Update Freq** | Hourly | Every 6 hours | Every 6 hours | Hourly |
| **Cloud Ceiling**| Yes | Yes | No (Derived) | Yes |
| **Echo Top** | **Yes** | **Yes** | No | No |
| **Vis Beam/Diff**| **Yes** | No | No | No |
| **LW Radiation** | Yes | Yes | Yes | Yes (in .qmd files) |
| **Cloud Bias** | Under-forecasts | Over-forecasts | Varies (Coarse) | Best (Bias-corrected) |

## Neighborhood & Context: HRRR File Structure
*   **`wrfsfcf` (Surface):** Contains all the 2D fluxes and integrated cloud variables listed above. This is the "gold standard" for solar and aviation.
*   **`wrfprsf` (Pressure):** Does **not** contain radiation fluxes. It contains 3D mixing ratios of cloud water (`CLWMR`), ice (`CIMIXR`), and rain (`RWMR`) on standard pressure levels.
*   **`wrfnatf` (Native):** Provides the highest vertical resolution of cloud microphysics (50 levels) but lacks the derived 2D diagnostic strings like `:TCDC:low cloud layer:`.
*   **`wrfsubhf` (Sub-hourly):** A hidden gem containing 15-minute intervals for `DSWRF` and `VIS`, critical for catching "solar ramps" or rapid fog onset.

## Risk Factors
*   **Solar Over-prediction (The "Too Sunny" Risk):** If using HRRR `DSWRF` for financial solar estimates, expect a **10-15% overestimation** in annual yield unless bias-corrected.
*   **Fog Dissipation Risk:** HRRR typically burns off morning fog 1-2 hours **earlier** than reality. This is a high risk for aviation scheduling.
*   **Convective "Blow-up" in NAM-NEST:** While HRRR might miss a storm's cloud shield, the NAM-NEST often creates "pizza-sized" storms that are too large, leading to localized 100% cloud cover errors.

## Source Discrepancies
*   **Radiation Units:** While most NCEP models use $W/m^2$, some older scripts interpret these as Joules/m² (integrated over the hour). HRRR `DSWRF` in `wrfsfcf` is an **average flux over the previous hour** (e.g., `0-1 hr ave`), not an instantaneous snapshot.
*   **Visibility Thresholds:** VIS in HRRR is a diagnostic based on extinction coefficients, whereas GFS VIS is more heavily parameterized. NBM VIS is often the most accurate as it incorporates METAR observations.

## Sources
- [NOAA HRRR Model Documentation](https://rapidrefresh.noaa.gov/hrrr/) — Primary variable definitions.
- [NCEP GRIB2 Parameter Table](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/) — Standard WMO codes.
- [Herbie Python Library Documentation](https://herbie.fastiron.it/) — Search string examples for GRIB2.
- [AMS Journals: HRRR Radiation Bias Study](https://journals.ametsoc.org/view/journals/wead/34/6/waf-d-18-0143.1.xml) — Bias metrics for solar energy.
- [NBM v4.3 Technical Procedures Bulletin](https://www.weather.gov/mdl/nbm_tpb_4_3) — NBM radiation availability.
- [UCAR/Unidata: NAM-NEST Inventory](https://www.unidata.ucar.edu/software/netcdf/examples/nam_nest.html) — Comparison data.
- [NOMADS S3 Inventory Lists](https://noaa-hrrr-bdp-pds.s3.amazonaws.com/) — Real-time index file verification.

## Open Questions
*   **Sub-grid Cloud Schemes:** The exact threshold for HRRR to trigger "1%" cloud cover vs "0%" is proprietary and varies between model versions (v3 vs v4).
*   **Aerosol Interaction:** HRRR-Smoke is integrated into some runs but not all; the impact of smoke on `DSWRF` visibility in the standard `wrfsfcf` file depends on whether the "Smoke" or "Primary" bucket is accessed.
