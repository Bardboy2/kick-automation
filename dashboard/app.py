#!/usr/bin/env python3
"""
dashboard/app.py — Kick Clip Review Dashboard
Local Flask web app.  Open http://localhost:5050 after running the pipeline.

Features:
  - Lists all pending clips with inline video preview
  - Shows AI-generated title / description / hashtags per platform (editable)
  - Approve → immediately posts to YouTube Shorts, TikTok, Instagram Reels
  - Reject → marks clip as rejected (no post)
  - History tab shows posted / rejected clips

Run:
    python dashboard/app.py
"""

import sys
import os
import json
import subprocess
from pathlib import Path

from flask import (Flask, render_template, request, jsonify,
                   send_from_directory, redirect, url_for)

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))   # so we can import db

import db  # noqa: E402  (imported after sys.path update)

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / "config" / ".env", override=True)
except ImportError:
    pass

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kick-dashboard-dev-key")

PORT = int(os.environ.get("DASHBOARD_PORT", "5050"))


# ── Video file serving ────────────────────────────────────────────────────────

@app.route("/video/<path:filepath>")
def serve_video(filepath):
    """Serve a local video file so the browser <video> element can play it."""
    full = Path("/") / filepath
    return send_from_directory(str(full.parent), full.name)


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    pending  = db.get_pending_clips()
    recent   = [c for c in db.get_recent_clips(50) if c["status"] != "pending"]
    return render_template("index.html", pending=pending, recent=recent)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/clip/<int:clip_id>")
def api_clip(clip_id):
    clip = db.get_clip(clip_id)
    if not clip:
        return jsonify({"error": "not found"}), 404
    return jsonify(clip)


@app.route("/api/clip/<int:clip_id>/approve", methods=["POST"])
def api_approve(clip_id):
    """
    Approve a clip:
      1. Save any edited metadata from the request body
      2. Mark as approved in DB
      3. Call publish.py to post immediately
      4. Mark as posted (or failed)
    """
    clip = db.get_clip(clip_id)
    if not clip:
        return jsonify({"error": "not found"}), 404

    # Accept edited metadata from the dashboard form
    body = request.get_json(silent=True) or {}
    if "metadata" in body:
        db.update_clip_metadata(clip_id, body["metadata"])
        clip["metadata_json"] = body["metadata"]

    db.approve_clip(clip_id)

    # Build the clip dict that publish.py expects
    clip_for_publish = _build_publish_payload(clip_id)

    # Call publish.py as a subprocess (keeps publish logic isolated)
    result = _run_publish(clip_for_publish)

    if result.get("error"):
        return jsonify({"status": "error", "detail": result["error"]}), 500

    db.mark_posted(clip_id, result)
    return jsonify({"status": "posted", "results": result})


@app.route("/api/clip/<int:clip_id>/reject", methods=["POST"])
def api_reject(clip_id):
    db.reject_clip(clip_id)
    return jsonify({"status": "rejected"})


@app.route("/api/stats")
def api_stats():
    all_clips = db.get_recent_clips(1000)
    counts    = {}
    for c in all_clips:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return jsonify(counts)


# ── Publish helper ────────────────────────────────────────────────────────────

def _build_publish_payload(clip_id: int) -> dict:
    """Re-fetch the clip from DB and shape it into what publish.py expects."""
    clip = db.get_clip(clip_id)
    meta = clip.get("metadata_json") or {}

    return {
        "clip_index":     clip["clip_index"],
        "captioned_path": clip["captioned_path"],
        "approved_path":  clip["captioned_path"],   # publish.py reads "approved_path"
        "video_path":     clip["clip_path"],
        "peak_time":      clip["peak_time"] or 0,
        "duration":       clip["duration"] or 0,
        "metadata":       meta,
        # Flatten for publish.py convenience
        "yt_title":       (meta.get("youtube") or {}).get("title", ""),
        "yt_description": (meta.get("youtube") or {}).get("description", ""),
        "yt_tags":        (meta.get("youtube") or {}).get("tags", []),
        "tt_title":       (meta.get("tiktok")  or {}).get("title", ""),
        "ig_caption":     (meta.get("instagram") or {}).get("caption", ""),
        "hashtags":       meta.get("hashtags", []),
    }


def _run_publish(clip_dict: dict) -> dict:
    """Invoke publish.py and return the results dict."""
    payload = {"clips": [clip_dict]}
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "publish.py"),
        "-",
        "--platforms", "youtube", "tiktok", "instagram",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=400,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr[-2000:]}
        return json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "publish.py timed out after 300s"}
    except Exception as exc:
        return {"error": str(exc)}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  Kick Review Dashboard → http://localhost:{PORT}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
