// Telegram tools — multi-identity message sending

import type { Env, ToolDef, ToolModule } from '../lib/types';

interface TelegramIdentity {
  bot_token: string;
  chat_id: string;
  elevenlabs_voice_id?: string;
}

function loadConfig(env: Env): Record<string, TelegramIdentity> {
  if (!env.TELEGRAM_CONFIG) return {};
  try {
    return JSON.parse(env.TELEGRAM_CONFIG);
  } catch {
    return {};
  }
}

function getIdentity(env: Env, name: string): TelegramIdentity {
  const config = loadConfig(env);
  const identity = config[name.toLowerCase()];
  if (!identity?.bot_token) throw new Error(`Telegram identity '${name}' not found or missing bot_token`);
  return identity;
}

async function telegramApi(token: string, method: string, body: Record<string, unknown>): Promise<any> {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

function j(data: unknown): string { return JSON.stringify(data, null, 2); }

export const TOOLS: ToolDef[] = [
  {
    name: 'list_telegram_identities',
    description: 'List all available Telegram identities that can send messages.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'send_telegram_message',
    description: 'Send a text message via Telegram as a specific identity.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string', description: 'Identity to send as (e.g. "avery", "claude")' },
        message: { type: 'string', description: 'Text message to send' },
        chat_id: { type: 'string', description: 'Optional specific chat ID (uses default if not provided)' },
      },
      required: ['identity', 'message'],
    },
  },
  {
    name: 'send_telegram_photo',
    description: 'Send a photo via Telegram by URL as a specific identity.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string', description: 'Identity to send as' },
        photo_url: { type: 'string', description: 'Public URL of the image to send' },
        caption: { type: 'string', description: 'Optional caption for the photo' },
        chat_id: { type: 'string', description: 'Optional specific chat ID' },
      },
      required: ['identity', 'photo_url'],
    },
  },
  {
    name: 'send_telegram_voice',
    description: 'Generate and send a voice message via Telegram using ElevenLabs TTS.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string', description: 'Identity to send as (must have ElevenLabs voice configured)' },
        text: { type: 'string', description: 'Text to convert to speech and send' },
        chat_id: { type: 'string', description: 'Optional specific chat ID' },
      },
      required: ['identity', 'text'],
    },
  },
  {
    name: 'schedule_telegram_message',
    description: 'Schedule a message to be sent at a future time. Note: in cloud mode, scheduling uses Telegram\'s built-in schedule_date parameter.',
    inputSchema: {
      type: 'object',
      properties: {
        identity: { type: 'string', description: 'Identity to send as' },
        message: { type: 'string', description: 'Text message to send' },
        send_at: { type: 'string', description: 'When to send (ISO format, e.g. "2025-01-15T08:00:00Z")' },
        chat_id: { type: 'string', description: 'Optional specific chat ID' },
      },
      required: ['identity', 'message', 'send_at'],
    },
  },
];

