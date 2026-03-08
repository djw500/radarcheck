# Solar Clearness Index Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace raw DSWRF (W/m²) display with a clearness index ("X% clear sky") computed server-side, shown only during daylight hours.

**Architecture:** Add a `solar` module to `radarcheck-core` with pure-math solar geometry functions. The Rust API server computes clear-sky DSWRF for each forecast timestamp, divides forecast value by it to get clearness index (0-100%), and returns `"value": 85` (percent) instead of raw W/m². Nighttime points return `null`. UI displays "% clear sky" with no backend data dependencies.

**Tech Stack:** Rust (f64 trig), no new dependencies.

---

### Task 1: Create solar geometry module with tests

**Files:**
- Create: `rust_worker/crates/core/src/solar.rs`

**Step 1: Write the module with tests**

Create `rust_worker/crates/core/src/solar.rs`:

```rust
//! Solar geometry — clearness index computation.
//!
//! Computes clear-sky downward shortwave radiation (DSWRF) from solar position,
//! and clearness index = forecast / clear_sky.

use std::f64::consts::PI;

const SOLAR_CONSTANT: f64 = 1361.0; // W/m², TSI
const CLEAR_SKY_TRANSMITTANCE: f64 = 0.75; // typical clear atmosphere

/// Compute solar declination angle (radians) from day of year (1-366).
fn solar_declination(day_of_year: u32) -> f64 {
    // Spencer (1971) approximation
    let b = 2.0 * PI * (day_of_year as f64 - 1.0) / 365.0;
    0.006918 - 0.399912 * b.cos() + 0.070257 * b.sin()
        - 0.006758 * (2.0 * b).cos() + 0.000907 * (2.0 * b).sin()
        - 0.002697 * (3.0 * b).cos() + 0.00148 * (3.0 * b).sin()
}

/// Compute solar elevation angle (radians) given lat (deg), declination (rad), hour angle (rad).
fn solar_elevation(lat_deg: f64, declination: f64, hour_angle: f64) -> f64 {
    let lat = lat_deg.to_radians();
    let sin_elev = lat.sin() * declination.sin()
        + lat.cos() * declination.cos() * hour_angle.cos();
    sin_elev.asin()
}

/// Compute hour angle (radians) from UTC hour (fractional) and longitude (degrees).
/// Solar noon occurs when hour_angle = 0.
fn hour_angle(utc_hour: f64, lon_deg: f64) -> f64 {
    // Solar time ≈ UTC + lon/15 (hours offset from Greenwich)
    let solar_hour = utc_hour + lon_deg / 15.0;
    // Hour angle: 0 at solar noon (12:00 solar time), 15°/hour
    (solar_hour - 12.0) * 15.0_f64.to_radians()
}

/// Compute clear-sky DSWRF (W/m²) at the surface.
/// Returns 0.0 if the sun is below the horizon.
fn clear_sky_dswrf(lat_deg: f64, lon_deg: f64, day_of_year: u32, utc_hour: f64) -> f64 {
    let decl = solar_declination(day_of_year);
    let ha = hour_angle(utc_hour, lon_deg);
    let elev = solar_elevation(lat_deg, decl, ha);

    if elev <= 0.0 {
        return 0.0; // nighttime
    }

    SOLAR_CONSTANT * CLEAR_SKY_TRANSMITTANCE * elev.sin()
}

/// Compute clearness index as a percentage (0-100).
///
/// Returns `None` if nighttime (clear_sky == 0).
/// Returns the clamped percentage if daytime.
pub fn clearness_index(
    forecast_dswrf: f64,
    lat_deg: f64,
    lon_deg: f64,
    day_of_year: u32,
    utc_hour: f64,
) -> Option<f64> {
    let cs = clear_sky_dswrf(lat_deg, lon_deg, day_of_year, utc_hour);
    if cs <= 0.0 {
        return None; // nighttime
    }
    let pct = (forecast_dswrf / cs * 100.0).clamp(0.0, 100.0);
    Some(pct)
}

/// Convenience: compute from a Unix timestamp instead of day_of_year + utc_hour.
pub fn clearness_index_from_unix(
    forecast_dswrf: f64,
    lat_deg: f64,
    lon_deg: f64,
    unix_secs: i64,
) -> Option<f64> {
    let (doy, utc_hour) = unix_to_doy_hour(unix_secs);
    clearness_index(forecast_dswrf, lat_deg, lon_deg, doy, utc_hour)
}

/// Convert Unix timestamp to (day_of_year, fractional_utc_hour).
fn unix_to_doy_hour(unix_secs: i64) -> (u32, f64) {
    // Days since epoch
    let total_secs = unix_secs;
    let secs_in_day = 86400_i64;
    let day_secs = total_secs.rem_euclid(secs_in_day);
    let utc_hour = day_secs as f64 / 3600.0;

    // Compute year and day-of-year from Unix timestamp
    let mut days = unix_secs / secs_in_day;
    let mut year = 1970_i64;
    loop {
        let days_in_year = if is_leap(year) { 366 } else { 365 };
        if days < days_in_year {
            break;
        }
        days -= days_in_year;
        year += 1;
    }
    let doy = (days + 1) as u32; // 1-based

    (doy, utc_hour)
}

fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_solar_declination_summer_solstice() {
        // Day 172 (June 21) — declination should be ~+23.4°
        let decl = solar_declination(172);
        let decl_deg = decl.to_degrees();
        assert!(decl_deg > 22.0 && decl_deg < 25.0,
            "Summer solstice declination {:.1}° should be ~23.4°", decl_deg);
    }

    #[test]
    fn test_solar_declination_winter_solstice() {
        // Day 355 (Dec 21) — declination should be ~-23.4°
        let decl = solar_declination(355);
        let decl_deg = decl.to_degrees();
        assert!(decl_deg < -22.0 && decl_deg > -25.0,
            "Winter solstice declination {:.1}° should be ~-23.4°", decl_deg);
    }

    #[test]
    fn test_clear_sky_noon_summer_philadelphia() {
        // Philadelphia (40°N, -75.4°W), June 21, solar noon (~17:00 UTC)
        let dswrf = clear_sky_dswrf(40.0, -75.4, 172, 17.0);
        // Expect ~900-1000 W/m² at noon in summer
        assert!(dswrf > 800.0 && dswrf < 1100.0,
            "Summer noon clear-sky DSWRF {:.0} W/m² should be ~900-1000", dswrf);
    }

    #[test]
    fn test_clear_sky_midnight_is_zero() {
        // Philadelphia, midnight UTC (evening local) in summer — sun is down
        let dswrf = clear_sky_dswrf(40.0, -75.4, 172, 4.0);
        assert_eq!(dswrf, 0.0, "Midnight should have 0 DSWRF");
    }

    #[test]
    fn test_clearness_index_nighttime_returns_none() {
        let ci = clearness_index(0.0, 40.0, -75.4, 172, 4.0);
        assert!(ci.is_none(), "Nighttime should return None");
    }

    #[test]
    fn test_clearness_index_clear_sky() {
        // Forecast matches clear-sky → ~100%
        let cs = clear_sky_dswrf(40.0, -75.4, 172, 17.0);
        let ci = clearness_index(cs, 40.0, -75.4, 172, 17.0).unwrap();
        assert!((ci - 100.0).abs() < 1.0, "Clear sky should give ~100%, got {:.1}%", ci);
    }

    #[test]
    fn test_clearness_index_overcast() {
        // Forecast = 200 W/m² when clear sky is ~950 → ~21%
        let ci = clearness_index(200.0, 40.0, -75.4, 172, 17.0).unwrap();
        assert!(ci > 15.0 && ci < 30.0, "Overcast should be ~21%, got {:.1}%", ci);
    }

    #[test]
    fn test_clearness_index_clamped_to_100() {
        // Forecast exceeds clear-sky (possible with reflections) → clamped to 100
        let ci = clearness_index(2000.0, 40.0, -75.4, 172, 17.0).unwrap();
        assert_eq!(ci, 100.0, "Should clamp to 100%");
    }

    #[test]
    fn test_clearness_index_from_unix() {
        // 2026-06-21 17:00 UTC
        // Unix: roughly 1782066000 (June 21 2026 17:00 UTC)
        // Let's compute: 2026-01-01 = 1735689600
        // Jan=31, Feb=28, Mar=31, Apr=30, May=31, Jun=20 = 171 days
        // 171 * 86400 = 14774400 + 17*3600 = 61200 → 1735689600 + 14774400 + 61200 = 1750525200
        let unix = 1750525200_i64;
        let ci = clearness_index_from_unix(500.0, 40.0, -75.4, unix);
        assert!(ci.is_some(), "Daytime should return Some");
        let pct = ci.unwrap();
        assert!(pct > 40.0 && pct < 60.0, "500 W/m² in summer noon should be ~50%, got {:.1}%", pct);
    }

    #[test]
    fn test_unix_to_doy_hour() {
        // 2026-01-01 00:00 UTC = Unix 1767225600 (approx)
        // Actually: 2025-01-01 = 1735689600, + 365*86400 = 1767225600
        let (doy, hour) = unix_to_doy_hour(1767225600);
        assert_eq!(doy, 1, "Jan 1 should be day 1");
        assert!((hour - 0.0).abs() < 0.01, "Should be hour 0");
    }
}
```

