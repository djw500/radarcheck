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
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

API_BASE = os.environ.get("RADARCHECK_API_BASE", "http://localhost:5001")
CACHE_DIR = Path(os.environ.get("QUALITATIVE_CACHE_DIR", "cache/qualitative"))
VARIABLES = ["t2m", "dpt", "cloud_cover", "dswrf", "apcp", "asnow", "snod", "wind_10m", "gust", "refc"]
SNAPSHOT_RETAIN_HOURS = 25
VALID_ICONS = {"sun", "moon", "cloud", "cloud-sun", "cloud-moon", "cloud-rain", "snowflake", "question"}


def sun_times(lat, lon, date):
    """Fetch official sunrise/sunset from sunrise-sunset.org API."""
    import urllib.request
    url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&date={date}&formatted=0"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "radarcheck/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "OK":
            r = data["results"]
            rise = datetime.datetime.fromisoformat(r["sunrise"])
            sset = datetime.datetime.fromisoformat(r["sunset"])
            return rise, sset
    except Exception as e:
        log.warning(f"Sunrise API failed for {date}: {e}")
    return None, None


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
                values_by_time[vt] = round(v, 4)
        runs_by_init.append((init_time, values_by_time))

    runs_by_init.sort(key=lambda x: x[0], reverse=True)
    return runs_by_init[:count]


