//! Solar geometry functions for computing clearness index.
//!
//! Clearness index = forecast DSWRF / clear-sky DSWRF, expressed as a percentage.
//! This replaces raw DSWRF (W/m²) in the UI with "% clear sky".

use chrono::{DateTime, Datelike, Timelike, Utc};

/// Solar constant in W/m² (total solar irradiance at top of atmosphere).
pub const SOLAR_CONSTANT: f64 = 1361.0;

/// Minimum clear-sky DSWRF (W/m²) to compute clearness index.
/// Below this threshold (sun near horizon), clearness index is unreliable
/// due to extreme air mass and diffuse twilight radiation artifacts.
const CLEARNESS_MIN_THRESHOLD: f64 = 20.0;

/// Solar declination using the Spencer (1971) Fourier approximation.
///
/// Returns declination in radians. `day_of_year` is 1-based (Jan 1 = 1).
pub fn solar_declination(day_of_year: u32) -> f64 {
    // Fractional year in radians (Spencer uses day angle B)
    let b = 2.0 * std::f64::consts::PI * (day_of_year as f64 - 1.0) / 365.0;

    // Spencer (1971) formula — returns radians directly
    0.006918
        - 0.399912 * b.cos()
        + 0.070257 * b.sin()
        - 0.006758 * (2.0 * b).cos()
        + 0.000907 * (2.0 * b).sin()
        - 0.002697 * (3.0 * b).cos()
        + 0.00148 * (3.0 * b).sin()
}

/// Solar elevation angle above the horizon.
///
/// All inputs in radians:
/// - `lat_rad`: observer latitude
/// - `declination`: solar declination (from `solar_declination`)
/// - `hour_angle`: solar hour angle (from `hour_angle`)
///
/// Returns elevation in radians. Negative means sun is below horizon.
pub fn solar_elevation(lat_rad: f64, declination: f64, hour_angle_rad: f64) -> f64 {
    let sin_elev = lat_rad.sin() * declination.sin()
        + lat_rad.cos() * declination.cos() * hour_angle_rad.cos();
    sin_elev.asin()
}

/// Equation of Time correction in minutes (Spencer 1971).
///
/// Accounts for the seasonal drift of solar noon due to orbital eccentricity
/// and axial tilt. Ranges ±15 minutes throughout the year.
fn equation_of_time_minutes(day_of_year: u32) -> f64 {
    let b = 2.0 * std::f64::consts::PI * (day_of_year as f64 - 1.0) / 365.0;
    229.18 * (0.000075 + 0.001868 * b.cos() - 0.032077 * b.sin()
        - 0.014615 * (2.0 * b).cos() - 0.040849 * (2.0 * b).sin())
}

/// Compute the solar hour angle from UTC hour, longitude, and day of year.
///
/// Uses Equation of Time to correct for seasonal drift of solar noon.
///
/// - `utc_hour`: fractional UTC hour (e.g. 17.5 = 17:30 UTC)
/// - `lon_deg`: longitude in degrees (west is negative)
/// - `day_of_year`: 1-based day of year (for EoT correction)
///
/// Returns hour angle in radians. 0 = solar noon, negative = morning, positive = afternoon.
pub fn hour_angle(utc_hour: f64, lon_deg: f64, day_of_year: u32) -> f64 {
    let eot_hours = equation_of_time_minutes(day_of_year) / 60.0;
    // True solar time = UTC + longitude offset + EoT
    let solar_hour = utc_hour + lon_deg / 15.0 + eot_hours;
    // Hour angle: 0 at noon, 15°/hour
    (solar_hour - 12.0) * 15.0_f64.to_radians()
}

