#!/usr/bin/env python3
"""
auth_tiktok.py — One-Time TikTok OAuth2 Setup
Run this ONCE to generate your access token. It is written to config/.env
so publish.py can use it on every run.

Prerequisites:
  1. Go to developers.tiktok.com → Create an app
  2. Add the "Content Posting API" product
  3. Copy Client Key + Client Secret into config/.env (or set as env vars)
  4. Add  http://localhost:8080/callback  as an allowed redirect URI in your app

Then run:
    python scripts/auth_tiktok.py

Note: TikTok access tokens expire after 24h. Re-run this script to refresh.
"""

import os
import json
import secrets
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests

try:
    from dotenv import load_dotenv, set_key
    load_dotenv(Path(__file__).parent.parent / "config" / ".env", override=True)
except ImportError:
    pass

BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / "config" / ".env"

REDIRECT_URI  = "http://localhost:8080/callback"
AUTH_URL      = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES        = "user.info.basic,video.publish"


# ─── Local callback server ────────────────────────────────────────────────────

_callback_data: dict = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _callback_data["code"]  = qs.get("code",  [None])[0]
        _callback_data["state"] = qs.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f0f0f;color:#e8e8e8">
          <h2 style="color:#53fc18">TikTok connected!</h2>
          <p>You can close this tab and return to the terminal.</p>
        </body></html>
        """)

    def log_message(self, *_):
        pass


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
    print("Opening your browser. Log in and grant access to your TikTok account.")
    print(f"\n  {auth_link}\n")
    webbrowser.open(auth_link)

    print("Waiting for TikTok to redirect back…")
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()

    code = _callback_data.get("code")
    if not code:
        print("[ERROR] No auth code received. Try again.")
        raise SystemExit(1)

    if _callback_data.get("state") != state:
        print("[ERROR] State mismatch — possible CSRF. Try again.")
        raise SystemExit(1)

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
    print("\nNote: TikTok access tokens expire in 24h. Re-run this script to refresh.\n")


if __name__ == "__main__":
    main()