def build_model_data(lat, lon, hours_ahead=48):
    """Fetch raw model data and build compact per-model, per-hour payload.

    Returns (model_data dict, hour_labels list, all_data for snapshot).
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    # Hardcoded to US Eastern (Radnor, PA) — container runs UTC
    eastern = ZoneInfo("America/New_York")

    # Build target hours for detailed 48h view
    target_hours = []
    for offset in range(1, hours_ahead + 1):
        t = (now + datetime.timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
        target_hours.append(t)

    # Labels in Eastern time with day-of-week for multi-day clarity
    today_eastern = now.astimezone(eastern).date()
    def make_label(t):
        t_east = t.astimezone(eastern)
        time_str = t_east.strftime("%-I%p").lower()
        day_diff = (t_east.date() - today_eastern).days
        if day_diff == 0:
            return time_str
        elif day_diff == 1:
            return f"{t_east.strftime('%a').lower()} {time_str}"
        else:
            return f"{t_east.strftime('%a').lower()} {time_str}"

    hour_labels = [make_label(t) for t in target_hours]
    hour_isos = [t.strftime("%Y-%m-%dT%H:00:00") for t in target_hours]

    # Fetch all variables — days=2 gets latest runs (GFS already forecasts 16 days)
    all_data = {}
    for var in VARIABLES:
        all_data[var] = fetch_multirun(lat, lon, var, model="all", days=2)

    def extract_hourly(runs_list, hour_isos):
        """Given a list of (init_time, {valid_time: value}), return values for target hours."""
        if not runs_list:
            return [None] * len(hour_isos)
        _, values_by_time = runs_list[0]
        result = []
        for iso in hour_isos:
            val = None
            for vt, v in values_by_time.items():
                if vt.startswith(iso[:13]):
                    val = v
                    break
            result.append(val)
        return result

    def extract_extended(runs_list, after_hour, max_hour, step=6):
        """Extract every Nth hour from available model data beyond after_hour.

        Instead of generating target times and hoping they align, scan the
        model's actual valid times and pick one per step-hour window.
        """
        if not runs_list:
            return [], [], []
        _, values_by_time = runs_list[0]
        cutoff = now + datetime.timedelta(hours=after_hour)
        end = now + datetime.timedelta(hours=max_hour)

        # Collect all valid times beyond cutoff, sorted
        future_points = []
        for vt, v in values_by_time.items():
            try:
                t = datetime.datetime.fromisoformat(vt.replace("Z", "+00:00"))
            except ValueError:
                continue
            if cutoff <= t <= end:
                future_points.append((t, v))
        future_points.sort()

        if not future_points:
            return [], [], []

        # Sample every step hours from the available data
        ext_labels = []
        ext_isos = []
        values = []
        next_target = future_points[0][0]
        for t, v in future_points:
            if t >= next_target:
                ext_labels.append(make_label(t))
                ext_isos.append(t.strftime("%Y-%m-%dT%H:00:00"))
                values.append(round(v, 1) if v is not None else None)
                next_target = t + datetime.timedelta(hours=step)

        return values, ext_labels, ext_isos

    # Build per-model data (48h detail)
    model_data = {}

    # HRRR: latest 2 runs + latest synoptic run (00/06/12/18 UTC, 48h)
    for var in VARIABLES:
        hrrr_runs = extract_latest_runs(all_data[var], "hrrr", count=2)
        for i, (init_time, _) in enumerate(hrrr_runs):
            label = "hrrr_latest" if i == 0 else "hrrr_previous"
            if label not in model_data:
                model_data[label] = {"init": init_time, "hours": hour_labels[:], "data": {}}
            model_data[label]["data"][var] = extract_hourly([hrrr_runs[i]], hour_isos)

    # HRRR synoptic: latest run at 00/06/12/18 UTC (these go 48h)
    SYNOPTIC_HOURS = {"00", "06", "12", "18"}
    for var in VARIABLES:
        all_hrrr = extract_latest_runs(all_data[var], "hrrr", count=20)
        for init_time, values_by_time in all_hrrr:
            try:
                init_hour = init_time[11:13]  # "HH" from ISO
            except (IndexError, TypeError):
                continue
            if init_hour in SYNOPTIC_HOURS and len(values_by_time) > 20:
                if "hrrr_synoptic" not in model_data:
                    model_data["hrrr_synoptic"] = {"init": init_time, "hours": hour_labels[:], "data": {}}
                model_data["hrrr_synoptic"]["data"][var] = extract_hourly(
                    [(init_time, values_by_time)], hour_isos
                )
                break

    # GFS, ECMWF, NBM: latest run each (48h detail)
    for model_id in ["gfs", "ecmwf_hres", "nbm"]:
        for var in VARIABLES:
            runs = extract_latest_runs(all_data[var], model_id, count=1)
            if runs:
                if model_id not in model_data:
                    model_data[model_id] = {"init": runs[0][0], "hours": hour_labels[:], "data": {}}
                model_data[model_id]["data"][var] = extract_hourly(runs, hour_isos)

    # NBM previous run apcp — for stitching gaps in raw HRRR table
    nbm_apcp_runs = extract_latest_runs(all_data.get("apcp", {}), "nbm", count=2)
    if len(nbm_apcp_runs) >= 2:
        model_data["_nbm_apcp_prev"] = extract_hourly([nbm_apcp_runs[1]], hour_isos)

    # GFS extended outlook: every 6h from hour 48 to hour 240 (days 3-10)
    for var in VARIABLES:
        gfs_runs = extract_latest_runs(all_data[var], "gfs", count=1)
        if gfs_runs:
            values, labels, ext_isos = extract_extended(gfs_runs, hours_ahead + 1, 240, step=6)
            if any(v is not None for v in values):
                if "gfs_extended" not in model_data:
                    model_data["gfs_extended"] = {
                        "init": gfs_runs[0][0],
                        "hours": labels,
                        "isos": ext_isos,
                        "data": {},
                        "note": "Every 6h, days 3-10 — for daily outlook buckets"
                    }
                model_data["gfs_extended"]["data"][var] = values

    # Build current all-model median for snapshots (backward compat, 48h only)
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

    return model_data, hour_labels, hour_isos, current_by_time, all_data


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


def build_prompt(model_data, trends, hour_labels, lat, lon):
    """Build the LLM prompt with raw model data and trend snapshots."""
    sections = []
    # Tell LLM the current local time so it knows what "today" means
    now = datetime.datetime.now(datetime.timezone.utc)
    eastern = ZoneInfo("America/New_York")
    local_now = now.astimezone(eastern)
    sections.append(f"""You are a meteorologist who finally got their own forecast column. This is your chance to shine.
You have raw data from 5 weather models (HRRR, GFS, ECMWF, NBM). Your job is to interpret
this data, tell the story of the next 24-48 hours, and make it genuinely engaging to read.
Be witty and have a voice, but always be precise and quantitative when it matters.
You're entertaining AND informative — a weather nerd's dream forecaster.

