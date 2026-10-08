#!/usr/bin/env python3
"""
run_custom_range.py — Run pipeline on a specific time range of a VOD.
Usage: python scripts/run_custom_range.py <vod_id> <start_mm:ss> <end_mm:ss> [--clips N]
"""
import sys
import json
import subprocess
import argparse
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = Path(__file__).parent

def parse_time(t: str) -> float:
    parts = t.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("vod_id")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--clips", type=int, default=6)
    args = parser.parse_args()

    vod_path = BASE_DIR / "data" / "downloads" / args.vod_id / "source.mp4"
    if not vod_path.exists():
        print(f"VOD not found: {vod_path}")
        sys.exit(1)

    start = parse_time(args.start)
    end   = parse_time(args.end)

    print(f"Detecting {args.clips} viral moments between {args.start} and {args.end}...")

    # Stage 1: detect peaks within the time range
    detect_cmd = [
        sys.executable, str(SCRIPTS_DIR / "detect_peaks.py"),
        str(vod_path),
        "--top", str(args.clips),
        "--min-gap", "60",
        "--clip-duration", "45",
        "--start", str(start),
        "--end",   str(end),
    ]
    r1 = subprocess.run(detect_cmd, capture_output=True, text=True)
    if r1.returncode != 0:
        print(f"detect_peaks failed:\n{r1.stderr[-1000:]}")
        sys.exit(1)

    peaks = json.loads(r1.stdout)
    print(f"Found {peaks.get('peaks_found', 0)} peaks.")

    # Stage 2: cut clips
    r2 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "cut_clips.py"), "-"],
        input=json.dumps(peaks), capture_output=True, text=True,
    )
    if r2.returncode != 0:
        print(f"cut_clips failed:\n{r2.stderr[-1000:]}")
        sys.exit(1)
    cuts = json.loads(r2.stdout)
    print(f"Cut {cuts.get('total', 0)} clips.")

    # Stage 3: generate metadata
    r3 = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "generate_metadata.py"), "-"],
        input=json.dumps(cuts), capture_output=True, text=True,
    )
    enriched = json.loads(r3.stdout) if r3.returncode == 0 else cuts

    # Stage 4: save to DB
    sys.path.insert(0, str(SCRIPTS_DIR))
    import db
    for clip in enriched.get("clips", []):
        clip.setdefault("captioned_path", clip["clip_path"])
        row_id = db.insert_clip(clip, args.vod_id)
        print(f"Clip saved to DB (id={row_id})")

    print("Done! Clips will be sent to Telegram shortly.")

if __name__ == "__main__":
    main()
