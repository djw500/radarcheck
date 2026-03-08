#!/usr/bin/env bash
# Manage background services: scheduler (enqueuer), Rust API server, and worker pool.
#
# Usage:
#   ./dev-services.sh start              # Start scheduler + server + workers
#   ./dev-services.sh stop               # Stop all
#   ./dev-services.sh restart            # stop + start
#   ./dev-services.sh status             # Show status + log tails
#   ./dev-services.sh logs               # Tail all logs live
#   ./dev-services.sh start scheduler    # Start scheduler only
#   ./dev-services.sh start workers      # Start workers only
#   ./dev-services.sh start server       # Start API server only
#   ./dev-services.sh stop scheduler
#   ./dev-services.sh stop workers
#   ./dev-services.sh stop server

set -euo pipefail

# Activate virtualenv if present (needed for scheduler + ECMWF worker)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif ! python3 -c "import flask" 2>/dev/null; then
    echo "No venv found and Flask not installed — installing dependencies..."
    pip install -r "$SCRIPT_DIR/requirements.txt" -q
fi

# Variable set — must match fly.toml TILE_BUILD_VARIABLES
export TILE_BUILD_VARIABLES="${TILE_BUILD_VARIABLES:-apcp,asnow,snod,t2m,cloud_cover}"
export TILE_BUILD_MAX_HOURS_NBM="${TILE_BUILD_MAX_HOURS_NBM:-48}"

SCHED_LOG="/tmp/scheduler.log"
SCHED_PID="/tmp/scheduler.pid"
SERVER_LOG="/tmp/radarcheck_server.log"
SERVER_PID="/tmp/radarcheck_server.pid"
SERVER_PORT="${RADARCHECK_PORT:-5001}"

MODELS=(hrrr nam_nest gfs nbm ecmwf_hres)

# Rust worker for NOAA models, Python for ECMWF (needs Herbie STAC API)
RUST_MODELS=(hrrr nam_nest gfs nbm ecmwf_hres)
PYTHON_MODELS=()
RUST_WORKER_BINARY="rust_worker/target/release/radarcheck-worker"
RUST_SERVER_BINARY="rust_worker/target/release/radarcheck-server"
RUST_BUILD_ENV="rust_worker/build-env.sh"

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

# ── Rust API Server ──────────────────────────────────────────────────────────

_ensure_rust_binaries() {
    local need_build=false
    if [[ ! -x "$SCRIPT_DIR/$RUST_WORKER_BINARY" ]] || [[ ! -x "$SCRIPT_DIR/$RUST_SERVER_BINARY" ]]; then
        need_build=true
    fi
    if $need_build; then
        echo "Building Rust binaries (release)..."
        (source "$SCRIPT_DIR/$RUST_BUILD_ENV" && cargo build --manifest-path "$SCRIPT_DIR/rust_worker/Cargo.toml" --release 2>&1 | tail -3)
        if [[ ! -x "$SCRIPT_DIR/$RUST_WORKER_BINARY" ]] || [[ ! -x "$SCRIPT_DIR/$RUST_SERVER_BINARY" ]]; then
            echo "ERROR: Failed to build Rust binaries" >&2
            return 1
        fi
        echo "Rust binaries built successfully"
    fi
}

start_server() {
    if [[ -f "$SERVER_PID" ]] && kill -0 "$(cat "$SERVER_PID")" 2>/dev/null; then
        echo "Server already running (pid $(cat "$SERVER_PID"))"
        return
    fi
    _ensure_rust_binaries || {
        echo "WARNING: Rust build failed, falling back to Flask"
        nohup python3 app.py -p "$SERVER_PORT" > "$SERVER_LOG" 2>&1 &
        echo $! > "$SERVER_PID"
        echo "Started Flask server (pid $!), port: $SERVER_PORT, log: $SERVER_LOG"
        return
    }
    nohup "$SCRIPT_DIR/$RUST_SERVER_BINARY" \
        --port "$SERVER_PORT" \
        --app-root "$SCRIPT_DIR" \
        --db-path "$SCRIPT_DIR/cache/jobs.db" \
        --tiles-dir "$SCRIPT_DIR/cache/tiles" \
        --cache-dir "$SCRIPT_DIR/cache" \
        > "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PID"
    echo "Started Rust server (pid $!), port: $SERVER_PORT, log: $SERVER_LOG"
}

