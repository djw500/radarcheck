from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template
import pytz

from config import repomap
from status_utils import (
    get_disk_usage,
    get_job_queue_status,
    get_rebuild_eta,
    get_run_grid,
    read_scheduler_logs,
    read_scheduler_status,
)

status_bp = Blueprint("status", __name__)


def _get_jobs_db():
    """Get a DB connection with short busy_timeout for API reads."""
    import sqlite3 as _sqlite3
    from tile_db import init_db as _init_tile_db
    conn = _init_tile_db(repomap.get("DB_PATH", "cache/jobs.db"))
    conn.execute("PRAGMA busy_timeout = 2000")
    return conn


# --- Status routes ---

@status_bp.route("/status")
def status_page():
    """Render system status dashboard."""
    return render_template("status.html")


@status_bp.route("/api/status/run-grid")
def api_status_run_grid():
    """Get per-model/run/hour job status grid from jobs table."""
    grid = get_run_grid()
    return jsonify(grid)


def _get_memory_info():
    """Read /proc/meminfo and return memory stats in bytes."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "MemFree"):
                    info[parts[0].rstrip(":")] = int(parts[1]) * 1024  # kB to bytes
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0))
        return {"total": total, "available": available, "used": total - available,
                "percent_used": round((total - available) / total * 100, 1) if total else 0}
    except Exception:
        return None


@status_bp.route("/api/status/summary")
def api_status_summary():
    """Get system status summary (cache, disk, scheduler)."""
    disk_usage = get_disk_usage()
    scheduler_status = read_scheduler_status()
    job_queue = get_job_queue_status()

    return jsonify({
        "disk_usage": disk_usage,
        "memory": _get_memory_info(),
        "scheduler_status": scheduler_status,
        "job_queue": job_queue,
        "rebuild_eta": get_rebuild_eta(),
        "timestamp": datetime.now(pytz.UTC).isoformat()
    })


@status_bp.route("/api/status/logs")
def api_status_logs():
    """Get recent scheduler logs."""
    try:
        lines = int(request.args.get("lines", 100))
    except ValueError:
        lines = 100

    log_data = read_scheduler_logs(lines=lines)
    return jsonify({"lines": log_data})


# --- Job management routes ---

@status_bp.route("/api/jobs/list")
def api_jobs_list():
    """Paginated job list with filters."""
    from jobs import get_jobs, count_by_status
    status_filter = request.args.get("status")
    type_filter = request.args.get("type")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = min(limit, 200)

    conn = _get_jobs_db()
    try:
        jobs = get_jobs(conn, job_type=type_filter, status=status_filter, limit=limit)
        counts = count_by_status(conn)
    finally:
        conn.close()
    return jsonify({"jobs": jobs, "counts": counts})


@status_bp.route("/api/jobs/retry-failed", methods=["POST"])
def api_jobs_retry_failed():
    """Reset failed jobs back to pending."""
    from jobs import retry_all_failed
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")

    conn = _get_jobs_db()
    try:
        retried = retry_all_failed(conn, job_id=job_id)
    finally:
        conn.close()
    return jsonify({"retried": retried})


@status_bp.route("/api/jobs/cancel", methods=["POST"])
def api_jobs_cancel():
    """Cancel pending/processing jobs."""
    from jobs import cancel
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    status_filter = data.get("status")

    conn = _get_jobs_db()
    try:
        cancelled = cancel(conn, job_id=job_id, status_filter=status_filter)
    finally:
        conn.close()
    return jsonify({"cancelled": cancelled})


@status_bp.route("/api/jobs/enqueue-run", methods=["POST"])
def api_jobs_enqueue_run():
    """Enqueue all jobs for a specific model/run."""
    from scripts.scheduler import enqueue_run_jobs
    from status_utils import SCHEDULED_MODELS, _get_max_hours_for_run as get_max_hours_for_run

    data = request.get_json(silent=True) or {}
    model_id = data.get("model_id")
    run_id = data.get("run_id")
    region_id = data.get("region_id", "ne")

    if not model_id or not run_id:
        return jsonify({"error": "model_id and run_id are required"}), 400

    model_cfg_entry = next((m for m in SCHEDULED_MODELS if m["id"] == model_id), None)
    default_max = model_cfg_entry["max_hours"] if model_cfg_entry else repomap["MODELS"].get(model_id, {}).get("max_forecast_hours", 48)
    max_hours = get_max_hours_for_run(model_id, run_id, default_max)

    conn = _get_jobs_db()
    try:
        enqueued = enqueue_run_jobs(conn, region_id, model_id, run_id, max_hours)
    finally:
        conn.close()
    return jsonify({"enqueued": enqueued})
