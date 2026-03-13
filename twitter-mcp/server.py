"""
X (Twitter) MCP Server - Standalone Edition

OAuth 2.0 PKCE-backed FastMCP server for reading and posting on X.
"""

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("twitter")

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_FILE = CONFIG_DIR / "credentials.json"

API_BASE_URL = "https://api.x.com/2"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

DEFAULT_TWEET_FIELDS = ",".join(
    [
        "author_id",
        "created_at",
        "conversation_id",
        "lang",
        "public_metrics",
        "referenced_tweets",
        "reply_settings",
        "source",
        "text",
    ]
)
DEFAULT_USER_FIELDS = ",".join(
    [
        "created_at",
        "description",
        "id",
        "name",
        "profile_image_url",
        "protected",
        "public_metrics",
        "url",
        "username",
        "verified",
    ]
)
DEFAULT_EXPANSIONS = "author_id"
DEFAULT_FOLLOW_USER_FIELDS = "created_at,description,name,profile_image_url,public_metrics,username,verified"

_config_cache: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    global _config_cache

    if _config_cache:
        return _config_cache

    if not CONFIG_FILE.exists():
        raise RuntimeError(
            "Not configured. Run 'python setup.py' first to authenticate with X."
        )

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        _config_cache = json.load(f)

    return _config_cache


def save_config(config: Dict[str, Any]) -> None:
    global _config_cache
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    _config_cache = config


def _token_headers(config: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "twitter-mcp/1.0",
    }

    client_secret = config.get("client_secret")
    if client_secret:
        raw = f"{config['client_id']}:{client_secret}".encode("utf-8")
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"

    return headers


def refresh_access_token() -> str:
    config = load_config()
    refresh_token = config.get("refresh_token")

    if not refresh_token:
        raise RuntimeError("No refresh token available. Run setup.py again.")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if not config.get("client_secret"):
        payload["client_id"] = config["client_id"]

    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers=_token_headers(config),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"Token refresh failed ({e.code}): {error_body}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Token refresh succeeded but no access token was returned.")

    config["access_token"] = access_token
    if token_data.get("refresh_token"):
        config["refresh_token"] = token_data["refresh_token"]
    config["token_type"] = token_data.get("token_type", "bearer")
    config["scope"] = token_data.get("scope", config.get("scope", ""))
    config["refreshed_at"] = datetime.now().isoformat()
    save_config(config)
    return access_token


def api_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    retry_on_401: bool = True,
) -> Dict[str, Any]:
    config = load_config()
    access_token = config.get("access_token", "")

    url = f"{API_BASE_URL}{endpoint}"
    if params:
        filtered_params = {k: v for k, v in params.items() if v is not None and v != ""}
        if filtered_params:
            url = f"{url}?{urllib.parse.urlencode(filtered_params, doseq=True)}"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "twitter-mcp/1.0",
    }

    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_on_401:
            refresh_access_token()
            return api_request(
                endpoint=endpoint,
                method=method,
                params=params,
                data=data,
                retry_on_401=False,
            )
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"X API request failed ({e.code}): {error_body}")


def multipart_request(
    url: str,
    fields: Dict[str, str],
    file_field_name: Optional[str] = None,
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    content_type: Optional[str] = None,
    method: str = "POST",
) -> Dict[str, Any]:
    config = load_config()
    boundary = f"----twittermcp{uuid.uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    if file_field_name and file_name and file_bytes is not None:
        file_content_type = content_type or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field_name}"; '
                f'filename="{file_name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Authorization": f"Bearer {config.get('access_token', '')}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "twitter-mcp/1.0",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as e:
        if e.code == 401:
            refresh_access_token()
            config = load_config()
            request.headers["Authorization"] = f"Bearer {config.get('access_token', '')}"
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {"success": True}
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"X multipart request failed ({e.code}): {error_body}")


