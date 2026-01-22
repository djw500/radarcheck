# Precipitation and Snowfall Accumulation Logic

This document explains how RadarCheck handles various precipitation data formats from different weather models (HRRR, GFS, NBM, NAM) to ensure a consistent, non-decreasing accumulation view for users.

## The Problem: Data Output Patterns

Weather models do not provide precipitation data in a unified way. There are three primary patterns found in GRIB files:

| Pattern | Behavior | Models Often Using This |
| :--- | :--- | :--- |
| **Strictly Cumulative** | The value represents the total since the start of the model run (Hour 0). Values always increase. | HRRR, GFS (mostly) |
| **Bucketed** | The value represents only what fell in the last interval (e.g., the last 1 hour). | NBM (hourly), GFS (some vars) |
| **Resetting** | The value accumulates until a fixed point (usually every 6 hours), then "empties the bucket" and starts from 0 again. | NAM, GFS (3-hour buckets in long range) |

If plotted raw, **Bucketed** and **Resetting** data would look like a "sawtooth" or a series of spikes, which is confusing for users expecting to see a total snowfall forecast.

## The Solution: The "Accountant" Logic

RadarCheck uses the `_accumulate_timeseries` helper (located in `app.py`) to process these raw values into a "Strictly Cumulative" series.

### 1. Handling Resets and Buckets
The logic works by inspecting the "delta" (the change) between consecutive forecast hours:
- **If the value increases:** We assume it is already accumulating (or is a bucket). We add the difference to our running total.
- **If the value decreases:** We assume a **Reset** or a **New Bucket** has occurred. Instead of treating the drop as "melting," we take the new value and **add it** to our previous high point.

### 2. Handling NaNs (Missing Data)
A critical bug was discovered where missing data (NaNs) were being treated as `0.0`. 
- **The Bug:** `[1.0, NaN, 1.2]` was seen as `[1.0, 0.0, 1.2]`. The jump from `0.0` back to `1.2` was seen as a "reset," causing the `1.2` to be added to the `1.0`, resulting in `2.2` (double counting).
- **The Fix:** We now use `_forward_fill_nan`. The series becomes `[1.0, 1.0, 1.2]`. Because `1.2 > 1.0`, the logic correctly identifies that no reset occurred, and the total stays a correct `1.2`.

## Sparse Data & Alignment (GFS)

Models like the GFS often provide some variables (like Temperature) every hour, but others (like Precipitation) only every 3 or 6 hours in the long-range forecast.

To prevent underestimation during Snowfall Derivation (which requires `APCP`, `CSNOW`, and `T2M` to align):
1. **Union of Hours:** We take every hour available across all three variables.
2. **Linear Interpolation:** We bridge the gaps. If we have precip at Hour 6 and Hour 12, we linearly spread that accumulation across the intervening hours so the snow "falls" smoothly rather than appearing in one massive jump.
3. **NaN Removal:** Before interpolating, we drop NaNs so the interpolation function can see the "true" data points on either side of the gap.

## Why do models use "Resetting" data?
While inconvenient for developers, resetting buckets are standard in meteorology for several reasons:
- **Intensity Tracking:** It makes it easier for meteorologists to see the "peak" of a storm in a specific 6-hour window.
- **Precision:** It prevents floating-point errors that can occur when adding tiny amounts of rain to a very large number over a 16-day forecast.
- **Legacy Standards:** Most global weather observations are reported in 6-hour "synoptic" blocks, and models maintain this for backward compatibility.
