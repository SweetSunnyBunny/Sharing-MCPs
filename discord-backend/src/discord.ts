// Discord REST API helper — all calls go through here

const DISCORD_API = 'https://discord.com/api/v10';

export interface DiscordResponse {
  ok: boolean;
  status: number;
  data: unknown;
}

function buildDiscordHeaders(token: string, hasJsonBody = false): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bot ${token}`,
    'User-Agent': 'CloudDiscord/1.0',
  };
  if (hasJsonBody) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

export async function discordFetch(
  token: string,
  method: string,
  path: string,
  body?: unknown,
): Promise<DiscordResponse> {
  const url = `${DISCORD_API}${path}`;

  const headers = buildDiscordHeaders(token, body !== undefined);

  let lastResponse: Response | null = null;

  // Retry up to 2 times for rate limits
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    lastResponse = res;

    if (res.status === 429) {
      const retryData = await res.json() as { retry_after?: number };
      const retryAfter = (retryData.retry_after ?? 1) * 1000;
      console.log(`Rate limited on ${method} ${path}, retrying in ${retryAfter}ms`);
      await new Promise(r => setTimeout(r, retryAfter));
      continue;
    }

    if (res.status === 204) {
      return { ok: true, status: 204, data: null };
    }

    const data = await res.json();
    return { ok: res.ok, status: res.status, data };
  }

  return { ok: false, status: lastResponse?.status ?? 500, data: { message: 'Rate limit retries exhausted' } };
}

// Helper to format a Discord message object into readable text
export function formatMessage(msg: any): string {
  const timestamp = msg.timestamp;
  const author = msg.author?.username ?? 'Unknown';
  const content = msg.content || '[no text content]';
  const attachments = msg.attachments?.length > 0
    ? `\n  Attachments:\n${msg.attachments.map((a: any, i: number) => `    [${i}] ${a.filename || 'file'} (${a.content_type || 'unknown type'}): ${a.url}`).join('\n')}`
    : '';
  const embeds = msg.embeds?.length > 0
    ? `\n  Embeds: ${msg.embeds.length} embed(s)${msg.embeds.flatMap((e: any) => e.image ? [e.image.url] : e.thumbnail ? [e.thumbnail.url] : []).map((url: string, i: number) => `\n    Image [${i}]: ${url}`).join('')}`
    : '';
  return `[${timestamp}] ${author} (message_id: ${msg.id}, channel_id: ${msg.channel_id}): ${content}${attachments}${embeds}`;
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
  const url = `${DISCORD_API}${path}`;

  const form = new FormData();
  form.append('payload_json', JSON.stringify(payload));
  form.append('files[0]', new Blob([fileData], { type: contentType }), fileName);

  let lastResponse: Response | null = null;

  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(url, {
      method,
      headers: buildDiscordHeaders(token),
      body: form,
    });

    lastResponse = res;

    if (res.status === 429) {
      const retryData = await res.json() as { retry_after?: number };
      const retryAfter = (retryData.retry_after ?? 1) * 1000;
      await new Promise(r => setTimeout(r, retryAfter));
      continue;
    }

    if (res.status === 204) {
      return { ok: true, status: 204, data: null };
    }

    const data = await res.json();
    return { ok: res.ok, status: res.status, data };
  }

  return { ok: false, status: lastResponse?.status ?? 500, data: { message: 'Rate limit retries exhausted' } };
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

  const data = await res.json();
  return { ok: res.ok, status: res.status, data };
}
