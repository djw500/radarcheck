from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, render_template, send_file

from config import repomap

logger = logging.getLogger(__name__)

writeup_bp = Blueprint("writeup", __name__)

WRITEUP_FILE = os.path.join(repomap["CACHE_DIR"], "forecast_writeup.json")


def _read_writeup() -> dict | None:
    if not os.path.exists(WRITEUP_FILE):
        return None
    try:
        with open(WRITEUP_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_writeup(data: dict) -> None:
    os.makedirs(os.path.dirname(WRITEUP_FILE), exist_ok=True)
    with open(WRITEUP_FILE, "w") as f:
        json.dump(data, f, indent=2)


@writeup_bp.route("/writeup")
def writeup_page():
    return render_template("writeup.html")


@writeup_bp.route("/api/writeup")
def api_writeup_get():
    data = _read_writeup()
    if not data:
        return jsonify({"writeup": None})
    return jsonify({"writeup": data})


@writeup_bp.route("/api/writeup", methods=["POST"])
def api_writeup_save():
    body = request.get_json(silent=True) or {}
    title = body.get("title", "").strip()
    text = body.get("body", "").strip()

    if not text:
        return jsonify({"error": "body is required"}), 400

    now = datetime.now(timezone.utc).isoformat()

    existing = _read_writeup()
    created_at = existing["created_at"] if existing else now

    data = {
        "title": title or "Forecast Writeup",
        "body": text,
        "detail": body.get("detail", "").strip() or None,
        "location": body.get("location"),
        "created_at": created_at,
        "updated_at": now,
    }

    _save_writeup(data)
    return jsonify({"ok": True, "updated_at": now})


# ---------------------------------------------------------------------------
# Audio endpoints
# ---------------------------------------------------------------------------

_audio_lock = threading.Lock()
_audio_generating = False


@writeup_bp.route("/api/writeup/audio")
def api_writeup_audio():
    """Serve the latest forecast audio MP3."""
    from audio_gen import get_latest_audio_path

    path = get_latest_audio_path()
    if not path or not os.path.exists(path):
        return jsonify({"error": "No audio available"}), 404
    return send_file(path, mimetype="audio/mpeg")


@writeup_bp.route("/api/writeup/audio/status")
def api_writeup_audio_status():
    """Check if audio exists and whether generation is in progress."""
    from audio_gen import get_latest_audio_path

    path = get_latest_audio_path()
    has_audio = path is not None and os.path.exists(path)
    return jsonify({
        "has_audio": has_audio,
        "generating": _audio_generating,
    })


@writeup_bp.route("/api/writeup/audio/generate", methods=["POST"])
def api_writeup_audio_generate():
    """Trigger audio generation from the current writeup text."""
    global _audio_generating

    if _audio_generating:
        return jsonify({"error": "Generation already in progress"}), 409

    writeup = _read_writeup()
    if not writeup or not writeup.get("body"):
        return jsonify({"error": "No writeup text to generate audio from"}), 400

    body = request.get_json(silent=True) or {}
    with_music = body.get("with_music", True)

    def _generate():
        global _audio_generating
        try:
            from audio_gen import generate_forecast_audio
            generate_forecast_audio(
                writeup["body"],
                with_music=with_music,
            )
        except Exception:
            logger.exception("Audio generation failed")
        finally:
            _audio_generating = False

    _audio_generating = True
    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "Audio generation started"})
