# Sandbox Handoff: TTS Audio Serving Test

## What's New

The TTS audio feature has been wired up end-to-end:

- `audio_gen.py` — Spark-TTS-0.5B via mlx-audio (Apple Silicon only, won't run in Docker)
- `routes/writeup.py` — Writeup CRUD + audio serve/status/generate endpoints
- `templates/writeup.html` — Writeup page with inline audio player
- `app.py` — Writeup blueprint registered, `/static/` exempted from auth

## What to Test

TTS generation itself requires Apple Silicon + MLX (can't run in sandbox). A test MP3 fixture is checked in at `tests/fixtures/sample_forecast.mp3`. Seed the audio cache before testing:

```bash
mkdir -p cache/audio
cp tests/fixtures/sample_forecast.mp3 cache/audio/forecast_test000000.mp3
```

Then:

1. Start the dev server: `python app.py -p 5001`
2. Create a writeup:
   ```bash
   curl -X POST http://localhost:5001/api/writeup \
     -H 'Content-Type: application/json' \
     -d '{"title": "Test Forecast", "body": "A test forecast writeup for audio testing."}'
   ```
3. Check audio status: `curl http://localhost:5001/api/writeup/audio/status`
   - Should return `{"has_audio": true, "generating": false}`
4. Serve audio: `curl http://localhost:5001/api/writeup/audio -o test.mp3`
   - Should return the MP3 file
5. Visit `http://localhost:5001/writeup` — verify the page renders with the audio player
6. Test the player controls (play/pause, seek, volume)

## Audio Flow Summary

```
POST /api/writeup           → saves forecast text to cache/forecast_writeup.json
POST /api/writeup/audio/generate → triggers TTS in background thread (Apple Silicon only)
GET  /api/writeup/audio/status   → {"has_audio": bool, "generating": bool}
GET  /api/writeup/audio          → serves latest cache/audio/forecast_*.mp3
GET  /writeup                    → writeup page with embedded audio player
```

## Known Limitations

- TTS generation only works on native macOS (Apple Silicon + MLX). The `/api/writeup/audio/generate` endpoint will fail in Docker with an ImportError — this is expected.
- No music bed yet (`static/audio/weather_bed.mp3`). The mixer gracefully skips it and serves voice-only audio.
- The test MP3 is generic TTS output, not an actual forecast.