Current local time: {local_now.strftime("%A, %B %-d, %Y %-I:%M%p")} Eastern (Radnor, PA area).
All hour labels in the data below are in Eastern time.
""")

    # Add sunrise/sunset times
    now = datetime.datetime.now(datetime.timezone.utc)
    sun_info = []
    for day_offset in range(3):
        d = (now + datetime.timedelta(days=day_offset)).date()
        rise, sset = sun_times(lat, lon, d)
        if rise and sset:
            # Hardcoded to US Eastern (Radnor, PA) — container runs UTC
            eastern = ZoneInfo("America/New_York")
            rise_local = rise.astimezone(eastern).strftime("%-I:%M%p").lower()
            sset_local = sset.astimezone(eastern).strftime("%-I:%M%p").lower()
            day_label = ["Today", "Tomorrow", d.strftime("%A")][day_offset]
            sun_info.append(f"{day_label} ({d}): sunrise {rise_local}, sunset {sset_local}")
    if sun_info:
        sections.append("## Sun times")
        sections.extend(sun_info)
        sections.append("")

    for model_label, mdata in model_data.items():
        if model_label.startswith("_"):
            continue
        init = mdata.get("init", "unknown")
        sections.append(f"## {model_label} (init: {init})")
        sections.append(f"Hours: {', '.join(hour_labels)}")
        for var, values in mdata.get("data", {}).items():
            vals_str = ", ".join(str(v) if v is not None else "-" for v in values)
            sections.append(f"  {var}: {vals_str}")
        sections.append("")

    sections.append("Variable key: t2m=temp(°F), dpt=dewpoint(°F), cloud_cover=clouds(%), dswrf=solar(W/m²), apcp=rain(in), asnow=snow(in), snod=snow_depth(in), wind_10m=wind(mph), gust=gusts(mph), refc=radar_reflectivity(dBZ, HRRR-only)")
    sections.append("Note: 'gfs_extended' has every-6h data for days 3-10 — use it for the daily outlook buckets beyond 24h.")
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
  "buckets": [
    {
      "time": "<label>",
      "hours_covered": ["5pm", "6pm"],
      "cloud_pct": <0-100>,
      "clearness": <0-100>,
      "precip_type": null | "rain" | "snow",
      "is_night": true/false,
      "temp": <number or string>,
      "lines": ["<line1>", "<line2>", ...],
      "icon": "<icon>"
    },
    ... as many as you need
  ],
  "narrative": "<3-5 sentence meteorologist brief>"
}

## Time buckets — three tiers of detail

**Today (next 24h):** Hourly during waking hours (~7am-10pm), grouped overnight.
- Go hour by hour so the user sees the temp curve and can plan their day
- Group overnight/sleeping hours into one bucket ("Tonight 10pm-6am")
- Use sunrise/sunset times to decide is_night and to mark transitions

**Tomorrow (hours 24-48):** A few larger blocks — morning, afternoon, evening, overnight.
- "Tomorrow morning", "Tomorrow afternoon", etc.
- Still cite model spread where interesting

**Days 3-10:** One bucket per day using the GFS extended data. YOU MUST COVER EVERY DAY that has data.
- Label as day name: "Thursday", "Friday", etc.
- Give the high/low range, sky condition, and any precip
- Flag any storms, big temp swings, or notable weather
- Confidence naturally decreases — say so
- You MAY group 2-3 consecutive similar days ("Thu-Sat: More of the same, highs near 50")
- But DO NOT skip days. The user wants to see the full 10-day outlook.

## Rules for each bucket

- "time": Your label. Can be "2pm", "Tonight 11pm-5am", "Morning", "Late afternoon", whatever fits.
- "temp": A number OR a string. Use a number for single hours (53). Use a string for ranges or uncertainty: "48-53", "low 50s", "~72". Express uncertainty when models disagree!
- "cloud_pct": 0-100, your best estimate for this period. Drives SVG cloud size.
- "clearness": 0-100, how bright. Drives SVG sun/moon brightness.
- "precip_type": null, "rain", or "snow"
- "is_night": true if before sunrise or after sunset
- "icon": one of sun, moon, cloud, cloud-sun, cloud-moon, cloud-rain, snowflake (SVG fallback)
- "hours_covered": list of hour labels from the input data that this bucket covers.
  - For single-hour buckets: ["2pm"]
  - For multi-hour buckets: ["10pm", "11pm", "12am", "1am", "2am", "3am", "4am", "5am", "6am"]
  - For tomorrow blocks: list all covered hour labels from the data
  - For day-3+ daily buckets: [] (empty — no hourly HRRR data available)
  - Use the EXACT hour labels from the model data (e.g. "5pm", "tmrw 1am", "fri 6am")
- "lines": 2-3 short lines displayed in the timeline:
  - Line 1: temperature (e.g. "53°F" or "48-53°F" or "Low 50s")
  - Line 2+: The most interesting thing about this period. Be precise AND engaging:
    - Quantify when useful: "HRRR says 72°F, GFS only 68°F" or "30% cloud cover"
    - Interpret conditions: "Dry enough for a bonfire" or "Muggy, you'll notice it"
    - Model drama: "ECMWF and HRRR agree, NBM is the outlier"
    - Forecast shifts: "Rain vanished from the latest HRRR run"
    - Express uncertainty: "Precip is a coin flip between models"
  - MUST mention precip if non-zero — include the amount (e.g. "0.2 in rain", "1 in snow")
  - Note: apcp values are CUMULATIVE from forecast start. To get period amounts, subtract consecutive values.
  - Wind/gust in mph. refc (composite reflectivity) is dBZ — 0=clear, 20-35=light rain, 35-50=moderate, 50+=severe. refc is HRRR-only (not available in GFS/NBM/ECMWF).
  - Don't be repetitive across consecutive buckets

## Rules for "narrative"

3-5 sentences. This is YOUR column — tell the story of the next 24-48 hours.
- Be precise: cite specific models, temperatures, times
- Express uncertainty where models disagree — don't pretend to know what you don't
- Call out forecast evolution vs earlier runs
- Paint the picture of what kind of day it will be
- Have personality but stay grounded in the data. No emoji.
- If there's a storm/significant weather coming later in the week, mention it

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

    # Support both "buckets" and "hours" keys
    buckets = data.get("buckets") or data.get("hours") or []
    if not isinstance(buckets, list) or len(buckets) < 1:
        raise ValueError("Missing or empty 'buckets'/'hours' array")

    # Validate and fix fields
    for h in buckets:
        if h.get("icon") not in VALID_ICONS:
            h["icon"] = "question"
        if not isinstance(h.get("lines"), list):
            h["lines"] = [f"{h.get('temp', '?')}°F"]
        # Ensure SVG fields have defaults
        h.setdefault("cloud_pct", 50)
        h.setdefault("clearness", 50)
        h.setdefault("precip_type", None)
        h.setdefault("is_night", False)
        # Normalize temp to string for display flexibility
        if isinstance(h.get("temp"), (int, float)):
            h["temp_display"] = f"{h['temp']:.0f}°F"
        elif isinstance(h.get("temp"), str):
            h["temp_display"] = h["temp"] if "°" in h["temp"] else h["temp"] + "°F"
        else:
            h["temp_display"] = "?"

    data["buckets"] = buckets
    if "narrative" not in data or not data["narrative"]:
        data["narrative"] = " ".join(buckets[0].get("lines", []))

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


def build_raw_hrrr(model_data, nbm_apcp_prev=None):
    """Extract latest HRRR run data, stitching with previous synoptic run.

    Uses hrrr_latest for all available hours, then fills remaining hours
    from hrrr_previous (typically the last synoptic run with 48h range).
    Also includes NBM precip (stitched from previous run via nbm_apcp_prev).

    Returns {"init": str, "synoptic_init": str|None, "hours": [...]}
    or None if HRRR data not available.
    """
    hrrr = model_data.get("hrrr_latest")
    if not hrrr:
        return None

    hours_list = hrrr.get("hours", [])
    data = hrrr.get("data", {})
    if not hours_list:
        return None

    # Get all variable names from both HRRR runs
    prev = model_data.get("hrrr_previous")
    prev_data = prev.get("data", {}) if prev else {}
    all_vars = set(data.keys()) | set(prev_data.keys())

    # NBM precip: latest run, stitched with previous where gaps exist
    nbm = model_data.get("nbm")
    nbm_apcp = list(nbm["data"].get("apcp", [])) if nbm else []
    if nbm_apcp_prev:
        for i in range(len(hours_list)):
            if i >= len(nbm_apcp):
                nbm_apcp.append(None)
            if nbm_apcp[i] is None and i < len(nbm_apcp_prev):
                nbm_apcp[i] = nbm_apcp_prev[i]

    per_hour = []
    for i, label in enumerate(hours_list):
        entry = {"hour": label}
        for var in all_vars:
            latest_vals = data.get(var, [])
            val = latest_vals[i] if i < len(latest_vals) else None
            if val is None and prev:
                prev_vals_list = prev_data.get(var, [])
                val = prev_vals_list[i] if i < len(prev_vals_list) else None
                if val is not None:
                    entry.setdefault("_stitched", True)
            entry[var] = val
        entry["nbm_apcp"] = nbm_apcp[i] if i < len(nbm_apcp) else None
        per_hour.append(entry)

    # De-accumulate precipitation variables (cumulative → per-hour)
    ACCUM_VARS = ["apcp", "asnow", "nbm_apcp"]
    for var in ACCUM_VARS:
        prev_val = None
        prev_stitched = None
        for entry in per_hour:
            val = entry.get(var)
            if val is None:
                prev_val = None
                prev_stitched = None
                continue
            is_stitched = entry.get("_stitched", False)
            # Reset at stitch boundary (don't diff across different runs)
            if prev_val is not None and is_stitched == prev_stitched:
                increment = round(max(0, val - prev_val), 2)
                prev_val = val
                entry[var] = increment
            else:
                prev_val = val
                entry[var] = round(val, 2)
            prev_stitched = is_stitched

    return {
        "init": hrrr.get("init", "unknown"),
        "synoptic_init": prev.get("init") if prev else None,
        "hours": per_hour,
    }


def _build_latest_table_legacy(model_data):
    """Legacy fallback for tests that don't pass all_data."""
    eastern = ZoneInfo("America/New_York")

    def _source_label(display_name, init_iso):
        try:
            t = datetime.datetime.fromisoformat(init_iso).astimezone(eastern)
            return f"{display_name} {t.strftime('%-I%p').lower()}"
        except Exception:
            return display_name

    source_keys = [
        ("hrrr_latest", "HRRR"),
        ("hrrr_previous", "HRRR"),
        ("hrrr_synoptic", "HRRR"),
        ("gfs", "GFS"),
    ]
    sources = []
    for key, display in source_keys:
        mdata = model_data.get(key)
        if mdata:
            label = _source_label(display, mdata.get("init", ""))
            sources.append((key, label, mdata))

    n_hours = 0
    hlabels = []
    for _, _, mdata in sources:
        if mdata.get("hours"):
            hlabels = mdata["hours"]
            n_hours = len(hlabels)
            break

    all_vars = set()
    for _, _, mdata in sources:
        all_vars.update(mdata.get("data", {}).keys())
    all_vars.discard("_stitched")

    hourly = []
    for i in range(n_hours):
        entry = {"hour": hlabels[i], "source": "\u2014"}
        for key, source_label, mdata in sources:
            data = mdata.get("data", {})
            t2m_vals = data.get("t2m", [])
            if i < len(t2m_vals) and t2m_vals[i] is not None:
                entry["source"] = source_label
                for var in all_vars:
                    vals = data.get(var, [])
                    entry[var] = vals[i] if i < len(vals) else None
                break
        hourly.append(entry)

    ACCUM_VARS = ["apcp", "asnow"]
    for var in ACCUM_VARS:
        prev_val = None
        prev_source = None
        for entry in hourly:
            val = entry.get(var)
            if val is None:
                prev_val = None
                prev_source = None
                continue
            source = entry.get("source")
            if prev_val is not None and source == prev_source:
                entry[var] = round(max(0, val - prev_val), 4)
                prev_val = val
            else:
                prev_val = val
                entry[var] = round(val, 4)
            prev_source = source

    nbm = model_data.get("nbm")
    nbm_apcp = list(nbm["data"].get("apcp", [])) if nbm and nbm.get("data") else []
    prev_nbm = None
    for i, entry in enumerate(hourly):
        val = nbm_apcp[i] if i < len(nbm_apcp) else None
        if val is not None and prev_nbm is not None:
            entry["nbm_apcp"] = round(max(0, val - prev_nbm), 4)
        elif val is not None:
            entry["nbm_apcp"] = round(val, 4)
        else:
            entry.setdefault("nbm_apcp", None)
        if val is not None:
            prev_nbm = val

    daily = _build_daily_section(model_data, all_vars)
    return {"hourly": hourly, "daily": daily}


