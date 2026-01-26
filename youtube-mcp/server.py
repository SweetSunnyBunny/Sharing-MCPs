"""
YouTube MCP Server - Standalone Edition

Let Claude access YouTube! Search, manage playlists, get video info, and transcripts.

Requirements:
    1. Google Cloud project with YouTube Data API v3 enabled
    2. OAuth credentials (client_secret.json)
    3. Run setup.py to authenticate

Built with love for sharing.
"""

import pickle
import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from pydantic import Field

try:
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    raise ImportError(
        "Missing Google API dependencies. Install with: "
        "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
    )

# Optional transcript support
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    TRANSCRIPT_AVAILABLE = True
except ImportError:
    TRANSCRIPT_AVAILABLE = False

mcp = FastMCP("youtube-mcp")

# Auth paths
AUTH_DIR = Path(__file__).parent / "auth"
TOKEN_PATH = AUTH_DIR / "token.pickle"
CACHE_DIR = Path(__file__).parent / "cache"

# Cached service
_service = None


def get_youtube_service():
    """Get authenticated YouTube service."""
    global _service

    if _service:
        return _service

    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, 'wb') as token:
                pickle.dump(creds, token)
        else:
            raise RuntimeError("Not authenticated. Run 'python setup.py' first.")

    _service = build('youtube', 'v3', credentials=creds)
    return _service


def extract_video_id(video: str) -> str:
    """Extract video ID from URL or return as-is if already an ID."""
    if re.match(r'^[a-zA-Z0-9_-]{11}$', video):
        return video

    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]

    for pattern in patterns:
        match = re.search(pattern, video)
        if match:
            return match.group(1)

    raise ValueError(f"Could not extract video ID from: {video}")


