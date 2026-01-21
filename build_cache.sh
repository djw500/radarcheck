#!/bin/bash
set -e

# Ensure logs directory exists
mkdir -p logs
LOG_FILE="logs/build_cache.log"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Error: Virtual environment not found." | tee -a "$LOG_FILE"
    exit 1
fi

echo "Starting local cache build at $(date)" | tee -a "$LOG_FILE"
echo "Logging to $LOG_FILE"
echo "--------------------------------------------------" | tee -a "$LOG_FILE"

# Run cache builder for temperature/precipitation focus across key models.
# You can override models/variables via environment variables:
#   MODELS="hrrr nam_nest gfs" VARIABLES="t2m dpt rh apcp prate asnow snod" GFS_MAX_HOURS=168 ./build_cache.sh

MODELS_DEFAULT=(hrrr nam_nest gfs)
VARS_DEFAULT=(t2m dpt rh apcp prate asnow snod)

IFS=' ' read -r -a MODELS <<< "${MODELS:-${MODELS_DEFAULT[*]}}"
IFS=' ' read -r -a VARS <<< "${VARIABLES:-${VARS_DEFAULT[*]}}"
GFS_MAX_HOURS=${GFS_MAX_HOURS:-168}

echo "Models: ${MODELS[*]}" | tee -a "$LOG_FILE"
echo "Variables: ${VARS[*]}" | tee -a "$LOG_FILE"

echo "Running cache_builder once for all models: ${MODELS[*]} $(date)" | tee -a "$LOG_FILE"
python -u cache_builder.py --models "${MODELS[@]}" --variables "${VARS[@]}" --latest-only --max-hours "$GFS_MAX_HOURS" >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

EXIT_CODE=$?

echo "--------------------------------------------------" | tee -a "$LOG_FILE"
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Cache build completed successfully at $(date)!" | tee -a "$LOG_FILE"
else
    echo "❌ Cache build failed with exit code $EXIT_CODE at $(date)." | tee -a "$LOG_FILE"
    echo "Last 20 lines of log:"
    tail -n 20 "$LOG_FILE"
    exit $EXIT_CODE
fi