def get_me() -> Dict[str, Any]:
    response = api_request(
        "/users/me",
        params={"user.fields": DEFAULT_USER_FIELDS},
    )
    data = response.get("data")
    if not data:
        raise RuntimeError("X API did not return authenticated user details.")
    return data


def _lookup_user_id(username_or_id: str) -> Dict[str, Any]:
    candidate = username_or_id.strip()
    if candidate.isdigit():
        response = api_request(
            f"/users/{candidate}",
            params={"user.fields": DEFAULT_USER_FIELDS},
        )
        data = response.get("data")
        if not data:
            raise RuntimeError(f"User not found: {username_or_id}")
        return data
    return _user_lookup_by_username(candidate)


def _user_lookup_by_username(username: str) -> Dict[str, Any]:
    clean_username = username.lstrip("@").strip()
    response = api_request(
        f"/users/by/username/{urllib.parse.quote(clean_username)}",
        params={"user.fields": DEFAULT_USER_FIELDS},
    )
    data = response.get("data")
    if not data:
        raise RuntimeError(f"User not found: {username}")
    return data


def _normalize_tweets(payload: Dict[str, Any], limit: Optional[int] = None) -> Dict[str, Any]:
    tweets = payload.get("data")
    includes = payload.get("includes", {})
    users = {
        user["id"]: {
            "username": user.get("username"),
            "name": user.get("name"),
            "profile_image_url": user.get("profile_image_url"),
        }
        for user in includes.get("users", [])
        if user.get("id")
    }

    if isinstance(tweets, dict):
        tweets = [tweets]
    tweets = tweets or []
    if limit is not None:
        tweets = tweets[:limit]

    normalized: List[Dict[str, Any]] = []
    for tweet in tweets:
        author = users.get(tweet.get("author_id"), {})
        normalized.append(
            {
                "id": tweet.get("id"),
                "text": tweet.get("text"),
                "author_id": tweet.get("author_id"),
                "author_username": author.get("username"),
                "author_name": author.get("name"),
                "created_at": tweet.get("created_at"),
                "conversation_id": tweet.get("conversation_id"),
                "lang": tweet.get("lang"),
                "reply_settings": tweet.get("reply_settings"),
                "source": tweet.get("source"),
                "referenced_tweets": tweet.get("referenced_tweets", []),
                "public_metrics": tweet.get("public_metrics", {}),
            }
        )

    result: Dict[str, Any] = {
        "success": True,
        "count": len(normalized),
        "tweets": normalized,
    }
    meta = payload.get("meta")
    if meta:
        result["meta"] = meta
    return result


def _api_max_results(requested: int) -> int:
    return max(5, min(requested, 100))


def _normalize_users(payload: Dict[str, Any], key: str = "data") -> Dict[str, Any]:
    users = payload.get(key)
    if isinstance(users, dict):
        users = [users]
    users = users or []
    return {
        "success": True,
        "count": len(users),
        "users": users,
        "meta": payload.get("meta", {}),
    }


