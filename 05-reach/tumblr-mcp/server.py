"""
Tumblr MCP Server - Standalone Edition

A simple MCP server that lets Claude post to your Tumblr blog.
Works with any MCP-compatible client (Claude Code, etc.)

Setup:
    1. Create a Tumblr app at https://www.tumblr.com/oauth/apps
    2. Run: python setup.py
    3. Run: python server.py (local) or deploy to cloud

Built with love for sharing.
"""

import json
import os
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from fastmcp import FastMCP
from pydantic import Field

# Initialize FastMCP server
mcp = FastMCP("tumblr")

# Configuration
CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_FILE = CONFIG_DIR / "credentials.json"

# Token cache
_config_cache: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    """Load configuration from file."""
    global _config_cache

    if _config_cache:
        return _config_cache

    if not CONFIG_FILE.exists():
        raise RuntimeError(
            "Not configured! Run 'python setup.py' first to authenticate with Tumblr."
        )

    with open(CONFIG_FILE, 'r') as f:
        _config_cache = json.load(f)

    return _config_cache


def save_config(config: Dict[str, Any]):
    """Save configuration to file."""
    global _config_cache
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    _config_cache = config


def refresh_access_token() -> str:
    """Refresh the access token."""
    config = load_config()

    refresh_token = config.get('refresh_token')
    if not refresh_token:
        raise RuntimeError("No refresh token. Run setup.py again.")

    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': config['client_id'],
        'client_secret': config['client_secret'],
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
            new_tokens = json.loads(response.read().decode())

            config['access_token'] = new_tokens['access_token']
            if 'refresh_token' in new_tokens:
                config['refresh_token'] = new_tokens['refresh_token']
            config['refreshed_at'] = datetime.now().isoformat()

            save_config(config)
            return new_tokens['access_token']
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        raise RuntimeError(f"Token refresh failed: {error_body}")


def api_request(
    endpoint: str,
    method: str = "GET",
    data: Optional[Dict] = None,
    params: Optional[Dict] = None,
    retry_on_401: bool = True
) -> Dict[str, Any]:
    """Make an authenticated request to Tumblr API."""
    config = load_config()
    access_token = config.get('access_token', '')

    url = f"https://api.tumblr.com/v2{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }

    body = json.dumps(data).encode() if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            return result.get('response', result)
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_on_401:
            refresh_access_token()
            return api_request(endpoint, method, data, params, retry_on_401=False)

        error_body = e.read().decode() if e.fp else str(e)
        raise RuntimeError(f"API request failed ({e.code}): {error_body}")


def legacy_post_request(blog_name: str, post_data: Dict[str, str], retry_on_401: bool = True) -> Dict[str, Any]:
    """Make a legacy post request using form-urlencoded data."""
    config = load_config()
    access_token = config.get('access_token', '')

    url = f"https://api.tumblr.com/v2/blog/{blog_name}/post"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    }

    body = urllib.parse.urlencode(post_data).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            return result.get('response', result)
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_on_401:
            refresh_access_token()
            return legacy_post_request(blog_name, post_data, retry_on_401=False)

        error_body = e.read().decode() if e.fp else str(e)
        raise RuntimeError(f"Post failed ({e.code}): {error_body}")


def legacy_reblog_request(blog_name: str, reblog_data: Dict[str, str], retry_on_401: bool = True) -> Dict[str, Any]:
    """Make a legacy reblog request."""
    config = load_config()
    access_token = config.get('access_token', '')

    url = f"https://api.tumblr.com/v2/blog/{blog_name}/post/reblog"

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    }

    body = urllib.parse.urlencode(reblog_data).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            return result.get('response', result)
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_on_401:
            refresh_access_token()
            return legacy_reblog_request(blog_name, reblog_data, retry_on_401=False)

        error_body = e.read().decode() if e.fp else str(e)
        raise RuntimeError(f"Reblog failed ({e.code}): {error_body}")


# =============================================================================
# CONNECTION TOOLS
# =============================================================================