def format_duration(duration: str) -> str:
    """Convert ISO 8601 duration to human readable format."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    if not match:
        return duration

    hours, minutes, seconds = match.groups()
    hours = int(hours) if hours else 0
    minutes = int(minutes) if minutes else 0
    seconds = int(seconds) if seconds else 0

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


# =============================================================================
# CONNECTION
# =============================================================================

@mcp.tool()
async def test_connection() -> Dict[str, Any]:
    """Test YouTube connection and get channel info."""
    try:
        service = get_youtube_service()
        response = service.channels().list(part='snippet,statistics', mine=True).execute()

        if response.get('items'):
            channel = response['items'][0]
            snippet = channel['snippet']
            stats = channel.get('statistics', {})

            return {
                "success": True,
                "channel_id": channel['id'],
                "channel_title": snippet.get('title'),
                "subscriber_count": stats.get('subscriberCount'),
                "video_count": stats.get('videoCount'),
                "view_count": stats.get('viewCount')
            }
        return {"success": True, "message": "Authenticated but no channel found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# SEARCH
# =============================================================================

@mcp.tool()
async def search(
    query: str = Field(..., description="Search query"),
    max_results: int = Field(10, description="Maximum results (1-50)"),
    search_type: str = Field("video", description="Type: video, channel, playlist"),
    order: str = Field("relevance", description="Order: relevance, date, viewCount, rating")
) -> Dict[str, Any]:
    """Search YouTube for videos, channels, or playlists."""
    try:
        service = get_youtube_service()

        response = service.search().list(
            part='snippet',
            q=query,
            type=search_type,
            maxResults=min(max_results, 50),
            order=order
        ).execute()

        results = []
        for item in response.get('items', []):
            snippet = item['snippet']
            result = {
                'title': snippet.get('title'),
                'description': snippet.get('description', '')[:200],
                'channel_title': snippet.get('channelTitle'),
                'published_at': snippet.get('publishedAt'),
                'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url')
            }

            if search_type == 'video':
                result['video_id'] = item['id'].get('videoId')
            elif search_type == 'channel':
                result['channel_id'] = item['id'].get('channelId')
            elif search_type == 'playlist':
                result['playlist_id'] = item['id'].get('playlistId')

            results.append(result)

        return {"success": True, "query": query, "count": len(results), "results": results}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def search_music(
    query: str = Field(..., description="Search query"),
    max_results: int = Field(10, description="Maximum results")
) -> Dict[str, Any]:
    """Search for music videos specifically."""
    try:
        service = get_youtube_service()

        response = service.search().list(
            part='snippet',
            q=query,
            type='video',
            videoCategoryId='10',  # Music category
            maxResults=min(max_results, 50)
        ).execute()

        results = []
        for item in response.get('items', []):
            snippet = item['snippet']
            results.append({
                'video_id': item['id'].get('videoId'),
                'title': snippet.get('title'),
                'channel_title': snippet.get('channelTitle'),
                'published_at': snippet.get('publishedAt'),
                'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url')
            })

        return {"success": True, "query": query, "count": len(results), "results": results}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# PLAYLISTS
# =============================================================================

@mcp.tool()
async def get_my_playlists() -> Dict[str, Any]:
    """Get all your playlists."""
    try:
        service = get_youtube_service()

        playlists = []
        next_page = None

        while True:
            response = service.playlists().list(
                part='snippet,contentDetails',
                mine=True,
                maxResults=50,
                pageToken=next_page
            ).execute()

            for item in response.get('items', []):
                snippet = item['snippet']
                playlists.append({
                    'id': item['id'],
                    'title': snippet.get('title'),
                    'description': snippet.get('description', '')[:200],
                    'video_count': item.get('contentDetails', {}).get('itemCount', 0)
                })

            next_page = response.get('nextPageToken')
            if not next_page:
                break

        return {"success": True, "count": len(playlists), "playlists": playlists}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def create_playlist(
    title: str = Field(..., description="Playlist title"),
    description: str = Field("", description="Playlist description"),
    privacy: str = Field("private", description="Privacy: private, unlisted, public")
) -> Dict[str, Any]:
    """Create a new playlist."""
    try:
        service = get_youtube_service()

        body = {
            'snippet': {'title': title, 'description': description},
            'status': {'privacyStatus': privacy}
        }

        playlist = service.playlists().insert(part='snippet,status', body=body).execute()

        return {
            "success": True,
            "playlist_id": playlist['id'],
            "title": playlist['snippet']['title'],
            "privacy": playlist['status']['privacyStatus']
        }
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def delete_playlist(playlist_id: str = Field(..., description="Playlist ID")) -> Dict[str, Any]:
    """Delete a playlist."""
    try:
        service = get_youtube_service()
        service.playlists().delete(id=playlist_id).execute()
        return {"success": True, "playlist_id": playlist_id, "action": "deleted"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_playlist_videos(
    playlist_id: str = Field(..., description="Playlist ID"),
    max_results: int = Field(50, description="Maximum videos")
) -> Dict[str, Any]:
    """Get videos in a playlist."""
    try:
        service = get_youtube_service()

        videos = []
        next_page = None

        while len(videos) < max_results:
            response = service.playlistItems().list(
                part='snippet,contentDetails',
                playlistId=playlist_id,
                maxResults=min(50, max_results - len(videos)),
                pageToken=next_page
            ).execute()

            for item in response.get('items', []):
                snippet = item['snippet']
                videos.append({
                    'video_id': snippet.get('resourceId', {}).get('videoId'),
                    'title': snippet.get('title'),
                    'channel_title': snippet.get('videoOwnerChannelTitle'),
                    'position': snippet.get('position'),
                    'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url')
                })

            next_page = response.get('nextPageToken')
            if not next_page:
                break

        return {"success": True, "playlist_id": playlist_id, "count": len(videos), "videos": videos}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def add_to_playlist(
    playlist_id: str = Field(..., description="Playlist ID"),
    video_id: str = Field(..., description="Video ID to add")
) -> Dict[str, Any]:
    """Add a video to a playlist."""
    try:
        service = get_youtube_service()

        body = {
            'snippet': {
                'playlistId': playlist_id,
                'resourceId': {'kind': 'youtube#video', 'videoId': video_id}
            }
        }

        item = service.playlistItems().insert(part='snippet', body=body).execute()

        return {"success": True, "playlist_id": playlist_id, "video_id": video_id, "item_id": item['id']}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def remove_from_playlist(
    playlist_id: str = Field(..., description="Playlist ID"),
    video_id: str = Field(..., description="Video ID to remove")
) -> Dict[str, Any]:
    """Remove a video from a playlist."""
    try:
        service = get_youtube_service()

        response = service.playlistItems().list(
            part='id,snippet',
            playlistId=playlist_id,
            videoId=video_id
        ).execute()

        items = response.get('items', [])
        if not items:
            return {"success": False, "error": "Video not found in playlist"}

        service.playlistItems().delete(id=items[0]['id']).execute()

        return {"success": True, "playlist_id": playlist_id, "video_id": video_id, "action": "removed"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# VIDEOS
# =============================================================================

@mcp.tool()
async def get_video(video_id: str = Field(..., description="Video ID or URL")) -> Dict[str, Any]:
    """Get detailed information about a video."""
    try:
        service = get_youtube_service()
        video_id = extract_video_id(video_id)

        response = service.videos().list(part='snippet,contentDetails,statistics', id=video_id).execute()

        items = response.get('items', [])
        if not items:
            return {"success": False, "error": "Video not found"}

        video = items[0]
        snippet = video['snippet']
        stats = video.get('statistics', {})
        content = video.get('contentDetails', {})

        return {
            "success": True,
            "video_id": video_id,
            "title": snippet.get('title'),
            "description": snippet.get('description', '')[:500],
            "channel_title": snippet.get('channelTitle'),
            "channel_id": snippet.get('channelId'),
            "published_at": snippet.get('publishedAt'),
            "duration": format_duration(content.get('duration', '')),
            "view_count": stats.get('viewCount'),
            "like_count": stats.get('likeCount'),
            "comment_count": stats.get('commentCount'),
            "tags": snippet.get('tags', [])[:10],
            "thumbnail": snippet.get('thumbnails', {}).get('high', {}).get('url')
        }
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_video_comments(
    video_id: str = Field(..., description="Video ID or URL"),
    max_results: int = Field(20, description="Maximum comments")
) -> Dict[str, Any]:
    """Get comments on a video."""
    try:
        service = get_youtube_service()
        video_id = extract_video_id(video_id)

        response = service.commentThreads().list(
            part='snippet',
            videoId=video_id,
            maxResults=min(max_results, 100),
            order='relevance'
        ).execute()

        comments = []
        for item in response.get('items', []):
            comment = item['snippet']['topLevelComment']['snippet']
            comments.append({
                'comment_id': item['id'],
                'author': comment.get('authorDisplayName'),
                'text': comment.get('textDisplay', '')[:500],
                'like_count': comment.get('likeCount', 0),
                'published_at': comment.get('publishedAt')
            })

        return {"success": True, "video_id": video_id, "count": len(comments), "comments": comments}
    except HttpError as e:
        if 'commentsDisabled' in str(e):
            return {"success": False, "error": "Comments are disabled for this video"}
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# TRANSCRIPTS
# =============================================================================

@mcp.tool()
async def get_transcript(
    video: str = Field(..., description="Video ID or URL"),
    include_timestamps: bool = Field(False, description="Include timestamps")
) -> Dict[str, Any]:
    """Get video transcript/captions."""
    if not TRANSCRIPT_AVAILABLE:
        return {"success": False, "error": "youtube-transcript-api not installed. Run: pip install youtube-transcript-api"}

    try:
        video_id = extract_video_id(video)

        CACHE_DIR.mkdir(exist_ok=True)
        cache_file = CACHE_DIR / f"{video_id}.json"

        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                transcript_data = json.load(f)['transcript']
        else:
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id)
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({'transcript': transcript_data, 'video_id': video_id}, f)

        if include_timestamps:
            transcript = transcript_data
        else:
            transcript = ' '.join([t['text'] for t in transcript_data])

        return {
            "success": True,
            "video_id": video_id,
            "transcript": transcript,
            "segment_count": len(transcript_data) if isinstance(transcript_data, list) else None
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def search_transcript(
    video: str = Field(..., description="Video ID or URL"),
    query: str = Field(..., description="Text to search for"),
    context_seconds: float = Field(30, description="Seconds of context around match")
) -> Dict[str, Any]:
    """Search within a video transcript."""
    if not TRANSCRIPT_AVAILABLE:
        return {"success": False, "error": "youtube-transcript-api not installed"}

    try:
        video_id = extract_video_id(video)

        result = await get_transcript(video=video, include_timestamps=True)
        if not result.get('success'):
            return result

        transcript = result['transcript']
        query_lower = query.lower()
        matches = []

        for segment in transcript:
            if query_lower in segment['text'].lower():
                start_time = max(0, segment['start'] - context_seconds)
                end_time = segment['start'] + segment['duration'] + context_seconds

                context_segments = [
                    s for s in transcript
                    if s['start'] >= start_time and s['start'] <= end_time
                ]

                matches.append({
                    'timestamp': segment['start'],
                    'timestamp_formatted': f"{int(segment['start']//60)}:{int(segment['start']%60):02d}",
                    'text': segment['text'],
                    'context': ' '.join([s['text'] for s in context_segments])
                })

        return {
            "success": True,
            "video_id": video_id,
            "query": query,
            "match_count": len(matches),
            "matches": matches[:20]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# RATINGS & LIKES
# =============================================================================

@mcp.tool()
async def get_liked_videos(max_results: int = Field(50, description="Maximum videos")) -> Dict[str, Any]:
    """Get your liked videos."""
    try:
        service = get_youtube_service()

        videos = []
        next_page = None

        while len(videos) < max_results:
            response = service.videos().list(
                part='snippet',
                myRating='like',
                maxResults=min(50, max_results - len(videos)),
                pageToken=next_page
            ).execute()

            for item in response.get('items', []):
                snippet = item['snippet']
                videos.append({
                    'video_id': item['id'],
                    'title': snippet.get('title'),
                    'channel_title': snippet.get('channelTitle'),
                    'published_at': snippet.get('publishedAt')
                })

            next_page = response.get('nextPageToken')
            if not next_page:
                break

        return {"success": True, "count": len(videos), "videos": videos}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def like_video(video_id: str = Field(..., description="Video ID or URL")) -> Dict[str, Any]:
    """Like a video."""
    try:
        service = get_youtube_service()
        video_id = extract_video_id(video_id)
        service.videos().rate(id=video_id, rating='like').execute()
        return {"success": True, "video_id": video_id, "rating": "like"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def dislike_video(video_id: str = Field(..., description="Video ID or URL")) -> Dict[str, Any]:
    """Dislike a video."""
    try:
        service = get_youtube_service()
        video_id = extract_video_id(video_id)
        service.videos().rate(id=video_id, rating='dislike').execute()
        return {"success": True, "video_id": video_id, "rating": "dislike"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def remove_rating(video_id: str = Field(..., description="Video ID or URL")) -> Dict[str, Any]:
    """Remove like/dislike rating from a video."""
    try:
        service = get_youtube_service()
        video_id = extract_video_id(video_id)
        service.videos().rate(id=video_id, rating='none').execute()
        return {"success": True, "video_id": video_id, "rating": "none"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# SUBSCRIPTIONS
# =============================================================================

@mcp.tool()
async def get_subscriptions(max_results: int = Field(50, description="Maximum results")) -> Dict[str, Any]:
    """Get your subscribed channels."""
    try:
        service = get_youtube_service()

        subscriptions = []
        next_page = None

        while len(subscriptions) < max_results:
            response = service.subscriptions().list(
                part='snippet',
                mine=True,
                maxResults=min(50, max_results - len(subscriptions)),
                pageToken=next_page
            ).execute()

            for item in response.get('items', []):
                snippet = item['snippet']
                subscriptions.append({
                    'subscription_id': item['id'],
                    'channel_id': snippet.get('resourceId', {}).get('channelId'),
                    'channel_title': snippet.get('title'),
                    'description': snippet.get('description', '')[:200]
                })

            next_page = response.get('nextPageToken')
            if not next_page:
                break

        return {"success": True, "count": len(subscriptions), "subscriptions": subscriptions}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def subscribe(channel_id: str = Field(..., description="Channel ID")) -> Dict[str, Any]:
    """Subscribe to a channel."""
    try:
        service = get_youtube_service()

        body = {'snippet': {'resourceId': {'kind': 'youtube#channel', 'channelId': channel_id}}}
        subscription = service.subscriptions().insert(part='snippet', body=body).execute()

        return {
            "success": True,
            "subscription_id": subscription['id'],
            "channel_id": channel_id,
            "channel_title": subscription['snippet'].get('title')
        }
    except HttpError as e:
        if 'subscriptionDuplicate' in str(e):
            return {"success": False, "error": "Already subscribed to this channel"}
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def unsubscribe(subscription_id: str = Field(..., description="Subscription ID")) -> Dict[str, Any]:
    """Unsubscribe from a channel."""
    try:
        service = get_youtube_service()
        service.subscriptions().delete(id=subscription_id).execute()
        return {"success": True, "subscription_id": subscription_id, "action": "unsubscribed"}
    except HttpError as e:
        return {"success": False, "error": f"YouTube API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
