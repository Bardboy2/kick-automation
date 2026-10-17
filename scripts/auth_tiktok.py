#!/usr/bin/env python3
"""
auth_tiktok.py — One-Time TikTok OAuth2 Setup
Run this ONCE to generate your access token. It is written to config/.env
so publish.py can use it on every run.

Prerequisites:
  1. developers.tiktok.com → create an org, then an app of type "Other"
  2. Add both the "Login Kit" and "Content Posting API" products
  3. Copy Client Key + Client Secret into config/.env
  4. Register this redirect URI on the app (TikTok requires HTTPS, so a
     localhost callback is not accepted):
        https://bardboy2.github.io/kick-automation/callback.html

Then run:
    python scripts/auth_tiktok.py

The browser lands on that page, which displays the authorization code for you
to paste back here. Tokens are written to config/.env; publish.py refreshes the
24h access token automatically from the saved refresh token.
"""

import os
import json
import secrets
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, unquote

import requests

try:
    from dotenv import load_dotenv, set_key
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=True)
except ImportError:
    pass

BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / "config" / ".env"

# TikTok rejects non-HTTPS redirect URIs, so localhost can't be used. The code
# is delivered to a static page on GitHub Pages and pasted back in by hand.
REDIRECT_URI  = os.environ.get(
    "TIKTOK_REDIRECT_URI",
    "https://bardboy2.github.io/kick-automation/callback.html",
)
AUTH_URL      = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"

# Scope has to match how we post: drafts need video.upload, public posts need
# video.publish (which requires an audited app). Asking for a scope the app
# doesn't have enabled makes TikTok reject the whole authorization.
_MODE  = os.environ.get("TIKTOK_MODE", "draft").lower()
SCOPES = os.environ.get(
    "TIKTOK_SCOPES",
    "user.info.basic,video.upload" if _MODE == "draft"
    else "user.info.basic,video.publish",
)


# ─── Main flow ────────────────────────────────────────────────────────────────

def main():
    client_key    = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()

    if not client_key or not client_secret:
        print("\n[ERROR] Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in config/.env first.\n")
        raise SystemExit(1)

    state = secrets.token_urlsafe(16)

    params = {
        "client_key":    client_key,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         state,
    }
    auth_link = f"{AUTH_URL}?{urlencode(params)}"

    print("\n─── TikTok OAuth Setup ────────────────────────────────────────")
    print("1. Opening your browser — log in and grant access to your TikTok account.")
    print("2. You'll land on a page showing an authorization code.")
    print("3. Copy that code and paste it below.\n")
    print(f"  {auth_link}\n")
    webbrowser.open(auth_link)

    code = input("Paste the authorization code here: ").strip()
    if not code:
        print("[ERROR] No code entered. Try again.")
        raise SystemExit(1)

    # TikTok URL-encodes the code; paste may include the wrapper or stray quotes.
    code = unquote(code).strip().strip('"').strip("'")

    # Exchange code for access token
    resp = requests.post(TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key":    client_key,
        "client_secret": client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
    })
    resp.raise_for_status()
    tokens = resp.json()

    if tokens.get("error"):
        print(f"[ERROR] TikTok token exchange failed: {tokens}")
        raise SystemExit(1)

    access_token  = tokens["data"]["access_token"]
    refresh_token = tokens["data"].get("refresh_token", "")
    expires_in    = tokens["data"].get("expires_in", 86400)

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "TIKTOK_ACCESS_TOKEN", access_token)
    if refresh_token:
        set_key(str(ENV_FILE), "TIKTOK_REFRESH_TOKEN", refresh_token)

    print("\n[OK] Token saved to config/.env")
    print(f"     TIKTOK_ACCESS_TOKEN = {access_token[:20]}…")
    print(f"     Expires in {expires_in // 3600}h")
    print("\nAccess tokens last 24h, but publish.py refreshes them automatically\n"
          "using the saved refresh token — no need to re-run this.\n")


if __name__ == "__main__":
    main()
