#!/usr/bin/env python3
"""
detect_peaks.py — Audio + Claude AI Viral Moment Detector
Two-stage pipeline:
  1. Audio RMS analysis — fast, cheap first pass to find candidate timestamps
  2. Claude AI scene analysis — extracts a frame at each candidate and asks
     Claude to score and describe the viral potential of the moment

The AI stage adds a description and an ai_score (0–10) to each clip, which
feeds into the caption and metadata generators downstream.

Usage:
    python detect_peaks.py <video_path> [--top N] [--min-gap 60] [--clip-duration 45]

Outputs JSON to stdout (same schema as before, with added ai_score / ai_description).
"""

import sys
import os
import json
import base64
import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

import shutil

import numpy as np
import anthropic

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=False)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [detect] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

FFMPEG  = shutil.which("ffmpeg")  or "/usr/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/bin/ffprobe"
log = logging.getLogger(__name__)

CONTENT_TYPE = "IRL / Just Chatting and Music / DJ live streams on Kick"

# Claude system prompt tuned for IRL + Music/DJ content
CLAUDE_SYSTEM = f"""\
You are an expert short-form content editor who specialises in finding viral \
moments in {CONTENT_TYPE}.

You will be shown a single frame from a live stream. Your job is to:
1. Decide how viral / high-energy this moment is on a scale from 0 to 10.
2. Write a one-sentence description of what is happening in the frame that \
   could be used as the basis for a social media caption.

Respond ONLY with valid JSON — no markdown fences — in this exact shape:
{{"score": <integer 0-10>, "description": "<one sentence>"}}

High-score moments (7-10): big reactions, laughing fits, crowd going wild, \
music drop, surprising or funny events, emotional peaks, arguments, or anything \
that would make someone stop scrolling.
Low-score moments (0-3): quiet talking, reading chat, dead air, menu screens.
"""

CLAUDE_USER = "Analyse this live stream frame and return the JSON score + description."


# ─── Audio extraction & peak detection (unchanged from original) ──────────────

def extract_audio_pcm(video_path: str, sample_rate: int = 16000):
    log.info("Extracting audio from %s…", video_path)
    cmd = [
        FFMPEG, "-y", "-i", video_path,
        "-ac", "1", "-ar", str(sample_rate),
        "-f", "f32le", "-vn", "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed:\n{result.stderr.decode()}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    log.info("Extracted %.1f seconds of audio.", len(audio) / sample_rate)
    return audio, sample_rate


def compute_rms_curve(audio: np.ndarray, sample_rate: int, window_sec: float = 1.0):
    frame_size = int(window_sec * sample_rate)
    n_frames   = len(audio) // frame_size
    rms_values = [
        float(np.sqrt(np.mean(audio[i * frame_size:(i + 1) * frame_size] ** 2)))
        for i in range(n_frames)
    ]
    times = np.arange(n_frames) * window_sec + window_sec / 2
    rms   = np.array(rms_values, dtype=np.float32)
    if rms.max() > 0:
        rms = rms / rms.max()
    return times, rms


def _smooth(arr: np.ndarray, kernel: int = 5) -> np.ndarray:
    kernel = max(1, kernel)
    pad    = kernel // 2
    padded = np.pad(arr, pad, mode="edge")
    return np.convolve(padded, np.ones(kernel) / kernel, mode="valid")[: len(arr)]


def find_audio_peaks(times, rms, top_n: int, min_gap_sec: float) -> list[dict]:
    smoothed   = _smooth(rms, kernel=11)
    candidates = [
        (float(smoothed[i]), float(times[i]))
        for i in range(1, len(smoothed) - 1)
        if smoothed[i] >= smoothed[i - 1] and smoothed[i] >= smoothed[i + 1]
    ]
    candidates.sort(key=lambda x: -x[0])

    selected = []
    for score, t in candidates:
        if all(abs(t - k["peak_time"]) >= min_gap_sec for k in selected):
            selected.append({"peak_time": t, "audio_score": round(score, 4)})
        if len(selected) == top_n:
            break

    selected.sort(key=lambda x: x["peak_time"])
    return selected


def compute_clip_window(peak_time: float, video_duration: float,
                        clip_duration: float = 45.0) -> tuple[float, float]:
    half  = clip_duration / 2.0
    start = max(0.0, peak_time - half)
    end   = min(video_duration, peak_time + half)
    if end - start < 30.0:
        deficit = 30.0 - (end - start)
        start   = max(0.0, start - deficit / 2)
        end     = min(video_duration, end + deficit / 2)
    if end - start > 60.0:
        end = start + 60.0
    return round(start, 3), round(end, 3)


def get_video_duration(video_path: str) -> float:
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
           "-show_format", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return float(json.loads(result.stdout)["format"]["duration"])


# ─── Claude AI scene analysis ─────────────────────────────────────────────────

def extract_frame(video_path: str, timestamp: float, out_path: str) -> bool:
    """Extract a single JPEG frame at `timestamp` seconds from the video."""
    cmd = [
        FFMPEG, "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "4",           # JPEG quality (lower = better)
        "-vf", "scale=640:-1", # resize to 640px wide to reduce API payload
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def analyse_frame_with_claude(frame_path: str, api_key: str) -> dict:
    """
    Send a frame to Claude claude-sonnet-4-6 and return {"score": int, "description": str}.
    Falls back to {"score": 5, "description": "Unable to analyse frame."} on error.
    """
    client = anthropic.Anthropic(api_key=api_key)

    with open(frame_path, "rb") as f:
        image_b64 = base64.standard_b64encode(f.read()).decode()

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=128,
            system=CLAUDE_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": "image/jpeg",
                            "data":       image_b64,
                        },
                    },
                    {"type": "text", "text": CLAUDE_USER},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        data = json.loads(raw)
        return {
            "ai_score":       max(0, min(10, int(data.get("score", 5)))),
            "ai_description": str(data.get("description", "")),
        }
    except Exception as exc:
        log.warning("Claude analysis failed: %s", exc)
        return {"ai_score": 5, "ai_description": ""}


