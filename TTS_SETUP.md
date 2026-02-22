# TTS Audio Setup Instructions

This was built in the sandbox and needs to be set up on the actual Mac.

## Step 1: Install Dependencies

```bash
cd /path/to/radarcheck
source .venv/bin/activate
bash install-tts.sh
```

This installs:
- `mlx-audio`, `soundfile` (pip, into venv)
- `ffmpeg`, `espeak` (Homebrew)

## Step 2: Generate Suno Music Bed

Go to Suno and generate a 30-60 second instrumental track. Prompt idea:

> ambient weather broadcast background music, subtle synth pad, professional news feel, loopable, no vocals, calm and authoritative

Download it and save as `static/audio/weather_bed.mp3`. The mixer plays it at 15% volume under the voice with a 2s fade-in intro and 3s fade-out outro.

## Step 3: Test

Start the dev server, make sure a writeup exists, then:

```bash
curl -X POST http://localhost:5001/api/writeup/audio/generate
# Poll until done:
curl http://localhost:5001/api/writeup/audio/status
# Listen:
curl http://localhost:5001/api/writeup/audio -o test.mp3
open test.mp3
```

The Qwen3-TTS model (~2GB) downloads automatically on first generation.

## How It Works

- `audio_gen.py` — TTS generation via Qwen3-TTS (mlx-audio), ffmpeg mixing with music bed
- `routes/writeup.py` — API endpoints: `/api/writeup/audio` (serve), `/api/writeup/audio/status` (poll), `/api/writeup/audio/generate` (trigger)
- `templates/writeup.html` — inline audio player with play/pause, seekable progress bar, volume toggle
- Listen button in nav triggers generation if no audio exists, plays if it does
- Caches by text hash — only regenerates when the writeup body changes
- The weather-analysis skill auto-triggers audio generation after pushing a writeup (Step 5)

## After Setup

Once installed, audio generation happens automatically when the weather-analysis skill pushes a forecast. The `/writeup` page shows the player when audio is available.
