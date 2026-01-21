#!/usr/bin/env python3
from __future__ import annotations

import datetime
import subprocess
import sys
import time
from typing import Iterable

from config import repomap


MODELS = {
    "hrrr": {"max_hours": None, "interval_hours": 1},
    "nam_nest": {"max_hours": None, "interval_hours": 6},
    "gfs": {"max_hours": 168, "interval_hours": 6},
}

VARIABLES = ["t2m", "apcp", "snod"]


def should_run_model(model_id: str, now_utc: datetime.datetime) -> bool:
    model_cfg = MODELS.get(model_id)
    if not model_cfg:
        return False
    interval = model_cfg.get("interval_hours", 6)
    return now_utc.hour % interval == 0


def build_tiles(model_id: str, region_id: str, max_hours: int | None) -> int:
    cmd = [
        sys.executable,
        "build_tiles.py",
        "--region",
        region_id,
        "--model",
        model_id,
        "--variables",
        *VARIABLES,
        "--clean-gribs",
    ]
    if max_hours:
        cmd.extend(["--max-hours", str(max_hours)])
    print(f"[tile-builder] Running: {' '.join(cmd)}")
    return subprocess.call(cmd)


def iter_regions() -> Iterable[str]:
    return repomap.get("TILING_REGIONS", {}).keys()


def main() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    print(f"[tile-builder] Starting scheduled build at {now.isoformat()}")
    failures: list[str] = []

    for region_id in iter_regions():
        for model_id, config in MODELS.items():
            if not should_run_model(model_id, now):
                print(f"[tile-builder] Skipping {model_id} (not scheduled hour)")
                continue
            status = build_tiles(model_id, region_id, config.get("max_hours"))
            if status != 0:
                failures.append(f"{model_id}:{region_id}")
            time.sleep(5)

    if failures:
        raise SystemExit(f"[tile-builder] Failed builds: {', '.join(failures)}")
    print("[tile-builder] Complete.")


if __name__ == "__main__":
    main()
