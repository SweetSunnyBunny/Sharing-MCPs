/**
 * YouTube tools — ported from youtube-multibot
 */

import { Env, googleGet as _googleGet, googlePost as _googlePost, googleDelete as _googleDelete, googlePut as _googlePut, validateIdentity, getIdentityList } from "./oauth.js";

const YT_API = "https://www.googleapis.com/youtube/v3";
const SERVICE = "youtube";

// Service-bound wrappers so every call uses the youtube refresh token
const googleGet = <T = unknown>(env: Env, identity: string, url: string) => _googleGet<T>(env, identity, url, SERVICE);
const googlePost = <T = unknown>(env: Env, identity: string, url: string, body: unknown) => _googlePost<T>(env, identity, url, body, SERVICE);
const googleDelete = (env: Env, identity: string, url: string) => _googleDelete(env, identity, url, SERVICE);
const googlePut = <T = unknown>(env: Env, identity: string, url: string, body: unknown) => _googlePut<T>(env, identity, url, body, SERVICE);

function qs(params: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) p.append(k, v);
  }
  return p.toString();
}

function extractVideoId(video: string): string {
  if (/^[a-zA-Z0-9_-]{11}$/.test(video)) return video;
  try {
    const url = new URL(video);
    if (url.hostname.includes("youtu.be")) return url.pathname.slice(1);
    return url.searchParams.get("v") || video;
  } catch {
    return video;
  }
}

function formatDuration(iso: string): string {
  const match = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
  if (!match) return iso;
  const h = match[1] ? `${match[1]}h ` : "";
  const m = match[2] ? `${match[2]}m ` : "";
  const s = match[3] ? `${match[3]}s` : "";
  return `${h}${m}${s}`.trim() || "0s";
}

// ============ Tool Definitions ============

