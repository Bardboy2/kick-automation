# Clipfam — Product Briefing

## What is Clipfam?
Clipfam is a multi-user SaaS Telegram bot product built on top of the `kick_automation` codebase.
Buyers (streamers, content creators) paste a Kick or Twitch VOD link into the bot, the system
automatically downloads it, detects the best moments, cuts clips, generates social media metadata,
and delivers the clips back to the buyer on Telegram. The buyer then posts manually to their own
social media. No social media account connection required.

---

## What this folder is
This folder (`clipfam`) is a copy of the original `kick_automation` personal automation.
The original folder (`kick_automation`) must NEVER be touched — it is the owner's personal automation
for their own Kick channel (owtyofficial). Clipfam is the separate product built for buyers.

---

## User Journey
1. Owner invites a buyer (adds their Telegram ID to the whitelist)
2. Buyer messages the bot → `/start` → welcome message + instructions
3. Buyer pastes a Kick or Twitch VOD link
4. Bot asks: "What is the creator's handle?" (always ask, never assume)
5. Bot downloads the VOD, runs the pipeline, cuts clips
6. Bot sends clips back to that buyer only (isolated — no buyer sees another buyer's clips)
7. Buyer downloads clips from Telegram and posts to their social media manually

---

## Access Control
- **Invite-only.** The owner controls a whitelist of approved Telegram chat IDs.
- Any user NOT on the whitelist who messages the bot gets a polite rejection.
- Whitelist is stored in `config/.env` as `ALLOWED_CHAT_IDS=id1,id2,id3`
- Owner can add/remove buyers by editing that list.

---

## Clip Count Formula
- Videos ≤ 10 minutes → **3 clips**
- Videos > 10 minutes → `ceil(duration_minutes / 10) × 3` clips
- Examples:
  - 7 min → 3 clips
  - 10 min → 3 clips
  - 11 min → 6 clips
  - 25 min → 9 clips
  - 40 min → 12 clips
- The pipeline must calculate VOD duration and pass `--num-clips N` to `detect_peaks.py`

---

## Watermark / Branding
- No watermark for now — branding (Clipfam logo) will be added later
- All clips go out clean with no overlay
- Pass `--no-watermark` always in Clipfam pipeline

---

## Metadata / Captions Rules
- All clips get **neutral** wording — no mention of any specific creator, channel, or platform brand
- No `@owtyofficial`, no `#kickstreaming`, no Kick-specific tags
- Descriptions should be generic: "Caught this live on stream", "This moment was unexpected", etc.
- Hashtags should be platform-neutral: `#streamclips`, `#viral`, `#livestream`, etc.
- The `generate_metadata.py` script must treat every clip as a non-owtyofficial clip

---

## Database Isolation
- Each buyer's clips are tagged with their `owner_chat_id` (Telegram chat ID)
- Buyers can only see and interact with their own clips
- DB tables need:
  - `users` table: `chat_id`, `username`, `first_name`, `is_active`, `created_at`
  - `vods` table: add `owner_chat_id TEXT` column
  - `clips` table: add `owner_chat_id TEXT` column
- `get_pending_clips()` must filter by `owner_chat_id`
- `send_clip()` must only send to the clip's `owner_chat_id`, not a hardcoded CHAT_ID

---

## Bot Behaviour Changes vs kick_automation
| Behaviour | kick_automation (personal) | Clipfam (product) |
|-----------|---------------------------|-------------------|
| Users | Single hardcoded CHAT_ID | Any whitelisted Telegram user |
| Clips sent to | One fixed chat | The user who submitted the link |
| Watermark | owtyofficial gets watermark | No watermark (branding TBD) |
| Metadata | owtyofficial Kick branding | Neutral, platform-agnostic |
| Access | Open (it's personal) | Invite-only whitelist |
| Clip count | Fixed 3 | Dynamic: ceil(mins/10) × 3 |
| Onboarding | None | /start flow with welcome message |

---

## Files That Need Changes
1. **`scripts/db.py`** — add `users` table, `owner_chat_id` on vods/clips, update `insert_clip`, `get_pending_clips`, `upsert_vod`
2. **`scripts/telegram_bot.py`** — multi-user state (per chat_id), whitelist gate, /start onboarding, send clips to owner only, remove hardcoded CHAT_ID
3. **`scripts/pipeline.py`** — accept `--num-clips N`, calculate from VOD duration, pass owner_chat_id through
4. **`scripts/detect_peaks.py`** — accept `--num-clips N` argument, return exactly N peaks
5. **`scripts/generate_metadata.py`** — always use neutral (non-owtyofficial) wording regardless of channel
6. **`scripts/cut_clips.py`** — always `--no-watermark`
7. **`config/.env`** — new bot token (`TELEGRAM_BOT_TOKEN`), add `ALLOWED_CHAT_IDS`

---

## Config / .env for Clipfam
The `.env` file in `config/` needs these keys (different values from kick_automation):
```
TELEGRAM_BOT_TOKEN=<new Clipfam bot token from BotFather>
TELEGRAM_CHAT_ID=<owner's chat ID — for admin notifications>
ALLOWED_CHAT_IDS=<comma-separated list of approved buyer chat IDs>
ANTHROPIC_API_KEY=<same or separate key>
```

---

## What NOT to build yet
- Payment / subscriptions (later)
- Clipfam watermark / branding (later)
- VPS deployment (later — currently runs locally)
- Android / iOS app (later)
- Social media auto-posting (buyers post manually)

---

## Product Name
**Clipfam**
- Telegram bot name: Clipfam
- Bot username: @ClipfamBot (or closest available)

---

## Owner Context
- The owner of kick_automation is a content creator on Kick (channel: owtyofficial)
- They built the personal automation first, now productising it as Clipfam
- The personal automation in `kick_automation` must always remain untouched
- Owner's Telegram ID should be in ALLOWED_CHAT_IDS so they can test the product bot too
