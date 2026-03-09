#!/usr/bin/env python3
"""Generate qualitative forecast summaries.

Runs hourly. Fetches multirun API data for all variables, aggregates
across models, derives sky/comfort conditions, generates AI text,
and caches the result.

Usage:
    python scripts/qualitative.py --once --lat 40.0 --lon -75.4
    python scripts/qualitative.py  # daemon mode, runs hourly
"""

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_BASE = os.environ.get("RADARCHECK_API_BASE", "http://localhost:5001")
CACHE_DIR = Path(os.environ.get("QUALITATIVE_CACHE_DIR", "cache/qualitative"))
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod"]
SNAPSHOT_RETAIN_HOURS = 25  # keep snapshots for 25h (covers 24h lookback)


def grid_key(lat, lon):
    """Round lat/lon to 0.1 degree grid for cache keying."""
    return f"{lat:.1f}_{lon:.1f}"


def fetch_multirun(lat, lon, variable, days=1):
    """Fetch multirun data from the API."""
    import urllib.request
    url = f"{API_BASE}/api/timeseries/multirun?lat={lat}&lon={lon}&variable={variable}&model=all&days={days}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"Failed to fetch {variable}: {e}")
        return None


def aggregate_hourly(all_data, hours_ahead=8):
    """Aggregate multirun data into per-hour median values across models.

    Returns dict: {hour_offset: {variable: median_value}}
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    result = {}

    for hour_offset in range(1, hours_ahead + 1):
        target_time = now + datetime.timedelta(hours=hour_offset)
        target_hour = target_time.replace(minute=0, second=0, microsecond=0)
        target_iso = target_hour.strftime("%Y-%m-%dT%H:00:00Z")

        hour_data = {}
        for var_id, var_response in all_data.items():
            if var_response is None:
                continue
            values = []
            for run_key, run_info in var_response.get("runs", {}).items():
                for pt in run_info.get("series", []):
                    # Match to nearest hour
                    if pt.get("valid_time", "").startswith(target_iso[:13]):
                        v = pt.get("value")
                        if v is not None:
                            values.append(v)
            if values:
                values.sort()
                mid = len(values) // 2
                hour_data[var_id] = values[mid]  # median

        result[hour_offset] = {
            "time": target_iso,
            "local_label": target_hour.astimezone().strftime("%-I%p").lower(),
            "values": hour_data,
        }

    return result


def derive_sky_condition(cloud_cover, solar_clearness):
    """Derive qualitative sky condition from cloud cover + solar clearness.

    Returns (label, icon) tuple.
    """
    # Nighttime: solar is None
    if solar_clearness is None:
        if cloud_cover is None:
            return ("Unknown", "question")
        if cloud_cover < 20:
            return ("Clear", "moon")
        if cloud_cover < 60:
            return ("Partly cloudy", "cloud-moon")
        if cloud_cover < 90:
            return ("Mostly cloudy", "cloud")
        return ("Overcast", "cloud")

    # Daytime: combine both signals
    if cloud_cover is not None:
        if cloud_cover < 20 and solar_clearness > 80:
            return ("Bright sunny", "sun")
        if cloud_cover < 60 and solar_clearness > 60:
            return ("Bright cloudy", "cloud-sun")
        if cloud_cover < 60:
            return ("Partly cloudy", "cloud-sun")
        if cloud_cover < 90 and solar_clearness > 40:
            return ("Mostly cloudy, some sun", "cloud-sun")
        if cloud_cover < 90:
            return ("Overcast", "cloud")
        if solar_clearness < 20:
            return ("Dark and heavy", "cloud")
        return ("Overcast", "cloud")

    # No cloud data, just solar
    if solar_clearness > 80:
        return ("Sunny", "sun")
    if solar_clearness > 50:
        return ("Hazy", "cloud-sun")
    return ("Cloudy", "cloud")


def derive_comfort(temp_f, dpt_f):
    """Derive comfort label from temperature and dew point."""
    if dpt_f is None:
        if temp_f is None:
            return None
        return f"{temp_f:.0f}F"

    if dpt_f < 40:
        comfort = "Crisp"
    elif dpt_f < 55:
        comfort = "Comfortable"
    elif dpt_f < 65:
        comfort = "Sticky"
    elif dpt_f < 70:
        comfort = "Muggy"
    else:
        comfort = "Oppressive"

    if temp_f is not None:
        return f"{temp_f:.0f}F - {comfort}"
    return comfort


def derive_precip(apcp, asnow):
    """Derive precipitation label."""
    if asnow is not None and asnow > 0.05:
        return f"Snow {asnow:.1f} in"
    if apcp is not None and apcp > 0.01:
        return f"Rain {apcp:.2f} in"
    return None


def build_hourly_summary(hourly_data):
    """Build derived conditions for each hour."""
    hours = []
    for offset in sorted(hourly_data.keys()):
        h = hourly_data[offset]
        vals = h["values"]
        sky_label, sky_icon = derive_sky_condition(
            vals.get("cloud_cover"), vals.get("dswrf")
        )
        comfort = derive_comfort(vals.get("t2m"), vals.get("dpt"))
        precip = derive_precip(vals.get("apcp"), vals.get("asnow"))

        hours.append({
            "time": h["time"],
            "label": h["local_label"],
            "sky": sky_label,
            "sky_icon": sky_icon,
            "comfort": comfort,
            "precip": precip,
            "raw": vals,
        })
    return hours


def compute_trends(current_hourly, cache_dir, grid_id):
    """Compare current forecast to snapshots from 1h/6h/24h ago.

    Returns dict of trend descriptions.
    """
    trends = {}
    now = datetime.datetime.now(datetime.timezone.utc)

    # Average the first 4 hours of current forecast
    current_avgs = {}
    for var in ["t2m", "dpt", "cloud_cover", "dswrf", "apcp"]:
        vals = [h["raw"].get(var) for h in current_hourly[:4] if h["raw"].get(var) is not None]
        if vals:
            current_avgs[var] = sum(vals) / len(vals)

    snapshot_dir = cache_dir / "snapshots" / grid_id
    for label, hours_ago in [("1h ago", 1), ("6h ago", 6), ("24h ago", 24)]:
        target_time = now - datetime.timedelta(hours=hours_ago)
        # Find closest snapshot file
        snapshot_file = find_closest_snapshot(snapshot_dir, target_time)
        if snapshot_file is None:
            continue

        try:
            with open(snapshot_file) as f:
                old_data = json.load(f)
            old_avgs = old_data.get("averages", {})
        except Exception:
            continue

        deltas = {}
        for var, threshold, unit, direction in [
            ("t2m", 3.0, "F", ("warmer", "cooler")),
            ("apcp", 0.05, "in", ("wetter", "drier")),
            ("cloud_cover", 15.0, "%", ("cloudier", "clearer")),
            ("dswrf", 15.0, "%", ("less sunny", "sunnier")),
        ]:
            if var in current_avgs and var in old_avgs:
                delta = current_avgs[var] - old_avgs[var]
                if abs(delta) >= threshold:
                    word = direction[0] if delta > 0 else direction[1]
                    deltas[var] = f"{word} than {label}"

        if deltas:
            trends[label] = deltas

    return trends, current_avgs


def find_closest_snapshot(snapshot_dir, target_time):
    """Find the snapshot file closest to target_time."""
    if not snapshot_dir.exists():
        return None

    best_file = None
    best_delta = float("inf")
    target_ts = target_time.timestamp()

    for f in snapshot_dir.iterdir():
        if not f.name.endswith(".json"):
            continue
        try:
            file_ts = float(f.stem)
            delta = abs(file_ts - target_ts)
            if delta < best_delta and delta < 7200:  # within 2 hours
                best_delta = delta
                best_file = f
        except ValueError:
            continue

    return best_file


def save_snapshot(cache_dir, grid_id, current_avgs):
    """Save current averages as a timestamped snapshot for future trend comparison."""
    snapshot_dir = cache_dir / "snapshots" / grid_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now(datetime.timezone.utc)
    filename = f"{now.timestamp():.0f}.json"
    with open(snapshot_dir / filename, "w") as f:
        json.dump({"averages": current_avgs, "time": now.isoformat()}, f)

    # Prune old snapshots
    cutoff = now - datetime.timedelta(hours=SNAPSHOT_RETAIN_HOURS)
    for snap in snapshot_dir.iterdir():
        try:
            ts = float(snap.stem)
            if ts < cutoff.timestamp():
                snap.unlink()
        except (ValueError, OSError):
            pass


def generate_ai_text(hours_summary, trends, lat, lon):
    """Generate qualitative text using Gemini Flash via llm CLI."""
    prompt_data = {
        "hours": [
            {"time": h["label"], "sky": h["sky"], "comfort": h["comfort"], "precip": h["precip"]}
            for h in hours_summary
        ],
        "trends": trends,
    }

    prompt = f"""Given this 8-hour forecast:
{json.dumps(prompt_data, indent=2)}

