#!/usr/bin/env python3
"""
Gmail MCP Setup - OAuth Authentication

Run this once to authenticate with Google:
    python setup.py

Requirements:
    1. Go to https://console.cloud.google.com
    2. Create a project (or use existing)
    3. Enable the Gmail API
    4. Create OAuth 2.0 credentials (Desktop app)
    5. Download and save as auth/client_secret.json
"""

import pickle
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Missing dependencies. Install with:")
    print("pip install google-auth-oauthlib")
    exit(1)

# Scopes for Gmail access
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.labels'
]

AUTH_DIR = Path(__file__).parent / "auth"
TOKEN_PATH = AUTH_DIR / "token.pickle"
CREDENTIALS_PATH = AUTH_DIR / "client_secret.json"


def main():
    print("Gmail MCP Setup")
    print("=" * 40)

    # Create auth directory
    AUTH_DIR.mkdir(exist_ok=True)

    # Check for credentials
    if not CREDENTIALS_PATH.exists():
        print(f"\nCredentials file not found: {CREDENTIALS_PATH}")
        print("\nTo set up:")
        print("1. Go to https://console.cloud.google.com")
        print("2. Create a project (or select existing)")
        print("3. Enable the Gmail API")
        print("4. Go to Credentials > Create Credentials > OAuth client ID")
        print("5. Application type: Desktop app")
        print("6. Download the JSON file")
        print(f"7. Save it as: {CREDENTIALS_PATH}")
        return

    # Run OAuth flow
    print("\nStarting OAuth flow...")
    print("A browser window will open for you to sign in.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    # Save credentials
    with open(TOKEN_PATH, 'wb') as token:
        pickle.dump(creds, token)

    print(f"\nAuthentication successful!")
    print(f"Token saved to: {TOKEN_PATH}")
    print("\nYou can now run the MCP server with: python run_server.py")


if __name__ == "__main__":
    main()
