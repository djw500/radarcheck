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

# Variable set — must match fly.toml TILE_BUILD_VARIABLES
export TILE_BUILD_VARIABLES="${TILE_BUILD_VARIABLES:-apcp,prate,asnow,csnow,snod,t2m}"
export TILE_BUILD_MAX_HOURS_NBM="${TILE_BUILD_MAX_HOURS_NBM:-48}"

SCHED_LOG="/tmp/scheduler.log"
SCHED_PID="/tmp/scheduler.pid"
WORKER_LOG="/tmp/workers.log"
WORKER_PID="/tmp/workers.pid"
WORKER_COUNT="${TILE_WORKER_COUNT:-2}"

# ── Scheduler ────────────────────────────────────────────────────────────────

start_scheduler() {
    if [[ -f "$SCHED_PID" ]] && kill -0 "$(cat "$SCHED_PID")" 2>/dev/null; then
        echo "Scheduler already running (pid $(cat "$SCHED_PID"))"
        return
    fi
    nohup python3 scripts/build_tiles_scheduled.py > "$SCHED_LOG" 2>&1 &
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

# ── Workers ──────────────────────────────────────────────────────────────────

start_workers() {
    if [[ -f "$WORKER_PID" ]] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null; then
        echo "Workers already running (pid $(cat "$WORKER_PID"))"
        return
    fi
    nohup python3 job_worker.py --workers "$WORKER_COUNT" --log-file "$WORKER_LOG" > "$WORKER_LOG" 2>&1 &
    echo $! > "$WORKER_PID"
    echo "Started $WORKER_COUNT workers (pid $!), log: $WORKER_LOG"
}

stop_workers() {
    if [[ -f "$WORKER_PID" ]] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null; then
        local pid; pid=$(cat "$WORKER_PID")
        # Kill the manager and any child processes it spawned (multiprocessing daemons
        # use their own process groups, so we kill children explicitly)
        local children
        children=$(awk -v ppid="$pid" '$4==ppid{print $1}' /proc/[0-9]*/stat 2>/dev/null | tr '\n' ' ')
        kill $children 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        echo "Stopped workers (pid $pid, children: ${children:-none})"
    else
        echo "Workers not running"
    fi
    rm -f "$WORKER_PID"
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
    if [[ -f "$WORKER_PID" ]] && kill -0 "$(cat "$WORKER_PID")" 2>/dev/null; then
        echo "Workers:   running (pid $(cat "$WORKER_PID"), count=$WORKER_COUNT)"
    else
        echo "Workers:   stopped"
    fi
    echo ""
    echo "=== Scheduler log (last 15 lines) ==="
    tail -n 15 "$SCHED_LOG" 2>/dev/null || echo "(no log yet)"
    echo ""
    echo "=== Worker log (last 15 lines) ==="
    tail -n 15 "$WORKER_LOG" 2>/dev/null || echo "(no log yet)"
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
        tail -f "$SCHED_LOG" "$WORKER_LOG" ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs} [scheduler|workers]" >&2
        exit 1 ;;
esac
