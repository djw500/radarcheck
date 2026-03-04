#!/usr/bin/env bash
# Run a headless weather forecast analysis using Claude Code.
#
# Usage:
#   ./scripts/run-forecast.sh                          # Radnor, PA (default)
#   ./scripts/run-forecast.sh 40.75 -73.99 "NYC"      # Custom location
#
# Prerequisites:
#   - Dev server running on :5001  (python app.py -p 5001)
#   - Claude Code CLI installed and authenticated

set -euo pipefail

# Resolve to project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LAT="${1:-40.0488}"
LON="${2:--75.389}"
LOCATION="${3:-Radnor, PA}"

# Ensure we run from project root so Claude picks up CLAUDE.md and .claude/skills/
cd "$PROJECT_ROOT"

# Check dev server
if ! curl -sf http://localhost:5001/health > /dev/null 2>&1; then
    echo "Error: Dev server not running on :5001"
    echo "Start it with: python app.py -p 5001"
    exit 1
fi

echo "Running forecast analysis for $LOCATION ($LAT, $LON)..."

# Phase 1: Pre-fetch all data (model timeseries + NWS) in parallel
echo "Fetching data..."
DATA_FILE=$(mktemp /tmp/forecast-data-XXXXX.json)
trap "rm -f $DATA_FILE" EXIT

python3 scripts/prefetch_forecast_data.py "$LAT" "$LON" "$LOCATION" > "$DATA_FILE"
DATA_SIZE=$(wc -c < "$DATA_FILE" | tr -d ' ')
echo "Data fetched: ${DATA_SIZE} bytes → $DATA_FILE"

# Phase 2: Claude analyzes the pre-fetched data and writes the forecast
echo "Analyzing..."

# Unset CLAUDECODE to allow nested invocation (we're likely inside a Claude session)
unset CLAUDECODE 2>/dev/null || true

# Build prompt with all variable substitutions
PROMPT_TEXT="$(cat <<PROMPT
You are running a headless weather forecast analysis for **${LOCATION}** (${LAT}, ${LON}).

All data has been pre-fetched into a JSON file. Your job is to ANALYZE the data and write the forecast.

## Step 1: Read the data and methodology

1. Read the pre-fetched data file at \`${DATA_FILE}\`.
2. Read the analysis skill at \`.claude/skills/weather-analysis/SKILL.md\` for the analytical methodology. Skip Step 1 (data fetching) -- that's already done. Follow Steps 2 through 5.

## Step 2: Understand the data structure

The JSON file contains:
- \`nws.current_conditions\`: Current weather (temperature, wind, description)
- \`nws.afd_text\`: NWS Area Forecast Discussion (forecaster's narrative)
- \`nws.hwo_text\`: Hazardous Weather Outlook
- \`models.{model}.{variable}.runs[]\`: Near-term model data (days=2). Each run has:
  - \`init_time\`, \`run_id\`, \`peak\`, \`final\` (summary stats)
  - \`series\`: array of \`[forecast_hour, value]\` pairs
- \`extended_range.{gfs|ecmwf_hres}.{apcp|t2m}\`: Extended range (GFS to 384h, ECMWF to 240h). Look at forecast_hour > 168 for Day 7+ signals.

**Models**: hrrr, gfs, nbm, ecmwf_hres (latest 4 synoptic runs each)
**Variables**: asnow (accumulated snowfall, hrrr/nbm), snod (snow depth), apcp (precip), t2m (temperature)
**Units**: asnow/snod/apcp in inches, t2m in Fahrenheit

## Step 3: Analyze and write

Follow the SKILL.md methodology (Steps 2-4):
- Establish synoptic baseline, check short-range confirmation, check NBM, temperature cross-check
- Compute implied snow ratios where relevant (SNOD or ASNOW / APCP)
- Cross-reference NWS AFD with your model analysis
- Scan extended range for Day 7-16 storm signals (APCP > 0.25 in with sub-freezing T2M)
- Present scenarios, not averages, when models diverge

## Step 4: Post the writeup

Use a Python script to POST (avoids shell escaping issues):
\`\`\`
python3 -c "
import json, requests
data = {
    'title': '${LOCATION} Forecast',
    'body': '''<2-paragraph summary>''',
    'detail': '''<full analysis with tables, ratios, extended outlook, what to watch>''',
    'location': {'lat': ${LAT}, 'lon': ${LON}, 'name': '${LOCATION}'}
}
r = requests.post('http://localhost:5001/api/writeup', json=data)
print(r.json())
"
\`\`\`

Use \`in\` or \`inches\` instead of the quote symbol. Use \`--\` instead of em dashes.

**Debug section**: The data JSON contains a \`_debug_prompt\` field with the full prompt text that was sent to you. Append it at the very bottom of the \`detail\` field as a section titled "## Debug: Prompt Context" wrapped in a markdown code block.

Then trigger audio:
\`\`\`
curl -X POST http://localhost:5001/api/writeup/audio/generate -H 'Content-Type: application/json' -d '{}'
\`\`\`
PROMPT
)"

# Inject the prompt into the data JSON so Claude can include it in the writeup
python3 -c "
import json, sys
with open('$DATA_FILE') as f:
    data = json.load(f)
data['_debug_prompt'] = sys.stdin.read()
with open('$DATA_FILE', 'w') as f:
    json.dump(data, f, separators=(',', ':'))
" <<< "$PROMPT_TEXT"

claude -p "$PROMPT_TEXT" \
  --allowedTools "Bash(curl*),Bash(python3*),Read" \
  --max-budget-usd 2.00

echo ""
echo "Done. Check the writeup at http://localhost:5001/writeup"