Write 2-3 sentences describing conditions and notable changes.
Be conversational, mention specific times if conditions shift.
If trends show changes vs earlier forecasts, mention them naturally.
Do not use emoji. Do not start with "The forecast" or "Looking ahead"."""

    try:
        result = subprocess.run(
            ["llm", "-m", "gemini-3-flash-preview"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        log.warning(f"LLM failed: {result.stderr}")
    except Exception as e:
        log.warning(f"LLM error: {e}")

    # Fallback: simple rule-based text from first hour's data
    first = hours_summary[0]
    return f"{first['sky']}. {first['comfort'] or ''}."


def generate_summary(lat, lon, cache_dir):
    """Main generation function for a single lat/lon."""
    grid_id = grid_key(lat, lon)
    log.info(f"Generating summary for {grid_id}")

    # Fetch all variables
    all_data = {}
    for var in VARIABLES:
        all_data[var] = fetch_multirun(lat, lon, var, days=1)

    # Aggregate into hourly medians
    hourly_data = aggregate_hourly(all_data)
    if not hourly_data:
        log.warning("No data available")
        return None

    # Derive conditions
    hours_summary = build_hourly_summary(hourly_data)

    # Compute trends
    trends, current_avgs = compute_trends(hours_summary, cache_dir, grid_id)

    # Save snapshot for future trend comparison
    save_snapshot(cache_dir, grid_id, current_avgs)

    # Generate AI text
    ai_text = generate_ai_text(hours_summary, trends, lat, lon)

    # Build final result
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "hours": hours_summary,
        "trends": trends,
        "text": ai_text,
    }

    # Cache result
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{grid_id}.json"
    with open(cache_file, "w") as f:
        json.dump(result, f, indent=2)

    log.info(f"Summary saved to {cache_file}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Qualitative forecast generator")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--lat", type=float, default=40.0)
    parser.add_argument("--lon", type=float, default=-75.4)
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between runs")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.once:
        result = generate_summary(args.lat, args.lon, CACHE_DIR)
        if result:
            print(json.dumps(result, indent=2))
        return

    while True:
        try:
            generate_summary(args.lat, args.lon, CACHE_DIR)
        except Exception as e:
            log.error(f"Generation failed: {e}", exc_info=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