def upload_media_file(path: str, media_category: Optional[str] = None) -> Dict[str, Any]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise RuntimeError(f"Media file not found: {file_path}")

    file_bytes = file_path.read_bytes()
    mime_type, _ = mimetypes.guess_type(str(file_path))
    mime_type = mime_type or "application/octet-stream"
    size = len(file_bytes)

    if not media_category:
        if mime_type == "image/gif":
            media_category = "tweet_gif"
        elif mime_type.startswith("image/"):
            media_category = "tweet_image"
        elif mime_type.startswith("video/"):
            media_category = "tweet_video"
        else:
            media_category = "tweet_image"

    init_response = api_request(
        "/media/upload/initialize",
        method="POST",
        data={
            "total_bytes": size,
            "media_type": mime_type,
            "media_category": media_category,
        },
    )
    media_id = init_response.get("data", {}).get("id")
    if not media_id:
        raise RuntimeError(f"Media initialize failed: {init_response}")

    chunk_size = 4 * 1024 * 1024
    segment_index = 0
    for offset in range(0, size, chunk_size):
        chunk = file_bytes[offset : offset + chunk_size]
        multipart_request(
            f"{API_BASE_URL}/media/upload/{media_id}/append",
            fields={"segment_index": str(segment_index)},
            file_field_name="media",
            file_name=file_path.name,
            file_bytes=chunk,
            content_type=mime_type,
        )
        segment_index += 1

    finalize_response = api_request(
        f"/media/upload/{media_id}/finalize",
        method="POST",
    )
    status_data = finalize_response.get("data", {})
    processing = status_data.get("processing_info", {})

    while processing and processing.get("state") in {"pending", "in_progress"}:
        check_after_secs = int(processing.get("check_after_secs", 1))
        import time

        time.sleep(min(check_after_secs, 5))
        status_response = api_request(
            "/media/upload",
            params={"command": "STATUS", "media_id": media_id},
        )
        status_data = status_response.get("data", {})
        processing = status_data.get("processing_info", {})

    if processing and processing.get("state") == "failed":
        raise RuntimeError(f"Media processing failed: {processing}")

    return {
        "media_id": media_id,
        "media_type": mime_type,
        "media_category": media_category,
        "size_bytes": size,
        "raw": finalize_response,
    }


