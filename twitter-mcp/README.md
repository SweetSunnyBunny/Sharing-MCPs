# X (Twitter) MCP Server

This MCP server gives an MCP-compatible client access to X accounts using OAuth 2.0 PKCE.

## What it can do

- Check the authenticated account
- Look up users and tweets
- Read a user's recent tweets
- Search recent tweets
- Read your mentions
- Create and delete tweets
- Create quote tweets
- Upload media for posts
- Like and unlike tweets
- Repost and unrepost tweets
- Bookmark and unbookmark tweets
- Follow and unfollow users
- Read follower and following lists

## Quick start

### 1. Create an X app

In the X Developer Portal:

1. Create a Project and App
2. Enable OAuth 2.0
3. Set the app type to `Web App` or `Native App`
4. Add this callback URL exactly:
   - `http://127.0.0.1:9876/callback`
5. Grant these scopes:
   - `bookmark.read`
   - `bookmark.write`
   - `follows.read`
   - `follows.write`
   - `media.write`
   - `tweet.read`
   - `tweet.write`
   - `users.read`
   - `like.read`
   - `like.write`
   - `offline.access`
6. Make sure the app has read/write permission
7. Copy the OAuth 2.0 Client ID
8. Copy the Client Secret too if your app provides one

Notes:
- `twitter_search_recent_tweets` depends on your X API access tier.
- This server uses OAuth 2.0 Authorization Code Flow with PKCE.

### 2. Install and authenticate

```bash
pip install -r requirements.txt
python setup.py
python setup.py --status
```

### 3. Run the server

```bash
python run_server.py
```

The default MCP endpoint is `http://localhost:8080/mcp`.

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

## Connect from an MCP client

Example MCP config:

```json
{
  "mcpServers": {
    "twitter": {
      "command": "uv",
      "args": [
        "run",
        "--with",
        "fastmcp",
        "--with",
        "pydantic",
        "--with",
        "uvicorn",
        "fastmcp",
        "run",
        "C:/AI/MCP/Sharing-MCPs/twitter-mcp/server.py"
      ]
    }
  }
}
```

If you prefer running the HTTP server yourself, start `python run_server.py` and point your client at `http://localhost:8080/mcp`.

## Files

- `setup.py`: local OAuth setup flow
- `server.py`: FastMCP server and tools
- `run_server.py`: local runner
- `config/credentials.json`: local tokens and account metadata

## Security

- Credentials are stored locally in `config/credentials.json`
- The `config` folder is gitignored by the setup script
- Do not commit your credentials file
