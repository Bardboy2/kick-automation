#!/usr/bin/env python3
"""
cut_clips.py — FFmpeg Clip Cutter + Watermark
Takes peak timestamps from detect_peaks.py and cuts short clips from the source VOD.
Burns the Kick logo + username watermark into the bottom-right corner of every clip.

Usage:
    python cut_clips.py <peaks_json_path_or_stdin> [--output-dir /data/clips]

    # Or pipe directly from detect_peaks.py:
    python detect_peaks.py source.mp4 | python cut_clips.py -

Outputs JSON with paths to the cut clips.
"""

import sys
import json
import argparse
import logging
import subprocess
import os
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cut] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "clips"
FFMPEG     = "/usr/local/bin/ffmpeg"

BASE_DIR    = Path(__file__).parent.parent
LOGO_PATH   = BASE_DIR / "config" / "kick_logo.png"

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / "config" / ".env", override=False)
except ImportError:
    pass

KICK_USERNAME = os.environ.get("KICK_CHANNEL_SLUG", "owtyofficial")
WATERMARK_COLOR = (83, 252, 24, 255)   # Kick green


# ─── Watermark builder ────────────────────────────────────────────────────────

def _get_font(size: int):
    for path in [
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial Black.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
    ]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def build_watermark(video_width: int, video_height: int, out_path: str) -> None:
    """
    Build a watermark PNG: Kick logo (small) + 'kick.com/USERNAME' text
    positioned in the bottom-right corner with padding.
    Saved as a transparent RGBA PNG.
    """
    logo_size   = 60    # logo icon size in pixels
    font_size   = 32
    padding     = 24
    gap         = 10    # gap between logo and text

    font = _get_font(font_size)

    # Measure text
    tmp_img  = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    text     = f"kick.com/{KICK_USERNAME}"
    bbox     = tmp_draw.textbbox((0, 0), text, font=font)
    tw       = bbox[2] - bbox[0]
    th       = bbox[3] - bbox[1]

    # Watermark canvas size
    wm_w = logo_size + gap + tw + padding * 2
    wm_h = max(logo_size, th) + padding * 2

    wm = Image.new("RGBA", (wm_w, wm_h), (0, 0, 0, 0))

    # Draw logo if it exists
    logo_x = padding
    logo_y = (wm_h - logo_size) // 2
    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo = logo.resize((logo_size, logo_size), Image.LANCZOS)
            wm.paste(logo, (logo_x, logo_y), logo)
        except Exception as e:
            log.warning("Could not load logo: %s", e)

    # Draw text
    draw  = ImageDraw.Draw(wm)
    tx    = logo_x + logo_size + gap
    ty    = (wm_h - th) // 2 - bbox[1]

    # Outline
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx == 0 and dy == 0:
                continue
            draw.text((tx + dx, ty + dy), text, font=font, fill=(0, 0, 0, 200))
    # Main text in Kick green
    draw.text((tx, ty), text, font=font, fill=WATERMARK_COLOR)

    wm.save(out_path, "PNG")