def _build_daily_section(model_data, all_vars):
    """Build daily aggregation rows from gfs_extended data."""
    eastern = ZoneInfo("America/New_York")
    ACCUM_VARS = ["apcp", "asnow"]

    def _source_label(display_name, init_iso):
        try:
            t = datetime.datetime.fromisoformat(init_iso).astimezone(eastern)
            return f"{display_name} {t.strftime('%-I%p').lower()}"
        except Exception:
            return display_name

    daily = []
    gfs_ext = model_data.get("gfs_extended")
    if gfs_ext and gfs_ext.get("isos"):
        ext_data = gfs_ext.get("data", {})
        ext_isos = gfs_ext.get("isos", [])
        gfs_source = _source_label("GFS", gfs_ext.get("init", ""))

        # De-accumulate extended precip first
        ext_precip = {}
        for var in ACCUM_VARS:
            raw = list(ext_data.get(var, []))
            increments = []
            prev = None
            for v in raw:
                if v is not None and prev is not None:
                    increments.append(round(max(0, v - prev), 4))
                elif v is not None:
                    increments.append(round(v, 4))
                else:
                    increments.append(None)
                if v is not None:
                    prev = v
            ext_precip[var] = increments

        # Group by Eastern date
        days = {}
        day_order = []
        for idx, iso in enumerate(ext_isos):
            try:
                dt = datetime.datetime.fromisoformat(iso + "+00:00").astimezone(eastern)
                day_key = dt.strftime("%a %b %-d")
            except Exception:
                continue
            if day_key not in days:
                days[day_key] = []
                day_order.append(day_key)
            point = {}
            for var in all_vars:
                if var in ACCUM_VARS:
                    vals = ext_precip.get(var, [])
                else:
                    vals = ext_data.get(var, [])
                point[var] = vals[idx] if idx < len(vals) else None
            days[day_key].append(point)

        for day_key in day_order[:5]:
            points = days[day_key]
            day_entry = {"day": day_key, "source": gfs_source}
            for var in all_vars:
                vals = [p[var] for p in points if p.get(var) is not None]
                if not vals:
                    day_entry[var] = {"min": None, "max": None, "avg": None}
                elif var in ACCUM_VARS:
                    day_entry[var] = {
                        "min": round(min(vals), 4),
                        "max": round(max(vals), 4),
                        "avg": round(sum(vals), 4),
                    }
                else:
                    day_entry[var] = {
                        "min": round(min(vals), 2),
                        "max": round(max(vals), 2),
                        "avg": round(sum(vals) / len(vals), 2),
                    }
            daily.append(day_entry)

    return daily


