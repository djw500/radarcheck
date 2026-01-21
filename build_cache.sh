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

# Run cache builder and capture output
# We use 'unbuffer' or python's -u flag to ensure output isn't buffered
python -u cache_builder.py --latest-only >> "$LOG_FILE" 2>&1

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