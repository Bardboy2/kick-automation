#!/usr/bin/env python3
"""
auth_youtube.py — One-Time YouTube OAuth2 Setup
Run this ONCE to generate your refresh token. The token is then written to
config/.env so publish.py can use it automatically on every run.

Prerequisites:
  1. Go to console.cloud.google.com
  2. Create a project → Enable "YouTube Data API v3"
  3. Credentials → Create OAuth 2.0 Client ID (Desktop app type)
  4. Download the JSON → save as  config/client_secret.json

Then run:
    python scripts/auth_youtube.py
"""

import os
import json
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

BASE_DIR    = Path(__file__).parent.parent
SECRET_FILE = BASE_DIR / "config" / "client_secret.json"
ENV_FILE    = BASE_DIR / "config" / ".env"

SCOPES       = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT_URI = "http://localhost:8080"
AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL    = "https://oauth2.googleapis.com/token"


# ─── Local callback server ────────────────────────────────────────────────────

_auth_code: str = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        qs = parse_qs(urlparse(self.path).query)
        _auth_code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#0f0f0f;color:#e8e8e8">
          <h2 style="color:#53fc18">YouTube connected!</h2>
          <p>You can close this tab and return to the terminal.</p>
        </body></html>
        """)

    def log_message(self, *_):
        pass   # suppress request logs


# ─── Main flow ────────────────────────────────────────────────────────────────

def main():
    if not SECRET_FILE.exists():
        print(f"\n[ERROR] client_secret.json not found at:\n  {SECRET_FILE}\n")
        print("Download it from console.cloud.google.com → Credentials → OAuth 2.0 Client IDs → Download")
        raise SystemExit(1)

    with open(SECRET_FILE) as f:
        secret = json.load(f)

    installed = secret.get("installed") or secret.get("web") or {}
    client_id     = installed["client_id"]
    client_secret = installed["client_secret"]

    # Step 1: Build auth URL
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",
    }
    auth_link = f"{AUTH_URL}?{urlencode(params)}"

    print("\n─── YouTube OAuth Setup ───────────────────────────────────────")
    print("Opening your browser. Log in and grant access to your YouTube account.")
    print(f"\n  {auth_link}\n")
    webbrowser.open(auth_link)

    # Step 2: Listen for callback
    print("Waiting for Google to redirect back…")
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.handle_request()   # one request only

    if not _auth_code:
        print("[ERROR] No auth code received. Try again.")
        raise SystemExit(1)

    # Step 3: Exchange code for tokens
    resp = requests.post(TOKEN_URL, data={
        "code":          _auth_code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    })
    resp.raise_for_status()
    tokens = resp.json()

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("[ERROR] No refresh token in response. Make sure you added prompt=consent.")
        raise SystemExit(1)

    # Step 4: Save to .env
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        ENV_FILE.write_text("")

    set_key(str(ENV_FILE), "YOUTUBE_CLIENT_ID",     client_id)
    set_key(str(ENV_FILE), "YOUTUBE_CLIENT_SECRET",  client_secret)
    set_key(str(ENV_FILE), "YOUTUBE_REFRESH_TOKEN",  refresh_token)

    print("\n[OK] Tokens saved to config/.env")
    print(f"     YOUTUBE_CLIENT_ID     = {client_id[:30]}…")
    print(f"     YOUTUBE_REFRESH_TOKEN = {refresh_token[:20]}…")
    print("\nYouTube is ready. You can now run the pipeline and dashboard.\n")


if __name__ == "__main__":
    main()
