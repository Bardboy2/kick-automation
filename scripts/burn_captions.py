#!/usr/bin/env python3
"""
burn_captions.py — Karaoke-style caption overlay (PIL + FFmpeg)
Takes transcribed clips JSON and generates THREE captioned video versions:
  1. Landscape 16:9 (source dimensions) with captions
  2. Portrait 9:16 smart crop (1080×1920) with captions
  3. Portrait 9:16 black background (1080×1920) with captions

Caption style matches Opus Clips: bold white text, yellow word highlight,
black outline, Arial Black, lower-third position.

FFmpeg's libass is not required — captions are composited via PIL PNGs +
the overlay filter with 'enable' time expressions.

Usage:
    python transcribe.py - | python burn_captions.py -
    python burn_captions.py transcribed.json
"""

import sys
import json
import logging
import argparse
import subprocess
import shutil
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [captions] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

FFMPEG     = "/usr/local/bin/ffmpeg"
FFPROBE    = "/usr/local/bin/ffprobe"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "clips"

# ── Caption style ──────────────────────────────────────────────────────────────
WHITE  = (255, 255, 255, 255)
YELLOW = (255, 230, 0,   255)
SHADOW = (0,   0,   0,   230)
OUTLINE_PX = 5

# Chunking
MAX_CHUNK_WORDS = 3
MAX_CHUNK_SEC   = 2.5
PAUSE_SPLIT_SEC = 0.4


# ── Font loading ───────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/Library/Fonts/Arial Black.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Word chunking ──────────────────────────────────────────────────────────────

def chunk_words(words: list) -> list[list]:
    """Group words into display chunks (≤3 words, ≤2.5s, split on pauses)."""
    if not words:
        return []
    chunks, current = [], []
    for w in words:
        if not w.get("word", "").strip():
            continue
        if current:
            gap = w["start"] - current[-1]["end"]
            dur = w["end"] - current[0]["start"]
            if gap > PAUSE_SPLIT_SEC or len(current) >= MAX_CHUNK_WORDS or dur > MAX_CHUNK_SEC:
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


# ── Frame generation ───────────────────────────────────────────────────────────

def _draw_outlined_text(draw, xy, text, font, fill, outline, ow):
    x, y = xy
    for dx in range(-ow, ow + 1):
        for dy in range(-ow, ow + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text(xy, text, font=font, fill=fill)


def make_caption_frame(
    chunk: list,
    highlight_idx: int,
    canvas_w: int,
    canvas_h: int,
    font_size: int = 88,
) -> Image.Image:
    """
    Transparent RGBA frame, canvas_w × canvas_h.
    Draws all chunk words at bottom; highlighted word in yellow, rest in white.
    Recurses with smaller font if text overflows width.
    """
    img  = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)

    words  = [w["word"] for w in chunk]
    colors = [YELLOW if i == highlight_idx else WHITE for i in range(len(words))]

    # Measure each word + space
    space_w = int(draw.textlength(" ", font=font))
    metrics = []
    for word in words:
        bbox = draw.textbbox((0, 0), word, font=font)
        metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    total_w = sum(w for w, _ in metrics) + space_w * max(0, len(words) - 1)
    max_h   = max((h for _, h in metrics), default=0)

    # Recurse with smaller font if too wide
    if total_w > canvas_w * 0.88 and font_size > 24:
        scale = (canvas_w * 0.84) / total_w
        return make_caption_frame(chunk, highlight_idx, canvas_w, canvas_h,
                                  int(font_size * scale))

    # Position: lower third, small margin from bottom
    margin_bottom = int(canvas_h * 0.07)
    y = canvas_h - max_h - margin_bottom - OUTLINE_PX * 2
    x = (canvas_w - total_w) // 2

    for word, color, (ww, _) in zip(words, colors, metrics):
        _draw_outlined_text(draw, (x, y), word, font, color, SHADOW, OUTLINE_PX)
        x += ww + space_w

    return img


# ── FFmpeg overlay builder ─────────────────────────────────────────────────────

def _has_audio(clip_path: str) -> bool:
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams", clip_path],
        capture_output=True, text=True,
    )
    return '"codec_type": "audio"' in r.stdout


