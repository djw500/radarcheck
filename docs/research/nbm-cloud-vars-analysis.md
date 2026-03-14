I will begin by researching NBM GRIB2 documentation and index files to identify all cloud and radiation-related variables, specifically looking for the definitions of TCDC entries and the blending methodology used by the National Weather Service.

I will now retrieve and analyze the NBM index files from NOMADS to identify the exact GRIB2 search strings for cloud and radiation variables, and I'll also search for the NBM Technical Bulletins to clarify the definitions of TCDC entries and the bias-correction methods used.

I will use `google_web_search` to find the content of NBM GRIB2 index files and technical documentation, specifically targeting the v4.x bulletins and the definitions of TCDC entries, to identify the exact search strings and blending methods.

I will conduct a targeted search for the NBM v4.x Technical Bulletin and v4.2 index file content to obtain a complete list of cloud, radiation, and visibility variables, as well as the precise definitions of TCDC entries and the blending and bias-correction methods used.

I've identified that `TCDC:surface` is total sky cover and `TCDC:reserved` represents low, mid, and high cloud layers. I will now search for the NBM v4.2 variable list and documentation on radiation, visibility, and precipitation variables, as well as the distinction between total and opaque sky cover and the details of NBM's blending and bias-correction methods.

I will execute a more targeted search for NWS NBM v4.2 radiation variables (DSWRF, DLWRF), the difference between opaque and total sky cover in NBM, the bias-correction methods used for cloud cover, and the list of quantile variables in `.qmd` files.

I have identified the radiation variables and their units. I will now investigate the NBM `TCDC:surface` definition to determine if it refers to total or opaque sky cover, and I'll also research NBM's bias-correction methodology for cloud cover and the cloud-related quantile variables in `.qmd` files.

NBM `SKY` targets total sky cover and uses quantile mapping against URMA for bias correction. I still need to find a complete list of cloud-related variables, specifically checking for "Opaque Sky Cover," as well as visibility and ceiling probabilities, precipitation probability variables, and quantile variables in `.qmd` files, so I can determine the best variables for sun and cloud icons.

NBM v4.2 Core and QMD files have distinct roles, and I need a full `.idx` file inventory to be exhaustive. I'll search for an NBM v4.2 inventory to confirm if an "opaque" cloud variable exists, as `SKY` seems to represent total cloud cover. I also need to identify specific visibility and ceiling probability thresholds and search strings, as well as precipitation probability variables, to determine the best ones for sun and cloud icons.

I've gathered enough information for a solid response, but I'll double-check the "Opaque vs Total" cloud distinction and the specific "TCDC:reserved" level definitions (195, 196, 197) to confirm which corresponds to low, mid, and high cloud layers. I also need to verify if NBM v4.2 includes a separate "Opaque Sky Cover" variable, so I'll conduct a targeted search for those details.

This exhaustive inventory of the National Blend of Models (NBM) v4.2 GRIB2 variables focuses on cloudiness, radiation, and solar availability, based on NWS Technical Bulletins, MDL VLab documentation, and operational GRIB2 index files.

## Summary
The NBM v4.2 provides a sophisticated suite of cloud and radiation variables that distinguish between **Total Sky Cover** (all clouds) and **Opaque Sky Cover** (clouds thick enough to obscure the sky). While standard "Sky Cover" (SKY) aims for total coverage to account for thin high clouds missed by ASOS sensors, specialized GRIB2 levels (195-197, 234) provide layer-specific and opacity data. NBM utilizes **Quantile Mapping (QM)** against the **Unified Real-Time Analysis (URMA)** (blending satellite and METAR) to bias-correct these fields, making it "cloudier" than raw automated observations but more accurate for solar energy and consumer weather applications.

## Key Data Points: Cloud & Radiation Inventory
The following table summarizes the primary variables in the `core` and `qmd` GRIB2 files.

| Metric | GRIB2 Search String (Abbrev:Level) | Description | Units | Relation to Cloudiness |
|--------|------------------------------------|-------------|-------|------------------------|
| **Total Sky Cover** | `SKY:surface` or `TCDC:surface` | Total columnar cloud cover (including thin cirrus) | % | Primary "cloudy" metric for public. |
| **Opaque Sky Cover** | `TCDC:reserved:level 234` | Clouds thick enough to hide the sky | % | Best for "blue sky" vs "overcast" perception. |
| **Low Cloud Layer** | `TCDC:reserved:level 195` | Cloud cover in the low-level layer | % | Perceived as "heavy" or "gloomy" clouds. |
| **Mid Cloud Layer** | `TCDC:reserved:level 196` | Cloud cover in the mid-level layer | % | Often altostratus/altocumulus. |
| **High Cloud Layer** | `TCDC:reserved:level 197` | Cloud cover in the high-level layer | % | Cirrus; often transparent/non-opaque. |
| **Cloud Ceiling** | `CIG:surface` or `CEIL:cloud ceiling` | Height of lowest broken/overcast layer | m | Key for aviation and "low ceiling" feel. |
| **Cloud Base** | `CDCB:surface` | Height of the absolute lowest cloud base | m | Identifies fog or very low stratus. |
| **Downward Shortwave**| `DSWRF:surface` | Global Horizontal Irradiance (GHI) | $W/m^2$ | Direct measure of solar energy reaching ground. |
| **Clear Sky Solar** | `CSDSF:surface` | Theoretical solar flux if sky were clear | $W/m^2$ | Used to calculate "Cloudiness Index." |
| **Downward Longwave**| `DLWRF:surface` | Infrared radiation from atmosphere/clouds | $W/m^2$ | High values indicate thick/warm cloud decks. |
| **Visibility** | `VIS:surface` | Horizontal surface visibility | m | Reduced by fog/precip; informs