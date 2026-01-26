#!/usr/bin/env python3
"""
Tumblr MCP Setup Script

Run this once to authenticate with Tumblr.

Usage:
    python setup.py              # Interactive setup
    python setup.py --status     # Check if authenticated
"""

import json
import sys
import webbrowser
import secrets
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import urllib.request
import urllib.error

# Configuration
CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_FILE = CONFIG_DIR / "credentials.json"
REDIRECT_URI = "http://localhost:9876/callback"
LOCAL_PORT = 9876
SCOPES = ["basic", "write", "offline_access"]


def ensure_config_dir():
    """Create config directory if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    gitignore = CONFIG_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# Never commit credentials\n*.json\n")


def load_config():
    """Load existing config."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return None


def save_config(config):
    """Save config."""
    ensure_config_dir()
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"  Saved credentials to {CONFIG_FILE}")


def check_status():
    """Check authentication status."""
    print("\n=== Tumblr MCP Status ===\n")

    config = load_config()
    if config:
        blog_name = config.get('blog_name', 'unknown')
        print(f"  Status: Authenticated")
        print(f"  Blog: {blog_name}.tumblr.com")
        print(f"  URL: https://{blog_name}.tumblr.com")
    else:
        print("  Status: Not authenticated")
        print("  Run 'python setup.py' to authenticate")

    print()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/callback':
            query = parse_qs(parsed.query)

            if 'error' in query:
                self.server.auth_error = query.get('error_description', query['error'])[0]
                self.server.auth_code = None
                response = b"<html><body><h1>Authentication Failed</h1><p>You can close this window.</p></body></html>"
            elif 'code' in query:
                self.server.auth_code = query['code'][0]
                self.server.auth_error = None
                if 'state' in query and query['state'][0] == self.server.expected_state:
                    response = b"<html><body><h1>Success!</h1><p>Authentication complete. You can close this window.</p></body></html>"
                else:
                    self.server.auth_error = "State mismatch"
                    self.server.auth_code = None
                    response = b"<html><body><h1>Security Error</h1><p>Please try again.</p></body></html>"
            else:
                self.server.auth_error = "No code received"
                self.server.auth_code = None
                response = b"<html><body><h1>Error</h1><p>No authorization code received.</p></body></html>"

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_response(404)
            self.end_headers()


def get_oauth_code(client_id):
    """Start local server and get OAuth code."""
    state = secrets.token_urlsafe(32)

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'state': state,
        'redirect_uri': REDIRECT_URI,
    }
    auth_url = f"https://www.tumblr.com/oauth2/authorize?{urlencode(params)}"

    server = HTTPServer(('localhost', LOCAL_PORT), OAuthCallbackHandler)
    server.auth_code = None
    server.auth_error = None
    server.expected_state = state
    server.timeout = 120

    print(f"\n  Opening browser for Tumblr authorization...")
    print(f"\n  (If browser doesn't open, visit this URL:)")
    print(f"\n  {auth_url}\n")

    webbrowser.open(auth_url)

    print("  Waiting for authorization (timeout: 2 minutes)...")
    server.handle_request()

    return server.auth_code, server.auth_error


def exchange_code_for_token(client_id, client_secret, code):
    """Exchange code for tokens."""
    data = urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'client_id': client_id,
        'client_secret': client_secret,
    }).encode()

    req = urllib.request.Request(
        'https://api.tumblr.com/v2/oauth2/token',
        data=data,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        print(f"  Error: {e.code} - {error_body}")
        return None


def get_user_info(access_token):
    """Get user info."""
    req = urllib.request.Request(
        'https://api.tumblr.com/v2/user/info',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            return data.get('response', {}).get('user', {})
    except Exception as e:
        print(f"  Error getting user info: {e}")
        return None


def print_instructions():
    """Print setup instructions."""
    print("""
================================================================================
                         TUMBLR APP SETUP INSTRUCTIONS
================================================================================

  Before authenticating, you need to create a Tumblr app:

  1. Go to: https://www.tumblr.com/oauth/apps

  2. Click "Register application"

  3. Fill in the form:
     - Application name: My Tumblr Bot (or whatever you want)
     - Application description: Personal posting bot
     - Default callback URL: http://localhost:9876/callback
     - OAuth2 redirect URLs: http://localhost:9876/callback

  4. Click "Register"

  5. You'll see your app with:
     - OAuth Consumer Key (this is your client_id)
     - OAuth Consumer Secret (this is your client_secret)

================================================================================
""")


def setup():
    """Interactive setup."""
    print("\n=== Tumblr MCP Setup ===\n")

    existing = load_config()
    if existing:
        blog_name = existing.get('blog_name', 'unknown')
        print(f"  Already authenticated as {blog_name}.tumblr.com")
        response = input("  Re-authenticate? (y/N): ").strip().lower()
        if response != 'y':
            print("  Keeping existing credentials.")
            return True

    print_instructions()

    print("  Enter your Tumblr app credentials:\n")

    client_id = input("  OAuth Consumer Key (client_id): ").strip()
    if not client_id:
        print("  Error: Client ID is required")
        return False

    client_secret = input("  OAuth Consumer Secret (client_secret): ").strip()
    if not client_secret:
        print("  Error: Client Secret is required")
        return False

    input("\n  Press Enter when ready to authenticate in browser...")

    code, error = get_oauth_code(client_id)

    if error:
        print(f"\n  Authorization failed: {error}")
        return False

    if not code:
        print("\n  No authorization code received.")
        return False

    print("\n  Got authorization code, exchanging for tokens...")

    tokens = exchange_code_for_token(client_id, client_secret, code)

    if not tokens:
        print("  Failed to get tokens.")
        return False

    access_token = tokens.get('access_token')
    refresh_token = tokens.get('refresh_token')

    if not access_token:
        print("  Error: No access token in response")
        return False

    user_info = get_user_info(access_token)
    blog_name = None

    if user_info:
        blogs = user_info.get('blogs', [])
        if blogs:
            primary = next((b for b in blogs if b.get('primary')), blogs[0])
            blog_name = primary.get('name')
            print(f"\n  Found blogs: {', '.join(b.get('name', '?') for b in blogs)}")
            print(f"  Primary blog: {blog_name}")

    if not blog_name:
        blog_name = input("  Couldn't fetch blog name. Enter manually: ").strip()

    config = {
        'client_id': client_id,
        'client_secret': client_secret,
        'access_token': access_token,
        'refresh_token': refresh_token,
        'blog_name': blog_name,
        'token_type': tokens.get('token_type', 'bearer'),
        'scope': tokens.get('scope', ''),
    }

    save_config(config)

    print(f"\n  Success! Authenticated as {blog_name}.tumblr.com")
    print(f"\n  You can now run the server with: python server.py")
    return True


def main():
    ensure_config_dir()

    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == '--status':
            check_status()
        elif arg == '--help' or arg == '-h':
            print(__doc__)
        else:
            print(f"Unknown argument: {arg}")
            print(__doc__)
    else:
        setup()


if __name__ == "__main__":
    main()
