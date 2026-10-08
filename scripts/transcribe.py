#!/usr/bin/env python3
"""
transcribe.py — Word-level audio transcription via faster-whisper
Takes cut clips JSON (from cut_clips.py), transcribes each clip with
word-level timestamps, and outputs the same JSON with 'words_json'
and 'transcript' added per clip.

Usage:
    python cut_clips.py ... | python transcribe.py -
    python transcribe.py cuts.json
"""

import sys
import json
import logging
import argparse
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [transcribe] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

WHISPER_MODEL = "base"


def transcribe_clip(clip_path: str, model=None) -> dict:
    """Transcribe a clip and return word-level timing data."""
    if model is None:
        try:
            from faster_whisper import WhisperModel
            log.info("Loading Whisper model '%s'...", WHISPER_MODEL)
            model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        except ImportError:
            log.warning("faster-whisper not installed — skipping transcription")
            return {"words": [], "language": "en", "duration": 0.0}

    log.info("Transcribing: %s", Path(clip_path).name)
    segments, info = model.transcribe(
        clip_path,
        word_timestamps=True,
        vad_filter=True,
        language="en",
    )

    words = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                word = w.word.strip()
                if word:
                    words.append({
                        "word":        word,
                        "start":       round(w.start, 3),
                        "end":         round(w.end, 3),
                        "probability": round(w.probability, 3),
                    })

    return {
        "words":    words,
        "language": info.language,
        "duration": round(info.duration, 3),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe clips with word-level timestamps."
    )
    parser.add_argument("cuts_json", help="Path to cuts JSON or '-' for stdin")
    parser.add_argument("--model", default=WHISPER_MODEL,
                        help="Whisper model size (tiny/base/small/medium)")
    args = parser.parse_args()

    if args.cuts_json == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(Path(args.cuts_json).read_text())

    # Load model once for all clips
    model = None
    try:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model '%s'...", args.model)
        model = WhisperModel(args.model, device="cpu", compute_type="int8")
    except ImportError:
        log.warning("faster-whisper not installed — words_json will be empty")

    clips_out = []
    for clip in data.get("clips", []):
        result = transcribe_clip(clip["clip_path"], model=model)
        clip["words_json"] = json.dumps(result["words"])
        clip["transcript"] = " ".join(w["word"] for w in result["words"])
        log.info("Clip %d: %d words | lang=%s",
                 clip.get("clip_index", 0), len(result["words"]), result["language"])
        clips_out.append(clip)

    data["clips"] = clips_out
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