**Step 2: Register module in lib.rs**

Add to `rust_worker/crates/core/src/lib.rs`:
```rust
pub mod solar;
```

**Step 3: Run tests**

Run: `cd /workspace/radarcheck/rust_worker && cargo test -p radarcheck-core solar -- --nocapture`
Expected: All 10 tests PASS

**Step 4: Commit**

```bash
git add rust_worker/crates/core/src/solar.rs rust_worker/crates/core/src/lib.rs
git commit -m "feat(solar): add solar geometry module with clearness index"
```

---

### Task 2: Wire clearness index into multirun API response

**Files:**
- Modify: `rust_worker/crates/server/src/main.rs:440-470` (multirun_blocking series loop)

**Step 1: Add the transformation**

In `multirun_blocking()`, around line 454-469 where the `series` Vec is built, add clearness index computation for DSWRF. Replace the series-building loop:

```rust
// BEFORE (current code, lines 454-469):
let mut series = Vec::new();
for (i, &h) in run_data.hours.iter().enumerate() {
    if i >= accum_values.len() {
        break;
    }
    let v = accum_values[i];
    if v.is_nan() {
        continue;
    }
    let valid_unix = run_data.init_unix + (h as i64) * 3600;
    let valid_time = unix_to_iso(valid_unix);
    series.push(serde_json::json!({
        "valid_time": valid_time,
        "forecast_hour": h,
        "value": v,
    }));
}

// AFTER:
let mut series = Vec::new();
let is_dswrf = variable_id == "dswrf";
for (i, &h) in run_data.hours.iter().enumerate() {
    if i >= accum_values.len() {
        break;
    }
    let v = accum_values[i];
    if v.is_nan() {
        continue;
    }
    let valid_unix = run_data.init_unix + (h as i64) * 3600;
    let valid_time = unix_to_iso(valid_unix);

    if is_dswrf {
        // Convert to clearness index; skip nighttime points
        match radarcheck_core::solar::clearness_index_from_unix(v, lat, lon, valid_unix) {
            Some(pct) => {
                series.push(serde_json::json!({
                    "valid_time": valid_time,
                    "forecast_hour": h,
                    "value": (pct * 10.0).round() / 10.0,
                }));
            }
            None => {
                // Nighttime — emit null value so chart shows gap
                series.push(serde_json::json!({
                    "valid_time": valid_time,
                    "forecast_hour": h,
                    "value": null,
                }));
            }
        }
    } else {
        series.push(serde_json::json!({
            "valid_time": valid_time,
            "forecast_hour": h,
            "value": v,
        }));
    }
}
```

