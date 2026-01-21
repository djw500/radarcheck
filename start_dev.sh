#!/bin/bash
set -e

# Ensure logs directory exists
mkdir -p logs

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Error: Virtual environment not found (checked 'venv' and '.venv')"
    echo "Please set up the environment first:"
    echo "  python3 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Function to cleanup background process on exit
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        echo ""
        echo "Stopping server (PID $SERVER_PID)..."
        kill $SERVER_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Start server
echo "Starting Flask server on http://localhost:5001..."
export PORT=5001
python app.py > logs/dev_server.log 2>&1 &
SERVER_PID=$!

# Wait for health check
echo "Waiting for health check..."
MAX_RETRIES=10
for i in $(seq 1 $MAX_RETRIES); do
    if curl -s http://localhost:5001/health >/dev/null; then
        echo "✅ Server is healthy!"
        echo "Health status:"
        curl -s http://localhost:5001/health
        echo ""
        echo "--------------------------------------------------"
        echo "Server is running. Logs: logs/dev_server.log"
        echo "Press Ctrl+C to stop."
        echo "--------------------------------------------------"
        
        # In a real interactive shell, we would wait here.
        # For the purpose of this script, we assume the user wants to keep it running.
        wait $SERVER_PID
        exit 0
    fi
    sleep 1
    echo -n "."
done

echo ""
echo "❌ Server failed to respond after $MAX_RETRIES seconds."
echo "Last 20 lines of log:"
tail -n 20 logs/dev_server.log
exit 1
