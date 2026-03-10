#!/usr/bin/env python3
"""Generate qualitative forecast summaries (v2 — LLM-driven).

Runs hourly. Fetches raw model data from HRRR (latest 2 runs) and GFS
(latest run), plus trend snapshots from 1h/6h/24h ago. Passes everything
to Gemini Flash which produces both structured hourly columns and a
meteorologist-style narrative.

Usage:
    python scripts/qualitative.py --once --lat 40.0 --lon -75.4
    python scripts/qualitative.py  # daemon mode, runs hourly
"""

import argparse
import datetime
import json
import logging
import os
import re
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
SNAPSHOT_RETAIN_HOURS = 25
VALID_ICONS = {"sun", "moon", "cloud", "cloud-sun", "cloud-moon", "cloud-rain", "snowflake", "question"}


def grid_key(lat, lon):
    """Round lat/lon to 0.1 degree grid for cache keying."""
    return f"{lat:.1f}_{lon:.1f}"


def fetch_multirun(lat, lon, variable, model="all", days=1):
    """Fetch multirun data from the API."""
    import urllib.request
    url = f"{API_BASE}/api/timeseries/multirun?lat={lat}&lon={lon}&variable={variable}&model={model}&days={days}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning(f"Failed to fetch {variable}/{model}: {e}")
        return None


def extract_latest_runs(api_response, model_id, count=1):
    """Extract the latest N runs for a specific model from multirun API response.

    Returns list of (init_time, {valid_time: value}) dicts, newest first.
    """
    if api_response is None:
        return []

    runs_by_init = []
    for run_key, run_info in api_response.get("runs", {}).items():
        rid = run_info.get("model_id", run_key.split("/")[0])
        if rid != model_id:
            continue
        init_time = run_info.get("init_time", "")
        values_by_time = {}
        for pt in run_info.get("series", []):
            vt = pt.get("valid_time", "")
            v = pt.get("value")
            if vt and v is not None:
                values_by_time[vt] = round(v, 1)
        runs_by_init.append((init_time, values_by_time))

    runs_by_init.sort(key=lambda x: x[0], reverse=True)
    return runs_by_init[:count]


