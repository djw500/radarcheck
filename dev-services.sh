#!/usr/bin/env bash
# Manage background services: scheduler (enqueuer) and worker pool.
# Flask dev server is not managed here — it auto-reloads on code changes.
#
# Usage:
#   ./dev-services.sh start              # Start scheduler + workers
#   ./dev-services.sh stop               # Stop all
#   ./dev-services.sh restart            # stop + start
#   ./dev-services.sh status             # Show status + log tails
#   ./dev-services.sh logs               # Tail both logs live
#   ./dev-services.sh start scheduler    # Start scheduler only
#   ./dev-services.sh start workers      # Start workers only
#   ./dev-services.sh stop scheduler
#   ./dev-services.sh stop workers

set -euo pipefail

# Activate virtualenv if present; otherwise ensure deps are installed system-wide
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif ! python3 -c "import flask" 2>/dev/null; then
    echo "No venv found and Flask not installed — installing dependencies..."
    pip install -r "$SCRIPT_DIR/requirements.txt" -q
fi

# Variable set — must match fly.toml TILE_BUILD_VARIABLES
export TILE_BUILD_VARIABLES="${TILE_BUILD_VARIABLES:-apcp,asnow,snod,t2m}"
export TILE_BUILD_MAX_HOURS_NBM="${TILE_BUILD_MAX_HOURS_NBM:-48}"

SCHED_LOG="/tmp/scheduler.log"
SCHED_PID="/tmp/scheduler.pid"

MODELS=(hrrr nam_nest gfs nbm ecmwf_hres)

# ── Scheduler ────────────────────────────────────────────────────────────────

start_scheduler() {
    if [[ -f "$SCHED_PID" ]] && kill -0 "$(cat "$SCHED_PID")" 2>/dev/null; then
        echo "Scheduler already running (pid $(cat "$SCHED_PID"))"
        return
    fi
    nohup python3 scripts/scheduler.py > "$SCHED_LOG" 2>&1 &
    echo $! > "$SCHED_PID"
    echo "Started scheduler (pid $!), log: $SCHED_LOG"
}

stop_scheduler() {
    if [[ -f "$SCHED_PID" ]] && kill -0 "$(cat "$SCHED_PID")" 2>/dev/null; then
        local pid; pid=$(cat "$SCHED_PID")
        kill "$pid" && echo "Stopped scheduler (pid $pid)"
    else
        echo "Scheduler not running"
    fi
    rm -f "$SCHED_PID"
}

# ── Workers (one per model — dev is not resource constrained) ─────────────────
# Workers auto-restart after --max-jobs to reclaim memory.
MAX_JOBS_PER_CYCLE="${MAX_JOBS_PER_CYCLE:-50}"

start_workers() {
    for model in "${MODELS[@]}"; do
        local pidfile="/tmp/worker_${model}.pid"
        local logfile="/tmp/worker_${model}.log"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "Worker[$model]: already running (pid $(cat "$pidfile"))"
            continue
        fi
        # Wrapper loop: restart worker when it exits after max-jobs
        nohup bash -c '
            while true; do
                python3 job_worker.py --model "'"$model"'" --poll-interval 10 --max-jobs '"$MAX_JOBS_PER_CYCLE"' --log-file "'"$logfile"'" 2>&1
                echo "$(date) Worker['"$model"'] restarting after max-jobs cycle..." >> "'"$logfile"'"
                sleep 2
            done
        ' > "$logfile" 2>&1 &
        echo $! > "$pidfile"
        echo "Worker[$model]: started (pid $!), log: $logfile, max_jobs=$MAX_JOBS_PER_CYCLE"
    done
}

stop_workers() {
    for model in "${MODELS[@]}"; do
        local pidfile="/tmp/worker_${model}.pid"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            local pid; pid=$(cat "$pidfile")
            # Kill the wrapper bash loop and all its children
            pkill -P "$pid" 2>/dev/null || true
            kill "$pid" 2>/dev/null || true
            echo "Worker[$model]: stopped (pid $pid)"
        else
            echo "Worker[$model]: not running"
        fi
        rm -f "$pidfile"
    done
}

# ── Combined ─────────────────────────────────────────────────────────────────

start_all() {
    start_scheduler
    start_workers
}

stop_all() {
    stop_scheduler
    stop_workers
}

status_all() {
    if [[ -f "$SCHED_PID" ]] && kill -0 "$(cat "$SCHED_PID")" 2>/dev/null; then
        echo "Scheduler: running (pid $(cat "$SCHED_PID"))"
    else
        echo "Scheduler: stopped"
    fi
    for model in "${MODELS[@]}"; do
        local pidfile="/tmp/worker_${model}.pid"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "Worker[$model]: running (pid $(cat "$pidfile"))"
        else
            echo "Worker[$model]: stopped"
        fi
    done
    echo ""
    echo "=== Scheduler log (last 10 lines) ==="
    tail -n 10 "$SCHED_LOG" 2>/dev/null || echo "(no log yet)"
    echo ""
    for model in "${MODELS[@]}"; do
        echo "=== Worker[$model] log (last 5 lines) ==="
        tail -n 5 "/tmp/worker_${model}.log" 2>/dev/null || echo "(no log yet)"
    done
}

case "${1:-}" in
    start)
        case "${2:-all}" in
            scheduler) start_scheduler ;;
            workers)   start_workers ;;
            all|"")    start_all ;;
        esac ;;
    stop)
        case "${2:-all}" in
            scheduler) stop_scheduler ;;
            workers)   stop_workers ;;
            all|"")    stop_all ;;
        esac ;;
    restart)
        stop_all; sleep 1; start_all ;;
    status)
        status_all ;;
    logs)
        tail -f "$SCHED_LOG" /tmp/worker_*.log 2>/dev/null ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs} [scheduler|workers]" >&2
        exit 1 ;;
esac
