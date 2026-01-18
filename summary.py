from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional


def load_center_values(
    cache_dir: str,
    location_id: str,
    model_id: str,
    run_id: str,
    variable_id: str,
) -> Optional[dict[str, Any]]:
    """Load center values JSON for a given variable."""
    candidates = [
        os.path.join(cache_dir, location_id, model_id, run_id, variable_id, "center_values.json"),
        os.path.join(cache_dir, location_id, run_id, variable_id, "center_values.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    return None


def _extract_values(payload: Optional[dict[str, Any]]) -> list[float]:
    if not payload:
        return []
    values = []
    for entry in payload.get("values", []):
        value = entry.get("value")
        if value is not None:
            values.append(float(value))
    return values


def summarize_run(
    cache_dir: str,
    location_id: str,
    model_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Summarize key metrics from cached center values."""
    summary = {
        "total_snowfall_inches": None,
        "total_precipitation_inches": None,
        "max_wind_gust_mph": None,
        "temperature_range_f": {"min": None, "max": None},
    }
    units = {}

    asnow_payload = load_center_values(cache_dir, location_id, model_id, run_id, "asnow")
    asnow_values = _extract_values(asnow_payload)
    if asnow_values:
        summary["total_snowfall_inches"] = max(asnow_values)
        units["total_snowfall_inches"] = asnow_payload.get("units")

    apcp_payload = load_center_values(cache_dir, location_id, model_id, run_id, "apcp")
    apcp_values = _extract_values(apcp_payload)
    if apcp_values:
        summary["total_precipitation_inches"] = max(apcp_values)
        units["total_precipitation_inches"] = apcp_payload.get("units")

    gust_payload = load_center_values(cache_dir, location_id, model_id, run_id, "gust")
    gust_values = _extract_values(gust_payload)
    if gust_values:
        summary["max_wind_gust_mph"] = max(gust_values)
        units["max_wind_gust_mph"] = gust_payload.get("units")

    t2m_payload = load_center_values(cache_dir, location_id, model_id, run_id, "t2m")
    t2m_values = _extract_values(t2m_payload)
    if t2m_values:
        summary["temperature_range_f"]["min"] = min(t2m_values)
        summary["temperature_range_f"]["max"] = max(t2m_values)
        units["temperature_range_f"] = t2m_payload.get("units")

    return {
        "location_id": location_id,
        "run_id": run_id,
        "model_id": model_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "units": units,
    }