def build_model_data(lat, lon, hours_ahead=24):
    """Fetch raw model data and build compact per-model, per-hour payload.

    Returns (model_data dict, hour_labels list, all_data for snapshot).
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    # Build target hours
    target_hours = []
    for offset in range(1, hours_ahead + 1):
        t = (now + datetime.timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
        target_hours.append(t)

    hour_labels = [t.astimezone().strftime("%-I%p").lower() for t in target_hours]
    hour_isos = [t.strftime("%Y-%m-%dT%H:00:00") for t in target_hours]

    # Fetch all variables for all models at once
    all_data = {}
    for var in VARIABLES:
        all_data[var] = fetch_multirun(lat, lon, var, model="all", days=1)

    def extract_hourly(runs_list, hour_isos):
        """Given a list of (init_time, {valid_time: value}), return values for target hours."""
        if not runs_list:
            return [None] * len(hour_isos)
        _, values_by_time = runs_list[0]
        result = []
        for iso in hour_isos:
            # Match by hour prefix (valid_time may have timezone suffix)
            val = None
            for vt, v in values_by_time.items():
                if vt.startswith(iso[:13]):
                    val = v
                    break
            result.append(val)
        return result

    # Build per-model data
    model_data = {}

    # HRRR: latest 2 runs
    for var in VARIABLES:
        hrrr_runs = extract_latest_runs(all_data[var], "hrrr", count=2)
        for i, (init_time, _) in enumerate(hrrr_runs):
            label = "hrrr_latest" if i == 0 else "hrrr_previous"
            if label not in model_data:
                model_data[label] = {"init": init_time, "hours": hour_labels[:], "data": {}}
            model_data[label]["data"][var] = extract_hourly([hrrr_runs[i]], hour_isos)

    # GFS: latest run
    for var in VARIABLES:
        gfs_runs = extract_latest_runs(all_data[var], "gfs", count=1)
        if gfs_runs:
            label = "gfs"
            if label not in model_data:
                model_data[label] = {"init": gfs_runs[0][0], "hours": hour_labels[:], "data": {}}
            model_data[label]["data"][var] = extract_hourly(gfs_runs, hour_isos)

    # Build current all-model median for snapshots (backward compat)
    current_by_time = {}
    for idx, iso in enumerate(hour_isos):
        full_iso = iso + "Z"
        vals = {}
        for var in VARIABLES:
            all_values = []
            if all_data[var] is None:
                continue
            for run_key, run_info in all_data[var].get("runs", {}).items():
                for pt in run_info.get("series", []):
                    if pt.get("valid_time", "").startswith(iso[:13]):
                        v = pt.get("value")
                        if v is not None:
                            all_values.append(v)
                            break
            if all_values:
                all_values.sort()
                vals[var] = all_values[len(all_values) // 2]
        current_by_time[full_iso] = vals

    return model_data, hour_labels, hour_isos, current_by_time


def load_trend_snapshots(cache_dir, grid_id, hour_isos):
    """Load trend snapshots and build per-hour delta data for LLM."""
    now = datetime.datetime.now(datetime.timezone.utc)
    snapshot_dir = cache_dir / "snapshots" / grid_id
    trend_vars = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp"]

    trends = {}
    for label, hours_ago in [("1h_ago", 1), ("6h_ago", 6), ("24h_ago", 24)]:
        target_time = now - datetime.timedelta(hours=hours_ago)
        snapshot_file = find_closest_snapshot(snapshot_dir, target_time)
        if snapshot_file is None:
            continue

        try:
            with open(snapshot_file) as f:
                old_data = json.load(f)
            old_by_time = old_data.get("by_valid_time", {})
        except Exception:
            continue

        if not old_by_time:
            continue

        # Build per-hour deltas for overlapping valid times
        per_hour = {}
        for iso in hour_isos:
            full_iso = iso + "Z"
            old_vals = old_by_time.get(full_iso, {})
            if not old_vals:
                continue
            deltas = {}
            for var in trend_vars:
                old_v = old_vals.get(var)
                if old_v is not None:
                    deltas[var] = round(old_v, 1)
            if deltas:
                per_hour[iso[:13]] = deltas

        if per_hour:
            trends[label] = per_hour

    return trends


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
            if delta < best_delta and delta < 7200:
                best_delta = delta
                best_file = f
        except ValueError:
            continue

    return best_file


def save_snapshot(cache_dir, grid_id, current_by_time):
    """Save current per-valid-time forecast as a timestamped snapshot."""
    snapshot_dir = cache_dir / "snapshots" / grid_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now(datetime.timezone.utc)
    filename = f"{now.timestamp():.0f}.json"
    with open(snapshot_dir / filename, "w") as f:
        json.dump({"by_valid_time": current_by_time, "time": now.isoformat()}, f)

    cutoff = now - datetime.timedelta(hours=SNAPSHOT_RETAIN_HOURS)
    for snap in snapshot_dir.iterdir():
        try:
            ts = float(snap.stem)
            if ts < cutoff.timestamp():
                snap.unlink()
        except (ValueError, OSError):
            pass


def build_prompt(model_data, trends, hour_labels):
    """Build the LLM prompt with raw model data and trend snapshots."""
    sections = []
    sections.append("""You are a meteorologist who finally got their own forecast column. This is your chance to shine.
Think of those entertaining highway signs that make people smile — that energy, but for weather.
You have raw data from multiple models. Your job is to interpret this data, tell the story of
the next 24 hours, and make it genuinely fun to read. Be witty, be opinionated, have a voice.
But always be accurate — you're entertaining AND informative.
""")

    for model_label, mdata in model_data.items():
        init = mdata.get("init", "unknown")
        sections.append(f"## {model_label} (init: {init})")
        sections.append(f"Hours: {', '.join(hour_labels)}")
        for var, values in mdata.get("data", {}).items():
            vals_str = ", ".join(str(v) if v is not None else "-" for v in values)
            sections.append(f"  {var}: {vals_str}")
        sections.append("")

    sections.append("Variable key: t2m=temp(°F), dpt=dewpoint(°F), cloud_cover=clouds(%), dswrf=solar(W/m²), apcp=rain(in), asnow=snow(in), snod=snow_depth(in)")
    sections.append("")

    if trends:
        sections.append("## Previous forecast snapshots (what older forecasts predicted for these same hours)")
        for label, per_hour in trends.items():
            sections.append(f"### {label}")
            for hour_key, vals in per_hour.items():
                vals_str = ", ".join(f"{k}={v}" for k, v in vals.items())
                sections.append(f"  {hour_key}: {vals_str}")
        sections.append("")

    sections.append("""## Your task

