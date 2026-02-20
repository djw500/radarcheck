#!/usr/bin/env bash
# Run Claude Code in yolo mode inside an isolated Docker container.
#
# The container can read/write source files normally, but:
#   - cache/ uses a named Docker volume (persistent, shared across containers)
#   - venv/ and .venv/ are shadowed (macOS binaries won't run on Linux)
#   - logs/ is an anonymous volume (ephemeral, per-container)
#   - Nothing else on your host is accessible
#
# Auth: ~/.claude is mounted so your OAuth session is shared.
#       ~/.claude.json is COPIED (not mounted) at startup via docker-entrypoint.sh
#       to avoid corruption from concurrent writes by the host Claude session.
# Port: 5001 is forwarded so `python app.py -p 5001` is reachable at localhost:5001
#
# Usage:
#   ./dev-claude.sh            # Start Claude Code in yolo mode
#   ./dev-claude.sh bash       # Drop into a shell instead
#   ./dev-claude.sh --rebuild  # Force image rebuild, then start

set -euo pipefail

# Always resolve paths relative to the script, not the caller's cwd
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

IMAGE="radarcheck-dev"
REBUILD=0

if [[ "${1:-}" == "--rebuild" ]]; then
    REBUILD=1
    shift
fi

if [[ $REBUILD -eq 1 ]] || ! docker image inspect "$IMAGE" &>/dev/null; then
    echo "Building $IMAGE..."
    docker build -f "$SCRIPT_DIR/Dockerfile.dev" -t "$IMAGE" "$SCRIPT_DIR"
fi

if [[ ! -d "${HOME}/.claude" ]]; then
    echo "WARNING: ~/.claude not found — Claude OAuth session may not be available." >&2
fi

# Stage .claude.json for the entrypoint to copy (avoids bind-mount race condition).
# The entrypoint copies .claude.json.host -> .claude.json at container startup.
if [[ -f "${HOME}/.claude.json" ]]; then
    cp "${HOME}/.claude.json" "${HOME}/.claude/.claude.json.host"
fi

# Shared named volume for cache — persists across container restarts,
# isolated from the host's local ./cache (which has macOS GRIBs/tiles).
docker volume create radarcheck-cache &>/dev/null

# Default command: claude in yolo mode. Override with e.g. ./dev-claude.sh bash
if [[ $# -eq 0 ]]; then
    set -- claude --dangerously-skip-permissions
fi

exec docker run -it --rm \
    -p 5001:5001 \
    -e PORT=5001 \
    -v "$SCRIPT_DIR:/app" \
    -v "${HOME}/.claude:/home/dev/.claude" \
    -v "radarcheck-cache:/app/cache" \
    -v "/app/.venv" \
    -v "/app/venv" \
    -v "/app/logs" \
    "$IMAGE" \
    "$@"