stop_server() {
    if [[ -f "$SERVER_PID" ]] && kill -0 "$(cat "$SERVER_PID")" 2>/dev/null; then
        local pid; pid=$(cat "$SERVER_PID")
        kill "$pid" && echo "Stopped server (pid $pid)"
    else
        echo "Server not running"
    fi
    rm -f "$SERVER_PID"
}

# ── Workers (one per model — dev is not resource constrained) ─────────────────
# Workers auto-restart after --max-jobs to reclaim memory.
MAX_JOBS_PER_CYCLE="${MAX_JOBS_PER_CYCLE:-50}"

_is_rust_model() {
    local model="$1"
    for m in "${RUST_MODELS[@]}"; do
        [[ "$m" == "$model" ]] && return 0
    done
    return 1
}

start_workers() {
    # Build Rust binaries once if any NOAA model needs it
    _ensure_rust_binaries || echo "WARNING: Rust build failed, NOAA models will use Python fallback"

    for model in "${MODELS[@]}"; do
        local pidfile="/tmp/worker_${model}.pid"
        local logfile="/tmp/worker_${model}.log"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "Worker[$model]: already running (pid $(cat "$pidfile"))"
            continue
        fi

        if _is_rust_model "$model" && [[ -x "$SCRIPT_DIR/$RUST_WORKER_BINARY" ]]; then
            # Rust worker for NOAA models
            nohup bash -c '
                source "'"$SCRIPT_DIR/$RUST_BUILD_ENV"'"
                while true; do
                    "'"$SCRIPT_DIR/$RUST_WORKER_BINARY"'" --model "'"$model"'" --poll-interval 10 --max-jobs '"$MAX_JOBS_PER_CYCLE"' --db-path '"$SCRIPT_DIR"'/cache/jobs.db --tiles-dir '"$SCRIPT_DIR"'/cache/tiles 2>&1
                    echo "$(date) Worker['"$model"'] (rust) restarting after max-jobs cycle..."
                    sleep 2
                done
            ' > "$logfile" 2>&1 &
            echo $! > "$pidfile"
            echo "Worker[$model]: started RUST (pid $!), log: $logfile, max_jobs=$MAX_JOBS_PER_CYCLE"
        else
            # Python worker for ECMWF (or Rust build fallback)
            nohup bash -c '
                while true; do
                    python3 job_worker.py --model "'"$model"'" --poll-interval 10 --max-jobs '"$MAX_JOBS_PER_CYCLE"' --log-file "'"$logfile"'" 2>&1
                    echo "$(date) Worker['"$model"'] (python) restarting after max-jobs cycle..."
                    sleep 2
                done
            ' > "$logfile" 2>&1 &
            echo $! > "$pidfile"
            echo "Worker[$model]: started PYTHON (pid $!), log: $logfile, max_jobs=$MAX_JOBS_PER_CYCLE"
        fi
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
    start_server
    start_workers
}

stop_all() {
    stop_scheduler
    stop_server
    stop_workers
}

status_all() {
    if [[ -f "$SCHED_PID" ]] && kill -0 "$(cat "$SCHED_PID")" 2>/dev/null; then
        echo "Scheduler: running (pid $(cat "$SCHED_PID"))"
    else
        echo "Scheduler: stopped"
    fi
    if [[ -f "$SERVER_PID" ]] && kill -0 "$(cat "$SERVER_PID")" 2>/dev/null; then
        echo "Server: running (rust, pid $(cat "$SERVER_PID"), port $SERVER_PORT)"
    else
        echo "Server: stopped"
    fi
    for model in "${MODELS[@]}"; do
        local pidfile="/tmp/worker_${model}.pid"
        local engine="python"
        _is_rust_model "$model" && engine="rust"
        if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "Worker[$model]: running ($engine, pid $(cat "$pidfile"))"
        else
            echo "Worker[$model]: stopped ($engine)"
        fi
    done
    echo ""
    echo "=== Server log (last 5 lines) ==="
    tail -n 5 "$SERVER_LOG" 2>/dev/null || echo "(no log yet)"
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
            server)    start_server ;;
            workers)   start_workers ;;
            all|"")    start_all ;;
        esac ;;
    stop)
        case "${2:-all}" in
            scheduler) stop_scheduler ;;
            server)    stop_server ;;
            workers)   stop_workers ;;
            all|"")    stop_all ;;
        esac ;;
    restart)
        stop_all; sleep 1; start_all ;;
    status)
        status_all ;;
    logs)
        tail -f "$SERVER_LOG" "$SCHED_LOG" /tmp/worker_*.log 2>/dev/null ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs} [scheduler|server|workers]" >&2
        exit 1 ;;
esac
