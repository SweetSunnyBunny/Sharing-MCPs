
const DISCORD_API = 'https://discord.com/api/v10';

export interface DiscordResponse {
  ok: boolean;
  status: number;
  data: unknown;
}

// Longest rate-limit wait we'll sit through inside a single request. Anything longer
// (usually a global or shared-bucket limit) is returned as an error instead of
// holding the Worker open.
const MAX_RETRY_WAIT_MS = 10_000;

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // Non-JSON bodies happen on upstream 5xx (HTML error pages) — keep them readable
    return { message: text.slice(0, 500) };
  }
}

async function sendWithRetry(
  method: string,
  path: string,
  makeInit: () => RequestInit,
): Promise<DiscordResponse> {
  const url = `${DISCORD_API}${path}`;
  let lastStatus = 500;

  // Retry up to 2 times for rate limits
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(url, makeInit());
    lastStatus = res.status;

    if (res.status === 429) {
      const retryData = await readBody(res) as { retry_after?: number } | null;
      const retryAfter = (retryData?.retry_after ?? 1) * 1000;
      if (retryAfter > MAX_RETRY_WAIT_MS) {
        return { ok: false, status: 429, data: { message: `Rate limited; retry after ${Math.ceil(retryAfter / 1000)}s` } };
      }
      console.log(`Rate limited on ${method} ${path}, retrying in ${retryAfter}ms`);
      await new Promise(r => setTimeout(r, retryAfter));
      continue;
    }

    if (res.status === 204) {
      return { ok: true, status: 204, data: null };
    }

    const data = await readBody(res);
    return { ok: res.ok, status: res.status, data };
  }

  return { ok: false, status: lastStatus, data: { message: 'Rate limit retries exhausted' } };
}

export async function discordFetch(
  token: string,
  method: string,
  path: string,
  body?: unknown,
  extraHeaders?: Record<string, string>,
): Promise<DiscordResponse> {
  const headers: Record<string, string> = {
    Authorization: `Bot ${token}`,
    'User-Agent': 'CloudDiscord/1.0',
    ...extraHeaders,
  };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  return sendWithRetry(method, path, () => ({
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }));
}

// Send a message with file attachment via multipart/form-data
export async function discordFetchMultipart(
  token: string,
  method: string,
  path: string,
  payload: Record<string, unknown>,
  fileData: Uint8Array,
  fileName: string,
  contentType: string,
): Promise<DiscordResponse> {
  // Build a fresh body per attempt so a retry never sends a consumed stream
  return sendWithRetry(method, path, () => {
    const form = new FormData();
    form.append('payload_json', JSON.stringify(payload));
    form.append('files[0]', new Blob([fileData], { type: contentType }), fileName);
    return {
      method,
      headers: {
        Authorization: `Bot ${token}`,
        'User-Agent': 'CloudDiscord/1.0',
      },
      body: form,
    };
  });
}

export async function discordWebhookFetch(
  webhookId: string,
  webhookToken: string,
  body: Record<string, unknown>,
): Promise<DiscordResponse> {
  const res = await fetch(`${DISCORD_API}/webhooks/${webhookId}/${webhookToken}?wait=true`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'CloudDiscord/1.0' },
    body: JSON.stringify(body),
  });

  if (res.status === 204) {
    return { ok: true, status: 204, data: null };
  }

  const data = await readBody(res);
  return { ok: res.ok, status: res.status, data };
}

// ============ TIME HELPERS (catch-up) ============

const DISCORD_EPOCH = 1420070400000n;

// Snowflakes encode their creation time, so a time can be turned into an ID to page or filter by
export function snowflakeFromTime(ms: number): string {
  return ((BigInt(Math.floor(ms)) - DISCORD_EPOCH) << 22n).toString();
}

export function parseSince(since: string, now = Date.now()): number {
  const relative = since.trim().match(/^(\d+(?:\.\d+)?)\s*(m|min|h|hr|d|day)s?$/i);
  if (relative) {
    const unit = relative[2].toLowerCase()[0];
    const ms = unit === 'm' ? 60_000 : unit === 'h' ? 3_600_000 : 86_400_000;
    return now - Number(relative[1]) * ms;
  }
  const parsed = Date.parse(since);
  if (Number.isNaN(parsed)) throw new Error(`Can't read "${since}" as a time. Use an ISO time or something like 30m, 2h, 1d.`);
  return parsed;
}

// ============ MESSAGE FORMATTING ============

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

function emojiLabel(emoji: any): string {
  return emoji?.id ? `:${emoji.name}:` : (emoji?.name ?? '?');
}

