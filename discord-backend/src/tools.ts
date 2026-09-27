// Discord MCP Tool definitions and REST API handlers

import { discordFetch, discordFetchMultipart, discordWebhookFetch, formatMessage } from './discord';
import { imageResult, messageImageResult, MAX_IMAGE_BYTES, type DiscordToolResult } from './image-content';
import { getIdentityDisplayName, getIdentityVoice, listIdentityNames, resolvePersona } from './identities';
import { archiveGeneratedImage } from './image-archive';

const OPENAI_IMAGE_MODEL = 'gpt-image-2.5-flare';

// ============ TOOL DEFINITIONS ============

// The exposed MCP surface is determined at runtime from the ALLOWED_TOOLS env
// var (see wrangler.toml). The full tool catalog is ALL_DISCORD_TOOLS below;
// what's actually advertised and callable is filtered per request via
// parseAllowedTools() + getActiveDiscordTools().

export const ALL_DISCORD_TOOLS = [
  // === MESSAGING ===
  {
    name: 'discord_read_messages',
    description: 'Read message history from a Discord channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID to read from' },
        limit: { type: 'number', description: 'Number of messages to fetch (1-100)', default: 50 },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_read_dm_messages',
    description: 'Read DM message history with a user',
    inputSchema: {
      type: 'object' as const,
      properties: {
        user_id: { type: 'string', description: 'The user ID to read DMs from' },
        limit: { type: 'number', description: 'Number of messages to fetch (1-100)', default: 50 },
      },
      required: ['user_id'],
    },
  },
  {
    name: 'discord_send_message',
    description: 'Send a message to a Discord channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID to send to' },
        content: { type: 'string', description: 'The message content' },
        reply_to: { type: 'string', description: 'Message ID to reply to (optional)' },
      },
      required: ['channel_id', 'content'],
    },
  },
  {
    name: 'discord_send_dm',
    description: 'Send a direct message to a user',
    inputSchema: {
      type: 'object' as const,
      properties: {
        user_id: { type: 'string', description: 'The user ID to DM' },
        content: { type: 'string', description: 'The message content' },
      },
      required: ['user_id', 'content'],
    },
  },
  {
    name: 'discord_edit_message',
    description: 'Edit a message sent by the bot',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID to edit' },
        new_content: { type: 'string', description: 'The new message content' },
      },
      required: ['channel_id', 'message_id', 'new_content'],
    },
  },
  {
    name: 'discord_delete_message',
    description: 'Delete a Discord message',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID to delete' },
      },
      required: ['channel_id', 'message_id'],
    },
  },
  {
    name: 'discord_bulk_delete_messages',
    description: 'Bulk delete messages (2-100, must be <14 days old). Requires MANAGE_MESSAGES.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_ids: { type: 'array', items: { type: 'string' }, description: 'Array of message IDs to delete (2-100)' },
      },
      required: ['channel_id', 'message_ids'],
    },
  },
  {
    name: 'discord_send_embed',
    description: 'Send a rich embed message to a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        title: { type: 'string', description: 'Embed title' },
        description: { type: 'string', description: 'Embed description (supports markdown)' },
        color: { type: 'string', description: 'Hex color (e.g., #FF0000)' },
        fields: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, value: { type: 'string' }, inline: { type: 'boolean' } }, required: ['name', 'value'] }, description: 'Embed fields' },
        footer: { type: 'string', description: 'Footer text' },
        thumbnail_url: { type: 'string', description: 'Thumbnail image URL' },
        image_url: { type: 'string', description: 'Large image URL' },
        url: { type: 'string', description: 'URL the title links to' },
        content: { type: 'string', description: 'Optional text content outside the embed' },
      },
      required: ['channel_id'],
    },
  },

  // === REACTIONS ===
  {
    name: 'discord_add_reaction',
    description: 'Add an emoji reaction to a message',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID' },
        emoji: { type: 'string', description: 'The emoji (Unicode or custom format <:name:id>)' },
      },
      required: ['channel_id', 'message_id', 'emoji'],
    },
  },
  {
    name: 'discord_remove_reaction',
    description: 'Remove a reaction from a message. Omit user_id to remove the bot\'s own reaction.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID' },
        emoji: { type: 'string', description: 'The emoji (Unicode or custom format <:name:id>)' },
        user_id: { type: 'string', description: 'User ID to remove reaction for (omit for bot\'s own)' },
      },
      required: ['channel_id', 'message_id', 'emoji'],
    },
  },
  {
    name: 'discord_get_message_reactions',
    description: 'Get all reactions on a message',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID' },
      },
      required: ['channel_id', 'message_id'],
    },
  },

  // === PINS ===
  {
    name: 'discord_pin_message',
    description: 'Pin a message to a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID to pin' },
      },
      required: ['channel_id', 'message_id'],
    },
  },
  {
    name: 'discord_unpin_message',
    description: 'Unpin a message from a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID to unpin' },
      },
      required: ['channel_id', 'message_id'],
    },
  },
  {
    name: 'discord_get_pinned_messages',
    description: 'Get all pinned messages in a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
      },
      required: ['channel_id'],
    },
  },

  // === CHANNELS ===
  {
    name: 'discord_list_channels',
    description: 'List all channels in a guild/server',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild/server ID' },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_list_servers',
    description: 'List all Discord servers the bot is in',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_create_channel',
    description: 'Create a new channel in a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        name: { type: 'string', description: 'Channel name' },
        type: { type: 'string', description: 'Channel type: text, voice, category, forum', default: 'text' },
        category_id: { type: 'string', description: 'Parent category ID (optional)' },
        topic: { type: 'string', description: 'Channel topic (optional)' },
      },
      required: ['guild_id', 'name'],
    },
  },
  {
    name: 'discord_edit_channel',
    description: 'Edit channel properties (name, topic, NSFW, etc.)',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        name: { type: 'string', description: 'New channel name' },
        topic: { type: 'string', description: 'New channel topic' },
        nsfw: { type: 'boolean', description: 'Set NSFW flag' },
        position: { type: 'number', description: 'Channel position' },
        parent_id: { type: 'string', description: 'Move to category (category ID or null to remove)' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_delete_channel',
    description: 'Delete a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID to delete' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_set_slowmode',
    description: 'Set slowmode rate limit for a channel (0 to disable)',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        seconds: { type: 'number', description: 'Slowmode delay in seconds (0-21600). 0 = disabled.' },
      },
      required: ['channel_id', 'seconds'],
    },
  },
  {
    name: 'discord_set_channel_permissions',
    description: 'Set permission overwrites for a role or user on a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        overwrite_id: { type: 'string', description: 'The role or user ID' },
        type: { type: 'number', description: '0 = role, 1 = member' },
        allow: { type: 'string', description: 'Bitwise permission flags to allow (as string)' },
        deny: { type: 'string', description: 'Bitwise permission flags to deny (as string)' },
      },
      required: ['channel_id', 'overwrite_id', 'type'],
    },
  },

  // === THREADS ===
  {
    name: 'discord_create_thread',
    description: 'Create a thread in a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        name: { type: 'string', description: 'Thread name' },
        message_id: { type: 'string', description: 'Message ID to create thread from (optional)' },
        auto_archive_duration: { type: 'number', description: 'Auto-archive after minutes: 60, 1440, 4320, 10080', default: 1440 },
      },
      required: ['channel_id', 'name'],
    },
  },
  {
    name: 'discord_manage_thread',
    description: 'Archive/unarchive or lock/unlock a thread',
    inputSchema: {
      type: 'object' as const,
      properties: {
        thread_id: { type: 'string', description: 'The thread ID' },
        archived: { type: 'boolean', description: 'Set archived state' },
        locked: { type: 'boolean', description: 'Set locked state' },
      },
      required: ['thread_id'],
    },
  },
  {
    name: 'discord_delete_thread',
    description: 'Delete a thread',
    inputSchema: {
      type: 'object' as const,
      properties: {
        thread_id: { type: 'string', description: 'The thread ID to delete' },
      },
      required: ['thread_id'],
    },
  },

  // === FORUM ===
  {
    name: 'discord_create_forum_post',
    description: 'Create a post in a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        name: { type: 'string', description: 'Post title' },
        content: { type: 'string', description: 'Initial message content' },
        applied_tags: { type: 'array', items: { type: 'string' }, description: 'Array of tag IDs to apply' },
      },
      required: ['channel_id', 'name', 'content'],
    },
  },
  {
    name: 'discord_edit_forum_post',
    description: 'Edit a forum post (rename, archive, lock, pin, change tags)',
    inputSchema: {
      type: 'object' as const,
      properties: {
        thread_id: { type: 'string', description: 'The forum post/thread ID' },
        name: { type: 'string', description: 'New post title' },
        archived: { type: 'boolean', description: 'Set archived state' },
        locked: { type: 'boolean', description: 'Set locked state' },
        pinned: { type: 'boolean', description: 'Pin/unpin the post in the forum' },
        applied_tags: { type: 'array', items: { type: 'string' }, description: 'Replace tags (array of tag IDs)' },
        auto_archive_duration: { type: 'number', description: 'Auto-archive minutes: 60, 1440, 4320, 10080' },
      },
      required: ['thread_id'],
    },
  },
  {
    name: 'discord_list_forum_posts',
    description: 'List active threads/posts in a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_list_archived_forum_posts',
    description: 'List archived (closed) threads/posts in a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        limit: { type: 'number', description: 'Max posts to return (1-100)', default: 25 },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_get_forum_tags',
    description: 'Get available tags for a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_create_forum_tag',
    description: 'Add a new tag to a forum channel (max 20 tags)',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        name: { type: 'string', description: 'Tag name' },
        emoji_name: { type: 'string', description: 'Unicode emoji for the tag (optional)' },
        emoji_id: { type: 'string', description: 'Custom emoji ID for the tag (optional)' },
        moderated: { type: 'boolean', description: 'Only moderators can apply this tag', default: false },
      },
      required: ['channel_id', 'name'],
    },
  },
  {
    name: 'discord_edit_forum_tag',
    description: 'Edit an existing tag on a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        tag_id: { type: 'string', description: 'The tag ID to edit' },
        name: { type: 'string', description: 'New tag name' },
        emoji_name: { type: 'string', description: 'New Unicode emoji' },
        emoji_id: { type: 'string', description: 'New custom emoji ID' },
        moderated: { type: 'boolean', description: 'Only moderators can apply' },
      },
      required: ['channel_id', 'tag_id'],
    },
  },
  {
    name: 'discord_delete_forum_tag',
    description: 'Delete a tag from a forum channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        tag_id: { type: 'string', description: 'The tag ID to delete' },
      },
      required: ['channel_id', 'tag_id'],
    },
  },
  {
    name: 'discord_set_forum_default_reaction',
    description: 'Set the default reaction emoji for new forum posts',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        emoji_name: { type: 'string', description: 'Unicode emoji (optional)' },
        emoji_id: { type: 'string', description: 'Custom emoji ID (optional, set both to null to clear)' },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_set_forum_settings',
    description: 'Configure forum channel settings (layout, sort order, post guidelines)',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The forum channel ID' },
        topic: { type: 'string', description: 'Forum guidelines/topic text' },
        default_sort_order: { type: 'number', description: '0 = latest activity, 1 = creation date' },
        default_forum_layout: { type: 'number', description: '0 = not set, 1 = list view, 2 = gallery view' },
        default_auto_archive_duration: { type: 'number', description: 'Default auto-archive minutes: 60, 1440, 4320, 10080' },
        default_thread_rate_limit: { type: 'number', description: 'Default slowmode for new posts (seconds, 0-21600)' },
        require_tag: { type: 'boolean', description: 'Require a tag when creating posts' },
      },
      required: ['channel_id'],
    },
  },

  // === ROLES ===
  {
    name: 'discord_list_roles',
    description: 'List all roles in a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_create_role',
    description: 'Create a new role',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        name: { type: 'string', description: 'Role name' },
        color: { type: 'string', description: 'Role color (hex, e.g., #FF0000)' },
        hoist: { type: 'boolean', description: 'Display separately in member list', default: false },
        mentionable: { type: 'boolean', description: 'Allow mentioning this role', default: false },
      },
      required: ['guild_id', 'name'],
    },
  },
  {
    name: 'discord_edit_role',
    description: 'Edit a role',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        role_id: { type: 'string', description: 'The role ID' },
        name: { type: 'string', description: 'New role name' },
        color: { type: 'string', description: 'New color (hex)' },
      },
      required: ['guild_id', 'role_id'],
    },
  },
  {
    name: 'discord_delete_role',
    description: 'Delete a role',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        role_id: { type: 'string', description: 'The role ID to delete' },
      },
      required: ['guild_id', 'role_id'],
    },
  },
  {
    name: 'discord_assign_role',
    description: 'Assign a role to a member',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID' },
        role_id: { type: 'string', description: 'The role ID to assign' },
      },
      required: ['guild_id', 'user_id', 'role_id'],
    },
  },
  {
    name: 'discord_remove_role',
    description: 'Remove a role from a member',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID' },
        role_id: { type: 'string', description: 'The role ID to remove' },
      },
      required: ['guild_id', 'user_id', 'role_id'],
    },
  },
  {
    name: 'discord_get_member_roles',
    description: 'Get all roles of a member',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID' },
      },
      required: ['guild_id', 'user_id'],
    },
  },

  // === MEMBERS & MODERATION ===
  {
    name: 'discord_get_guild_members',
    description: 'Get list of members in a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        limit: { type: 'number', description: 'Max members to fetch (1-1000)', default: 100 },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_change_nickname',
    description: 'Change the bot nickname in a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        nickname: { type: 'string', description: 'New nickname (empty to reset)' },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_get_user_info',
    description: 'Get information about a Discord user',
    inputSchema: {
      type: 'object' as const,
      properties: {
        user_id: { type: 'string', description: 'The user ID' },
      },
      required: ['user_id'],
    },
  },
  {
    name: 'discord_kick_member',
    description: 'Kick a member from a guild. Requires KICK_MEMBERS permission.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID to kick' },
        reason: { type: 'string', description: 'Reason for the kick (shown in audit log)' },
      },
      required: ['guild_id', 'user_id'],
    },
  },
  {
    name: 'discord_ban_member',
    description: 'Ban a member from a guild. Requires BAN_MEMBERS permission.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID to ban' },
        reason: { type: 'string', description: 'Reason for the ban (shown in audit log)' },
        delete_message_seconds: { type: 'number', description: 'Delete messages from this user in the last N seconds (0-604800)', default: 0 },
      },
      required: ['guild_id', 'user_id'],
    },
  },
  {
    name: 'discord_unban_member',
    description: 'Unban a user from a guild. Requires BAN_MEMBERS permission.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID to unban' },
      },
      required: ['guild_id', 'user_id'],
    },
  },
  {
    name: 'discord_timeout_member',
    description: 'Timeout (mute) a member. Set duration to 0 to remove timeout.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        user_id: { type: 'string', description: 'The user ID' },
        duration_seconds: { type: 'number', description: 'Timeout duration in seconds (0 to remove, max 2419200 = 28 days)' },
        reason: { type: 'string', description: 'Reason for the timeout' },
      },
      required: ['guild_id', 'user_id', 'duration_seconds'],
    },
  },

  // === SERVER / GUILD ===
  {
    name: 'discord_get_guild_info',
    description: 'Get detailed information about a guild/server',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_get_audit_log',
    description: 'Get the audit log for a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
        action_type: { type: 'number', description: 'Filter by action type (optional)' },
        user_id: { type: 'string', description: 'Filter by user who performed action (optional)' },
        limit: { type: 'number', description: 'Number of entries (1-100)', default: 25 },
      },
      required: ['guild_id'],
    },
  },

  // === INVITES ===
  {
    name: 'discord_create_invite',
    description: 'Create an invite for a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        max_age: { type: 'number', description: 'Invite duration in seconds (0 = never expires)', default: 86400 },
        max_uses: { type: 'number', description: 'Max number of uses (0 = unlimited)', default: 0 },
        unique: { type: 'boolean', description: 'Create a unique invite (won\'t reuse existing)', default: false },
      },
      required: ['channel_id'],
    },
  },
  {
    name: 'discord_list_invites',
    description: 'List all invites for a guild',
    inputSchema: {
      type: 'object' as const,
      properties: {
        guild_id: { type: 'string', description: 'The guild ID' },
      },
      required: ['guild_id'],
    },
  },
  {
    name: 'discord_delete_invite',
    description: 'Delete/revoke an invite',
    inputSchema: {
      type: 'object' as const,
      properties: {
        invite_code: { type: 'string', description: 'The invite code (not the full URL)' },
      },
      required: ['invite_code'],
    },
  },

  // === IMAGES ===
  {
    name: 'discord_fetch_image',
    description: 'View a received or previously sent Discord image as a native vision preview plus source link. Get message_id and channel_id from discord_read_messages. Supports embedded images when there are no attachments. No local Discord process needed.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        message_id: { type: 'string', description: 'The message ID with the image' },
        attachment_index: { type: 'number', description: 'Which attachment (0-indexed)', default: 0 },
      },
      required: ['channel_id', 'message_id'],
    },
  },
  {
    name: 'discord_fetch_dm_image',
    description: 'View a received or previously sent DM image as a native vision preview plus source link. Get message_id from discord_read_dm_messages. Use the same identity that can access the DM.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        user_id: { type: 'string', description: 'The user ID' },
        message_id: { type: 'string', description: 'The message ID with the image' },
        attachment_index: { type: 'number', description: 'Which attachment (0-indexed)', default: 0 },
      },
      required: ['user_id', 'message_id'],
    },
  },

  // === FILES ===
  {
    name: 'discord_send_file',
    description: 'Send a file/image/audio attachment to a channel. Provide EITHER file_url (preferred) OR file_base64.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        file_url: { type: 'string', description: 'URL of the file to fetch and send (preferred)' },
        file_base64: { type: 'string', description: 'File content as base64 (fallback for small files)' },
        file_name: { type: 'string', description: 'File name with extension (e.g., audio.mp3, image.png)' },
        content_type: { type: 'string', description: 'MIME type when using base64 (e.g., audio/mpeg)', default: 'application/octet-stream' },
        message: { type: 'string', description: 'Optional message to send with the file' },
      },
      required: ['channel_id', 'file_name'],
    },
  },
  {
    name: 'discord_send_dm_file',
    description: 'Send a file/image/audio attachment as a DM. Provide EITHER file_url (preferred) OR file_base64.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        user_id: { type: 'string', description: 'The user ID to DM' },
        file_url: { type: 'string', description: 'URL of the file to fetch and send (preferred)' },
        file_base64: { type: 'string', description: 'File content as base64 (fallback for small files)' },
        file_name: { type: 'string', description: 'File name with extension' },
        content_type: { type: 'string', description: 'MIME type when using base64', default: 'application/octet-stream' },
        message: { type: 'string', description: 'Optional message to send with the file' },
      },
      required: ['user_id', 'file_name'],
    },
  },
  {
    name: 'discord_list_identities',
    description: 'List the configured Discord identities available for token routing',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_webhook_impersonate',
    description: 'Post via a temporary webhook using an identity persona, custom username, and optional avatar URL',
    inputSchema: {
      type: 'object' as const,
      properties: {
        identity: { type: 'string', description: 'Configured identity name to use for token routing and default persona lookup' },
        channel_id: { type: 'string', description: 'The channel ID' },
        content: { type: 'string', description: 'The message content' },
        persona: { type: 'string', description: 'Optional configured persona preset key for the identity' },
        username: { type: 'string', description: 'Override webhook username' },
        avatar_url: { type: 'string', description: 'Override webhook avatar URL' },
        embed_title: { type: 'string', description: 'Optional embed title' },
        embed_description: { type: 'string', description: 'Optional embed description' },
        embed_color: { type: 'string', description: 'Optional embed color hex (e.g., #FFAA00)' },
      },
      required: ['identity', 'channel_id', 'content'],
    },
  },
  {
    name: 'discord_list_webhook_personas',
    description: 'Show the default webhook persona settings for a configured identity',
    inputSchema: {
      type: 'object' as const,
      properties: {
        identity: { type: 'string', description: 'Configured identity name' },
      },
      required: ['identity'],
    },
  },
  {
    name: 'discord_list_identity_voices',
    description: 'List the configured ElevenLabs default voice settings for each identity',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_generate_image',
    description: `Generate an image with OpenAI ${OPENAI_IMAGE_MODEL}, post it to Discord, and return a native image preview for immediate review plus message IDs to view it again. Requires OPENAI_API_KEY secret.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        prompt: { type: 'string', description: 'Image generation prompt' },
        channel_id: { type: 'string', description: 'Channel ID to send to (use this OR user_id)' },
        user_id: { type: 'string', description: 'User ID to DM (use this OR channel_id)' },
        caption: { type: 'string', description: 'Optional caption to send with the generated image' },
        size: { type: 'string', description: 'Image size. Examples: 1024x1024, 1536x1024, 1024x1536, auto', default: '1024x1024' },
        quality: { type: 'string', description: 'Image quality for GPT image models. Example values: low, medium, high, xhigh, max, auto', default: 'medium' },
        output_format: { type: 'string', description: 'Output image format: png, jpeg, or webp', default: 'png' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'discord_generate_image_only',
    description: `Generate an image with OpenAI ${OPENAI_IMAGE_MODEL} and return a native image preview without posting to Discord or putting base64 in text. Also attempts the configured vault backup. Requires OPENAI_API_KEY secret.`,
    inputSchema: {
      type: 'object' as const,
      properties: {
        prompt: { type: 'string', description: 'Image generation prompt' },
        size: { type: 'string', description: 'Image size. Examples: 1024x1024, 1536x1024, 1024x1536, auto', default: '1024x1024' },
        quality: { type: 'string', description: 'Image quality for GPT image models. Example values: low, medium, high, xhigh, max, auto', default: 'medium' },
        output_format: { type: 'string', description: 'Output image format: png, jpeg, or webp', default: 'png' },
      },
      required: ['prompt'],
    },
  },

  // === VOICE ===
  {
    name: 'discord_send_voice_note',
    description: 'Generate a voice note using ElevenLabs TTS and send it to a channel or DM. Requires ELEVENLABS_API_KEY secret. Provide channel_id OR user_id. If voice_id is omitted, the tool will try the selected identity default.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        text: { type: 'string', description: 'Text to speak' },
        channel_id: { type: 'string', description: 'Channel ID to send to (use this OR user_id)' },
        user_id: { type: 'string', description: 'User ID to DM (use this OR channel_id)' },
        voice_id: { type: 'string', description: 'ElevenLabs voice ID override (optional)' },
        message: { type: 'string', description: 'Optional text message to send alongside the voice note' },
      },
      required: ['text'],
    },
  },

  // === POLLS ===
  {
    name: 'discord_create_poll',
    description: 'Create a poll in a channel',
    inputSchema: {
      type: 'object' as const,
      properties: {
        channel_id: { type: 'string', description: 'The channel ID' },
        question: { type: 'string', description: 'The poll question' },
        answers: { type: 'array', items: { type: 'string' }, description: 'Array of answer options (2-10)' },
        duration_hours: { type: 'number', description: 'Poll duration in hours (1-168)', default: 24 },
        allow_multiselect: { type: 'boolean', description: 'Allow multiple selections', default: false },
      },
      required: ['channel_id', 'question', 'answers'],
    },
  },
];

const TOOL_IDENTITY_PROPERTY = {
  type: 'string',
  description: 'Optional identity name for multi-bot token routing (for example: claude, avery, rowan, pack)',
};

for (const tool of ALL_DISCORD_TOOLS) {
  const properties = ((tool.inputSchema as any).properties ??= {});
  if (!('identity' in properties)) {
    properties.identity = TOOL_IDENTITY_PROPERTY;
  }
}

// Parse the comma-separated ALLOWED_TOOLS env var into a Set. Whitespace around
// names is trimmed; empty entries are skipped. Empty/undefined env value yields
// an empty allowlist (no tools exposed) — explicit allowlisting required.
export function parseAllowedTools(raw: string | undefined): Set<string> {
  if (!raw) return new Set();
  return new Set(
    raw
      .split(',')
      .map(s => s.trim())
      .filter(Boolean),
  );
}

export function getActiveDiscordTools(allowed: Set<string>): typeof ALL_DISCORD_TOOLS {
  return ALL_DISCORD_TOOLS.filter(tool => allowed.has(tool.name));
}

// ============ CHANNEL TYPE MAP (Discord API integers) ============

const CHANNEL_TYPES: Record<string, number> = {
  text: 0,
  voice: 2,
  category: 4,
  forum: 15,
};

function parseColor(color?: string): number | undefined {
  if (!color) return undefined;
  if (color.startsWith('#')) return parseInt(color.slice(1), 16);
  return parseInt(color, 16);
}

function base64ToUint8Array(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// URL allowlist for server-side fetch — prevents SSRF
function validateFetchUrl(urlStr: string): URL {
  let url: URL;
  try {
    url = new URL(urlStr);
  } catch {
    throw new Error('Invalid URL');
  }
  if (url.protocol !== 'https:') {
    throw new Error('Only HTTPS URLs are allowed');
  }
  const hostname = url.hostname.toLowerCase();
  // Block private/internal ranges and cloud metadata endpoints
  const blocked = [
    /^localhost$/,
    /^127\./,
    /^10\./,
    /^172\.(1[6-9]|2\d|3[01])\./,
    /^192\.168\./,
    /^169\.254\./,
    /^0\./,
    /^\[/,            // IPv6 brackets
    /\.local$/,
    /\.internal$/,
  ];
  if (blocked.some(re => re.test(hostname))) {
    throw new Error('URL points to a blocked address range');
  }
  return url;
}

// Resolve file data from either URL or base64
async function resolveFileData(args: Record<string, unknown>): Promise<{ data: Uint8Array; contentType: string }> {
  if (args.file_url) {
    const url = validateFetchUrl(args.file_url as string);
    const res = await fetch(url.toString());
    if (!res.ok) throw new Error(`Failed to fetch file from URL: ${res.status}`);
    return {
      data: new Uint8Array(await res.arrayBuffer()),
      contentType: res.headers.get('content-type') || 'application/octet-stream',
    };
  }
  if (args.file_base64) {
    return {
      data: base64ToUint8Array(args.file_base64 as string),
      contentType: (args.content_type as string) || 'application/octet-stream',
    };
  }
  throw new Error('Must provide either file_url or file_base64');
}

async function generateOpenAiImage(
  apiKey: string,
  args: Record<string, unknown>,
): Promise<{ data: Uint8Array; rawBase64: string; contentType: string; revisedPrompt?: string; outputFormat: string }> {
  const size = (args.size as string | undefined) || '1024x1024';
  const quality = (args.quality as string | undefined) || 'medium';
  const outputFormat = (args.output_format as string | undefined) || 'png';
  const prompt = args.prompt as string;

  const res = await fetch('https://api.openai.com/v1/images/generations', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: OPENAI_IMAGE_MODEL,
      prompt,
      size,
      quality,
      output_format: outputFormat,
      n: 1,
    }),
  });

  const json = await res.json() as {
    data?: Array<{ b64_json?: string; revised_prompt?: string }>;
    error?: { message?: string };
  };

  if (!res.ok) {
    throw new Error(`OpenAI image generation failed: ${json.error?.message || res.status}`);
  }

  const imageBase64 = json.data?.[0]?.b64_json;
  if (!imageBase64) {
    throw new Error('OpenAI image generation returned no image data');
  }

  return {
    data: base64ToUint8Array(imageBase64),
    rawBase64: imageBase64,
    contentType: outputFormat === 'jpeg' ? 'image/jpeg' : outputFormat === 'webp' ? 'image/webp' : 'image/png',
    revisedPrompt: json.data?.[0]?.revised_prompt,
    outputFormat,
  };
}

export interface ToolExtras {
  elevenLabsApiKey?: string;
  openAiApiKey?: string;
  machineAgentUrl?: string;
  machineAgentApiKey?: string;
  imageArchiveDir?: string;
}

// Discord audit log action types for readable output
const AUDIT_ACTION_NAMES: Record<number, string> = {
  1: 'GUILD_UPDATE', 10: 'CHANNEL_CREATE', 11: 'CHANNEL_UPDATE', 12: 'CHANNEL_DELETE',
  13: 'CHANNEL_OVERWRITE_CREATE', 14: 'CHANNEL_OVERWRITE_UPDATE', 15: 'CHANNEL_OVERWRITE_DELETE',
  20: 'MEMBER_KICK', 22: 'MEMBER_BAN_ADD', 23: 'MEMBER_BAN_REMOVE', 24: 'MEMBER_UPDATE',
  25: 'MEMBER_ROLE_UPDATE', 26: 'MEMBER_MOVE', 27: 'MEMBER_DISCONNECT',
  30: 'ROLE_CREATE', 31: 'ROLE_UPDATE', 32: 'ROLE_DELETE',
  40: 'INVITE_CREATE', 41: 'INVITE_UPDATE', 42: 'INVITE_DELETE',
  50: 'WEBHOOK_CREATE', 51: 'WEBHOOK_UPDATE', 52: 'WEBHOOK_DELETE',
  72: 'MESSAGE_DELETE', 73: 'MESSAGE_BULK_DELETE', 74: 'MESSAGE_PIN', 75: 'MESSAGE_UNPIN',
  110: 'THREAD_CREATE', 111: 'THREAD_UPDATE', 112: 'THREAD_DELETE',
  140: 'AUTO_MODERATION_RULE_CREATE', 141: 'AUTO_MODERATION_RULE_UPDATE', 142: 'AUTO_MODERATION_RULE_DELETE',
  143: 'AUTO_MODERATION_BLOCK_MESSAGE',
};

// ============ TOOL HANDLER ============

export async function handleDiscordTool(
  token: string,
  name: string,
  args: Record<string, unknown>,
  allowed: Set<string>,
  extras?: ToolExtras,
): Promise<DiscordToolResult> {
  if (!allowed.has(name)) {
    throw new Error(`Tool disabled in this worker: ${name}`);
  }

  switch (name) {
    // === MESSAGING ===
    case 'discord_read_messages': {
      const limit = Math.min((args.limit as number) || 50, 100);
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}/messages?limit=${limit}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msgs = (res.data as any[]).reverse();
      if (msgs.length === 0) return 'No messages found';
      return msgs.map(formatMessage).join('\n\n');
    }

    case 'discord_read_dm_messages': {
      const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
      if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
      const dmChannelId = (dmRes.data as any).id;
      const limit = Math.min((args.limit as number) || 50, 100);
      const res = await discordFetch(token, 'GET', `/channels/${dmChannelId}/messages?limit=${limit}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msgs = (res.data as any[]).reverse();
      if (msgs.length === 0) return 'No DM messages found';
      return msgs.map(formatMessage).join('\n\n');
    }

    case 'discord_send_message': {
      const body: any = { content: args.content };
      if (args.reply_to) {
        body.message_reference = { message_id: args.reply_to };
      }
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/messages`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Message sent (ID: ${(res.data as any).id})`;
    }

    case 'discord_send_dm': {
      const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
      if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
      const dmChannelId = (dmRes.data as any).id;
      const res = await discordFetch(token, 'POST', `/channels/${dmChannelId}/messages`, { content: args.content });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `DM sent (ID: ${(res.data as any).id})`;
    }

    case 'discord_edit_message': {
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}/messages/${args.message_id}`, { content: args.new_content });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Message ${args.message_id} edited`;
    }

    case 'discord_delete_message': {
      const res = await discordFetch(token, 'DELETE', `/channels/${args.channel_id}/messages/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Message ${args.message_id} deleted`;
    }

    case 'discord_bulk_delete_messages': {
      const ids = args.message_ids as string[];
      if (ids.length < 2 || ids.length > 100) throw new Error('Must provide 2-100 message IDs');
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/messages/bulk-delete`, { messages: ids });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Bulk deleted ${ids.length} messages`;
    }

    case 'discord_send_embed': {
      const embed: any = {};
      if (args.title) embed.title = args.title;
      if (args.description) embed.description = args.description;
      if (args.url) embed.url = args.url;
      if (args.footer) embed.footer = { text: args.footer };
      if (args.thumbnail_url) embed.thumbnail = { url: args.thumbnail_url };
      if (args.image_url) embed.image = { url: args.image_url };
      if (args.fields) embed.fields = args.fields;
      const color = parseColor(args.color as string | undefined);
      if (color !== undefined) embed.color = color;
      const body: any = { embeds: [embed] };
      if (args.content) body.content = args.content;
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/messages`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Embed sent (ID: ${(res.data as any).id})`;
    }

    // === REACTIONS ===
    case 'discord_add_reaction': {
      const emoji = encodeURIComponent(args.emoji as string);
      const res = await discordFetch(token, 'PUT', `/channels/${args.channel_id}/messages/${args.message_id}/reactions/${emoji}/@me`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Reacted with ${args.emoji}`;
    }

    case 'discord_remove_reaction': {
      const emoji = encodeURIComponent(args.emoji as string);
      const target = args.user_id ? args.user_id : '@me';
      const res = await discordFetch(token, 'DELETE', `/channels/${args.channel_id}/messages/${args.message_id}/reactions/${emoji}/${target}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Removed ${args.emoji} reaction`;
    }

    case 'discord_get_message_reactions': {
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}/messages/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msg = res.data as any;
      if (!msg.reactions || msg.reactions.length === 0) return 'No reactions on this message';
      return msg.reactions.map((r: any) => `${r.emoji.name}: ${r.count} reaction(s)`).join('\n');
    }

    // === PINS ===
    case 'discord_pin_message': {
      const res = await discordFetch(token, 'PUT', `/channels/${args.channel_id}/pins/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Message ${args.message_id} pinned`;
    }

    case 'discord_unpin_message': {
      const res = await discordFetch(token, 'DELETE', `/channels/${args.channel_id}/pins/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Message ${args.message_id} unpinned`;
    }

    case 'discord_get_pinned_messages': {
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}/pins`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msgs = res.data as any[];
      if (msgs.length === 0) return 'No pinned messages';
      return msgs.map(formatMessage).join('\n\n');
    }

    // === CHANNELS ===
    case 'discord_list_channels': {
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/channels`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const channels = res.data as any[];

      const categories = channels.filter(ch => ch.type === 4);
      const lines: string[] = [];

      for (const cat of categories) {
        lines.push(`[Category] ${cat.name} (${cat.id})`);
        const children = channels.filter(ch => ch.parent_id === cat.id).sort((a, b) => a.position - b.position);
        for (const ch of children) {
          const typeLabel = ch.type === 2 ? 'voice' : ch.type === 15 ? 'forum' : '#';
          lines.push(`  ${typeLabel} ${ch.name} (${ch.id})`);
        }
      }

      const uncategorized = channels.filter(ch => !ch.parent_id && ch.type !== 4);
      if (uncategorized.length > 0) {
        lines.push('[No category]');
        for (const ch of uncategorized) {
          const typeLabel = ch.type === 2 ? 'voice' : ch.type === 15 ? 'forum' : '#';
          lines.push(`  ${typeLabel} ${ch.name} (${ch.id})`);
        }
      }

      return lines.join('\n');
    }

    case 'discord_list_servers': {
      const res = await discordFetch(token, 'GET', '/users/@me/guilds');
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const guilds = res.data as any[];
      return guilds.map(g => `${g.name} (${g.id})`).join('\n');
    }

    case 'discord_create_channel': {
      const body: any = {
        name: args.name,
        type: CHANNEL_TYPES[(args.type as string) || 'text'] ?? 0,
      };
      if (args.category_id) body.parent_id = args.category_id;
      if (args.topic) body.topic = args.topic;
      const res = await discordFetch(token, 'POST', `/guilds/${args.guild_id}/channels`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const ch = res.data as any;
      return `Channel created: ${ch.name} (ID: ${ch.id})`;
    }

    case 'discord_edit_channel': {
      const body: any = {};
      if (args.name) body.name = args.name;
      if (args.topic !== undefined) body.topic = args.topic;
      if (args.nsfw !== undefined) body.nsfw = args.nsfw;
      if (args.position !== undefined) body.position = args.position;
      if (args.parent_id !== undefined) body.parent_id = args.parent_id || null;
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Channel ${args.channel_id} updated`;
    }

    case 'discord_delete_channel': {
      const res = await discordFetch(token, 'DELETE', `/channels/${args.channel_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Channel ${args.channel_id} deleted`;
    }

    case 'discord_set_slowmode': {
      const seconds = Math.min(Math.max((args.seconds as number) || 0, 0), 21600);
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, { rate_limit_per_user: seconds });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return seconds === 0 ? `Slowmode disabled on ${args.channel_id}` : `Slowmode set to ${seconds}s on ${args.channel_id}`;
    }

    case 'discord_set_channel_permissions': {
      const body: any = { type: args.type };
      if (args.allow) body.allow = args.allow;
      if (args.deny) body.deny = args.deny;
      const res = await discordFetch(token, 'PUT', `/channels/${args.channel_id}/permissions/${args.overwrite_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Permission overwrite set on channel ${args.channel_id}`;
    }

    // === THREADS ===
    case 'discord_create_thread': {
      const body: any = {
        name: args.name,
        auto_archive_duration: args.auto_archive_duration || 1440,
      };

      let res;
      if (args.message_id) {
        res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/messages/${args.message_id}/threads`, body);
      } else {
        body.type = 11; // PUBLIC_THREAD
        res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/threads`, body);
      }
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const thread = res.data as any;
      return `Thread created: ${thread.name} (ID: ${thread.id})`;
    }

    case 'discord_manage_thread': {
      const body: any = {};
      if (args.archived !== undefined) body.archived = args.archived;
      if (args.locked !== undefined) body.locked = args.locked;
      const res = await discordFetch(token, 'PATCH', `/channels/${args.thread_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Thread ${args.thread_id} updated`;
    }

    case 'discord_delete_thread': {
      const res = await discordFetch(token, 'DELETE', `/channels/${args.thread_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Thread ${args.thread_id} deleted`;
    }

    // === FORUM ===
    case 'discord_create_forum_post': {
      const body: any = {
        name: args.name,
        message: { content: args.content },
        auto_archive_duration: 1440,
      };
      if (args.applied_tags) body.applied_tags = args.applied_tags;
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/threads`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const thread = res.data as any;
      return `Forum post created: ${thread.name} (ID: ${thread.id})`;
    }

    case 'discord_edit_forum_post': {
      const body: any = {};
      if (args.name) body.name = args.name;
      if (args.archived !== undefined) body.archived = args.archived;
      if (args.locked !== undefined) body.locked = args.locked;
      if (args.applied_tags) body.applied_tags = args.applied_tags;
      if (args.auto_archive_duration) body.auto_archive_duration = args.auto_archive_duration;
      // Forum pin uses flags — bit 1 (value 2) = PINNED
      if (args.pinned !== undefined) {
        body.flags = (args.pinned as boolean) ? 2 : 0;
      }
      const res = await discordFetch(token, 'PATCH', `/channels/${args.thread_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Forum post ${args.thread_id} updated`;
    }

    case 'discord_list_forum_posts': {
      // Get active threads for the guild, then filter by parent channel
      const chRes = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
      if (!chRes.ok) throw new Error(`Discord API error: ${JSON.stringify(chRes.data)}`);
      const channel = chRes.data as any;
      const guildId = channel.guild_id;

      const res = await discordFetch(token, 'GET', `/guilds/${guildId}/threads/active`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const data = res.data as any;
      const threads = (data.threads || []).filter((t: any) => t.parent_id === args.channel_id);
      if (threads.length === 0) return 'No active forum posts';
      return threads.map((t: any) => {
        const tags = t.applied_tags?.length ? ` [tags: ${t.applied_tags.join(', ')}]` : '';
        const pinned = (t.flags & 2) ? ' [PINNED]' : '';
        return `${t.name} (ID: ${t.id})${pinned}${tags} — ${t.message_count || 0} messages`;
      }).join('\n');
    }

    case 'discord_list_archived_forum_posts': {
      const limit = Math.min((args.limit as number) || 25, 100);
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}/threads/archived/public?limit=${limit}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const data = res.data as any;
      const threads = data.threads || [];
      if (threads.length === 0) return 'No archived forum posts';
      return threads.map((t: any) => {
        const tags = t.applied_tags?.length ? ` [tags: ${t.applied_tags.join(', ')}]` : '';
        const locked = t.thread_metadata?.locked ? ' [LOCKED]' : '';
        return `${t.name} (ID: ${t.id})${locked}${tags} — ${t.message_count || 0} messages`;
      }).join('\n');
    }

    case 'discord_get_forum_tags': {
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const ch = res.data as any;
      const tags = ch.available_tags || [];
      if (tags.length === 0) return 'No tags configured on this forum';
      return tags.map((t: any) => {
        const emoji = t.emoji_name || (t.emoji_id ? `<:tag:${t.emoji_id}>` : '');
        const mod = t.moderated ? ' [mod-only]' : '';
        return `${emoji ? emoji + ' ' : ''}${t.name} (ID: ${t.id})${mod}`;
      }).join('\n');
    }

    case 'discord_create_forum_tag': {
      // Get current tags, append new one, update channel
      const chRes = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
      if (!chRes.ok) throw new Error(`Discord API error: ${JSON.stringify(chRes.data)}`);
      const ch = chRes.data as any;
      const tags = ch.available_tags || [];
      if (tags.length >= 20) throw new Error('Forum already has maximum 20 tags');
      const newTag: any = { name: args.name, moderated: args.moderated ?? false };
      if (args.emoji_name) newTag.emoji_name = args.emoji_name;
      if (args.emoji_id) newTag.emoji_id = args.emoji_id;
      tags.push(newTag);
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, { available_tags: tags });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      // Find the created tag (it'll have an ID now)
      const updated = res.data as any;
      const created = (updated.available_tags || []).find((t: any) => t.name === args.name);
      return `Tag created: ${args.name}${created?.id ? ` (ID: ${created.id})` : ''}`;
    }

    case 'discord_edit_forum_tag': {
      const chRes = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
      if (!chRes.ok) throw new Error(`Discord API error: ${JSON.stringify(chRes.data)}`);
      const ch = chRes.data as any;
      const tags = (ch.available_tags || []).map((t: any) => {
        if (t.id !== args.tag_id) return t;
        const updated = { ...t };
        if (args.name) updated.name = args.name;
        if (args.emoji_name !== undefined) updated.emoji_name = args.emoji_name || null;
        if (args.emoji_id !== undefined) updated.emoji_id = args.emoji_id || null;
        if (args.moderated !== undefined) updated.moderated = args.moderated;
        return updated;
      });
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, { available_tags: tags });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Tag ${args.tag_id} updated`;
    }

    case 'discord_delete_forum_tag': {
      const chRes = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
      if (!chRes.ok) throw new Error(`Discord API error: ${JSON.stringify(chRes.data)}`);
      const ch = chRes.data as any;
      const tags = (ch.available_tags || []).filter((t: any) => t.id !== args.tag_id);
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, { available_tags: tags });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Tag ${args.tag_id} deleted`;
    }

    case 'discord_set_forum_default_reaction': {
      const reaction: any = {};
      if (args.emoji_name) reaction.emoji_name = args.emoji_name;
      else reaction.emoji_name = null;
      if (args.emoji_id) reaction.emoji_id = args.emoji_id;
      else reaction.emoji_id = null;
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, { default_reaction_emoji: reaction });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Default forum reaction updated`;
    }

    case 'discord_set_forum_settings': {
      const body: any = {};
      if (args.topic !== undefined) body.topic = args.topic;
      if (args.default_sort_order !== undefined) body.default_sort_order = args.default_sort_order;
      if (args.default_forum_layout !== undefined) body.default_forum_layout = args.default_forum_layout;
      if (args.default_auto_archive_duration !== undefined) body.default_auto_archive_duration = args.default_auto_archive_duration;
      if (args.default_thread_rate_limit !== undefined) body.default_thread_rate_limit_per_user = args.default_thread_rate_limit;
      if (args.require_tag !== undefined) {
        // Bit 4 (value 16) = REQUIRE_TAG
        const chRes = await discordFetch(token, 'GET', `/channels/${args.channel_id}`);
        if (chRes.ok) {
          const currentFlags = (chRes.data as any).flags || 0;
          body.flags = (args.require_tag as boolean) ? (currentFlags | 16) : (currentFlags & ~16);
        }
      }
      const res = await discordFetch(token, 'PATCH', `/channels/${args.channel_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Forum settings updated for ${args.channel_id}`;
    }

    // === ROLES ===
    case 'discord_list_roles': {
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/roles`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const roles = res.data as any[];
      return roles.map(r => `@${r.name} [#${r.color.toString(16).padStart(6, '0')}] - ID: ${r.id}`).join('\n');
    }

    case 'discord_create_role': {
      const body: any = {
        name: args.name,
        hoist: args.hoist ?? false,
        mentionable: args.mentionable ?? false,
      };
      const color = parseColor(args.color as string | undefined);
      if (color !== undefined) body.color = color;
      const res = await discordFetch(token, 'POST', `/guilds/${args.guild_id}/roles`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const role = res.data as any;
      return `Role created: @${role.name} (ID: ${role.id})`;
    }

    case 'discord_edit_role': {
      const body: any = {};
      if (args.name) body.name = args.name;
      const color = parseColor(args.color as string | undefined);
      if (color !== undefined) body.color = color;
      const res = await discordFetch(token, 'PATCH', `/guilds/${args.guild_id}/roles/${args.role_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Role ${args.role_id} updated`;
    }

    case 'discord_delete_role': {
      const res = await discordFetch(token, 'DELETE', `/guilds/${args.guild_id}/roles/${args.role_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Role ${args.role_id} deleted`;
    }

    case 'discord_assign_role': {
      const res = await discordFetch(token, 'PUT', `/guilds/${args.guild_id}/members/${args.user_id}/roles/${args.role_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Role ${args.role_id} assigned to user ${args.user_id}`;
    }

    case 'discord_remove_role': {
      const res = await discordFetch(token, 'DELETE', `/guilds/${args.guild_id}/members/${args.user_id}/roles/${args.role_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Role ${args.role_id} removed from user ${args.user_id}`;
    }

    case 'discord_get_member_roles': {
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/members/${args.user_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const member = res.data as any;
      const rolesRes = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/roles`);
      const allRoles = rolesRes.ok ? (rolesRes.data as any[]) : [];
      const roleMap = new Map(allRoles.map(r => [r.id, r.name]));
      const memberRoles = (member.roles as string[]).map(id => `@${roleMap.get(id) || id} (${id})`);
      return `Roles for ${member.user?.username || args.user_id}:\n${memberRoles.join('\n')}`;
    }

    // === MEMBERS & MODERATION ===
    case 'discord_get_guild_members': {
      const limit = Math.min((args.limit as number) || 100, 1000);
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/members?limit=${limit}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const members = res.data as any[];
      return members.map(m => {
        const nick = m.nick ? ` (${m.nick})` : '';
        const roles = (m.roles as string[]).length;
        return `${m.user.username}${nick} - ${roles} roles - ID: ${m.user.id}`;
      }).join('\n');
    }

    case 'discord_change_nickname': {
      const botRes = await discordFetch(token, 'GET', '/users/@me');
      if (!botRes.ok) throw new Error(`Failed to get bot user`);
      const botId = (botRes.data as any).id;
      const res = await discordFetch(token, 'PATCH', `/guilds/${args.guild_id}/members/${botId}`, {
        nick: (args.nickname as string) || null,
      });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return args.nickname ? `Nickname changed to: ${args.nickname}` : 'Nickname reset';
    }

    case 'discord_get_user_info': {
      const res = await discordFetch(token, 'GET', `/users/${args.user_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const user = res.data as any;
      return `Username: ${user.username}\nID: ${user.id}\nBot: ${user.bot || false}\nAvatar: https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png`;
    }

    case 'discord_kick_member': {
      const headers: Record<string, string> = {};
      if (args.reason) headers['X-Audit-Log-Reason'] = encodeURIComponent(args.reason as string);
      const res = await discordFetch(token, 'DELETE', `/guilds/${args.guild_id}/members/${args.user_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `User ${args.user_id} kicked from guild${args.reason ? ` (reason: ${args.reason})` : ''}`;
    }

    case 'discord_ban_member': {
      const body: any = {};
      if (args.delete_message_seconds) body.delete_message_seconds = Math.min(args.delete_message_seconds as number, 604800);
      const res = await discordFetch(token, 'PUT', `/guilds/${args.guild_id}/bans/${args.user_id}`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `User ${args.user_id} banned from guild${args.reason ? ` (reason: ${args.reason})` : ''}`;
    }

    case 'discord_unban_member': {
      const res = await discordFetch(token, 'DELETE', `/guilds/${args.guild_id}/bans/${args.user_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `User ${args.user_id} unbanned`;
    }

    case 'discord_timeout_member': {
      const duration = args.duration_seconds as number;
      let communicationDisabledUntil: string | null = null;
      if (duration > 0) {
        const until = new Date(Date.now() + Math.min(duration, 2419200) * 1000);
        communicationDisabledUntil = until.toISOString();
      }
      const res = await discordFetch(token, 'PATCH', `/guilds/${args.guild_id}/members/${args.user_id}`, {
        communication_disabled_until: communicationDisabledUntil,
      });
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return duration > 0
        ? `User ${args.user_id} timed out for ${duration}s`
        : `Timeout removed for user ${args.user_id}`;
    }

    // === SERVER / GUILD ===
    case 'discord_get_guild_info': {
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}?with_counts=true`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const g = res.data as any;
      const lines = [
        `Name: ${g.name}`,
        `ID: ${g.id}`,
        `Owner ID: ${g.owner_id}`,
        `Members: ~${g.approximate_member_count || 'unknown'}`,
        `Online: ~${g.approximate_presence_count || 'unknown'}`,
        `Boost Tier: ${g.premium_tier}`,
        `Boosts: ${g.premium_subscription_count || 0}`,
        `Verification Level: ${g.verification_level}`,
        `NSFW Level: ${g.nsfw_level}`,
        `Created: ${g.id ? new Date(Number(BigInt(g.id) >> 22n) + 1420070400000).toISOString() : 'unknown'}`,
      ];
      if (g.description) lines.push(`Description: ${g.description}`);
      if (g.vanity_url_code) lines.push(`Vanity URL: discord.gg/${g.vanity_url_code}`);
      return lines.join('\n');
    }

    case 'discord_get_audit_log': {
      let path = `/guilds/${args.guild_id}/audit-logs?limit=${Math.min((args.limit as number) || 25, 100)}`;
      if (args.action_type !== undefined) path += `&action_type=${args.action_type}`;
      if (args.user_id) path += `&user_id=${args.user_id}`;
      const res = await discordFetch(token, 'GET', path);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const log = res.data as any;
      const users = new Map((log.users || []).map((u: any) => [u.id, u.username]));
      const entries = (log.audit_log_entries || []).map((e: any) => {
        const who = users.get(e.user_id) || e.user_id;
        const action = AUDIT_ACTION_NAMES[e.action_type] || `ACTION_${e.action_type}`;
        const reason = e.reason ? ` — "${e.reason}"` : '';
        const target = e.target_id ? ` on ${e.target_id}` : '';
        return `[${e.id}] ${who}: ${action}${target}${reason}`;
      });
      return entries.length > 0 ? entries.join('\n') : 'No audit log entries found';
    }

    // === INVITES ===
    case 'discord_create_invite': {
      const body: any = {
        max_age: (args.max_age as number) ?? 86400,
        max_uses: (args.max_uses as number) ?? 0,
        unique: args.unique ?? false,
      };
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/invites`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const inv = res.data as any;
      return `Invite created: https://discord.gg/${inv.code} (max uses: ${inv.max_uses || 'unlimited'}, expires: ${inv.max_age ? inv.max_age + 's' : 'never'})`;
    }

    case 'discord_list_invites': {
      const res = await discordFetch(token, 'GET', `/guilds/${args.guild_id}/invites`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const invites = res.data as any[];
      if (invites.length === 0) return 'No active invites';
      return invites.map(i => {
        const uses = `${i.uses}/${i.max_uses || 'inf'}`;
        const channel = i.channel?.name || i.channel_id;
        const creator = i.inviter?.username || 'unknown';
        return `discord.gg/${i.code} — #${channel} by ${creator} (${uses} uses)`;
      }).join('\n');
    }

    case 'discord_delete_invite': {
      const res = await discordFetch(token, 'DELETE', `/invites/${args.invite_code}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Invite ${args.invite_code} deleted`;
    }

    // === IMAGES ===
    case 'discord_fetch_image': {
      const res = await discordFetch(token, 'GET', `/channels/${args.channel_id}/messages/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msg = res.data as any;
      return messageImageResult(msg, args.attachment_index, args.channel_id as string);
    }

    case 'discord_fetch_dm_image': {
      const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
      if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
      const dmChannelId = (dmRes.data as any).id;
      const res = await discordFetch(token, 'GET', `/channels/${dmChannelId}/messages/${args.message_id}`);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      const msg = res.data as any;
      return messageImageResult(msg, args.attachment_index, dmChannelId);
    }

    // === FILES ===
    case 'discord_send_file': {
      const { data: fileData, contentType } = await resolveFileData(args);
      const fileName = args.file_name as string;
      const payload: any = { attachments: [{ id: 0, filename: fileName }] };
      if (args.message) payload.content = args.message;
      const res = await discordFetchMultipart(token, 'POST', `/channels/${args.channel_id}/messages`, payload, fileData, fileName, contentType);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `File sent: ${fileName} (message ID: ${(res.data as any).id})`;
    }

    case 'discord_send_dm_file': {
      const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
      if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
      const dmChannelId = (dmRes.data as any).id;
      const { data: fileData, contentType } = await resolveFileData(args);
      const fileName = args.file_name as string;
      const payload: any = { attachments: [{ id: 0, filename: fileName }] };
      if (args.message) payload.content = args.message;
      const res = await discordFetchMultipart(token, 'POST', `/channels/${dmChannelId}/messages`, payload, fileData, fileName, contentType);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `DM file sent: ${fileName} (message ID: ${(res.data as any).id})`;
    }

    case 'discord_list_identities': {
      return listIdentityNames()
        .map(identity => `${identity} - ${getIdentityDisplayName(identity)}`)
        .join('\n');
    }

    case 'discord_webhook_impersonate': {
      const identity = String(args.identity || '').trim().toLowerCase();
      if (!identity) throw new Error('identity is required');

      const preset = resolvePersona(identity, args.persona as string | undefined);
      const effectiveUsername = (args.username as string | undefined) || preset.username || getIdentityDisplayName(identity);
      const effectiveAvatarUrl = (args.avatar_url as string | undefined) || preset.avatar_url;
      const effectiveEmbedTitle = args.embed_title as string | undefined;
      const effectiveEmbedDescription = args.embed_description as string | undefined;
      const effectiveEmbedColor = (args.embed_color as string | undefined) || preset.embed_color;

      const createWebhookRes = await discordFetch(token, 'POST', `/channels/${args.channel_id}/webhooks`, {
        name: `temp-${identity}`,
      });
      if (!createWebhookRes.ok) {
        throw new Error(`Failed to create webhook: ${JSON.stringify(createWebhookRes.data)}`);
      }

      const webhook = createWebhookRes.data as { id: string; token: string };
      try {
        const body: Record<string, unknown> = {
          content: args.content,
          username: effectiveUsername,
        };
        if (effectiveAvatarUrl) {
          body.avatar_url = effectiveAvatarUrl;
        }
        if (effectiveEmbedTitle || effectiveEmbedDescription) {
          const embed: Record<string, unknown> = {};
          if (effectiveEmbedTitle) embed.title = effectiveEmbedTitle;
          if (effectiveEmbedDescription) embed.description = effectiveEmbedDescription;
          const color = parseColor(effectiveEmbedColor);
          if (color !== undefined) embed.color = color;
          body.embeds = [embed];
        }

        const res = await discordWebhookFetch(webhook.id, webhook.token, body);
        if (!res.ok) {
          throw new Error(`Webhook send failed: ${JSON.stringify(res.data)}`);
        }

        return `Webhook message sent as ${effectiveUsername} (message ID: ${(res.data as any).id})`;
      } finally {
        await discordFetch(token, 'DELETE', `/webhooks/${webhook.id}`);
      }
    }

    case 'discord_list_webhook_personas': {
      const identity = String(args.identity || '').trim().toLowerCase();
      if (!identity) throw new Error('identity is required');
      const preset = resolvePersona(identity);
      const lines = [
        `Identity: ${getIdentityDisplayName(identity)}`,
        `Username: ${preset.username || '(not set)'}`,
        `Avatar URL: ${preset.avatar_url || '(not set)'}`,
        `Embed Color: ${preset.embed_color || '(not set)'}`,
      ];
      return lines.join('\n');
    }

    case 'discord_list_identity_voices': {
      return listIdentityNames()
        .map(identity => {
          const voice = getIdentityVoice(identity);
          return [
            `${identity} - ${getIdentityDisplayName(identity)}`,
            `  voice_id: ${voice.voice_id || '(not set)'}`,
            `  stability: ${voice.stability ?? '(default)'}`,
            `  similarity_boost: ${voice.similarity_boost ?? '(default)'}`,
            `  style: ${voice.style ?? '(default)'}`,
            `  use_speaker_boost: ${voice.use_speaker_boost ?? '(default)'}`,
          ].join('\n');
        })
        .join('\n\n');
    }

    case 'discord_generate_image_only': {
      if (!extras?.openAiApiKey) throw new Error('OpenAI API key not configured');

      const generated = await generateOpenAiImage(extras.openAiApiKey, args);
      const archiveStatus = await archiveGeneratedImage(generated.rawBase64, generated.outputFormat, args.identity, extras);
      const lines = [
        `Generated with ${OPENAI_IMAGE_MODEL}`,
        archiveStatus,
        generated.revisedPrompt ? `Revised prompt: ${generated.revisedPrompt}` : null,
      ].filter(Boolean);
      return imageResult(lines.join('\n'), generated.rawBase64, generated.contentType);
    }

    case 'discord_generate_image': {
      if (!extras?.openAiApiKey) throw new Error('OpenAI API key not configured');

      const generated = await generateOpenAiImage(extras.openAiApiKey, args);
      const archiveStatus = await archiveGeneratedImage(generated.rawBase64, generated.outputFormat, args.identity, extras);
      const extension = generated.outputFormat === 'jpeg' ? 'jpg' : generated.outputFormat;
      const fileName = `generated-${Date.now()}.${extension}`;
      const payload: any = {
        attachments: [{ id: 0, filename: fileName }],
      };

      const caption = args.caption as string | undefined;
      if (caption || generated.revisedPrompt) {
        payload.content = [caption, generated.revisedPrompt ? `(revised prompt: ${generated.revisedPrompt})` : null]
          .filter(Boolean)
          .join('\n');
      }

      let targetChannelId = args.channel_id as string | undefined;
      if (!targetChannelId && args.user_id) {
        const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
        if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
        targetChannelId = (dmRes.data as any).id;
      }
      if (!targetChannelId) throw new Error('Must provide channel_id or user_id');

      const res = await discordFetchMultipart(
        token,
        'POST',
        `/channels/${targetChannelId}/messages`,
        payload,
        generated.data,
        fileName,
        generated.contentType,
      );
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);

      const posted = res.data as any;
      const receipt = [
        `Generated image sent (${fileName}, message ID: ${posted.id}, channel ID: ${targetChannelId})`,
        archiveStatus,
        posted.attachments?.[0]?.url ? `Source URL: ${posted.attachments[0].url}` : null,
        `Review again: discord_fetch_image(channel_id="${targetChannelId}", message_id="${posted.id}") with the same identity.`,
      ].filter(Boolean).join('\n');
      if (generated.data.byteLength > MAX_IMAGE_BYTES && posted.attachments?.length) {
        try {
          const preview = await messageImageResult(posted, 0, targetChannelId);
          return { ...preview, content: [{ type: 'text', text: receipt }, ...preview.content] };
        } catch { /* Preserve the successful post receipt even if preview fails. */ }
      }
      return imageResult(receipt, generated.rawBase64, generated.contentType);
    }

    // === VOICE ===
    case 'discord_send_voice_note': {
      if (!extras?.elevenLabsApiKey) throw new Error('ElevenLabs API key not configured');

      const identity = typeof args.identity === 'string' ? args.identity.trim().toLowerCase() : '';
      const identityVoice = identity ? getIdentityVoice(identity) : {};
      const voiceId = (args.voice_id as string | undefined) || identityVoice.voice_id;
      if (!voiceId) throw new Error('voice_id is required — get yours from https://elevenlabs.io/app/voice-lab');
      const text = args.text as string;

      // Generate audio via ElevenLabs
      const ttsRes = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${voiceId}`, {
        method: 'POST',
        headers: {
          'xi-api-key': extras.elevenLabsApiKey,
          'Content-Type': 'application/json',
          'Accept': 'audio/mpeg',
        },
        body: JSON.stringify({
          text,
          model_id: 'eleven_v3',
          voice_settings: {
            stability: identityVoice.stability ?? 0.5,
            similarity_boost: identityVoice.similarity_boost ?? 0.75,
            style: identityVoice.style ?? 0,
            use_speaker_boost: identityVoice.use_speaker_boost ?? true,
          },
        }),
      });

      if (!ttsRes.ok) {
        const errText = await ttsRes.text();
        throw new Error(`ElevenLabs error ${ttsRes.status}: ${errText}`);
      }

      const audioData = new Uint8Array(await ttsRes.arrayBuffer());
      const fileName = 'voice-note.mp3';

      // Resolve target channel
      let targetChannelId = args.channel_id as string | undefined;
      if (!targetChannelId && args.user_id) {
        const dmRes = await discordFetch(token, 'POST', '/users/@me/channels', { recipient_id: args.user_id });
        if (!dmRes.ok) throw new Error(`Failed to open DM: ${JSON.stringify(dmRes.data)}`);
        targetChannelId = (dmRes.data as any).id;
      }
      if (!targetChannelId) throw new Error('Must provide channel_id or user_id');

      const payload: any = { attachments: [{ id: 0, filename: fileName }] };
      if (args.message) payload.content = args.message;

      const res = await discordFetchMultipart(token, 'POST', `/channels/${targetChannelId}/messages`, payload, audioData, fileName, 'audio/mpeg');
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Voice note sent (${audioData.byteLength} bytes, message ID: ${(res.data as any).id})`;
    }

    // === POLLS ===
    case 'discord_create_poll': {
      const answers = (args.answers as string[]).map(text => ({ poll_media: { text } }));
      const body = {
        content: '',
        poll: {
          question: { text: args.question },
          answers,
          duration: Math.min(Math.max((args.duration_hours as number) || 24, 1), 168),
          allow_multiselect: args.allow_multiselect ?? false,
        },
      };
      const res = await discordFetch(token, 'POST', `/channels/${args.channel_id}/messages`, body);
      if (!res.ok) throw new Error(`Discord API error: ${JSON.stringify(res.data)}`);
      return `Poll created: ${(res.data as any).id}`;
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}