def build_latest_table(model_data, all_data=None, hour_labels=None, hour_isos=None):
    """Build unified best-available forecast table with source attribution.

    For each hour, picks the most recent HRRR run that has data for that hour.
    Falls back to GFS only when no HRRR run covers an hour.
    Returns {"hourly": [...], "daily": [...]}.
    """
    if not model_data:
        return {"hourly": [], "daily": []}

    eastern = ZoneInfo("America/New_York")

    def _source_label(display_name, init_iso):
        try:
            t = datetime.datetime.fromisoformat(init_iso).astimezone(eastern)
            return f"{display_name} {t.strftime('%-I%p').lower()}"
        except Exception:
            return display_name

    # Fall back to old behavior if all_data not provided (tests)
    if all_data is None or hour_labels is None or hour_isos is None:
        return _build_latest_table_legacy(model_data)

    # ---- Collect all HRRR runs, sorted newest-first ----
    # Scan ALL variables to discover run keys (a partial run may only have
    # some variables tiled so far — we must not miss it).
    hrrr_runs = []
    all_run_keys = {}  # run_key -> init_time
    for var in VARIABLES:
        api_resp = all_data.get(var)
        if not api_resp:
            continue
        for run_key, run_info in api_resp.get("runs", {}).items():
            if run_info.get("model_id", run_key.split("/")[0]) == "hrrr":
                if run_key not in all_run_keys:
                    all_run_keys[run_key] = run_info.get("init_time", "")

    # Build (init_time, run_key, {var: {valid_iso: value}}) for each run
    for run_key, init_time in all_run_keys.items():
        run_vars = {}
        for var in VARIABLES:
            api_resp = all_data.get(var)
            if api_resp and run_key in api_resp.get("runs", {}):
                pts = api_resp["runs"][run_key].get("series", [])
                run_vars[var] = {
                    pt["valid_time"]: round(pt["value"], 4)
                    for pt in pts if pt.get("value") is not None
                }
            else:
                run_vars[var] = {}
        hrrr_runs.append((init_time, run_key, run_vars))

    hrrr_runs.sort(key=lambda x: x[0], reverse=True)  # newest first

    # Also get GFS as fallback
    gfs_run_vars = {}
    gfs_init = ""
    gfs_data = all_data.get("t2m")
    if gfs_data:
        for run_key, run_info in gfs_data.get("runs", {}).items():
            if run_info.get("model_id", run_key.split("/")[0]) == "gfs":
                if run_info.get("init_time", "") > gfs_init:
                    gfs_init = run_info["init_time"]
        if gfs_init:
            for var in VARIABLES:
                api_resp = all_data.get(var)
                if not api_resp:
                    continue
                for run_key, run_info in api_resp.get("runs", {}).items():
                    if run_info.get("model_id", run_key.split("/")[0]) == "gfs" and \
                       run_info.get("init_time", "") == gfs_init:
                        gfs_run_vars[var] = {
                            pt["valid_time"]: round(pt["value"], 4)
                            for pt in run_info.get("series", [])
                            if pt.get("value") is not None
                        }
                        break

    gfs_label = _source_label("GFS", gfs_init) if gfs_init else "GFS"

    # ---- Per-hour, per-variable best-run selection ----
    # For each hour AND each variable, pick the newest HRRR run with data.
    # "source" = primary run (newest with t2m). Variables from a different
    # (newer) run get a "*" suffix in the "starred" list.
    hourly = []
    for i, iso in enumerate(hour_isos):
        iso_prefix = iso[:13]  # "YYYY-MM-DDTHH"
        entry = {"hour": hour_labels[i], "source": "\u2014", "_run_key": None, "_var_run_keys": {}, "starred": []}

        # Find primary source (newest HRRR with t2m for this hour)
        primary_run_key = None
        for init_time, run_key, run_vars in hrrr_runs:
            t2m_map = run_vars.get("t2m", {})
            if any(vt.startswith(iso_prefix) for vt in t2m_map):
                entry["source"] = _source_label("HRRR", init_time)
                entry["_run_key"] = run_key
                primary_run_key = run_key
                break

        # For each variable, find the newest HRRR run with data
        for var in VARIABLES:
            val = None
            val_run_key = None
            for _, run_key, run_vars in hrrr_runs:
                var_map = run_vars.get(var, {})
                v = next((v for vt, v in var_map.items() if vt.startswith(iso_prefix)), None)
                if v is not None:
                    val = v
                    val_run_key = run_key
                    break
            entry[var] = val
            if val_run_key:
                entry["_var_run_keys"][var] = val_run_key
            # Mark with * if this value came from a different run than primary
            if val is not None and val_run_key and primary_run_key and val_run_key != primary_run_key:
                entry["starred"].append(var)

        # Fallback to GFS for variables still null
        if primary_run_key is None and gfs_run_vars:
            t2m_gfs = gfs_run_vars.get("t2m", {})
            if any(vt.startswith(iso_prefix) for vt in t2m_gfs):
                entry["source"] = gfs_label
                entry["_run_key"] = "gfs"
                for var in VARIABLES:
                    if entry.get(var) is None:
                        var_map = gfs_run_vars.get(var, {})
                        val = next((v for vt, v in var_map.items() if vt.startswith(iso_prefix)), None)
                        entry[var] = val
                        if val is not None:
                            entry["_var_run_keys"][var] = "gfs"

        hourly.append(entry)

    # ---- De-accumulate precip per source run (using per-variable run keys) ----
    ACCUM_VARS = ["apcp", "asnow"]
    for var in ACCUM_VARS:
        prev_val = None
        prev_run = None
        for entry in hourly:
            val = entry.get(var)
            run_key = entry.get("_var_run_keys", {}).get(var) or entry.get("_run_key")
            if val is None or run_key is None:
                prev_val = None
                prev_run = None
                continue
            if prev_val is not None and run_key == prev_run:
                entry[var] = round(max(0, val - prev_val), 4)
                prev_val = val
            else:
                prev_val = val
                entry[var] = round(val, 4)
            prev_run = run_key

    # NBM precip overlay — per-hour best-run from all_data (like HRRR)
    nbm_apcp_runs = []
    nbm_apcp_data = all_data.get("apcp") if all_data else None
    if nbm_apcp_data:
        for run_key, run_info in nbm_apcp_data.get("runs", {}).items():
            if run_info.get("model_id", run_key.split("/")[0]) == "nbm":
                init_time = run_info.get("init_time", "")
                vals = {
                    pt["valid_time"]: round(pt["value"], 4)
                    for pt in run_info.get("series", [])
                    if pt.get("value") is not None
                }
                nbm_apcp_runs.append((init_time, run_key, vals))
        nbm_apcp_runs.sort(key=lambda x: x[0], reverse=True)

    # For each hour, pick newest NBM run with data
    for i, entry in enumerate(hourly):
        if i < len(hour_isos):
            iso_prefix = hour_isos[i][:13]
            for _, run_key, vals in nbm_apcp_runs:
                v = next((v for vt, v in vals.items() if vt.startswith(iso_prefix)), None)
                if v is not None:
                    entry["nbm_apcp"] = v
                    entry["_nbm_run_key"] = run_key
                    break
            else:
                entry.setdefault("nbm_apcp", None)
                entry.setdefault("_nbm_run_key", None)

    # De-accumulate NBM precip per source run
    prev_nbm = None
    prev_nbm_run = None
    for entry in hourly:
        val = entry.get("nbm_apcp")
        run_key = entry.get("_nbm_run_key")
        if val is None or run_key is None:
            prev_nbm = None
            prev_nbm_run = None
            continue
        if prev_nbm is not None and run_key == prev_nbm_run:
            entry["nbm_apcp"] = round(max(0, val - prev_nbm), 4)
            prev_nbm = val
        else:
            prev_nbm = val
            entry["nbm_apcp"] = round(val, 4)
        prev_nbm_run = run_key

    # Clean up internal keys
    for entry in hourly:
        entry.pop("_run_key", None)
        entry.pop("_var_run_keys", None)
        entry.pop("_nbm_run_key", None)

    all_vars = set(VARIABLES)
    daily = _build_daily_section(model_data, all_vars)
    return {"hourly": hourly, "daily": daily}