/// Clear-sky downward shortwave radiation at the surface.
///
/// Uses Beer-Lambert air-mass model instead of flat transmittance:
/// - Earth-Sun distance eccentricity correction (±3.3% seasonal)
/// - Kasten-Young air mass formula (accounts for atmospheric path length)
/// - Transmittance = 0.7^(AM^0.678) — standard clear-sky optical depth
///
/// Returns 0.0 if the sun is below the horizon.
///
/// - `lat_deg`: latitude in degrees
/// - `lon_deg`: longitude in degrees (west is negative)
/// - `day_of_year`: 1-based day of year
/// - `utc_hour`: fractional UTC hour
pub fn clear_sky_dswrf(lat_deg: f64, lon_deg: f64, day_of_year: u32, utc_hour: f64) -> f64 {
    let decl = solar_declination(day_of_year);
    let ha = hour_angle(utc_hour, lon_deg, day_of_year);
    let lat_rad = lat_deg.to_radians();
    let elev = solar_elevation(lat_rad, decl, ha);

    let elev_deg = elev.to_degrees();
    if elev_deg <= 0.0 {
        return 0.0;
    }

    // Earth-Sun distance eccentricity (varies ±3.3% through the year)
    let b = 2.0 * std::f64::consts::PI * day_of_year as f64 / 365.0;
    let eccentricity = 1.0 + 0.033 * b.cos();
    let extraterrestrial = SOLAR_CONSTANT * eccentricity;

    // Air mass: Kasten-Young (1989) — avoids extreme values near horizon
    let air_mass = 1.0 / (elev.sin() + 0.50572 * (elev_deg + 6.07995).powf(-1.6364));

    // Clear-sky transmittance based on air mass (Beer-Lambert with empirical exponent)
    let transmittance = 0.7_f64.powf(air_mass.powf(0.678));

    extraterrestrial * transmittance * elev.sin()
}

/// Clearness index: forecast DSWRF as a percentage of clear-sky DSWRF.
///
/// Returns `None` at night (clear-sky DSWRF ≈ 0), otherwise returns
/// the ratio clamped to 0–100%.
///
/// - `forecast_dswrf`: forecast surface shortwave radiation in W/m²
/// - `lat_deg`, `lon_deg`: observer position in degrees
/// - `day_of_year`: 1-based day of year
/// - `utc_hour`: fractional UTC hour
pub fn clearness_index(
    forecast_dswrf: f64,
    lat_deg: f64,
    lon_deg: f64,
    day_of_year: u32,
    utc_hour: f64,
) -> Option<f64> {
    let clear = clear_sky_dswrf(lat_deg, lon_deg, day_of_year, utc_hour);

    // Twilight threshold: below 20 W/m² (sun near horizon), clearness index
    // is unreliable due to extreme air mass and diffuse radiation artifacts
    if clear < CLEARNESS_MIN_THRESHOLD {
        return None;
    }

    let ratio = (forecast_dswrf / clear) * 100.0;
    Some(ratio.clamp(0.0, 100.0))
}

/// Convert a Unix timestamp (seconds since 1970-01-01 UTC) to (day_of_year, fractional_utc_hour).
///
/// Day of year is 1-based. Fractional hour includes minutes and seconds.
pub fn unix_to_doy_hour(unix_secs: i64) -> (u32, f64) {
    let dt = DateTime::<Utc>::from_timestamp(unix_secs, 0)
        .expect("invalid unix timestamp");
    let doy = dt.ordinal(); // 1-based day of year
    let hour = dt.hour() as f64 + dt.minute() as f64 / 60.0 + dt.second() as f64 / 3600.0;
    (doy, hour)
}

/// Convenience wrapper: clearness index from a Unix timestamp.
///
/// Converts `unix_secs` to day-of-year and UTC hour, then delegates to `clearness_index`.
pub fn clearness_index_from_unix(
    forecast_dswrf: f64,
    lat_deg: f64,
    lon_deg: f64,
    unix_secs: i64,
) -> Option<f64> {
    let (doy, utc_hour) = unix_to_doy_hour(unix_secs);
    clearness_index(forecast_dswrf, lat_deg, lon_deg, doy, utc_hour)
}

#[cfg(test)]
mod tests {
    use super::*;

    const PHILLY_LAT: f64 = 40.0;
    const PHILLY_LON: f64 = -75.4;

    /// Summer solstice (day ~172): declination should be ~+23.44°
    #[test]
    fn test_solar_declination_summer_solstice() {
        let decl = solar_declination(172);
        let decl_deg = decl.to_degrees();
        assert!(
            (decl_deg - 23.44).abs() < 1.0,
            "Summer solstice declination should be ~23.44°, got {decl_deg:.2}°"
        );
    }

    /// Winter solstice (day ~355): declination should be ~-23.44°
    #[test]
    fn test_solar_declination_winter_solstice() {
        let decl = solar_declination(355);
        let decl_deg = decl.to_degrees();
        assert!(
            (decl_deg + 23.44).abs() < 1.0,
            "Winter solstice declination should be ~-23.44°, got {decl_deg:.2}°"
        );
    }