def get_video_size(video_path: str) -> tuple[int, int]:
    """Return (width, height) of the video."""
    from pathlib import Path as P
    cmd = ["/usr/local/bin/ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_streams", video_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return 1080, 1920   # fallback to expected vertical size
    data = json.loads(r.stdout)
    vs = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not vs:
        return 1080, 1920
    return int(vs["width"]), int(vs["height"])


# ─── FFmpeg clip cutting ───────────────────────────────────────────────────────

def cut_clip(
    video_path: str,
    start: float,
    end: float,
    output_path: Path,
    vertical: bool = True,
    watermark: bool = True,
) -> bool:
    """
    Cut a clip from video_path between start and end seconds.
    Crops to 9:16 (1080x1920) if vertical=True, then burns Kick watermark
    into bottom-centre (unless watermark=False).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Step 1: Cut + crop to 9:16
        raw_cut = tmp_path / "raw_cut.mp4"

        if vertical:
            vf = (
                "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                "scale=1080:1920:flags=lanczos,"
                "setsar=1"
            )
            cmd = [
                FFMPEG, "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(duration),
                "-vf", vf,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(raw_cut),
            ]
        else:
            cmd = [
                FFMPEG, "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(duration),
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(raw_cut),
            ]

        log.info("Cutting clip: %.1fs → %.1fs (%.1fs)", start, end, duration)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log.error("FFmpeg cut failed:\n%s", r.stderr[-2000:])
            return False

        if not watermark:
            # No watermark — just move the raw cut to the output path
            shutil.copy2(str(raw_cut), str(output_path))
        else:
            # Step 2: Build watermark PNG sized for the clip
            w, h = get_video_size(str(raw_cut))
            wm_path = str(tmp_path / "watermark.png")
            build_watermark(w, h, wm_path)

            # Step 3: Overlay watermark bottom-centre with 20px margin
            wm_img    = Image.open(wm_path)
            wm_w, wm_h_px = wm_img.size
            x_pos = (w - wm_w) // 2
            y_pos = h - wm_h_px - 20

            # Check if source has audio
            probe = subprocess.run(
                ["/usr/local/bin/ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", str(raw_cut)],
                capture_output=True, text=True
            )
            has_audio = '"codec_type": "audio"' in probe.stdout

            filter_str = f"[0:v][1:v]overlay={x_pos}:{y_pos}[v]"
            overlay_cmd = [
                FFMPEG, "-y",
                "-i", str(raw_cut),   # input 0: clip
                "-i", wm_path,        # input 1: watermark PNG
                "-filter_complex", filter_str,
                "-map", "[v]",
            ]
            if has_audio:
                overlay_cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
            overlay_cmd += [
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-movflags", "+faststart",
                str(output_path),
            ]

            log.info("Burning watermark → %s", output_path.name)
            r2 = subprocess.run(overlay_cmd, capture_output=True, text=True)
            if r2.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
                log.warning("Watermark burn failed — copying clip without watermark.")
                shutil.copy2(str(raw_cut), str(output_path))

    size_mb = output_path.stat().st_size / 1e6
    log.info("Clip ready: %s (%.1f MB)", output_path.name, size_mb)
    return True


# ─── Main ─────────────────────────────────────────────────────────────────────

def cut_all(peaks_data: dict, output_dir: Path, vertical: bool = True, watermark: bool = True) -> dict:
    clips_in  = peaks_data.get("clips", [])
    clips_out = []

    for clip in clips_in:
        video_path = clip["video_path"]
        idx        = clip["clip_index"]
        start      = clip["start"]
        end        = clip["end"]
        score      = clip["score"]

        p = Path(video_path)
        source_id = p.parent.name if p.stem == "source" else p.stem
        out_name  = f"{source_id}_clip{idx:02d}.mp4"
        out_path  = output_dir / source_id / out_name

        success = cut_clip(video_path, start, end, out_path, vertical=vertical, watermark=watermark)

        if success:
            clips_out.append({
                "clip_index":  idx,
                "clip_path":   str(out_path),
                "start":       start,
                "end":         end,
                "duration":    clip["duration"],
                "peak_time":   clip["peak_time"],
                "score":       score,
                "video_path":  video_path,
            })
        else:
            log.warning("Skipping clip %d due to FFmpeg error.", idx)

    return {"clips": clips_out, "total": len(clips_out)}


def main():
    parser = argparse.ArgumentParser(description="Cut clips from a VOD using peak timestamps.")
    parser.add_argument("peaks_json",  help="Path to peaks JSON file, or '-' to read from stdin")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--landscape",     action="store_true")
    parser.add_argument("--no-watermark",  action="store_true", help="Skip watermark overlay")
    args = parser.parse_args()

    if args.peaks_json == "-":
        peaks_data = json.load(sys.stdin)
    else:
        peaks_data = json.loads(Path(args.peaks_json).read_text())

    out_dir  = Path(args.output_dir)
    vertical  = not args.landscape
    watermark = not args.no_watermark
    result    = cut_all(peaks_data, out_dir, vertical=vertical, watermark=watermark)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