**Step 2: Add the same transformation to the stitched endpoint**

Find the equivalent series-building loop in the stitched handler (around line 580-610) and apply the same `is_dswrf` check with `clearness_index_from_unix`.

**Step 3: Verify `radarcheck_core::solar` is accessible**

The server crate already depends on `radarcheck-core`. Verify with:
```bash
grep radarcheck-core rust_worker/crates/server/Cargo.toml
```

**Step 4: Build and verify**

Run: `cd /workspace/radarcheck/rust_worker && cargo build -p radarcheck-server 2>&1 | tail -5`
Expected: Compiles without errors

**Step 5: Commit**

```bash
git add rust_worker/crates/server/src/main.rs
git commit -m "feat(server): convert DSWRF to clearness index in API responses"
```

---

### Task 3: Update UI to display clearness index

**Files:**
- Modify: `templates/index.html` — VAR_CONFIG entry + null handling

**Step 1: Update VAR_CONFIG for dswrf**

Change the `dswrf` entry in VAR_CONFIG (line 279-280) from:
```javascript
'dswrf': { label: 'Solar Radiation', unit: 'W/m²', decimals: 0,
    color: (v) => { const t = Math.min(v / 1000, 1); return `rgba(250,204,21,${0.1 + t * 0.5})`; } }
```
to:
```javascript
'dswrf': { label: 'Solar', unit: '% clear sky', decimals: 0,
    color: (v) => { const t = Math.min(v / 100, 1); return `rgba(250,204,21,${0.1 + t * 0.5})`; } }
```

