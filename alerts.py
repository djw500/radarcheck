from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

NWS_ALERTS_API = "https://api.weather.gov/alerts/active"


def get_alerts_for_location(lat: float, lon: float) -> list[dict[str, Any]]:
    """Fetch active NWS alerts for a location."""
    params = {"point": f"{lat},{lon}"}
    try:
        response = requests.get(NWS_ALERTS_API, params=params, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch NWS alerts: %s", exc)
        return []
    return response.json().get("features", [])
