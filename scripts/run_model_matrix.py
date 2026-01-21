#!/usr/bin/env python3
"""
Run cache builds for a single location across multiple models and variables,
then verify center_values outputs and print a concise summary.

Examples:
  python scripts/run_model_matrix.py --location boston --models hrrr nam_nest --latest-only
  python scripts/run_model_matrix.py --location boston --models gfs --variables apcp --latest-only

Notes:
  - This orchestrates the existing cache_builder CLI in sequence.
  - It verifies that for each model/variable pair a center_values.json exists
    and has at least one value entry, reporting per-variable status.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path to import modules
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from forecast_table import (
    get_latest_run_id,
    load_center_values_for_variable,
)
from config import repomap, WEATHER_VARIABLES


def run_cache_builder(
    python_bin: str,
    location: str,
    model: str,
    variables: Optional[List[str]] = None,
    latest_only: bool = True,
    max_hours: Optional[int] = None,
) -> int:
    """Invoke cache_builder for given location/model/variables."""
    cmd = [python_bin, "cache_builder.py", "--location", location, "--model", model]
    if variables and len(variables) > 0:
        cmd.extend(["--variables", *variables])
    if latest_only:
        cmd.append("--latest-only")
    if max_hours is not None:
        cmd.extend(["--max-hours", str(max_hours)])

    print(f"\n>>> Running: {' '.join(cmd)}")
    return subprocess.call(cmd)


def verify_center_values(
    cache_dir: str,
    location: str,
    model: str,
    run_id: Optional[str],
    variables: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Load center_values.json for each variable and collect a status summary."""
    if run_id is None:
        run_id = get_latest_run_id(cache_dir, location, model)
    if run_id is None:
        return {}, "no-run"

    results: Dict[str, Dict[str, Any]] = {}
    for var in variables:
        payload = load_center_values_for_variable(cache_dir, location, model, run_id, var)
        ok = bool(payload and isinstance(payload.get("values"), list) and len(payload.get("values")) > 0)
        units = None
        if payload:
            units = payload.get("units")
        results[var] = {
            "ok": ok,
            "units": units,
            "count": len(payload.get("values", [])) if payload else 0,
        }
    return results, run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Run model/variable matrix for a location")
    parser.add_argument("--location", required=True, help="Location ID (e.g., boston)")
    parser.add_argument(
        "--models",
        nargs="*",
        default=None,
        help="Model IDs to run (default: all from config)",
    )
    parser.add_argument(
        "--variables",
        nargs="*",
        default=None,
        help="Variable IDs to run (default: all from config)",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python executable to use (default: current interpreter)",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Limit to latest run only",
    )
    parser.add_argument(
        "--gfs-max-hours",
        type=int,
        default=168,
        help="Cap GFS hours (default: 168)",
    )
    args = parser.parse_args()

    location = args.location
    models = args.models or ["hrrr", "nam_nest", "gfs"]
    # Default to temperature and precipitation-related variables if not specified
    if args.variables:
        variables = args.variables
    else:
        variables = []
        for vid, vcfg in WEATHER_VARIABLES.items():
            cat = vcfg.get("category")
            if cat in ("temperature", "precipitation", "winter"):
                if vid in ("apcp", "prate", "asnow", "snod", "t2m", "dpt", "rh"):
                    variables.append(vid)

    # Basic location validation
    if location not in repomap["LOCATIONS"]:
        print(f"Error: Unknown location '{location}'. Known: {', '.join(repomap['LOCATIONS'].keys())}")
        sys.exit(2)

    # Run each model and verify
    summary: Dict[str, Any] = {}
    for model in models:
        if model not in repomap["MODELS"]:
            print(f"Skipping unknown model '{model}'")
            continue

        # Cap GFS to one week (168 hours) unless user provided a smaller value elsewhere
        gfs_cap = args.gfs_max_hours if model == "gfs" else None
        rc = run_cache_builder(
            args.python_bin,
            location,
            model,
            variables,
            latest_only=args.latest_only,
            max_hours=gfs_cap,
        )
        if rc != 0:
            print(f"cache_builder exited with code {rc} for model '{model}'")

        results, run_id = verify_center_values(
            repomap["CACHE_DIR"], location, model, None, variables
        )
        summary[model] = {"run_id": run_id, "variables": results}

    # Print concise matrix summary
    print("\n=== Verification Summary ===")
    print(f"Location: {location}")
    for model, info in summary.items():
        run_id = info.get("run_id")
        print(f"\nModel: {model} (run: {run_id})")
        vars_info: Dict[str, Dict[str, Any]] = info.get("variables", {})
        for var, vinfo in vars_info.items():
            status = "OK" if vinfo.get("ok") else "MISS"
            units = vinfo.get("units") or ""
            count = vinfo.get("count", 0)
            print(f"  - {var:10s} : {status:4s}  values={count:3d}  units={units}")

    # Exit non-zero if any MISS was found
    any_miss = any(
        not vinfo.get("ok")
        for _, info in summary.items()
        for vinfo in info.get("variables", {}).values()
    )
    if any_miss:
        sys.exit(1)


if __name__ == "__main__":
    main()
