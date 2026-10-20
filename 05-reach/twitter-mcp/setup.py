#!/usr/bin/env python3
"""
X (Twitter) MCP Setup Script

Usage:
    python setup.py
    python setup.py --status
"""

import base64
import hashlib
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.error
import urllib.request

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_FILE = CONFIG_DIR / "credentials.json"

AUTH_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:9876/callback"
LOCAL_PORT = 9876
SCOPES = [
    "bookmark.read",
    "bookmark.write",
    "follows.read",
    "follows.write",
    "media.write",
    "tweet.read",
    "tweet.write",
    "users.read",
    "like.read",
    "like.write",
    "offline.access",
]


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    gitignore = CONFIG_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Never commit local credentials\n*.json\n", encoding="utf-8")


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_config(config) -> None:
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"  Saved credentials to {CONFIG_FILE}")


def print_status() -> None:
    print("\n=== X MCP Status ===\n")
    config = load_config()
    if not config:
        print("  Status: Not authenticated")
        print("  Run 'python setup.py' to authenticate\n")
        return

    print("  Status: Authenticated")
    print(f"  Username: @{config.get('username', 'unknown')}")
    print(f"  User ID: {config.get('user_id', 'unknown')}")
    print(f"  Scopes: {config.get('scope', '')}\n")


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        auth_code = None
        auth_error = None

        if "error" in query:
            auth_error = query.get("error_description", query["error"])[0]
        elif "code" not in query:
            auth_error = "No authorization code received"
        elif query.get("state", [None])[0] != self.server.expected_state:
            auth_error = "State mismatch"
        else:
            auth_code = query["code"][0]

        self.server.auth_code = auth_code
        self.server.auth_error = auth_error

        if auth_error:
            body = "<html><body><h1>Authentication Failed</h1><p>You can close this window.</p></body></html>"
        else:
            body = "<html><body><h1>Success</h1><p>Authentication complete. You can close this window.</p></body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


def get_oauth_code(client_id: str):
    state = secrets.token_urlsafe(32)
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    server = HTTPServer(("127.0.0.1", LOCAL_PORT), OAuthCallbackHandler)
    server.auth_code = None
    server.auth_error = None
    server.expected_state = state
    server.timeout = 180

    print("\n  Opening browser for X authorization...")
    print("\n  If the browser does not open, visit this URL manually:\n")
    print(f"  {auth_url}\n")
    webbrowser.open(auth_url)

    print("  Waiting for authorization (timeout: 3 minutes)...")
    server.handle_request()
    return server.auth_code, server.auth_error, verifier


def token_headers(client_id: str, client_secret: str) -> dict:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "twitter-mcp/1.0",
    }
    if client_secret:
        raw = f"{client_id}:{client_secret}".encode("utf-8")
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
    return headers


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, verifier: str):
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    if not client_secret:
        payload["client_id"] = client_id

    body = urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers=token_headers(client_id, client_secret),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        print(f"\n  Token exchange failed ({e.code}): {error_body}")
        return None


def get_me(access_token: str):
    params = urlencode(
        {
            "user.fields": "created_at,description,profile_image_url,protected,public_metrics,url,username,verified"
        }
    )
    request = urllib.request.Request(
        f"https://api.x.com/2/users/me?{params}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "twitter-mcp/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("data")
    except Exception as e:
        print(f"\n  Could not fetch authenticated user info: {e}")
        return None


def print_instructions() -> None:
    print(
        """
================================================================================
                           X APP SETUP INSTRUCTIONS
================================================================================

  Before authenticating, create or open an app in the X Developer Portal:

  1. Open the developer portal and create a Project + App
  2. Enable OAuth 2.0 for the app
  3. Set the app type to a Web App or Native App
  4. Add this callback URL exactly:
       http://127.0.0.1:9876/callback
  5. Grant scopes:
       bookmark.read bookmark.write follows.read follows.write media.write
       tweet.read tweet.write users.read like.read like.write offline.access
  6. Set app permissions to allow read and write
  7. Copy your OAuth 2.0 Client ID
  8. If your app has a Client Secret, keep that too

  Notes:
  - Recent search access depends on your X API plan
  - This setup uses OAuth 2.0 Authorization Code Flow with PKCE

================================================================================
"""
    )


def setup() -> bool:
    print("\n=== X MCP Setup ===\n")

    existing = load_config()
    if existing:
        print(f"  Already authenticated as @{existing.get('username', 'unknown')}")
        response = input("  Re-authenticate? (y/N): ").strip().lower()
        if response != "y":
            print("  Keeping existing credentials.")
            return True

    print_instructions()

    print("  Enter your X app credentials:\n")
    client_id = input("  OAuth 2.0 Client ID: ").strip()
    if not client_id:
        print("  Error: Client ID is required.")
        return False

    client_secret = input("  Client Secret (optional, press Enter to skip): ").strip()

    input("\n  Press Enter when ready to authenticate in the browser...")

    code, error, verifier = get_oauth_code(client_id)
    if error:
        print(f"\n  Authorization failed: {error}")
        return False
    if not code:
        print("\n  No authorization code received.")
        return False

    print("\n  Exchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(client_id, client_secret, code, verifier)
    if not tokens:
        return False

    access_token = tokens.get("access_token")
    if not access_token:
        print("  Error: No access token was returned.")
        return False

    me = get_me(access_token)
    if not me:
        print("  Error: Auth succeeded but account lookup failed.")
        return False

    config = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "token_type": tokens.get("token_type", "bearer"),
        "scope": tokens.get("scope", ""),
        "user_id": me.get("id"),
        "username": me.get("username"),
        "name": me.get("name"),
    }
    save_config(config)

    print(f"\n  Success! Authenticated as @{me.get('username')}")
    print("\n  You can now run the server with: python run_server.py")
    return True


def main():
    ensure_config_dir()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "--status":
            print_status()
        elif arg in {"--help", "-h"}:
            print(__doc__)
        else:
            print(f"Unknown argument: {arg}")
            print(__doc__)
    else:
        setup()


if __name__ == "__main__":
    main()
