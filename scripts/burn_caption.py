#!/usr/bin/env python3
"""
burn_caption.py — Create a 9:16 short with caption box.

Produces a 1080x1920 (9:16) video with:
  - Black background
  - Original landscape clip centered vertically
  - White rounded-rectangle caption box at the top (above the video)

This single output is used for all platforms: YouTube Shorts, TikTok, Instagram Reels, Facebook Reels.

Usage:
    python scripts/burn_caption.py --video clip.mp4 --text "Caption here" --output out.mp4
"""

import argparse
import subprocess
import tempfile
import textwrap
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Pillow is required: pip install Pillow")

try:
    from pilmoji import Pilmoji
    _PILMOJI_AVAILABLE = True
except ImportError:
    _PILMOJI_AVAILABLE = False

OUTPUT_W = 1080
OUTPUT_H = 1920


def _load_font(size: int):
    candidates = [
        ("/System/Library/Fonts/Supplemental/Impact.ttf", 0),
        ("/Library/Fonts/Arial Bold.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/System/Library/Fonts/Helvetica.ttc", 1),
        ("/System/Library/Fonts/Arial.ttf", 0),
        ("/System/Library/Fonts/Helvetica.ttc", 0),
    ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_rounded_rect(draw, xy, radius, fill):
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill)
    except AttributeError:
        x0, y0, x1, y1 = xy
        r = radius
        draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
        draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
        draw.ellipse([x0, y0, x0 + r * 2, y0 + r * 2], fill=fill)
        draw.ellipse([x1 - r * 2, y0, x1, y0 + r * 2], fill=fill)
        draw.ellipse([x0, y1 - r * 2, x0 + r * 2, y1], fill=fill)
        draw.ellipse([x1 - r * 2, y1 - r * 2, x1, y1], fill=fill)


def make_caption_png(text: str, video_y: int) -> tuple[str, int]:
    """
    Render the caption as a full-frame transparent PNG (OUTPUT_W x video_y).
    The white box is drawn within the black top area above the video.
    Returns (png_path, caption_height).
    """
    font_size   = 64
    pad_x       = 48
    pad_y       = 30
    side_margin = int(OUTPUT_W * 0.06)
    max_text_w  = OUTPUT_W - side_margin * 2 - pad_x * 2

    font = _load_font(font_size)

    dummy = Image.new("RGBA", (1, 1))
    d     = ImageDraw.Draw(dummy)
    sample_bbox = d.textbbox((0, 0), "W" * 10, font=font)
    char_w      = (sample_bbox[2] - sample_bbox[0]) / 10
    chars_per_line = max(8, int(max_text_w / char_w))

    lines = textwrap.wrap(text.strip(), width=chars_per_line) or [text.strip()]

    line_bboxes  = [d.textbbox((0, 0), ln, font=font) for ln in lines]
    line_widths  = [b[2] - b[0] for b in line_bboxes]
    line_heights = [b[3] - b[1] for b in line_bboxes]
    line_tops    = [b[1]        for b in line_bboxes]

    line_h   = max(line_heights) if line_heights else font_size
    line_gap = int(line_h * 0.25)
    text_w   = max(line_widths)  if line_widths  else 100
    text_h   = sum(line_heights) + line_gap * max(0, len(lines) - 1)

    box_w = min(text_w + pad_x * 2, OUTPUT_W - side_margin * 2)
    box_h = text_h + pad_y * 2

    # Centre the box vertically in the black top area
    margin_top = max(24, (video_y - box_h) // 2)

    # Full-frame transparent canvas
    img  = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    box_x0 = (OUTPUT_W - box_w) // 2
    box_y0 = margin_top

    # Drop shadow
    _draw_rounded_rect(draw,
        [box_x0 + 3, box_y0 + 3, box_x0 + box_w + 3, box_y0 + box_h + 3],
        radius=22, fill=(0, 0, 0, 65))

    # White box
    _draw_rounded_rect(draw,
        [box_x0, box_y0, box_x0 + box_w, box_y0 + box_h],
        radius=22, fill=(255, 255, 255, 250))

    # Text — centered in box
    y = box_y0 + pad_y
    if _PILMOJI_AVAILABLE:
        with Pilmoji(img) as pilmoji:
            for i, line in enumerate(lines):
                lw = line_widths[i]
                x  = box_x0 + (box_w - lw) // 2 - line_bboxes[i][0]
                pilmoji.text((x, y - line_tops[i]), line, font=font, fill=(12, 12, 12, 255))
                y += line_heights[i] + line_gap
    else:
        for i, line in enumerate(lines):
            lw = line_widths[i]
            x  = box_x0 + (box_w - lw) // 2 - line_bboxes[i][0]
            draw.text((x, y - line_tops[i]), line, font=font, fill=(12, 12, 12, 255))
            y += line_heights[i] + line_gap

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name, box_y0 + box_h


def get_video_dims(video_path: str) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", video_path],
        capture_output=True, text=True,
    )
    parts = result.stdout.strip().split("x")
    try:
        return int(parts[0]), int(parts[1])
    except Exception:
        return 1280, 720


def burn_caption(video_path: str, caption_text: str, output_path: str) -> None:
    """
    Build a 9:16 short: black bg + landscape clip centered + caption box at top.
    """
    video_w, video_h = get_video_dims(video_path)

    # Calculate where the video will land after pad so caption sits above it
    scaled_h = int(OUTPUT_W * video_h / video_w)
    video_y  = (OUTPUT_H - scaled_h) // 2

    png_path, _ = make_caption_png(caption_text, video_y)

    # pad filter: scale to OUTPUT_W wide, then add black bars to fill OUTPUT_H
    filter_complex = (
        f"[0:v]scale={OUTPUT_W}:-2,pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black[padded];"
        f"[padded][1:v]overlay=0:0[v]"
    )

    def _run(extra_enc: list) -> subprocess.CompletedProcess:
        return subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", png_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            *extra_enc,
            "-c:a", "copy",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ], capture_output=True, text=True, timeout=600)

    try:
        # Try hardware encoding first (much faster on Mac)
        result = _run(["-c:v", "h264_videotoolbox", "-b:v", "6M"])
        if result.returncode != 0:
            result = _run(["-c:v", "libx264", "-crf", "23", "-preset", "ultrafast"])
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-800:]}")
    finally:
        Path(png_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create 9:16 short with caption box.")
    parser.add_argument("--video",  required=True, help="Input landscape video")
    parser.add_argument("--text",   required=True, help="Caption text")
    parser.add_argument("--output", required=True, help="Output 9:16 video path")
    args = parser.parse_args()
    burn_caption(args.video, args.text, args.output)
    print(f"Done: {args.output}")
