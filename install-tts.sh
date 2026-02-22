#!/usr/bin/env bash
# Install dependencies for local TTS audio generation (Qwen3-TTS via MLX).
# Run this on the dev machine (Apple Silicon Mac).

set -e

echo "=== Installing TTS dependencies ==="

# Python packages (into active venv)
pip install mlx-audio soundfile

# System dependencies (macOS)
if command -v brew &>/dev/null; then
    echo "Installing ffmpeg and espeak via Homebrew..."
    brew install ffmpeg espeak
else
    echo "WARNING: Homebrew not found. Install ffmpeg and espeak manually."
    echo "  - ffmpeg: https://ffmpeg.org/download.html"
    echo "  - espeak: https://github.com/espeak-ng/espeak-ng"
fi

# Create audio directories
mkdir -p static/audio cache/audio

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Generate a weather-themed music bed with Suno (30-60s loop)"
echo "  2. Save it as: static/audio/weather_bed.mp3"
echo "  3. The TTS model (Qwen3-TTS) will download automatically on first use"
echo ""
echo "Test with:"
echo "  curl -X POST http://localhost:5001/api/writeup/audio/generate"
echo "  # Wait for generation, then:"
echo "  curl http://localhost:5001/api/writeup/audio -o test.mp3"