**Step 2: Handle null values in chart rendering**

In `updateChart()` around line 510 where `y` values are extracted:
```javascript
let y = run.series.map(p => p.value);
```

Add null filtering for the chart (Plotly handles nulls as gaps natively, so this should work automatically). But for the table/hover interpolation, add a null check in `interpolateValue()` around line 663:

```javascript
// In interpolateValue, before returning series[idx].value:
if (series[idx].value === null) return null;
```

And around line 675-676:
```javascript
const v1 = series[leftIdx].value;
const v2 = series[rightIdx].value;
// Add: if either is null, return null
if (v1 === null || v2 === null) return null;
```

**Step 3: Update Rust config display name**

In `rust_worker/crates/core/src/config.rs`, change the dswrf variable config (line 312):
```rust
display_name: "Solar",
units: "% clear sky",
```

And in `config.py`, update the dswrf entry:
```python
"display_name": "Solar",
"units": "% clear sky",
```

**Step 4: Commit**

```bash
git add templates/index.html rust_worker/crates/core/src/config.rs config.py
git commit -m "feat(ui): display solar as clearness index (% clear sky)"
```

---

### Task 4: Build, test end-to-end, Gemini code review

**Step 1: Run Rust tests**

```bash
cd /workspace/radarcheck/rust_worker && cargo test -p radarcheck-core 2>&1 | tail -20
```
Expected: All tests pass including new solar tests

**Step 2: Build release binary**

```bash
cd /workspace/radarcheck/rust_worker && cargo build --release 2>&1 | tail -5
```

**Step 3: Start server and test API**

```bash
# Kill existing server
fuser -k 5001/tcp 2>/dev/null
# Start new server
./rust_worker/target/release/radarcheck-server \
    --port 5001 --app-root /workspace/radarcheck \
    --db-path /workspace/radarcheck/cache/jobs.db \
    --tiles-dir /workspace/radarcheck/cache/tiles \
    --cache-dir /workspace/radarcheck/cache &
sleep 2
# Test API - should return % clear sky values (0-100) during day, null at night
curl -s "http://localhost:5001/api/timeseries/multirun?variable=dswrf&lat=40.0&lon=-75.4&model=hrrr" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for key, run in list(data.get('runs', {}).items())[:1]:
    print(f'Run: {key}')
    for pt in run['series'][:8]:
        print(f'  {pt[\"valid_time\"]}: {pt[\"value\"]}')
"
```
Expected: Values are 0-100 (percent) during daytime, `null` at night

**Step 4: Gemini code review**

```bash
cd /workspace/radarcheck && git diff HEAD~3 | llm -m gemini-3.1-pro-preview "Review this diff for a weather app. It adds solar clearness index computation. Check: 1) Is the solar geometry math correct (Spencer declination, hour angle)? 2) Is the clear-sky model reasonable? 3) Any edge cases with the nighttime null handling? 4) Any issues with the API response format change? Be concise."
```

**Step 5: Final commit (if any fixes from review)**

```bash
git add -A && git commit -m "fix: address code review feedback for solar clearness index"
```

---

## Summary of changes

| File | Change |
|------|--------|
| `rust_worker/crates/core/src/solar.rs` | NEW: solar geometry + clearness index (10 tests) |
| `rust_worker/crates/core/src/lib.rs` | Add `pub mod solar;` |
| `rust_worker/crates/server/src/main.rs` | Convert DSWRF → clearness % in multirun + stitched |
| `rust_worker/crates/core/src/config.rs` | Update dswrf display_name/units |
| `config.py` | Update dswrf display_name/units |
| `templates/index.html` | Update VAR_CONFIG unit + null handling |
