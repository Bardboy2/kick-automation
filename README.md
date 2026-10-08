# Kick Viral Clip Automation

End-to-end pipeline that monitors your Kick channel, detects viral moments using
**Claude AI + audio analysis**, captions them with **Whisper word-by-word captions**
(TikTok style), and lets you review clips in a **local web dashboard** before
auto-posting to **YouTube Shorts, TikTok, and Instagram Reels**.

---

## How it works

```
Kick VOD  ──►  monitor.py        Poll your channel every 5 min
               ↓
               detect_peaks.py   Audio RMS peaks → Claude AI scene analysis
               ↓
               cut_clips.py      FFmpeg: cut + crop to 9:16 (vertical)
               ↓
               caption.py        Whisper word-level transcription → viral ASS captions
               ↓
               generate_metadata.py   Claude writes titles / descriptions / hashtags
               ↓
               db.py             Save to SQLite (data/db.sqlite)
               ↓
          dashboard/app.py  ──►  http://localhost:5050
                                 Preview · Edit metadata · Approve → POST
                                            ↓
                                 YouTube Shorts  TikTok  Instagram Reels
```

---

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.11+ | `brew install python` |
| FFmpeg 6+ | `brew install ffmpeg` |
| yt-dlp | `pip install yt-dlp` |

---

## Step 1 — Install Python dependencies

```bash
cd kick_automation
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Step 2 — Configure environment variables

```bash
cp config/.env.example config/.env
open config/.env   # fill in values
```

Minimum required to get started:
```
KICK_CHANNEL_SLUG=yourchannelname
ANTHROPIC_API_KEY=sk-ant-...
```

Add YouTube / TikTok / Instagram credentials later (see Steps 4–6 below).

---

## Step 3 — Test the pipeline on a single VOD

```bash
source .venv/bin/activate

# Download any VOD manually first
yt-dlp -o data/downloads/test/source.mp4 "https://kick.com/video/YOUR_VOD_ID"

# Run the pipeline
python scripts/pipeline.py --vod-id test --vod-path data/downloads/test/source.mp4
```

When it finishes, clips appear in the dashboard:

```bash
python dashboard/app.py
# → open http://localhost:5050
```

---

## Step 4 — Set up YouTube Shorts

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → Enable **YouTube Data API v3**
3. **Credentials** → Create **OAuth 2.0 Client ID** (Desktop app) → Download JSON
4. Save it as `config/client_secret.json`
5. Run the one-time auth helper:
   ```bash
   python scripts/auth_youtube.py
   ```
   A browser opens — log in and approve. Tokens are saved to `config/.env` automatically.

---

## Step 5 — Set up TikTok

1. Go to [developers.tiktok.com](https://developers.tiktok.com) → Create app
2. Add the **Content Posting API** product
3. Add `http://localhost:8080/callback` as an allowed redirect URI
4. Copy **Client Key** and **Client Secret** to `config/.env`
5. Run the auth helper:
   ```bash
   python scripts/auth_tiktok.py
   ```

> Note: TikTok access tokens expire in 24h. Re-run `auth_tiktok.py` to refresh.

---

## Step 6 — Set up Instagram Reels

1. Go to [developers.facebook.com](https://developers.facebook.com) → Create app (Business type)
2. Add **Instagram Graph API** product → connect your Instagram Business/Creator account
3. Generate a **long-lived user token** (valid 60 days)
4. Set in `config/.env`:
   ```
   INSTAGRAM_ACCESS_TOKEN=EAAxxxxx
   INSTAGRAM_USER_ID=12345678901
   CLIP_PUBLIC_BASE_URL=https://your-bucket.s3.amazonaws.com/clips
   ```

> Instagram requires a **publicly accessible** video URL.
> Upload approved clips to an S3 bucket and set `CLIP_PUBLIC_BASE_URL` to its base URL.

---

## Step 7 — Start the monitor (automated mode)

```bash
source .venv/bin/activate

# Terminal 1 — monitor polls Kick every 5 min and runs pipeline automatically
python scripts/monitor.py

# Terminal 2 — keep the dashboard open to review clips
python dashboard/app.py
```

---

## Using the Dashboard

Open **http://localhost:5050** in your browser.

- **Left panel** — list of pending clips (newest first), each showing AI score + audio energy
- **Right panel** — selected clip:
  - `<video>` preview (plays directly in browser)
  - Claude's description of what's happening in the clip
  - Editable title / description / hashtags for each platform
  - **Approve & Post** — saves edits then posts to all three platforms immediately
  - **Reject** — discards the clip

History tab shows all posted / rejected clips with direct links to live posts.

---

## Project structure

```
kick_automation/
├── scripts/
│   ├── monitor.py            Polls Kick for new VODs; triggers pipeline
│   ├── detect_peaks.py       Audio RMS + Claude AI scene analysis
│   ├── cut_clips.py          FFmpeg clip cutting, 9:16 crop
│   ├── caption.py            Whisper word-by-word viral captions (ASS + burn)
│   ├── generate_metadata.py  Claude titles / descriptions / hashtags
│   ├── pipeline.py           Orchestrates all stages; writes to SQLite
│   ├── publish.py            Posts to YouTube / TikTok / Instagram
│   ├── db.py                 SQLite helper (shared by pipeline + dashboard)
│   ├── auth_youtube.py       One-time YouTube OAuth setup
│   └── auth_tiktok.py        One-time TikTok OAuth setup
├── dashboard/
│   ├── app.py                Flask web review dashboard
│   └── templates/
│       └── index.html        Dashboard UI
├── config/
│   └── .env.example          Environment variable template
├── data/                     Created automatically at runtime
│   ├── downloads/            Raw VOD files
│   ├── clips/                9:16 cut clips
│   ├── captioned/            Captioned clips (final)
│   ├── state/                Monitor state (seen VOD IDs)
│   ├── logs/                 Pipeline run summaries
│   └── db.sqlite             Clip database
└── requirements.txt
```

---

## Tuning

| Setting | .env key | Default | Effect |
|---------|----------|---------|--------|
| Max clips per VOD | `CLIPS_PER_VOD` | 3 | How many clips to extract |
| Min gap between clips | `MIN_GAP_SEC` | 60s | Avoids overlapping clips |
| Target clip length | `CLIP_DURATION_SEC` | 45s | 30–60s target |
| Poll frequency | `POLL_INTERVAL` | 300s | How often to check Kick |
| Whisper model | `WHISPER_MODEL` | small | `tiny/base/small/medium/large` |
| Dashboard port | `DASHBOARD_PORT` | 5050 | Change if port is in use |

---

## Troubleshooting

**No VODs found** — Check `KICK_CHANNEL_SLUG` matches your channel URL exactly (e.g. `kick.com/yourname` → slug is `yourname`).

**Whisper is slow** — Switch to `WHISPER_MODEL=tiny` or `base` in `.env` for faster (less accurate) captions. Or run on a machine with a GPU.

**Claude scene analysis skipped** — Check `ANTHROPIC_API_KEY` is set correctly. The pipeline still works without it; it just uses audio-only detection.

**YouTube 403** — Your refresh token may have expired. Re-run `python scripts/auth_youtube.py`.

**TikTok upload fails** — Access token expires after 24h. Re-run `python scripts/auth_tiktok.py`.

**Instagram upload fails** — Ensure `CLIP_PUBLIC_BASE_URL` points to a public URL (not localhost). The clip file must be accessible from the internet.

**Caption burn fails** — Make sure FFmpeg is installed: `brew install ffmpeg`. The `.ass` subtitle file needs libass support in FFmpeg — Homebrew's build includes it.
