#!/usr/bin/env python3
"""Pre-fetch all forecast data for headless analysis.

Grabs model timeseries from the local API and NWS data in parallel,
then writes a compact JSON to stdout.

Usage:
    python3 scripts/prefetch_forecast_data.py 40.0488 -75.389 "Radnor, PA"
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

API_BASE = "http://localhost:5001"
NWS_BASE = "https://api.weather.gov"
NWS_HEADERS = {"User-Agent": "RadarCheck/1.0", "Accept": "application/geo+json"}

# Model+variable combos for near-term (days=2)
NEAR_TERM = [
    ("hrrr", "asnow"), ("hrrr", "snod"), ("hrrr", "apcp"), ("hrrr", "t2m"),
    ("nam_nest", "snod"), ("nam_nest", "apcp"), ("nam_nest", "t2m"),
    ("gfs", "snod"), ("gfs", "apcp"), ("gfs", "t2m"),
    ("nbm", "asnow"), ("nbm", "apcp"), ("nbm", "t2m"),
    ("ecmwf_hres", "snod"), ("ecmwf_hres", "apcp"), ("ecmwf_hres", "t2m"),
]

# Extended range (days=1, for forecast_hour > 168)
EXTENDED = [
    ("gfs", "apcp"), ("gfs", "t2m"),
    ("ecmwf_hres", "apcp"), ("ecmwf_hres", "t2m"),
]


def fetch_multirun(lat, lon, model, variable, days):
    """Fetch timeseries data from local API and compact it."""
    url = f"{API_BASE}/api/timeseries/multirun"
    params = {"lat": lat, "lon": lon, "model": model, "variable": variable, "days": days}
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"error": str(e)}

    runs_raw = data.get("runs", {})
    if not runs_raw:
        return {"runs": []}

    runs = []
    for key, run_data in sorted(runs_raw.items(), key=lambda x: x[1].get("init_time", "")):
        series = run_data.get("series", [])
        # Compact: [forecast_hour, value]
        compact_series = [[pt["forecast_hour"], round(pt["value"], 3)] for pt in series]
        values = [pt["value"] for pt in series]
        peak = round(max(values), 3) if values else 0
        final = round(values[-1], 3) if values else 0
        runs.append({
            "run_id": run_data["run_id"],
            "init_time": run_data["init_time"],
            "peak": peak,
            "final": final,
            "n_points": len(series),
            "series": compact_series,
        })
    return {"runs": runs}


def c_to_f(c):
    """Celsius to Fahrenheit."""
    if c is None:
        return None
    return round(c * 9 / 5 + 32, 1)


def kmh_to_mph(kmh):
    """km/h to mph."""
    if kmh is None:
        return None
    return round(kmh * 0.621371, 1)


def fetch_nws_data(lat, lon):
    """Fetch NWS current conditions, AFD, and HWO."""
    result = {
        "office": None,
        "current_conditions": None,
        "afd_text": None,
        "hwo_text": None,
    }

    # 1. Get point metadata (forecast office + station list)
    try:
        r = requests.get(f"{NWS_BASE}/points/{lat},{lon}", headers=NWS_HEADERS, timeout=15)
        r.raise_for_status()
        props = r.json()["properties"]
        office = props.get("cwa", "")
        stations_url = props.get("observationStations", "")
        result["office"] = office
    except Exception as e:
        result["error"] = f"Points lookup failed: {e}"
        return result

    # 2. Get nearest station and current conditions
    try:
        r = requests.get(stations_url, headers=NWS_HEADERS, timeout=15)
        r.raise_for_status()
        features = r.json().get("features", [])
        if features:
            station_id = features[0]["properties"]["stationIdentifier"]
            r2 = requests.get(
                f"{NWS_BASE}/stations/{station_id}/observations/latest",
                headers=NWS_HEADERS, timeout=15,
            )
            r2.raise_for_status()
            obs = r2.json()["properties"]

            temp_c = obs.get("temperature", {}).get("value")
            dewpoint_c = obs.get("dewpoint", {}).get("value")
            wind_speed_kmh = obs.get("windSpeed", {}).get("value")
            wind_gust_kmh = obs.get("windGust", {}).get("value")
            wind_dir_deg = obs.get("windDirection", {}).get("value")
            wind_chill_c = obs.get("windChill", {}).get("value")
            visibility_m = obs.get("visibility", {}).get("value")

            # Convert wind direction degrees to cardinal
            directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
            wind_dir = ""
            if wind_dir_deg is not None:
                idx = round(wind_dir_deg / 22.5) % 16
                wind_dir = directions[idx]

            wind_str = f"{wind_dir} {kmh_to_mph(wind_speed_kmh)} mph"
            if wind_gust_kmh:
                wind_str += f" gusting {kmh_to_mph(wind_gust_kmh)} mph"

            result["current_conditions"] = {
                "station": station_id,
                "timestamp": obs.get("timestamp", ""),
                "temperature_f": c_to_f(temp_c),
                "description": obs.get("textDescription", ""),
                "wind": wind_str,
                "wind_chill_f": c_to_f(wind_chill_c),
                "dewpoint_f": c_to_f(dewpoint_c),
                "visibility_miles": round(visibility_m / 1609.34, 1) if visibility_m else None,
                "pressure_inhg": round(obs.get("barometricPressure", {}).get("value", 0) / 3386.39, 2) if obs.get("barometricPressure", {}).get("value") else None,
            }
    except Exception as e:
        result["current_conditions_error"] = str(e)

    # 3. Fetch AFD and HWO in parallel
    def fetch_product(product_type):
        try:
            r = requests.get(
                f"{NWS_BASE}/products",
                params={"type": product_type, "location": office, "limit": 1},
                headers=NWS_HEADERS, timeout=15,
            )
            r.raise_for_status()
            graphs = r.json().get("@graph", [])
            if not graphs:
                return None
            product_url = graphs[0].get("@id", "")
            if not product_url:
                return None
            r2 = requests.get(product_url, headers=NWS_HEADERS, timeout=15)
            r2.raise_for_status()
            return r2.json().get("productText", "")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        afd_future = pool.submit(fetch_product, "AFD")
        hwo_future = pool.submit(fetch_product, "HWO")
        result["afd_text"] = afd_future.result()
        result["hwo_text"] = hwo_future.result()

    return result


def main():
    if len(sys.argv) < 3:
        print("Usage: prefetch_forecast_data.py LAT LON [LOCATION_NAME]", file=sys.stderr)
        sys.exit(1)

    lat = float(sys.argv[1])
    lon = float(sys.argv[2])
    location_name = sys.argv[3] if len(sys.argv) > 3 else f"{lat}, {lon}"

    output = {
        "location": {"lat": lat, "lon": lon, "name": location_name},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "nws": {},
        "models": {},
        "extended_range": {},
    }

    # Fetch everything in parallel
    futures = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        # NWS data
        nws_future = pool.submit(fetch_nws_data, lat, lon)

        # Near-term model data
        for model, var in NEAR_TERM:
            key = (model, var, "near")
            futures[key] = pool.submit(fetch_multirun, lat, lon, model, var, days=2)

        # Extended range model data
        for model, var in EXTENDED:
            key = (model, var, "ext")
            futures[key] = pool.submit(fetch_multirun, lat, lon, model, var, days=1)

        # Collect NWS
        output["nws"] = nws_future.result()

        # Collect model data
        for (model, var, range_type), future in futures.items():
            result = future.result()
            if range_type == "near":
                if model not in output["models"]:
                    output["models"][model] = {}
                output["models"][model][var] = result
            else:
                if model not in output["extended_range"]:
                    output["extended_range"][model] = {}
                output["extended_range"][model][var] = result

    json.dump(output, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