    /// Philadelphia at noon local (17:00 UTC) on summer solstice: ~800-1100 W/m²
    #[test]
    fn test_clear_sky_noon_summer_philadelphia() {
        let dswrf = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 17.0);
        println!("Clear-sky DSWRF at Philly noon summer: {dswrf:.1} W/m²");
        assert!(
            dswrf > 800.0 && dswrf < 1100.0,
            "Expected 800-1100 W/m², got {dswrf:.1}"
        );
    }

    /// Philadelphia at midnight local (4:00 UTC): should be 0
    #[test]
    fn test_clear_sky_midnight_is_zero() {
        let dswrf = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 4.0);
        assert!(
            dswrf == 0.0,
            "Midnight DSWRF should be 0, got {dswrf:.1}"
        );
    }

    /// Clearness index at night should return None
    #[test]
    fn test_clearness_index_nighttime_returns_none() {
        let ci = clearness_index(0.0, PHILLY_LAT, PHILLY_LON, 172, 4.0);
        assert!(ci.is_none(), "Nighttime clearness index should be None");
    }

    /// Clearness index when forecast equals clear-sky: should be ~100%
    #[test]
    fn test_clearness_index_clear_sky() {
        let clear = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 17.0);
        let ci = clearness_index(clear, PHILLY_LAT, PHILLY_LON, 172, 17.0)
            .expect("Should not be None during daytime");
        println!("Clearness index for clear sky: {ci:.1}%");
        assert!(
            (ci - 100.0).abs() < 1.0,
            "Clear-sky clearness index should be ~100%, got {ci:.1}%"
        );
    }

    /// Clearness index for overcast (~200 W/m² from ~950 clear): ~21%
    #[test]
    fn test_clearness_index_overcast() {
        let clear = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 17.0);
        let overcast_dswrf = clear * 0.21;
        let ci = clearness_index(overcast_dswrf, PHILLY_LAT, PHILLY_LON, 172, 17.0)
            .expect("Should not be None during daytime");
        println!("Clearness index for overcast: {ci:.1}%");
        assert!(
            (ci - 21.0).abs() < 2.0,
            "Overcast clearness index should be ~21%, got {ci:.1}%"
        );
    }

    /// Clearness index should clamp to 100% even if forecast exceeds clear-sky
    #[test]
    fn test_clearness_index_clamped_to_100() {
        let clear = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 17.0);
        // Forecast higher than clear-sky (can happen with cloud-edge enhancement)
        let ci = clearness_index(clear * 1.5, PHILLY_LAT, PHILLY_LON, 172, 17.0)
            .expect("Should not be None during daytime");
        assert!(
            (ci - 100.0).abs() < 0.01,
            "Clearness index should be clamped to 100%, got {ci:.1}%"
        );
    }

    /// clearness_index_from_unix: test with a known daytime timestamp
    /// 2024-06-20 17:00 UTC (summer solstice, noon in Philly)
    #[test]
    fn test_clearness_index_from_unix() {
        // 2024-06-20 17:00:00 UTC
        let unix_secs = 1718902800_i64;
        let clear = clear_sky_dswrf(PHILLY_LAT, PHILLY_LON, 172, 17.0);
        let ci = clearness_index_from_unix(clear, PHILLY_LAT, PHILLY_LON, unix_secs)
            .expect("Should not be None during daytime");
        println!("Clearness index from unix: {ci:.1}%");
        assert!(
            (ci - 100.0).abs() < 5.0,
            "Should be close to 100% since we passed the clear-sky value, got {ci:.1}%"
        );
    }

    /// unix_to_doy_hour: Jan 1 00:00:00 UTC → (1, 0.0)
    #[test]
    fn test_unix_to_doy_hour() {
        // 2024-01-01 00:00:00 UTC
        let unix_secs = 1704067200_i64;
        let (doy, hour) = unix_to_doy_hour(unix_secs);
        assert_eq!(doy, 1, "Jan 1 should be day 1, got {doy}");
        assert!(
            hour.abs() < 0.001,
            "Midnight should be hour 0.0, got {hour:.3}"
        );
    }
}