async function handle(name: string, args: Record<string, unknown>, env: Env): Promise<string> {
  switch (name) {
    case 'list_telegram_identities': {
      const config = loadConfig(env);
      const identities = Object.entries(config).map(([name, cfg]) => ({
        name,
        display_name: name.charAt(0).toUpperCase() + name.slice(1),
        chat_id: cfg.chat_id,
        has_voice: !!cfg.elevenlabs_voice_id,
      }));
      if (!identities.length) return 'No Telegram identities configured.';
      let result = 'Available Telegram identities:\n\n';
      for (const id of identities) {
        result += `- ${id.display_name} (${id.name})\n  Chat ID: ${id.chat_id}\n  Voice: ${id.has_voice ? 'yes' : 'no'}\n`;
      }
      return result;
    }

    case 'send_telegram_message': {
      const id = getIdentity(env, String(args.identity));
      const chatId = String(args.chat_id || id.chat_id);

      // Send typing indicator
      await telegramApi(id.bot_token, 'sendChatAction', { chat_id: chatId, action: 'typing' });

      // Try with Markdown first, fall back to plain
      let result = await telegramApi(id.bot_token, 'sendMessage', {
        chat_id: chatId,
        text: String(args.message),
        parse_mode: 'Markdown',
      });

      if (!result.ok) {
        result = await telegramApi(id.bot_token, 'sendMessage', {
          chat_id: chatId,
          text: String(args.message),
        });
      }

      return result.ok
        ? `Message sent successfully as ${String(args.identity).charAt(0).toUpperCase() + String(args.identity).slice(1)}!`
        : `Error sending message: ${result.description || 'Unknown error'}`;
    }

    case 'send_telegram_photo': {
      const id = getIdentity(env, String(args.identity));
      const chatId = String(args.chat_id || id.chat_id);

      await telegramApi(id.bot_token, 'sendChatAction', { chat_id: chatId, action: 'upload_photo' });

      const body: Record<string, unknown> = { chat_id: chatId, photo: String(args.photo_url) };
      if (args.caption) body.caption = String(args.caption).slice(0, 1024);

      const result = await telegramApi(id.bot_token, 'sendPhoto', body);

      return result.ok
        ? `Photo sent successfully as ${String(args.identity).charAt(0).toUpperCase() + String(args.identity).slice(1)}!`
        : `Error sending photo: ${result.description || 'Unknown error'}`;
    }

    case 'send_telegram_voice': {
      const id = getIdentity(env, String(args.identity));
      const chatId = String(args.chat_id || id.chat_id);

      if (!id.elevenlabs_voice_id || !env.ELEVENLABS_API_KEY) {
        return `Error: ElevenLabs not configured for identity '${args.identity}'.`;
      }

      // Generate audio via ElevenLabs
      const ttsRes = await fetch(
        `https://api.elevenlabs.io/v1/text-to-speech/${id.elevenlabs_voice_id}`,
        {
          method: 'POST',
          headers: {
            'xi-api-key': env.ELEVENLABS_API_KEY,
            'Content-Type': 'application/json',
            Accept: 'audio/mpeg',
          },
          body: JSON.stringify({
            text: String(args.text),
            model_id: 'eleven_v3',
          }),
        },
      );

      if (!ttsRes.ok) {
        const errText = await ttsRes.text();
        return `Error generating voice: ${ttsRes.status} ${errText.slice(0, 200)}`;
      }

      const audioBuffer = await ttsRes.arrayBuffer();

      // Send via Telegram sendVoice using multipart
      await telegramApi(id.bot_token, 'sendChatAction', { chat_id: chatId, action: 'record_voice' });

      const boundary = '----SocialBackend' + Date.now();
      const parts: Uint8Array[] = [];
      const enc = new TextEncoder();

      // chat_id field
      parts.push(enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n${chatId}\r\n`));

      // voice file
      parts.push(enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="voice"; filename="voice.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n`));
      parts.push(new Uint8Array(audioBuffer));
      parts.push(enc.encode('\r\n'));

      // close
      parts.push(enc.encode(`--${boundary}--\r\n`));

      // Concat
      const totalLen = parts.reduce((acc, p) => acc + p.byteLength, 0);
      const body = new Uint8Array(totalLen);
      let offset = 0;
      for (const p of parts) { body.set(p, offset); offset += p.byteLength; }

      const sendRes = await fetch(`https://api.telegram.org/bot${id.bot_token}/sendVoice`, {
        method: 'POST',
        headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
        body: body,
      });
      const sendResult = await sendRes.json() as any;

      return sendResult.ok
        ? `Voice message sent successfully as ${String(args.identity).charAt(0).toUpperCase() + String(args.identity).slice(1)}!`
        : `Error sending voice: ${sendResult.description || 'Unknown error'}`;
    }

    case 'schedule_telegram_message': {
      const id = getIdentity(env, String(args.identity));
      const chatId = String(args.chat_id || id.chat_id);
      const sendAt = new Date(String(args.send_at));
      const nowMs = Date.now();

      if (sendAt.getTime() <= nowMs) {
        return 'Error: Scheduled time must be in the future.';
      }

      // Telegram supports schedule_date as Unix timestamp (for bots in some contexts).
      // As a fallback, we'll note this is a cloud limitation.
      const unixTime = Math.floor(sendAt.getTime() / 1000);

      const result = await telegramApi(id.bot_token, 'sendMessage', {
        chat_id: chatId,
        text: String(args.message),
        schedule_date: unixTime,
      });

      if (result.ok) {
        return `Message scheduled for ${args.send_at} as ${String(args.identity).charAt(0).toUpperCase() + String(args.identity).slice(1)}!`;
      }

      // If schedule_date isn't supported, inform the user
      return `Note: Telegram Bot API may not support scheduled messages directly. Error: ${result.description || 'Unknown'}. Consider using a Cron Trigger or Durable Object for deferred sending.`;
    }

    default:
      throw new Error(`Unknown telegram tool: ${name}`);
  }
}

const telegram: ToolModule = { tools: TOOLS, handle };
export default telegram;