export const YOUTUBE_TOOLS = [
  { name: "youtube_list_identities", description: "List available YouTube identities.", inputSchema: { type: "object" as const, properties: {}, required: [] as string[] } },
  { name: "youtube_test_connection", description: "Test YouTube connection.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const } }, required: ["identity"] } },
  { name: "youtube_get_my_channel", description: "Get your channel info.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const } }, required: ["identity"] } },
  { name: "youtube_search", description: "Search YouTube videos.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, query: { type: "string" as const }, max_results: { type: "number" as const }, type: { type: "string" as const } }, required: ["identity", "query"] } },
  { name: "youtube_search_music", description: "Search YouTube Music.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, query: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity", "query"] } },
  { name: "youtube_get_video", description: "Get video details.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "video"] } },
  { name: "youtube_get_video_comments", description: "Get comments on a video.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity", "video"] } },
  { name: "youtube_get_transcript", description: "Get video transcript/captions.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "video"] } },
  { name: "youtube_search_transcript", description: "Search within a video transcript.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const }, query: { type: "string" as const } }, required: ["identity", "video", "query"] } },
  { name: "youtube_get_my_playlists", description: "Get your playlists.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity"] } },
  { name: "youtube_create_playlist", description: "Create a new playlist.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, title: { type: "string" as const }, description: { type: "string" as const }, privacy: { type: "string" as const } }, required: ["identity", "title"] } },
  { name: "youtube_delete_playlist", description: "Delete a playlist.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, playlist_id: { type: "string" as const } }, required: ["identity", "playlist_id"] } },
  { name: "youtube_get_playlist_videos", description: "Get videos in a playlist.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, playlist_id: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity", "playlist_id"] } },
  { name: "youtube_add_to_playlist", description: "Add a video to a playlist.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, playlist_id: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "playlist_id", "video"] } },
  { name: "youtube_remove_from_playlist", description: "Remove a video from a playlist.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, playlist_item_id: { type: "string" as const } }, required: ["identity", "playlist_item_id"] } },
  { name: "youtube_get_subscriptions", description: "Get your subscriptions.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity"] } },
  { name: "youtube_subscribe", description: "Subscribe to a channel.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, channel_id: { type: "string" as const } }, required: ["identity", "channel_id"] } },
  { name: "youtube_unsubscribe", description: "Unsubscribe from a channel.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, subscription_id: { type: "string" as const } }, required: ["identity", "subscription_id"] } },
  { name: "youtube_get_liked_videos", description: "Get your liked videos.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity"] } },
  { name: "youtube_like_video", description: "Like a video.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "video"] } },
  { name: "youtube_dislike_video", description: "Dislike a video.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "video"] } },
  { name: "youtube_remove_rating", description: "Remove rating from a video.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const } }, required: ["identity", "video"] } },
  { name: "youtube_post_comment", description: "Post a comment on a video.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, video: { type: "string" as const }, text: { type: "string" as const } }, required: ["identity", "video", "text"] } },
  { name: "youtube_reply_to_comment", description: "Reply to a comment.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, comment_id: { type: "string" as const }, text: { type: "string" as const } }, required: ["identity", "comment_id", "text"] } },
];

// ============ Handler ============

export async function handleYouTube(env: Env, name: string, args: Record<string, unknown>): Promise<string> {
  const identity = typeof args.identity === "string" ? validateIdentity(env, args.identity) : "";

  switch (name) {
    case "youtube_list_identities":
      return JSON.stringify({ identities: getIdentityList(env) });

    case "youtube_test_connection": {
      const result = await googleGet<{ items: Array<{ snippet: { title: string } }> }>(env, identity, `${YT_API}/channels?part=snippet&mine=true`);
      const channel = result.items?.[0];
      return JSON.stringify({ identity, channel: channel?.snippet?.title || "unknown", status: "connected" });
    }

    case "youtube_get_my_channel": {
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/channels?part=snippet,statistics,contentDetails&mine=true`);
      return JSON.stringify({ identity, channel: result.items?.[0] || null });
    }

    case "youtube_search": {
      const maxResults = Math.min(Number(args.max_results) || 10, 50);
      const type = typeof args.type === "string" ? args.type : "video";
      const params = qs({ part: "snippet", q: String(args.query), maxResults: String(maxResults), type });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/search?${params}`);
      return JSON.stringify({ identity, query: args.query, count: (result.items || []).length, results: result.items });
    }

    case "youtube_search_music": {
      const maxResults = Math.min(Number(args.max_results) || 10, 50);
      const params = qs({ part: "snippet", q: String(args.query), maxResults: String(maxResults), type: "video", videoCategoryId: "10" });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/search?${params}`);
      return JSON.stringify({ identity, query: args.query, count: (result.items || []).length, results: result.items });
    }

    case "youtube_get_video": {
      const videoId = extractVideoId(String(args.video));
      const result = await googleGet<{ items: Array<{ snippet: unknown; statistics: unknown; contentDetails: { duration: string } }> }>(
        env, identity, `${YT_API}/videos?part=snippet,statistics,contentDetails&id=${videoId}`,
      );
      const video = result.items?.[0];
      if (!video) return JSON.stringify({ error: "Video not found" });
      return JSON.stringify({ identity, video_id: videoId, duration: formatDuration(video.contentDetails.duration), ...video });
    }

    case "youtube_get_video_comments": {
      const videoId = extractVideoId(String(args.video));
      const maxResults = Math.min(Number(args.max_results) || 20, 100);
      const params = qs({ part: "snippet", videoId, maxResults: String(maxResults), order: "relevance" });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/commentThreads?${params}`);
      return JSON.stringify({ identity, video_id: videoId, count: (result.items || []).length, comments: result.items });
    }

    case "youtube_get_transcript": {
      const videoId = extractVideoId(String(args.video));
      // List available captions
      const captions = await googleGet<{ items: Array<{ id: string; snippet: { language: string; trackKind: string } }> }>(
        env, identity, `${YT_API}/captions?part=snippet&videoId=${videoId}`,
      );
      if (!captions.items?.length) return JSON.stringify({ identity, video_id: videoId, error: "No captions available" });
      // Try to get English caption
      const enCaption = captions.items.find((c) => c.snippet.language === "en") || captions.items[0];
      return JSON.stringify({ identity, video_id: videoId, caption_id: enCaption.id, language: enCaption.snippet.language, note: "Use youtube_search_transcript to search within captions, or the Watch Videos MCP for full transcript." });
    }

    case "youtube_search_transcript": {
      // YouTube Data API doesn't directly support transcript text search
      // This returns available caption info; full transcript needs the timedtext API
      const videoId = extractVideoId(String(args.video));
      return JSON.stringify({ identity, video_id: videoId, query: args.query, note: "Transcript text search requires caption download. Use the Watch Videos MCP for full transcript access." });
    }

    case "youtube_get_my_playlists": {
      const maxResults = Math.min(Number(args.max_results) || 25, 50);
      const params = qs({ part: "snippet,contentDetails", mine: "true", maxResults: String(maxResults) });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/playlists?${params}`);
      return JSON.stringify({ identity, count: (result.items || []).length, playlists: result.items });
    }

    case "youtube_create_playlist": {
      const privacy = typeof args.privacy === "string" ? args.privacy : "private";
      const playlist = await googlePost(env, identity, `${YT_API}/playlists?part=snippet,status`, {
        snippet: { title: String(args.title), description: args.description || "" },
        status: { privacyStatus: privacy },
      });
      return JSON.stringify({ identity, created: true, playlist });
    }

    case "youtube_delete_playlist": {
      await googleDelete(env, identity, `${YT_API}/playlists?id=${args.playlist_id}`);
      return JSON.stringify({ identity, playlist_id: args.playlist_id, deleted: true });
    }

    case "youtube_get_playlist_videos": {
      const maxResults = Math.min(Number(args.max_results) || 25, 50);
      const params = qs({ part: "snippet,contentDetails", playlistId: String(args.playlist_id), maxResults: String(maxResults) });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/playlistItems?${params}`);
      return JSON.stringify({ identity, playlist_id: args.playlist_id, count: (result.items || []).length, videos: result.items });
    }

    case "youtube_add_to_playlist": {
      const videoId = extractVideoId(String(args.video));
      const item = await googlePost(env, identity, `${YT_API}/playlistItems?part=snippet`, {
        snippet: { playlistId: String(args.playlist_id), resourceId: { kind: "youtube#video", videoId } },
      });
      return JSON.stringify({ identity, added: true, item });
    }

    case "youtube_remove_from_playlist": {
      await googleDelete(env, identity, `${YT_API}/playlistItems?id=${args.playlist_item_id}`);
      return JSON.stringify({ identity, playlist_item_id: args.playlist_item_id, removed: true });
    }

    case "youtube_get_subscriptions": {
      const maxResults = Math.min(Number(args.max_results) || 25, 50);
      const params = qs({ part: "snippet", mine: "true", maxResults: String(maxResults), order: "alphabetical" });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/subscriptions?${params}`);
      return JSON.stringify({ identity, count: (result.items || []).length, subscriptions: result.items });
    }

    case "youtube_subscribe": {
      const sub = await googlePost(env, identity, `${YT_API}/subscriptions?part=snippet`, {
        snippet: { resourceId: { kind: "youtube#channel", channelId: String(args.channel_id) } },
      });
      return JSON.stringify({ identity, subscribed: true, subscription: sub });
    }

    case "youtube_unsubscribe": {
      await googleDelete(env, identity, `${YT_API}/subscriptions?id=${args.subscription_id}`);
      return JSON.stringify({ identity, subscription_id: args.subscription_id, unsubscribed: true });
    }

    case "youtube_get_liked_videos": {
      const maxResults = Math.min(Number(args.max_results) || 25, 50);
      const params = qs({ part: "snippet,contentDetails", myRating: "like", maxResults: String(maxResults) });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${YT_API}/videos?${params}`);
      return JSON.stringify({ identity, count: (result.items || []).length, videos: result.items });
    }

    case "youtube_like_video": {
      const videoId = extractVideoId(String(args.video));
      await googlePost(env, identity, `${YT_API}/videos/rate?id=${videoId}&rating=like`, {});
      return JSON.stringify({ identity, video_id: videoId, liked: true });
    }

    case "youtube_dislike_video": {
      const videoId = extractVideoId(String(args.video));
      await googlePost(env, identity, `${YT_API}/videos/rate?id=${videoId}&rating=dislike`, {});
      return JSON.stringify({ identity, video_id: videoId, disliked: true });
    }

    case "youtube_remove_rating": {
      const videoId = extractVideoId(String(args.video));
      await googlePost(env, identity, `${YT_API}/videos/rate?id=${videoId}&rating=none`, {});
      return JSON.stringify({ identity, video_id: videoId, rating_removed: true });
    }

    case "youtube_post_comment": {
      const videoId = extractVideoId(String(args.video));
      const comment = await googlePost(env, identity, `${YT_API}/commentThreads?part=snippet`, {
        snippet: { videoId, topLevelComment: { snippet: { textOriginal: String(args.text) } } },
      });
      return JSON.stringify({ identity, video_id: videoId, posted: true, comment });
    }

    case "youtube_reply_to_comment": {
      const reply = await googlePost(env, identity, `${YT_API}/comments?part=snippet`, {
        snippet: { parentId: String(args.comment_id), textOriginal: String(args.text) },
      });
      return JSON.stringify({ identity, comment_id: args.comment_id, replied: true, reply });
    }

    default:
      throw new Error(`Unknown YouTube tool: ${name}`);
  }
}
