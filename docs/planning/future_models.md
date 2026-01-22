# Future Model Integrations

Based on a review of open data availability (Jan 2026), the following models are strong candidates for integration into `radarcheck`.

## 1. NOAA National Blend of Models (NBM)
**Status:** Highly Recommended
- **Region:** CONUS (2.5km), Alaska, Hawaii, Puerto Rico.
- **Source:** AWS Open Data / NOMADS.
- **Format:** GRIB2.
- **Why:** It blends output from 30+ models (including ECMWF, GFS, HRRR) to create a superior consensus forecast.
- **Resolution:** 2.5km (Excellent for high-res tiles).
- **Variables:** Full suite including Snow, Ice, Precip probabilities.
- **Integration:** Standard GRIB fetch (like HRRR).

## 2. DWD ICON (Global & EU)
**Status:** Recommended (Global Alternative)
- **Region:** Global (13km), EU (6.5km), D2 (2km Germany).
- **Source:** DWD Open Data / AWS.
- **Format:** GRIB2 (Triangular Grid).
- **Challenge:** Uses an unstructured triangular grid. Requires interpolation to Lat/Lon grid before tiling. `eccodes` / `CDO` tools can handle this.
- **Why:** State-of-the-art non-hydrostatic global model. Often outperforms GFS.

## 3. Canadian GEM (GDPS/RDPS)
**Status:** Recommended (Winter Weather)
- **Region:** Global (GDPS 15km), Regional (RDPS 10km North America).
- **Source:** MSC Datamart (HTTP).
- **Format:** GRIB2.
- **Why:** Excellent performance for winter storms in North America.
- **Integration:** Standard GRIB fetch.

## 4. ECMWF Open Data (AIFS / IFS)
**Status:** Partial / Experimental
- **Source:** AWS Open Data.
- **Limitation:** Operational HRES (0.1°) is **not** free. The Open Data set is coarser (0.4° / ~40km).
- **AI Models (AIFS):** ECMWF is releasing AI-based forecasts (AIFS) which are very fast and surprisingly accurate. Check for open GRIB2 releases of AIFS.
- **Verdict:** The 0.4° resolution is too coarse for our 0.1° tile system without heavy interpolation. Use NBM (which includes ECMWF) instead.

## 5. UK Met Office (Global)
**Status:** API-based
- **Source:** Weather DataHub.
- **Access:** API key required (Free tier exists).
- **Why:** Strong global performance.
- **Challenge:** API rate limits may hinder bulk GRIB downloading for tiles.

## Recommendation Priority
1. **NBM:** Best value for US users (High res, consensus accuracy).
2. **ICON:** Best open global alternative to GFS.
3. **GEM:** Strong addition for cold-season redundancy.