# ─── Main detection pipeline ──────────────────────────────────────────────────

def detect(video_path: str, top_n: int = 3, min_gap: float = 60.0,
           clip_duration: float = 45.0,
           range_start=None,
           range_end=None) -> dict:
    video_duration = get_video_duration(video_path)
    log.info("Video duration: %.1fs", video_duration)

    # Stage 1 — audio peaks
    audio, sr    = extract_audio_pcm(video_path)
    times, rms   = compute_rms_curve(audio, sr)

    # Restrict to time range if specified
    if range_start is not None or range_end is not None:
        rs = range_start or 0.0
        re = range_end or video_duration
        mask  = (times >= rs) & (times <= re)
        times = times[mask]
        rms   = rms[mask]
        log.info("Restricted to %.1fs–%.1fs range.", rs, re)

    peaks        = find_audio_peaks(times, rms, top_n=top_n, min_gap_sec=min_gap)
    log.info("Audio peaks: %s", [round(p["peak_time"]) for p in peaks])

    # Stage 2 — Claude AI frame analysis
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    use_ai  = bool(api_key)
    if not use_ai:
        log.warning("ANTHROPIC_API_KEY not set — skipping Claude scene analysis.")

    clips = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, peak in enumerate(peaks):
            start, end = compute_clip_window(
                peak["peak_time"], video_duration, clip_duration
            )
            clip = {
                "clip_index":  i,
                "start":       start,
                "end":         end,
                "duration":    round(end - start, 3),
                "peak_time":   peak["peak_time"],
                "score":       peak["audio_score"],
                "video_path":  video_path,
                "ai_score":    5,
                "ai_description": "",
            }

            if use_ai:
                frame_path = str(Path(tmp) / f"frame_{i}.jpg")
                if extract_frame(video_path, peak["peak_time"], frame_path):
                    log.info("Analysing frame at %.1fs with Claude…", peak["peak_time"])
                    ai = analyse_frame_with_claude(frame_path, api_key)
                    clip.update(ai)
                    log.info("  AI score: %d/10 — %s",
                             clip["ai_score"], clip["ai_description"][:80])
                else:
                    log.warning("Frame extraction failed for peak at %.1fs", peak["peak_time"])

            clips.append(clip)

    # Re-rank by combined score (audio 40% + AI 60%)
    if use_ai:
        for c in clips:
            c["combined_score"] = round(
                0.4 * c["score"] + 0.6 * (c["ai_score"] / 10.0), 4
            )
        clips.sort(key=lambda x: -x["combined_score"])
        log.info("Re-ranked clips by combined audio+AI score.")
    else:
        for c in clips:
            c["combined_score"] = c["score"]

    return {
        "clips":          clips,
        "video_duration": video_duration,
        "peaks_found":    len(peaks),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Detect viral moments using audio peaks + Claude AI analysis."
    )
    parser.add_argument("video_path")
    parser.add_argument("--top",           type=int,   default=3)
    parser.add_argument("--min-gap",       type=float, default=60.0)
    parser.add_argument("--clip-duration", type=float, default=45.0)
    parser.add_argument("--start",         type=float, default=None, help="Range start in seconds")
    parser.add_argument("--end",           type=float, default=None, help="Range end in seconds")
    args = parser.parse_args()

    result = detect(
        args.video_path,
        top_n=args.top,
        min_gap=args.min_gap,
        clip_duration=args.clip_duration,
        range_start=args.start,
        range_end=args.end,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