Produce JSON with this exact structure:

{
  "hours": [
    {
      "time": "<hour label>",
      "icon": "<icon>",
      "cloud_pct": <0-100>,
      "clearness": <0-100>,
      "precip_type": null | "rain" | "snow",
      "is_night": true/false,
      "lines": ["<line1>", "<line2>", ...],
      "temp": <number>
    },
    ... one per forecast hour
  ],
  "narrative": "<3-5 sentence meteorologist brief>"
}

## Rules for "lines" (this is the most important part)

Each hour gets 2-3 short lines displayed in a vertical timeline. You have space — use it creatively.
- Line 1: ALWAYS the temperature (e.g. "53°F")
- Line 2+: The most INTERESTING thing about this hour. Be creative and fun! Ideas:
  - Interpret conditions with personality: "Crisp enough for a hoodie" not "dewpoint 32°F"
  - Model drama: "GFS wants rain, HRRR says no way"
  - Forecast evolution: "Yesterday's storm? Gone. Poof." or "Rain just crashed the party"
  - Vibe check: "Perfect dog-walking weather", "Peak patio hour", "You'll regret shorts"
  - Snarky observations: "Models finally agree on something", "GFS being optimistic again"
  - Notable transitions: "Clouds rolling in like they own the place"
- Do NOT repeat the same commentary for consecutive hours. If 6 hours are all clear, find something different and interesting to say each time. Boring is the only sin.
- MUST include precip info if rain/snow is non-zero.
- Temperature is the ONLY raw number that should appear. Interpret everything else.

## Rules for SVG fields

- "cloud_pct": 0 = clear sky, 100 = fully overcast. Drives cloud shape size in the icon.
- "clearness": 0 = dark/hazy, 100 = brilliant sun/bright moon. Drives sun/moon opacity.
- "precip_type": null if dry, "rain" or "snow" if precipitating
- "is_night": true if before sunrise or after sunset
- "icon": one of sun, moon, cloud, cloud-sun, cloud-moon, cloud-rain, snowflake (used as fallback)

## Rules for "narrative"

3-5 sentences. This is YOUR column — own it. Tell the story of the next 24 hours with personality.
- Attribute to specific models when they disagree (make it dramatic if warranted)
- Call out how the forecast has evolved vs earlier runs
- Mention specific times when conditions shift
- Describe what kind of day/night it will be — paint the picture
- Have fun with it. Be the weather person people actually want to read. No emoji though.

