#!/usr/bin/env python3
"""
forecast_table.py - Generate simple tabular view of forecast data.

Reads center_values.json files from the cache and produces a table
showing all weather variables across all forecast hours for a location.

Usage:
    python forecast_table.py --location philly
    python forecast_table.py --location philly --format html --output table.html
    python forecast_table.py --location philly --format json
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Optional

from config import repomap, WEATHER_VARIABLES


def get_latest_run_id(cache_dir: str, location_id: str, model_id: str) -> Optional[str]:
    """Get the latest run ID for a location/model by reading the 'latest' symlink."""
    latest_link = os.path.join(cache_dir, location_id, model_id, "latest")
    if os.path.islink(latest_link):
        return os.path.basename(os.readlink(latest_link))

    # Fallback: find the most recent run directory
    model_dir = os.path.join(cache_dir, location_id, model_id)
    if not os.path.isdir(model_dir):
        # Try legacy structure without model subdirectory
        model_dir = os.path.join(cache_dir, location_id)
        if not os.path.isdir(model_dir):
            return None

    runs = [d for d in os.listdir(model_dir) if d.startswith("run_")]
    if not runs:
        return None
    return sorted(runs, reverse=True)[0]


def load_center_values_for_variable(
    cache_dir: str, location_id: str, model_id: str, run_id: str, variable_id: str
) -> Optional[dict]:
    """Load center_values.json for a specific variable."""
    # Try new structure first: cache/location/model/run/variable/center_values.json
    paths_to_try = [
        os.path.join(cache_dir, location_id, model_id, run_id, variable_id, "center_values.json"),
        os.path.join(cache_dir, location_id, run_id, variable_id, "center_values.json"),
        os.path.join(cache_dir, location_id, run_id, "center_values.json"),
    ]

    for path in paths_to_try:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
    return None


def load_all_center_values(
    cache_dir: str, location_id: str, model_id: str = "hrrr", run_id: Optional[str] = None
) -> dict:
    """
    Load center values for all available variables.

    Returns:
        {
            "metadata": {"location_id", "model_id", "run_id", "init_time"},
            "variables": {
                "variable_id": {
                    "config": {...},
                    "values": {hour: {"value": x, "valid_time": t}, ...}
                }
            }
        }
    """
    if run_id is None:
        run_id = get_latest_run_id(cache_dir, location_id, model_id)
        if run_id is None:
            return {"metadata": {}, "variables": {}}

    result = {
        "metadata": {
            "location_id": location_id,
            "model_id": model_id,
            "run_id": run_id,
            "init_time": None,
        },
        "variables": {},
    }

    # Load each available variable
    for var_id, var_config in WEATHER_VARIABLES.items():
        data = load_center_values_for_variable(cache_dir, location_id, model_id, run_id, var_id)
        if data and "values" in data:
            values_by_hour = {}
            for entry in data["values"]:
                hour = entry.get("forecast_hour")
                if hour is not None:
                    values_by_hour[hour] = {
                        "value": entry.get("value"),
                        "valid_time": entry.get("valid_time"),
                    }
            if values_by_hour:
                result["variables"][var_id] = {
                    "config": var_config,
                    "values": values_by_hour,
                }

            # Extract init time from first payload
            if result["metadata"]["init_time"] is None and data.get("init_time"):
                result["metadata"]["init_time"] = data["init_time"]

    return result


def format_value(value: Optional[float], var_config: dict, include_units: bool = True) -> str:
    """Format a value with units."""
    if value is None:
        return "-"

    units = var_config.get("units", "")

    # Format based on typical precision for the variable
    if units in ("dBZ", "J/kg", "m²/s²", "%"):
        formatted = f"{value:.0f}"
    elif units in ("°F", "mph"):
        formatted = f"{value:.1f}"
    elif units in ("in", "in/hr", "mi"):
        formatted = f"{value:.2f}"
    else:
        formatted = f"{value:.1f}"

    if include_units and units:
        return f"{formatted} {units}"
    return formatted


def build_forecast_table(data: dict) -> list[dict]:
    """
    Build a table structure from loaded data.

    Returns list of rows:
        [
            {"hour": 1, "valid_time": "...", "refc": "35 dBZ", "t2m": "45 °F", ...},
            ...
        ]
    """
    if not data.get("variables"):
        return []

    # Find all hours across all variables
    all_hours = set()
    for var_data in data["variables"].values():
        all_hours.update(var_data["values"].keys())

    if not all_hours:
        return []

    # Build rows
    rows = []
    for hour in sorted(all_hours):
        row = {"hour": hour, "valid_time": None}

        for var_id, var_data in data["variables"].items():
            hour_data = var_data["values"].get(hour, {})
            value = hour_data.get("value")
            row[var_id] = format_value(value, var_data["config"])

            if row["valid_time"] is None and hour_data.get("valid_time"):
                row["valid_time"] = hour_data["valid_time"]

        rows.append(row)

    return rows


def get_variable_display_order() -> list[str]:
    """Get ordered list of variable IDs for display."""
    # Group by category, then sort within category
    categories_order = ["temperature", "precipitation", "wind", "winter", "severe", "surface"]
    ordered = []

    for cat in categories_order:
        for var_id, var_config in WEATHER_VARIABLES.items():
            if var_config.get("category") == cat and var_id not in ordered:
                ordered.append(var_id)

    # Add any remaining
    for var_id in WEATHER_VARIABLES:
        if var_id not in ordered:
            ordered.append(var_id)

    return ordered


def format_table_terminal(data: dict, rows: list[dict]) -> str:
    """Format table for terminal output."""
    if not rows:
        return "No data available."

    # Header info
    meta = data.get("metadata", {})
    lines = [
        f"Location: {meta.get('location_id', 'unknown')}",
        f"Model: {meta.get('model_id', 'unknown')}",
        f"Run: {meta.get('run_id', 'unknown')}",
        f"Init Time: {meta.get('init_time', 'unknown')}",
        "",
    ]

    # Get available variables in display order
    available_vars = [v for v in get_variable_display_order() if v in data.get("variables", {})]

    # Build header
    headers = ["Hour", "Valid Time"]
    for var_id in available_vars:
        var_config = WEATHER_VARIABLES.get(var_id, {})
        name = var_config.get("display_name", var_id)
        units = var_config.get("units", "")
        headers.append(f"{name} ({units})" if units else name)

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        col_widths[0] = max(col_widths[0], len(str(row.get("hour", ""))))
        valid_time = row.get("valid_time", "")
        if valid_time:
            # Just show time portion
            try:
                dt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
                valid_time = dt.strftime("%m/%d %H:%M")
            except (ValueError, AttributeError):
                valid_time = str(valid_time)[:16]
        col_widths[1] = max(col_widths[1], len(valid_time))

        for i, var_id in enumerate(available_vars):
            val_str = row.get(var_id, "-")
            # Strip units for terminal (they're in header)
            if " " in val_str and val_str != "-":
                val_str = val_str.split()[0]
            col_widths[i + 2] = max(col_widths[i + 2], len(val_str))

    # Format header row
    header_row = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * w for w in col_widths)
    lines.extend([header_row, separator])

    # Format data rows
    for row in rows:
        values = [str(row.get("hour", "")).rjust(col_widths[0])]

        valid_time = row.get("valid_time", "")
        if valid_time:
            try:
                dt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
                valid_time = dt.strftime("%m/%d %H:%M")
            except (ValueError, AttributeError):
                valid_time = str(valid_time)[:16]
        values.append(valid_time.ljust(col_widths[1]))

        for i, var_id in enumerate(available_vars):
            val_str = row.get(var_id, "-")
            if " " in val_str and val_str != "-":
                val_str = val_str.split()[0]
            values.append(val_str.rjust(col_widths[i + 2]))

        lines.append(" | ".join(values))

    return "\n".join(lines)


def format_table_html(data: dict, rows: list[dict]) -> str:
    """Format table as HTML."""
    if not rows:
        return "<p>No data available.</p>"

    meta = data.get("metadata", {})
    available_vars = [v for v in get_variable_display_order() if v in data.get("variables", {})]

    html = [
        "<!DOCTYPE html>",
        "<html><head>",
        "<title>Forecast Table</title>",
        "<style>",
        "body { font-family: -apple-system, sans-serif; margin: 20px; }",
        "table { border-collapse: collapse; width: 100%; }",
        "th, td { border: 1px solid #ddd; padding: 8px; text-align: right; }",
        "th { background-color: #4a90d9; color: white; }",
        "tr:nth-child(even) { background-color: #f2f2f2; }",
        "tr:hover { background-color: #ddd; }",
        ".header-info { margin-bottom: 20px; }",
        ".header-info p { margin: 5px 0; }",
        "</style>",
        "</head><body>",
        "<div class='header-info'>",
        f"<p><strong>Location:</strong> {meta.get('location_id', 'unknown')}</p>",
        f"<p><strong>Model:</strong> {meta.get('model_id', 'unknown')}</p>",
        f"<p><strong>Run:</strong> {meta.get('run_id', 'unknown')}</p>",
        f"<p><strong>Init Time:</strong> {meta.get('init_time', 'unknown')}</p>",
        "</div>",
        "<table>",
        "<thead><tr>",
        "<th>Hour</th>",
        "<th>Valid Time</th>",
    ]

    for var_id in available_vars:
        var_config = WEATHER_VARIABLES.get(var_id, {})
        name = var_config.get("display_name", var_id)
        units = var_config.get("units", "")
        html.append(f"<th>{name}<br><small>({units})</small></th>" if units else f"<th>{name}</th>")

    html.append("</tr></thead><tbody>")

    for row in rows:
        html.append("<tr>")
        html.append(f"<td>{row.get('hour', '')}</td>")

        valid_time = row.get("valid_time", "")
        if valid_time:
            try:
                dt = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
                valid_time = dt.strftime("%m/%d %H:%M")
            except (ValueError, AttributeError):
                valid_time = str(valid_time)[:16]
        html.append(f"<td>{valid_time}</td>")

        for var_id in available_vars:
            val_str = row.get(var_id, "-")
            if " " in val_str and val_str != "-":
                val_str = val_str.split()[0]  # Strip units, they're in header
            html.append(f"<td>{val_str}</td>")

        html.append("</tr>")

    html.extend(["</tbody></table>", "</body></html>"])
    return "\n".join(html)


def format_table_json(data: dict, rows: list[dict]) -> str:
    """Format table as JSON."""
    output = {
        "metadata": data.get("metadata", {}),
        "columns": ["hour", "valid_time"] + [
            v for v in get_variable_display_order() if v in data.get("variables", {})
        ],
        "rows": rows,
    }
    return json.dumps(output, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Generate forecast data table for a location"
    )
    parser.add_argument(
        "--location", "-l",
        required=True,
        help="Location ID (e.g., philly, nyc, boston)"
    )
    parser.add_argument(
        "--model", "-m",
        default="hrrr",
        help="Model ID (default: hrrr)"
    )
    parser.add_argument(
        "--run", "-r",
        default=None,
        help="Run ID (default: latest)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["terminal", "html", "json"],
        default="terminal",
        help="Output format (default: terminal)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file (default: stdout)"
    )
    parser.add_argument(
        "--cache-dir",
        default=repomap["CACHE_DIR"],
        help=f"Cache directory (default: {repomap['CACHE_DIR']})"
    )

    args = parser.parse_args()

    # Load data
    data = load_all_center_values(
        args.cache_dir, args.location, args.model, args.run
    )

    if not data.get("variables"):
        print(f"No data found for location '{args.location}' with model '{args.model}'",
              file=sys.stderr)
        print(f"Available locations: {', '.join(repomap['LOCATIONS'].keys())}",
              file=sys.stderr)
        sys.exit(1)

    # Build table
    rows = build_forecast_table(data)

    # Format output
    if args.format == "terminal":
        output = format_table_terminal(data, rows)
    elif args.format == "html":
        output = format_table_html(data, rows)
    else:
        output = format_table_json(data, rows)

    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
