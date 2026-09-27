// Match the machine Worker: image bytes belong in MCP image content, never text.
export type ContentBlock = { type: 'text'; text: string }
  | { type: 'image'; data: string; mimeType: string };
export interface RichToolResult { content: ContentBlock[]; isError?: boolean }
export type DiscordToolResult = string | RichToolResult;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export function asToolResult(result: DiscordToolResult): RichToolResult {
  return typeof result === 'string' ? { content: [{ type: 'text', text: result }] } : result;
}

export function imageResult(text: string, data: string, mimeType: string): RichToolResult {
  if (data.length > Math.ceil(MAX_IMAGE_BYTES / 3) * 4) {
    return { content: [{ type: 'text', text: `${text}\nPreview exceeds the 5 MB vision limit. Use the saved original or fetch the posted message for a smaller preview.` }] };
  }
  return { content: [{ type: 'text', text }, { type: 'image', data, mimeType }] };
}

function discordMediaUrl(value: string): URL {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')
      || !/^(cdn\.discordapp\.com|media\.discordapp\.net|images-ext-\d+\.discordapp\.net)$/.test(url.hostname)) {
    throw new Error('Image must come from the Discord attachment CDN or media proxy');
  }
  return url;
}

function previewUrl(value: string, dimension: number): string {
  const url = discordMediaUrl(value);
  if (url.hostname === 'cdn.discordapp.com') url.hostname = 'media.discordapp.net';
  url.searchParams.set('width', String(dimension));
  url.searchParams.set('height', String(dimension));
  url.searchParams.set('format', 'webp');
  url.searchParams.set('quality', '80');
  return url.toString();
}

function detectImageType(data: Uint8Array): string {
  if (data[0] === 0x89 && String.fromCharCode(...data.slice(1, 4)) === 'PNG') return 'image/png';
  if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return 'image/jpeg';
  if (String.fromCharCode(...data.slice(0, 6)).match(/^GIF8[79]a$/)) return 'image/gif';
  if (String.fromCharCode(...data.slice(0, 4)) === 'RIFF' && String.fromCharCode(...data.slice(8, 12)) === 'WEBP') return 'image/webp';
  throw new Error('Discord returned an unsupported image or non-image response');
}

async function fetchPreview(url: string): Promise<{ data: string; mimeType: string }> {
  discordMediaUrl(url);
  // Workers supports manual/follow, not Node's redirect:'error'. Checking status
  // below rejects redirects without following them outside the Discord CDN.
  const response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(20000) });
  if (!response.ok) throw new Error(`Discord image download failed (HTTP ${response.status})`);
  if (Number(response.headers.get('content-length')) > MAX_IMAGE_BYTES) {
    await response.body?.cancel();
    throw new Error('Image exceeds preview limit');
  }
  if (!response.body) throw new Error('Discord returned no image body');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_IMAGE_BYTES) {
        await reader.cancel();
        throw new Error('Image exceeds preview limit');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  const mimeType = detectImageType(bytes);
  // Chunking avoids the argument-stack overflow caused by spreading a full PNG.
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return { data: btoa(binary), mimeType };
}

export async function messageImageResult(msg: any, index: unknown, channelId: string): Promise<RichToolResult> {
  const idx = index ?? 0;
  if (typeof idx !== 'number' || !Number.isInteger(idx) || idx < 0) {
    throw new Error('attachment_index must be a non-negative integer');
  }
  const attachments = msg.attachments || [];
  // When there are no attachments, also support link previews and webhook images.
  const images = attachments.length ? attachments : (msg.embeds || []).flatMap((embed: any) =>
    embed.image ? [embed.image] : embed.thumbnail ? [embed.thumbnail] : []);
  const selected = images[idx];
  if (!selected) throw new Error(`Image index ${idx} out of range (${images.length} attachment(s) or embedded image(s))`);
  const declaredType = selected.content_type;
  if (declaredType && !declaredType.startsWith('image/')) throw new Error('Selected attachment is not an image');
  // Attachment originals use the signed CDN URL. External embeds need Discord's
  // proxy. Some attachment proxy URLs reject resizes even while the original works.
  const source = attachments.length ? selected.url : selected.proxy_url || selected.url;
  discordMediaUrl(source);
  const text = [
    `Image from message ${msg.id}, channel ${channelId}, ${attachments.length ? 'attachment' : 'embedded image'} ${idx}.`,
    `Source URL: ${selected.url}`,
    `Review again: discord_fetch_image(channel_id="${channelId}", message_id="${msg.id}", attachment_index=${idx}) with the same identity.`,
    'The source URL can expire; fetching by message ID obtains a fresh URL.',
  ].join('\n');
  const needsPreview = selected.size > MAX_IMAGE_BYTES || Math.max(selected.width || 0, selected.height || 0) > 1600;
  const urls = [...new Set([
    needsPreview ? previewUrl(source, 1280) : source,
    source, previewUrl(source, 1280), previewUrl(source, 768),
  ])];
  let failure = 'Image preview unavailable';
  for (const url of urls) {
    try {
      const preview = await fetchPreview(url);
      return imageResult(text, preview.data, preview.mimeType);
    } catch (error) {
      failure = error instanceof Error ? error.message : 'Image preview unavailable';
    }
  }
  return { isError: true, content: [{ type: 'text', text: `${text}\nCould not load a supported image preview within 5 MB: ${failure}. The image has NOT been viewed; the source link is available above.` }] };
}
