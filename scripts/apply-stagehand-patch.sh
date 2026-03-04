#!/usr/bin/env bash
# apply-stagehand-patch.sh — Patch stagehand-mcp-local for local-only usage
# Patches:
#   1. sessionManager.js — add STAGEHAND_USER_DATA_DIR support
#   2. tools/index.js — remove browserbase_session_create tool
# Run this after container startup or npx cache clear.
set -euo pipefail

DIST=$(find /home/dev/.npm/_npx -path "*/stagehand-mcp-local/dist" -type d 2>/dev/null | head -1)

if [ -z "$DIST" ]; then
    echo "[patch] stagehand-mcp-local not in npx cache. Populating..."
    npx -y stagehand-mcp-local@latest --help >/dev/null 2>&1 || true
    DIST=$(find /home/dev/.npm/_npx -path "*/stagehand-mcp-local/dist" -type d 2>/dev/null | head -1)
    if [ -z "$DIST" ]; then
        echo "[patch] ERROR: Could not find dist dir after npx install"
        exit 1
    fi
fi

SM_FILE="$DIST/sessionManager.js"
INDEX_FILE="$DIST/tools/index.js"

# --- Patch 1: persistent Chrome profiles ---
if grep -q "STAGEHAND_USER_DATA_DIR" "$SM_FILE"; then
    echo "[patch] userDataDir patch already applied"
else
    sed -i '/                \],$/a\                userDataDir: process.env.STAGEHAND_USER_DATA_DIR || undefined,\n                preserveUserDataDir: !!process.env.STAGEHAND_USER_DATA_DIR,' "$SM_FILE"
    if grep -q "STAGEHAND_USER_DATA_DIR" "$SM_FILE"; then
        echo "[patch] userDataDir patch applied: $SM_FILE"
    else
        echo "[patch] ERROR: userDataDir patch failed"
        exit 1
    fi
fi

# --- Patch 2: remove create session tool (Browserbase-only) ---
if grep -q "sessionManagementTools" "$INDEX_FILE" && ! grep -q "// PATCHED: removed create session" "$INDEX_FILE"; then
    # Replace sessionTools spread with just the close tool (index 1)
    sed -i 's/\.\.\.sessionTools,/sessionTools[1], \/\/ PATCHED: removed create session (browserbase-only)/' "$INDEX_FILE"
    # Remove the sessionManagementTools export since it includes create
    sed -i 's/^export const sessionManagementTools.*/export const sessionManagementTools = [sessionTools[1]]; \/\/ PATCHED/' "$INDEX_FILE"
    echo "[patch] Removed browserbase_session_create tool"
else
    echo "[patch] create session already removed or not found"
fi

# --- Patch 3: add 120s timeout to all tool actions ---
CTX_FILE="$DIST/context.js"
if ! grep -q "PATCHED: action timeout" "$CTX_FILE"; then
    # Wrap the action() call in context.run() with Promise.race against a 120s timeout
    sed -i 's/const actionResult = await toolResult.action();/const TIMEOUT_MS = 120000;\n                    const actionResult = await Promise.race([\n                        toolResult.action(),\n                        new Promise((_, reject) => setTimeout(() => reject(new Error(`Tool ${tool.schema.name} timed out after ${TIMEOUT_MS\/1000}s`)), TIMEOUT_MS))\n                    ]); \/\/ PATCHED: action timeout/' "$CTX_FILE"
    if grep -q "PATCHED: action timeout" "$CTX_FILE"; then
        echo "[patch] Added 120s action timeout"
    else
        echo "[patch] WARNING: timeout patch failed (non-fatal)"
    fi
else
    echo "[patch] action timeout already applied"
fi

# Ensure profile directory exists
mkdir -p /home/dev/.chrome-profile
echo "[patch] Done. Profile dir ready: /home/dev/.chrome-profile/"
