"""Generate spoken forecast audio using local TTS (Spark-TTS via MLX).

Produces an MP3 file from forecast text, optionally mixed with a background
music bed via ffmpeg.

Dependencies (installed via install-tts.sh):
    pip install mlx-audio soundfile
    brew install ffmpeg espeak
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_CACHE_DIR = os.path.join("cache", "audio")
MUSIC_BED_PATH = os.path.join("static", "audio", "weather_bed.mp3")

TTS_MODEL = "prince-canuma/Spark-TTS-0.5B"


def _ensure_dirs():
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    os.makedirs(os.path.join("static", "audio"), exist_ok=True)


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


def _generate_tts(text: str, output_path: str) -> bool:
    """Generate speech using Spark-TTS via mlx-audio.

    Returns True on success, False on failure.
    """
    try:
        from mlx_audio.tts.generate import generate_audio

        # Generate WAV first, then convert to MP3
        wav_dir = os.path.dirname(output_path) or "."
        wav_prefix = "tts_voice"

        generate_audio(
            text=text,
            model=TTS_MODEL,
            output_path=wav_dir,
            file_prefix=wav_prefix,
            audio_format="wav",
            verbose=True,
        )

        # Spark-TTS appends _000 suffix to filenames
        wav_path = os.path.join(wav_dir, f"{wav_prefix}_000.wav")
        if not os.path.exists(wav_path):
            # Fallback: check without suffix
            wav_path = os.path.join(wav_dir, f"{wav_prefix}.wav")
        if not os.path.exists(wav_path):
            logger.error("TTS generation produced no output file in %s", wav_dir)
            return False

        # Convert WAV to MP3 via ffmpeg
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-b:a", "192k", "-ar", "44100", output_path],
                capture_output=True, check=True, timeout=60,
            )
            os.remove(wav_path)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("ffmpeg conversion failed, keeping WAV: %s", exc)
            os.rename(wav_path, output_path)

        return True

    except ImportError:
        logger.error(
            "mlx-audio not installed. Run: pip install mlx-audio soundfile"
        )
        return False
    except Exception:
        logger.exception("TTS generation failed")
        return False


def _mix_with_music(voice_path: str, output_path: str, music_volume: float = 0.15) -> bool:
    """Mix voice audio with background music bed using ffmpeg.

    The music fades in, ducks under the voice, and fades out.
    Returns True on success.
    """
    if not os.path.exists(MUSIC_BED_PATH):
        logger.info("No music bed at %s, skipping mix", MUSIC_BED_PATH)
        # Just copy voice as-is
        if voice_path != output_path:
            subprocess.run(["cp", voice_path, output_path], check=True)
        return True

    try:
        # Get voice duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", voice_path],
            capture_output=True, text=True, check=True, timeout=30,
        )
        voice_duration = float(probe.stdout.strip())

        # Total duration: 2s intro + voice + 2s outro
        total_duration = voice_duration + 4.0
        fade_out_start = total_duration - 3.0

        # Complex filter:
        # - Music: loop/trim to length, set volume low, fade in 2s, fade out 3s
        # - Voice: delay by 2s (after music intro)
        # - Mix both streams
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration:.1f},"
            f"volume={music_volume},"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_start:.1f}:d=3[music];"
            f"[0:a]adelay=2000|2000[voice];"
            f"[voice][music]amix=inputs=2:duration=longest:dropout_transition=2[out]"
        )

        subprocess.run(
            ["ffmpeg", "-y",
             "-i", voice_path,
             "-i", MUSIC_BED_PATH,
             "-filter_complex", filter_complex,
             "-map", "[out]",
             "-b:a", "192k", "-ar", "44100",
             output_path],
            capture_output=True, check=True, timeout=120,
        )
        return True

    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
        logger.warning("Music mix failed, using voice only: %s", exc)
        if voice_path != output_path:
            subprocess.run(["cp", voice_path, output_path], check=True)
        return True


def generate_forecast_audio(
    text: str,
    with_music: bool = True,
) -> str | None:
    """Generate forecast audio and return the file path, or None on failure.

    Caches by text hash — regenerates only when text changes.
    """
    _ensure_dirs()

    text_id = _text_hash(text)
    final_path = os.path.join(AUDIO_CACHE_DIR, f"forecast_{text_id}.mp3")

    # Return cached version if text hasn't changed
    if os.path.exists(final_path):
        logger.info("Using cached audio: %s", final_path)
        return final_path

    # Generate TTS
    voice_path = os.path.join(AUDIO_CACHE_DIR, f"voice_{text_id}.mp3")
    if not _generate_tts(text, voice_path):
        return None

    # Mix with music if available and requested
    if with_music:
        if not _mix_with_music(voice_path, final_path):
            return None
        # Clean up intermediate voice file
        if os.path.exists(voice_path) and voice_path != final_path:
            os.remove(voice_path)
    else:
        os.rename(voice_path, final_path)

    logger.info("Generated forecast audio: %s", final_path)
    return final_path


def get_latest_audio_path() -> str | None:
    """Return path to the most recent forecast audio, if any."""
    _ensure_dirs()
    audio_files = sorted(
        Path(AUDIO_CACHE_DIR).glob("forecast_*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(audio_files[0]) if audio_files else None
