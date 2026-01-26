# YouTube MCP Server

Let Claude access YouTube! Search videos, manage playlists, get transcripts, and more.

## What It Does

- **Search** - Find videos, channels, and playlists
- **Playlists** - Create, manage, add/remove videos
- **Videos** - Get info, comments, transcripts
- **Ratings** - Like, dislike, view liked videos
- **Subscriptions** - View and manage subscribed channels

---

## Setup (One-Time)

### Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable the **YouTube Data API v3**

### Step 2: Create OAuth Credentials

1. Go to APIs & Services > Credentials
2. Click **Create Credentials > OAuth client ID**
3. Configure OAuth consent screen if prompted
4. Application type: **Desktop app**
5. Download the JSON file
6. Create an `auth` folder and save as `auth/client_secret.json`

### Step 3: Install & Authenticate

```bash
pip install -r requirements.txt
python setup.py
```

### Step 4: Run the Server

```bash
python run_server.py
```

---

## Available Tools

### Search
| Tool | Description |
|------|-------------|
| `test_connection` | Check connection and channel info |
| `search` | Search videos, channels, playlists |
| `search_music` | Search music videos specifically |

### Playlists
| Tool | Description |
|------|-------------|
| `get_my_playlists` | List your playlists |
| `create_playlist` | Create new playlist |
| `delete_playlist` | Delete a playlist |
| `get_playlist_videos` | Get videos in playlist |
| `add_to_playlist` | Add video to playlist |
| `remove_from_playlist` | Remove from playlist |

### Videos
| Tool | Description |
|------|-------------|
| `get_video` | Get video details |
| `get_video_comments` | Get video comments |

### Transcripts
| Tool | Description |
|------|-------------|
| `get_transcript` | Get video transcript/captions |
| `search_transcript` | Search within transcript |

### Ratings
| Tool | Description |
|------|-------------|
| `get_liked_videos` | Your liked videos |
| `like_video` | Like a video |
| `dislike_video` | Dislike a video |
| `remove_rating` | Remove rating |

### Subscriptions
| Tool | Description |
|------|-------------|
| `get_subscriptions` | Your subscribed channels |
| `subscribe` | Subscribe to channel |
| `unsubscribe` | Unsubscribe from channel |

---

## Example Usage

**"Search for relaxing music"**
```
Claude uses search_music(query="relaxing piano music")
```

**"Create a playlist for my workout songs"**
```
Claude uses create_playlist(title="Workout Mix", privacy="private")
```

**"What's this video about?"**
```
Claude uses get_transcript() to read the captions
and summarize the content
```

**"Find where they mention 'machine learning' in this video"**
```
Claude uses search_transcript() to find the exact
timestamps where that phrase appears
```

---

## Transcripts

The transcript feature uses `youtube-transcript-api` to fetch captions. This works for:
- Videos with auto-generated captions
- Videos with manual captions

Results are cached locally to avoid re-fetching.

---

## API Quota Notes

YouTube API has daily quota limits. Most operations cost 1-100 quota units:
- Search: ~100 units
- Video info: ~1 unit
- Playlist operations: ~50 units

The free tier provides 10,000 units/day, which is plenty for personal use.

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
