# X (Twitter) MCP

Read account information and posts, and manage posts, media, likes, bookmarks, and follows through the X API. You need your own X developer application and API access for the endpoints you want to use. Availability and charges depend on your account access; installing this server does not grant API access.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\twitter-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\twitter-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Register your application and sign in

1. Open the [X Developer Portal](https://developer.x.com/) and create or select your own project/application. Enable OAuth 2.0 user authentication with read/write access. See the [official OAuth 2.0 PKCE guide](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) for current app settings.
2. Add this callback URL exactly: **`http://127.0.0.1:9876/callback`**. This script uses `127.0.0.1`, not `localhost`.
3. Copy the application's **OAuth 2.0 Client ID**. If the application provides a Client Secret, keep that available too. These are different from OAuth 1.0 API keys.
4. Run the local login wizard:

```powershell
.\.venv\Scripts\python.exe .\setup.py
```

5. Enter the Client ID, and the Client Secret if your app requires one; otherwise leave the secret blank. Open the printed URL if your browser does not open automatically. Sign into the X account you want to connect and approve the access. Return to PowerShell within three minutes.
6. Setup creates **`config/credentials.json`** with your account and tokens. Keep it private. This package does not need a `.env` file.

The script requests `tweet.read`, `tweet.write`, `users.read`, `like.read`, `like.write`, `bookmark.read`, `bookmark.write`, `follows.read`, `follows.write`, `media.write`, and `offline.access`. Your application/API access must permit the operations you use. The offline scope permits token renewal.

To inspect the locally saved account:

```powershell
.\.venv\Scripts\python.exe .\setup.py --status
```

This reads the saved file; use the live tool check in step 4 to confirm the API accepts it.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\run_stdio.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "twitter-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/twitter-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/twitter-mcp/run_stdio.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask: **"Use twitter_test_connection and tell me which account is connected."** A successful response identifies your own account. This check does not post, like, or follow anything.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.

- **Redirect mismatch:** check that the app uses `http://127.0.0.1:9876/callback` exactly. Another setup process using port 9876 must be stopped before retrying.
- **401 or expired/revoked token:** rerun `setup.py` with the same app, then restart the MCP client.
- **403 or an unavailable endpoint:** check that your app has the relevant scopes and your X API access includes that endpoint. Recent search and other tools may require different access.
- **429:** the API is rate limiting the account/app. Wait for its limit to reset; restarting this server does not reset provider limits.

## Files and HTTP mode

`setup.py` handles OAuth; `server.py` implements the tools; **`run_stdio.py`** is the local client entry point used above. `run_server.py` and running `server.py` directly start HTTP on port 8080, listening on all interfaces. That HTTP launcher adds no authentication and is not needed for this local setup. Do not expose it publicly without adding access control.

## Available tools

| Tool | Description |
|------|-------------|
| `twitter_test_connection` | Test auth and return the current account |
| `twitter_get_me` | Get the authenticated user profile |
| `twitter_get_user` | Look up a public user by username |
| `twitter_get_tweet` | Get a tweet by ID |
| `twitter_get_user_tweets` | Read recent tweets from a user |
| `twitter_search_recent_tweets` | Search recent tweets |
| `twitter_get_mentions` | Read recent mentions for your account |
| `twitter_create_tweet` | Create a new tweet, reply, quote tweet, or media tweet |
| `twitter_upload_media` | Upload local image/GIF/video and get a media ID |
| `twitter_delete_tweet` | Delete one of your tweets |
| `twitter_like_tweet` | Like a tweet |
| `twitter_unlike_tweet` | Remove a like |
| `twitter_repost_tweet` | Repost a tweet |
| `twitter_unrepost_tweet` | Undo a repost |
| `twitter_get_quote_tweets` | Get quote tweets for a post |
| `twitter_add_bookmark` | Bookmark a tweet |
| `twitter_remove_bookmark` | Remove a bookmark |
| `twitter_get_bookmarks` | List bookmarks |
| `twitter_follow_user` | Follow a user |
| `twitter_unfollow_user` | Unfollow a user |
| `twitter_get_following` | List followed accounts |
| `twitter_get_followers` | List followers |
