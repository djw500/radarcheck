#!/usr/bin/env bash
# Copy .claude.json from the mounted .claude dir into $HOME at startup.
# This avoids bind-mounting the file directly, which races with the host
# Claude session that also writes to it.
if [[ -f "$HOME/.claude/.claude.json.host" ]]; then
    cp "$HOME/.claude/.claude.json.host" "$HOME/.claude.json"
fi

exec "$@"