def generate_summary(lat, lon, cache_dir):
    """Main generation function for a single lat/lon."""
    grid_id = grid_key(lat, lon)
    log.info(f"Generating summary for {grid_id}")

    # Fetch raw model data
    model_data, hour_labels, hour_isos, current_by_time, _all_data = build_model_data(lat, lon)

    if not model_data:
        log.warning("No model data available")
        return None

    # Load trend snapshots
    trends = load_trend_snapshots(cache_dir, grid_id, hour_isos)

    # Save snapshot for future trend comparison
    save_snapshot(cache_dir, grid_id, current_by_time)

    # Build prompt
    prompt = build_prompt(model_data, trends, hour_labels, lat, lon)

    # Call LLM
    raw_output = {"stdout": "", "stderr": "", "exit_code": None}
    llm_data = None
    try:
        result = subprocess.run(
            ["gemini", "-m", "gemini-3.1-pro-preview"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
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
            "buckets": fallback_hours,
            "narrative": "Forecast summary temporarily unavailable. Showing basic conditions from HRRR.",
        }

    # Build final result
    raw_hrrr = build_raw_hrrr(model_data, nbm_apcp_prev=model_data.get("_nbm_apcp_prev"))

    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "lat": lat,
        "lon": lon,
        "buckets": llm_data.get("buckets", llm_data.get("hours", [])),
        "narrative": llm_data["narrative"],
        "raw_hrrr": raw_hrrr,
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