@mcp.tool(name="twitter_test_connection")
async def test_connection() -> Dict[str, Any]:
    """Test X API connectivity and return the authenticated account."""
    try:
        me = get_me()
        return {
            "success": True,
            "id": me.get("id"),
            "name": me.get("name"),
            "username": me.get("username"),
            "description": me.get("description"),
            "verified": me.get("verified"),
            "protected": me.get("protected"),
            "public_metrics": me.get("public_metrics", {}),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_me")
async def twitter_get_me() -> Dict[str, Any]:
    """Get the authenticated X account profile."""
    try:
        return {"success": True, "user": get_me()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_user")
async def twitter_get_user(
    username: str = Field(..., description="X username, with or without leading @"),
) -> Dict[str, Any]:
    """Look up a public X profile by username."""
    try:
        return {"success": True, "user": _user_lookup_by_username(username)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_tweet")
async def twitter_get_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID"),
) -> Dict[str, Any]:
    """Get a single tweet/post by ID."""
    try:
        payload = api_request(
            f"/tweets/{tweet_id}",
            params={
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=1)
        normalized["tweet"] = normalized["tweets"][0] if normalized["tweets"] else None
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_user_tweets")
async def twitter_get_user_tweets(
    username: str = Field(..., description="X username, with or without leading @"),
    max_results: int = Field(10, description="Desired number of tweets to return"),
    exclude_replies: bool = Field(False, description="Exclude reply posts"),
    exclude_reposts: bool = Field(False, description="Exclude reposts/retweets"),
) -> Dict[str, Any]:
    """Get recent tweets/posts from a user timeline."""
    try:
        user = _user_lookup_by_username(username)
        exclusions = []
        if exclude_replies:
            exclusions.append("replies")
        if exclude_reposts:
            exclusions.append("retweets")

        payload = api_request(
            f"/users/{user['id']}/tweets",
            params={
                "max_results": _api_max_results(max_results),
                "exclude": exclusions or None,
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=max_results)
        normalized["user"] = {
            "id": user.get("id"),
            "name": user.get("name"),
            "username": user.get("username"),
        }
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_search_recent_tweets")
async def twitter_search_recent_tweets(
    query: str = Field(..., description="Recent search query using X API query syntax"),
    max_results: int = Field(10, description="Desired number of tweets to return"),
) -> Dict[str, Any]:
    """Search recent tweets/posts. Availability depends on your X API access tier."""
    try:
        payload = api_request(
            "/tweets/search/recent",
            params={
                "query": query,
                "max_results": _api_max_results(max_results),
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=max_results)
        normalized["query"] = query
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_mentions")
async def twitter_get_mentions(
    max_results: int = Field(10, description="Desired number of mentions to return"),
) -> Dict[str, Any]:
    """Get recent mentions for the authenticated account."""
    try:
        me = get_me()
        payload = api_request(
            f"/users/{me['id']}/mentions",
            params={
                "max_results": _api_max_results(max_results),
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=max_results)
        normalized["user"] = {
            "id": me.get("id"),
            "name": me.get("name"),
            "username": me.get("username"),
        }
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_create_tweet")
async def twitter_create_tweet(
    text: str = Field(..., description="Tweet/Post text"),
    reply_to_tweet_id: Optional[str] = Field(
        None, description="Optional tweet ID to reply to"
    ),
    quote_tweet_id: Optional[str] = Field(
        None, description="Optional tweet ID to quote"
    ),
    media_ids: Optional[List[str]] = Field(
        None, description="Optional uploaded media IDs to attach"
    ),
) -> Dict[str, Any]:
    """Create a new tweet/post, optionally as a reply."""
    try:
        if quote_tweet_id and media_ids:
            raise RuntimeError("quote_tweet_id and media_ids cannot be used together.")
        payload: Dict[str, Any] = {"text": text}
        if reply_to_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to_tweet_id}
        if quote_tweet_id:
            payload["quote_tweet_id"] = quote_tweet_id
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        response = api_request("/tweets", method="POST", data=payload)
        return {
            "success": True,
            "tweet_id": response.get("data", {}).get("id"),
            "text": response.get("data", {}).get("text"),
            "raw": response,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_upload_media")
async def twitter_upload_media(
    file_path: str = Field(..., description="Local path to an image/GIF/video file"),
    media_category: Optional[str] = Field(
        None,
        description="Optional X media category such as tweet_image, tweet_gif, or tweet_video",
    ),
) -> Dict[str, Any]:
    """Upload media and return a media ID for use with twitter_create_tweet."""
    try:
        result = upload_media_file(file_path, media_category)
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_delete_tweet")
async def twitter_delete_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID to delete"),
) -> Dict[str, Any]:
    """Delete one of your tweets/posts."""
    try:
        response = api_request(f"/tweets/{tweet_id}", method="DELETE")
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_like_tweet")
async def twitter_like_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID to like"),
) -> Dict[str, Any]:
    """Like a tweet/post from the authenticated account."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/likes",
            method="POST",
            data={"tweet_id": tweet_id},
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_unlike_tweet")
async def twitter_unlike_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID to unlike"),
) -> Dict[str, Any]:
    """Remove a like from a tweet/post."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/likes/{tweet_id}",
            method="DELETE",
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_repost_tweet")
async def twitter_repost_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID to repost/retweet"),
) -> Dict[str, Any]:
    """Repost/retweet a tweet/post."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/retweets",
            method="POST",
            data={"tweet_id": tweet_id},
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_unrepost_tweet")
async def twitter_unrepost_tweet(
    tweet_id: str = Field(..., description="Tweet/Post ID to unrepost/unretweet"),
) -> Dict[str, Any]:
    """Undo a repost/retweet."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/retweets/{tweet_id}",
            method="DELETE",
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_quote_tweets")
async def twitter_get_quote_tweets(
    tweet_id: str = Field(..., description="Tweet/Post ID to inspect"),
    max_results: int = Field(10, description="Desired number of quote tweets to return"),
) -> Dict[str, Any]:
    """Get quote tweets for a post."""
    try:
        payload = api_request(
            f"/tweets/{tweet_id}/quote_tweets",
            params={
                "max_results": _api_max_results(max_results),
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=max_results)
        normalized["source_tweet_id"] = tweet_id
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_add_bookmark")
async def twitter_add_bookmark(
    tweet_id: str = Field(..., description="Tweet/Post ID to bookmark"),
) -> Dict[str, Any]:
    """Bookmark a tweet/post."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/bookmarks",
            method="POST",
            data={"tweet_id": tweet_id},
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_remove_bookmark")
async def twitter_remove_bookmark(
    tweet_id: str = Field(..., description="Tweet/Post ID to remove from bookmarks"),
) -> Dict[str, Any]:
    """Remove a bookmarked tweet/post."""
    try:
        me = get_me()
        response = api_request(
            f"/users/{me['id']}/bookmarks/{tweet_id}",
            method="DELETE",
        )
        return {"success": True, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_bookmarks")
async def twitter_get_bookmarks(
    max_results: int = Field(10, description="Desired number of bookmarked tweets to return"),
) -> Dict[str, Any]:
    """Get bookmarked tweets/posts for the authenticated account."""
    try:
        me = get_me()
        payload = api_request(
            f"/users/{me['id']}/bookmarks",
            params={
                "max_results": _api_max_results(max_results),
                "tweet.fields": DEFAULT_TWEET_FIELDS,
                "user.fields": DEFAULT_USER_FIELDS,
                "expansions": DEFAULT_EXPANSIONS,
            },
        )
        normalized = _normalize_tweets(payload, limit=max_results)
        normalized["user"] = {
            "id": me.get("id"),
            "name": me.get("name"),
            "username": me.get("username"),
        }
        return normalized
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_follow_user")
async def twitter_follow_user(
    username_or_id: str = Field(
        ..., description="Target X username (with or without @) or numeric user ID"
    ),
) -> Dict[str, Any]:
    """Follow a user from the authenticated account."""
    try:
        me = get_me()
        target = _lookup_user_id(username_or_id)
        response = api_request(
            f"/users/{me['id']}/following",
            method="POST",
            data={"target_user_id": target["id"]},
        )
        return {"success": True, "target": target, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_unfollow_user")
async def twitter_unfollow_user(
    username_or_id: str = Field(
        ..., description="Target X username (with or without @) or numeric user ID"
    ),
) -> Dict[str, Any]:
    """Unfollow a user from the authenticated account."""
    try:
        me = get_me()
        target = _lookup_user_id(username_or_id)
        response = api_request(
            f"/users/{me['id']}/following/{target['id']}",
            method="DELETE",
        )
        return {"success": True, "target": target, "raw": response}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_following")
async def twitter_get_following(
    username_or_id: Optional[str] = Field(
        None,
        description="User whose following list to fetch. Defaults to the authenticated account.",
    ),
    max_results: int = Field(20, description="Desired number of users to return"),
) -> Dict[str, Any]:
    """Get accounts followed by a user."""
    try:
        target = get_me() if not username_or_id else _lookup_user_id(username_or_id)
        payload = api_request(
            f"/users/{target['id']}/following",
            params={
                "max_results": _api_max_results(max_results),
                "user.fields": DEFAULT_FOLLOW_USER_FIELDS,
            },
        )
        result = _normalize_users(payload)
        result["target_user"] = {
            "id": target.get("id"),
            "username": target.get("username"),
            "name": target.get("name"),
        }
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="twitter_get_followers")
async def twitter_get_followers(
    username_or_id: Optional[str] = Field(
        None,
        description="User whose follower list to fetch. Defaults to the authenticated account.",
    ),
    max_results: int = Field(20, description="Desired number of users to return"),
) -> Dict[str, Any]:
    """Get followers for a user."""
    try:
        target = get_me() if not username_or_id else _lookup_user_id(username_or_id)
        payload = api_request(
            f"/users/{target['id']}/followers",
            params={
                "max_results": _api_max_results(max_results),
                "user.fields": DEFAULT_FOLLOW_USER_FIELDS,
            },
        )
        result = _normalize_users(payload)
        result["target_user"] = {
            "id": target.get("id"),
            "username": target.get("username"),
            "name": target.get("name"),
        }
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"
    mcp.run(transport="streamable-http", host=host, port=port)