// Format a Discord message for a model reading it: who (display name and handle),
// what, and what's attached. The "message_id:" / "channel_id:" labels and numbered
// attachments are ours and stay stable — tools and habits rely on them.
export function formatMessage(msg: any): string {
  const timestamp = msg.timestamp;
  const user = msg.author ?? {};
  const handle = user.username ?? 'Unknown';
  const author = user.global_name && user.global_name !== handle ? `${user.global_name} (@${handle})` : handle;
  const bot = user.bot ? ' [bot]' : '';
  const edited = msg.edited_timestamp ? ' (edited)' : '';
  // type 1 is a forward, not a reply
  const replyTo = msg.message_reference?.message_id && msg.message_reference?.type !== 1
    ? ` (reply to ${msg.message_reference.message_id})`
    : '';

  const extras: string[] = [];
  for (const snapshot of msg.message_snapshots ?? []) {
    const forwarded = snapshot.message ?? {};
    const files = forwarded.attachments?.length ? ` [+${forwarded.attachments.length} attachment(s)]` : '';
    extras.push(`Forwarded: ${clip(forwarded.content || '[no text]', 500)}${files}`);
  }
  if (msg.attachments?.length) {
    extras.push(`Attachments:\n${msg.attachments.map((a: any, i: number) => {
      const voice = a.duration_secs ? `, voice message ${Math.round(a.duration_secs)}s` : '';
      return `    [${i}] ${a.filename || 'file'} (${a.content_type || 'unknown type'}${voice}): ${a.url}`;
    }).join('\n')}`);
  }
  (msg.embeds ?? []).forEach((embed: any) => {
    const parts = [embed.title, embed.description].filter(Boolean).map((t: string) => clip(t, 300));
    const fields = embed.fields?.length ? ` [${embed.fields.length} field(s)]` : '';
    const image = embed.image?.url ?? embed.thumbnail?.url;
    const imageText = image ? `\n    Image: ${image}` : '';
    if (parts.length || fields || image) extras.push(`Embed: ${parts.join(' — ')}${fields}${imageText}`);
  });
  if (msg.poll) {
    const counts = new Map((msg.poll.results?.answer_counts ?? []).map((c: any) => [c.answer_id, c.count]));
    const answers = (msg.poll.answers ?? []).map((a: any) => `${a.poll_media?.text ?? '?'} (${counts.get(a.answer_id) ?? 0})`);
    const state = msg.poll.results?.is_finalized ? 'closed' : 'open';
    extras.push(`Poll (${state}): ${msg.poll.question?.text ?? ''} — ${answers.join(' / ')}`);
  }
  if (msg.sticker_items?.length) {
    extras.push(`Stickers: ${msg.sticker_items.map((st: any) => st.name).join(', ')}`);
  }
  if (msg.reactions?.length) {
    extras.push(`Reactions: ${msg.reactions.map((r: any) => `${emojiLabel(r.emoji)} ${r.count}`).join(', ')}`);
  }
  if (msg.thread) {
    extras.push(`Thread: ${msg.thread.name} (ID: ${msg.thread.id})`);
  }

  // Show <@id> mentions as @name, so the reader knows who was addressed
  const named = new Map<string, string>((msg.mentions ?? []).map((u: any) => [u.id, u.global_name || u.username]));
  const text = (msg.content || '').replace(/<@!?(\d+)>/g, (tag: string, id: string) => named.has(id) ? `@${named.get(id)}` : tag);
  const content = text || (extras.length ? '' : '[no text content]');
  const tail = extras.map(e => `\n  ${e}`).join('');
  return `[${timestamp}] ${author}${bot} (message_id: ${msg.id}, channel_id: ${msg.channel_id})${replyTo}${edited}: ${content}${tail}`;
}

// ============ LONG MESSAGES ============

// Discord rejects content over 2000 characters. Split on paragraph, then line, then word
// boundaries, and keep code fences balanced across the pieces. (Friend's, verbatim.)
export const MESSAGE_LIMIT = 2000;

export function splitMessage(text: string, limit = MESSAGE_LIMIT): string[] {
  if (text.length <= limit) return [text];
  const chunks: string[] = [];
  let rest = text;
  let openFence: string | null = null;

  while (rest.length > 0) {
    const prefix = openFence ? `${openFence}\n` : '';
    // Room for a closing fence if this piece ends inside a code block
    const budget = limit - prefix.length - 4;
    if (prefix.length + rest.length <= limit) {
      chunks.push(prefix + rest);
      break;
    }
    let cut = -1;
    let sepLength = 0;
    for (const sep of ['\n\n', '\n', ' ']) {
      const at = rest.lastIndexOf(sep, budget);
      if (at > budget / 2) { cut = at; sepLength = sep.length; break; }
    }
    if (cut === -1) cut = budget;
    let piece = rest.slice(0, cut);
    // Drop only the break we cut at, so indentation on the next line survives (code blocks)
    rest = rest.slice(cut + sepLength);

    // Track whether this piece leaves a ``` block open
    let fence: string | null = openFence;
    for (const match of piece.matchAll(/^```(\S*)/gm)) {
      fence = fence ? null : '```' + match[1];
    }
    piece = prefix + piece;
    if (fence) piece += '\n```';
    openFence = fence;
    chunks.push(piece);
  }
  return chunks;
}