def burn_overlay(
    clip_path: str,
    frames: list,           # [(png_path, t_start, t_end), ...]
    output_path: Path,
    pre_filter: str = None, # e.g. crop/scale for portrait versions
) -> bool:
    """
    Composite caption PNGs onto video via FFmpeg overlay filter chain.
    pre_filter is applied to [0:v] before overlaying (for portrait transforms).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not frames:
        # No captions — just apply pre_filter if any
        if pre_filter:
            r = subprocess.run([
                FFMPEG, "-y", "-i", clip_path,
                "-vf", pre_filter,
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(output_path),
            ], capture_output=True, text=True)
            if r.returncode != 0:
                log.error("FFmpeg (no-captions) failed:\n%s", r.stderr[-2000:])
                return False
        else:
            shutil.copy2(clip_path, str(output_path))
        return True

    # Build filter_complex
    fc_parts = []
    cur = "[0:v]"
    if pre_filter:
        fc_parts.append(f"[0:v]{pre_filter}[base]")
        cur = "[base]"

    for i, (_, t0, t1) in enumerate(frames):
        out = f"[v{i}]"
        fc_parts.append(
            f"{cur}[{i+1}:v]overlay=x=0:y=0:enable='between(t,{t0:.3f},{t1:.3f})'{out}"
        )
        cur = out

    filter_complex = ";".join(fc_parts)

    # Build full command
    cmd = [FFMPEG, "-y", "-i", clip_path]
    for png_path, _, _ in frames:
        cmd += ["-i", png_path]
    cmd += ["-filter_complex", filter_complex, "-map", cur]
    if _has_audio(clip_path):
        cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-movflags", "+faststart", str(output_path)]

    log.info("FFmpeg overlay → %s (%d frames)", output_path.name, len(frames))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("FFmpeg overlay failed:\n%s", r.stderr[-3000:])
        return False

    log.info("Done: %s (%.1f MB)", output_path.name, output_path.stat().st_size / 1e6)
    return True


# ── Per-clip burn orchestration ────────────────────────────────────────────────

PORTRAIT_CROP_FILTER = (
    "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
    "scale=1080:1920:flags=lanczos,"
    "setsar=1"
)
PORTRAIT_BG_FILTER = (
    "scale=1080:-2,"
    "pad=1080:1920:0:(oh-ih)/2:black,"
    "setsar=1"
)


def burn_clip(clip: dict, out_dir: Path, tmp: Path) -> dict:
    """
    Generate landscape + 2 portrait captioned versions.
    Returns dict of {landscape_path, portrait_crop_path, portrait_blackbg_path}.
    """
    clip_path = clip["clip_path"]
    base      = Path(clip_path).stem          # e.g. "abc123_clip01"
    src_id    = Path(clip_path).parent.name   # e.g. "abc123"
    clip_dir  = out_dir / src_id
    clip_dir.mkdir(parents=True, exist_ok=True)

    words  = json.loads(clip.get("words_json") or "[]")
    chunks = chunk_words(words)

    ls_frames = []   # (path, t_start, t_end) — landscape 1920×1080
    pt_frames = []   # portrait 1080×1920

    for ci, chunk in enumerate(chunks):
        for wi, word in enumerate(chunk):
            t0 = word["start"]
            t1 = max(word["end"], t0 + 0.05)

            ls_img = make_caption_frame(chunk, wi, 1920, 1080, font_size=88)
            ls_png = str(tmp / f"ls_{ci:03d}_{wi:02d}.png")
            ls_img.save(ls_png, "PNG")
            ls_frames.append((ls_png, t0, t1))

            pt_img = make_caption_frame(chunk, wi, 1080, 1920, font_size=80)
            pt_png = str(tmp / f"pt_{ci:03d}_{wi:02d}.png")
            pt_img.save(pt_png, "PNG")
            pt_frames.append((pt_png, t0, t1))

    log.info("Clip %s: %d caption frames", base, len(ls_frames))

    ls_out = clip_dir / f"{base}_landscape.mp4"
    cr_out = clip_dir / f"{base}_portrait_crop.mp4"
    bg_out = clip_dir / f"{base}_portrait_blackbg.mp4"

    burn_overlay(clip_path, ls_frames, ls_out, pre_filter=None)
    burn_overlay(clip_path, pt_frames, cr_out, pre_filter=PORTRAIT_CROP_FILTER)
    burn_overlay(clip_path, pt_frames, bg_out, pre_filter=PORTRAIT_BG_FILTER)

    return {
        "landscape_path":       str(ls_out),
        "portrait_crop_path":   str(cr_out),
        "portrait_blackbg_path": str(bg_out),
        "captioned_path":       str(ls_out),  # keep captioned_path for pipeline compat
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Burn karaoke-style captions onto clips (3 formats each)."
    )
    parser.add_argument("cuts_json", help="Path to transcribed cuts JSON or '-' for stdin")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    if args.cuts_json == "-":
        data = json.load(sys.stdin)
    else:
        data = json.loads(Path(args.cuts_json).read_text())

    out_dir = Path(args.output_dir)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        clips_out = []
        for clip in data.get("clips", []):
            try:
                paths = burn_clip(clip, out_dir, tmp)
                clip.update(paths)
            except Exception as e:
                log.error("Clip %d burn failed: %s", clip.get("clip_index", 0), e)
            clips_out.append(clip)

    data["clips"] = clips_out
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
