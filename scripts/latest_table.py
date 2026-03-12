#!/usr/bin/env python3
"""Live latest-table generator.

Called by the Rust server on demand (subprocess).
Fetches current tile data via the multirun API, merges by priority,
and outputs JSON to stdout.

Usage:
    python3 scripts/latest_table.py [--lat 40.0] [--lon -75.4]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# qualitative.py is in scripts/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qualitative import build_model_data, build_latest_table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lat", type=float, default=40.0)
    parser.add_argument("--lon", type=float, default=-75.4)
    args = parser.parse_args()

    model_data, hour_labels, hour_isos, _, all_data = build_model_data(args.lat, args.lon)
    result = build_latest_table(model_data, all_data, hour_labels, hour_isos)
    json.dump(result, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