@mcp.tool(name="tumblr_test_connection")
async def test_connection() -> Dict[str, Any]:
    """
    Test Tumblr API connection and show account info.

    Returns:
        Connection status and blog info
    """
    try:
        user_info = api_request("/user/info")

        user = user_info.get('user', {})
        blogs = user.get('blogs', [])
        primary_blog = next((b for b in blogs if b.get('primary')), blogs[0] if blogs else {})

        return {
            "success": True,
            "username": user.get('name'),
            "blog_name": primary_blog.get('name'),
            "blog_title": primary_blog.get('title'),
            "blog_url": primary_blog.get('url'),
            "posts": primary_blog.get('posts', 0),
            "followers": primary_blog.get('followers', 0),
            "total_blogs": len(blogs),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_get_user_info")
async def get_user_info() -> Dict[str, Any]:
    """
    Get detailed information about your Tumblr account.

    Returns:
        User info including all blogs
    """
    try:
        user_info = api_request("/user/info")

        user = user_info.get('user', {})
        blogs = []

        for blog in user.get('blogs', []):
            blogs.append({
                'name': blog.get('name'),
                'title': blog.get('title'),
                'url': blog.get('url'),
                'primary': blog.get('primary', False),
                'posts': blog.get('posts', 0),
                'followers': blog.get('followers', 0),
                'description': blog.get('description', '')[:200],
            })

        return {
            "success": True,
            "username": user.get('name'),
            "following": user.get('following', 0),
            "likes": user.get('likes', 0),
            "blogs": blogs,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# POST CREATION TOOLS
# =============================================================================

@mcp.tool(name="tumblr_create_text_post")
async def create_text_post(
    content: str = Field(..., description="Post content (HTML or plain text)"),
    title: Optional[str] = Field(None, description="Optional post title"),
    tags: Optional[str] = Field(None, description="Comma-separated tags"),
    state: str = Field("published", description="Post state: published, draft, queue, private")
) -> Dict[str, Any]:
    """
    Create a text post on your Tumblr blog.

    Args:
        content: The post content
        title: Optional title
        tags: Comma-separated tags (e.g., "art, inspiration")
        state: published, draft, queue, or private

    Returns:
        Created post info
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        post_data = {
            'type': 'text',
            'body': content,
            'state': state,
        }

        if title:
            post_data['title'] = title

        if tags:
            post_data['tags'] = tags

        result = legacy_post_request(blog_name, post_data)

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": result.get('id'),
            "state": state,
            "url": f"https://{blog_name}.tumblr.com/post/{result.get('id')}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_create_photo_post")
async def create_photo_post(
    image_url: str = Field(..., description="URL of the image to post"),
    caption: Optional[str] = Field(None, description="Caption for the image"),
    tags: Optional[str] = Field(None, description="Comma-separated tags"),
    link_url: Optional[str] = Field(None, description="URL the image links to when clicked"),
    state: str = Field("published", description="Post state: published, draft, queue, private")
) -> Dict[str, Any]:
    """
    Create a photo post on your Tumblr blog.

    Args:
        image_url: URL of the image
        caption: Optional caption
        tags: Comma-separated tags
        link_url: Optional click-through URL
        state: published, draft, queue, or private

    Returns:
        Created post info
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        post_data = {
            'type': 'photo',
            'source': image_url,
            'state': state,
        }

        if caption:
            post_data['caption'] = caption

        if link_url:
            post_data['link'] = link_url

        if tags:
            post_data['tags'] = tags

        result = legacy_post_request(blog_name, post_data)

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": result.get('id'),
            "state": state,
            "url": f"https://{blog_name}.tumblr.com/post/{result.get('id')}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_create_quote_post")
async def create_quote_post(
    quote: str = Field(..., description="The quote text"),
    source: Optional[str] = Field(None, description="Source/attribution for the quote"),
    tags: Optional[str] = Field(None, description="Comma-separated tags"),
    state: str = Field("published", description="Post state: published, draft, queue, private")
) -> Dict[str, Any]:
    """
    Create a quote post on your Tumblr blog.

    Args:
        quote: The quote text
        source: Attribution/source
        tags: Comma-separated tags
        state: published, draft, queue, or private

    Returns:
        Created post info
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        post_data = {
            'type': 'quote',
            'quote': quote,
            'state': state,
        }

        if source:
            post_data['source'] = source

        if tags:
            post_data['tags'] = tags

        result = legacy_post_request(blog_name, post_data)

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": result.get('id'),
            "state": state,
            "url": f"https://{blog_name}.tumblr.com/post/{result.get('id')}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_create_link_post")
async def create_link_post(
    url: str = Field(..., description="The URL to share"),
    title: Optional[str] = Field(None, description="Title for the link"),
    description: Optional[str] = Field(None, description="Description of the link"),
    tags: Optional[str] = Field(None, description="Comma-separated tags"),
    state: str = Field("published", description="Post state: published, draft, queue, private")
) -> Dict[str, Any]:
    """
    Create a link post on your Tumblr blog.

    Args:
        url: The URL to share
        title: Link title
        description: Link description
        tags: Comma-separated tags
        state: published, draft, queue, or private

    Returns:
        Created post info
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        post_data = {
            'type': 'link',
            'url': url,
            'state': state,
        }

        if title:
            post_data['title'] = title

        if description:
            post_data['description'] = description

        if tags:
            post_data['tags'] = tags

        result = legacy_post_request(blog_name, post_data)

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": result.get('id'),
            "state": state,
            "url": f"https://{blog_name}.tumblr.com/post/{result.get('id')}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# REBLOG TOOLS
# =============================================================================

@mcp.tool(name="tumblr_reblog")
async def reblog(
    post_url: str = Field(..., description="URL of the post to reblog"),
    comment: Optional[str] = Field(None, description="Optional comment to add"),
    tags: Optional[str] = Field(None, description="Comma-separated tags"),
    state: str = Field("published", description="Post state: published, draft, queue, private")
) -> Dict[str, Any]:
    """
    Reblog a post to your Tumblr blog.

    Args:
        post_url: Full URL of the post to reblog
        comment: Optional comment to add
        tags: Comma-separated tags
        state: published, draft, queue, or private

    Returns:
        Reblog result
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        import re
        match = re.search(r'https?://([^.]+)\.tumblr\.com/post/(\d+)', post_url)
        if not match:
            return {"success": False, "error": "Invalid Tumblr post URL"}

        source_blog = match.group(1)
        post_id = match.group(2)

        post_info = api_request(f"/blog/{source_blog}/posts/{post_id}")

        reblog_key = post_info.get('reblog_key')
        if not reblog_key:
            return {"success": False, "error": "Could not get reblog key"}

        reblog_data = {
            'id': post_id,
            'reblog_key': reblog_key,
            'state': state,
        }

        if comment:
            reblog_data['comment'] = comment

        if tags:
            reblog_data['tags'] = tags

        result = legacy_reblog_request(blog_name, reblog_data)

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": result.get('id'),
            "reblogged_from": source_blog,
            "state": state,
            "url": f"https://{blog_name}.tumblr.com/post/{result.get('id')}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# POST MANAGEMENT TOOLS
# =============================================================================

@mcp.tool(name="tumblr_get_posts")
async def get_posts(
    limit: int = Field(20, description="Number of posts to get (1-50)"),
    offset: int = Field(0, description="Post offset for pagination"),
    post_type: Optional[str] = Field(None, description="Filter by type: text, photo, quote, link, video, audio")
) -> Dict[str, Any]:
    """
    Get posts from your Tumblr blog.

    Args:
        limit: Number of posts (max 50)
        offset: Pagination offset
        post_type: Optional filter by post type

    Returns:
        List of posts
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        params = {
            "limit": min(limit, 50),
            "offset": offset,
            "npf": "true",
        }

        if post_type:
            params["type"] = post_type

        result = api_request(f"/blog/{blog_name}/posts", params=params)

        posts = []
        for post in result.get('posts', []):
            posts.append({
                'id': post.get('id'),
                'type': post.get('type'),
                'state': post.get('state'),
                'note_count': post.get('note_count', 0),
                'timestamp': post.get('timestamp'),
                'date': post.get('date'),
                'tags': post.get('tags', []),
                'url': post.get('post_url'),
                'summary': post.get('summary', '')[:200],
            })

        return {
            "success": True,
            "blog_name": blog_name,
            "total_posts": result.get('total_posts', len(posts)),
            "count": len(posts),
            "posts": posts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_delete_post")
async def delete_post(
    post_id: str = Field(..., description="Post ID to delete")
) -> Dict[str, Any]:
    """
    Delete a post. This cannot be undone!

    Args:
        post_id: Post ID to delete

    Returns:
        Deletion result
    """
    try:
        config = load_config()
        blog_name = config.get('blog_name')

        api_request(f"/blog/{blog_name}/posts/{post_id}", method="DELETE")

        return {
            "success": True,
            "blog_name": blog_name,
            "post_id": post_id,
            "message": "Post deleted",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# DASHBOARD & SOCIAL TOOLS
# =============================================================================

@mcp.tool(name="tumblr_get_dashboard")
async def get_dashboard(
    limit: int = Field(20, description="Number of posts to get"),
    post_type: Optional[str] = Field(None, description="Filter by type: text, photo, quote, link, video, audio")
) -> Dict[str, Any]:
    """
    Get posts from your dashboard (blogs you follow).

    Args:
        limit: Number of posts
        post_type: Optional type filter

    Returns:
        Dashboard posts
    """
    try:
        params = {
            "limit": min(limit, 50),
            "npf": "true",
        }

        if post_type:
            params["type"] = post_type

        result = api_request("/user/dashboard", params=params)

        posts = []
        for post in result.get('posts', []):
            posts.append({
                'id': post.get('id'),
                'blog_name': post.get('blog_name'),
                'type': post.get('type'),
                'note_count': post.get('note_count', 0),
                'tags': post.get('tags', []),
                'url': post.get('post_url'),
                'reblog_key': post.get('reblog_key'),
                'summary': post.get('summary', '')[:200],
            })

        return {
            "success": True,
            "count": len(posts),
            "posts": posts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_follow")
async def follow_blog(
    blog_url: str = Field(..., description="Blog URL to follow (e.g., 'example.tumblr.com')")
) -> Dict[str, Any]:
    """
    Follow a blog.

    Args:
        blog_url: Blog URL

    Returns:
        Follow result
    """
    try:
        api_request("/user/follow", method="POST", data={
            "url": blog_url if blog_url.startswith('http') else f"https://{blog_url}",
        })

        return {
            "success": True,
            "blog_url": blog_url,
            "action": "followed",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(name="tumblr_search_tag")
async def search_tag(
    tag: str = Field(..., description="Tag to search for"),
    limit: int = Field(20, description="Number of posts to get")
) -> Dict[str, Any]:
    """
    Search posts by tag.

    Args:
        tag: Tag to search
        limit: Number of posts

    Returns:
        Tagged posts
    """
    try:
        result = api_request("/tagged", params={
            "tag": tag,
            "limit": min(limit, 50),
            "npf": "true",
        })

        posts = []
        for post in result if isinstance(result, list) else result.get('posts', result):
            if isinstance(post, dict):
                posts.append({
                    'id': post.get('id'),
                    'blog_name': post.get('blog_name'),
                    'type': post.get('type'),
                    'note_count': post.get('note_count', 0),
                    'tags': post.get('tags', []),
                    'url': post.get('post_url'),
                    'reblog_key': post.get('reblog_key'),
                    'summary': post.get('summary', '')[:200],
                })

        return {
            "success": True,
            "tag": tag,
            "count": len(posts),
            "posts": posts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