Output ONLY the JSON object. No markdown fences, no explanation.""")

    return "\n".join(sections)


def parse_llm_response(stdout):
    """Parse and validate the LLM JSON response."""
    text = stdout.strip()

    # Strip markdown code fences if present
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()

    data = json.loads(text)

    if not isinstance(data.get("hours"), list) or len(data["hours"]) < 1:
        raise ValueError("Missing or empty 'hours' array")

    # Validate and fix fields
    for h in data["hours"]:
        if h.get("icon") not in VALID_ICONS:
            h["icon"] = "question"
        if not isinstance(h.get("lines"), list):
            h["lines"] = [f"{h.get('temp', '?')}°F"]
        # Ensure SVG fields have defaults
        h.setdefault("cloud_pct", 50)
        h.setdefault("clearness", 50)
        h.setdefault("precip_type", None)
        h.setdefault("is_night", False)

    if "narrative" not in data or not data["narrative"]:
        data["narrative"] = " ".join(data["hours"][0].get("lines", []))

    return data


# --- Fallback: rule-based derivation (used if LLM fails) ---

def derive_sky_condition(cloud_cover, solar):
    if solar is not None and solar <= 0:
        solar = None
    if solar is None:
        if cloud_cover is None:
            return ("Unknown", "question")
        if cloud_cover < 20:
            return ("Clear", "moon")
        if cloud_cover < 60:
            return ("Partly cloudy", "cloud-moon")
        if cloud_cover < 90:
            return ("Mostly cloudy", "cloud")
        return ("Overcast", "cloud")
    if cloud_cover is not None:
        if cloud_cover < 20:
            return ("Sunny", "sun")
        if cloud_cover < 60:
            return ("Partly cloudy", "cloud-sun")
        if cloud_cover < 90:
            return ("Mostly cloudy", "cloud")
        return ("Overcast", "cloud")
    return ("Unknown", "question")


def build_fallback(model_data, hour_labels):
    """Build fallback hourly data from rule-based derivation."""
    hours = []
    # Use hrrr_latest if available, else first model
    src = model_data.get("hrrr_latest") or next(iter(model_data.values()), None)
    if src is None:
        return hours

    data = src.get("data", {})
    for i, label in enumerate(hour_labels):
        t2m = data.get("t2m", [None] * 8)[i] if i < len(data.get("t2m", [])) else None
        cloud = data.get("cloud_cover", [None] * 8)[i] if i < len(data.get("cloud_cover", [])) else None
        solar = data.get("dswrf", [None] * 8)[i] if i < len(data.get("dswrf", [])) else None
        apcp = data.get("apcp", [None] * 8)[i] if i < len(data.get("apcp", [])) else None

        sky_label, icon = derive_sky_condition(cloud, solar)
        lines = []
        if t2m is not None:
            lines.append(f"{t2m:.0f}°F")
        lines.append(sky_label)
        if apcp is not None and apcp > 0.01:
            lines.append(f"Rain {apcp:.2f} in")

        hours.append({
            "time": label,
            "icon": icon,
            "lines": lines,
            "temp": round(t2m) if t2m is not None else None,
        })
    return hours


def generate_summary(lat, lon, cache_dir):
    """Main generation function for a single lat/lon."""
    grid_id = grid_key(lat, lon)
    log.info(f"Generating summary for {grid_id}")

    # Fetch raw model data
    model_data, hour_labels, hour_isos, current_by_time = build_model_data(lat, lon)

    if not model_data:
        log.warning("No model data available")
        return None

    # Load trend snapshots
    trends = load_trend_snapshots(cache_dir, grid_id, hour_isos)

    # Save snapshot for future trend comparison
    save_snapshot(cache_dir, grid_id, current_by_time)

    # Build prompt
    prompt = build_prompt(model_data, trends, hour_labels)

    # Call LLM
    raw_output = {"stdout": "", "stderr": "", "exit_code": None}
    llm_data = None
    try:
        result = subprocess.run(
            ["gemini", "-m", "gemini-3.1-pro-preview"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        raw_output["stdout"] = result.stdout
        raw_output["stderr"] = result.stderr
        raw_output["exit_code"] = result.returncode

        if result.stdout.strip():
            llm_data = parse_llm_response(result.stdout)
            log.info("LLM produced valid JSON response")
        else:
            log.warning(f"LLM produced no output: {result.stderr[:200]}")
    except json.JSONDecodeError as e:
        log.warning(f"LLM returned invalid JSON: {e}")
        raw_output["parse_error"] = str(e)
    except Exception as e:
        log.warning(f"LLM error: {e}")
        raw_output["error"] = str(e)

    # Fallback if LLM failed
    if llm_data is None:
        log.info("Using rule-based fallback")
        fallback_hours = build_fallback(model_data, hour_labels)
        llm_data = {
            "hours": fallback_hours,
            "narrative": "Forecast summary temporarily unavailable. Showing basic conditions from HRRR.",
        }

    # Build final result
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "hours": llm_data["hours"],
        "narrative": llm_data["narrative"],
        "prompt": prompt,
        "llm_raw": raw_output,
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
