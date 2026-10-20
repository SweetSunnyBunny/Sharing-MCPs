import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from './index';
import { asToolResult, messageImageResult, MAX_IMAGE_BYTES } from './image-content';
import { handleDiscordTool } from './tools';
import { formatMessage } from './discord';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V8AAAAASUVORK5CYII=', 'base64');
const attachment = { url: 'https://cdn.discordapp.com/attachments/123/456/test.png?ex=fresh', filename: 'test.png', content_type: 'image/png' };
const message = { id: '456', channel_id: '123', author: { username: 'Claude', bot: true }, attachments: [attachment] };
const allowed = new Set(['discord_fetch_image', 'discord_fetch_dm_image']);

test('channel and DM images pass through the real Worker as image blocks, never base64 text', async () => {
  const original = globalThis.fetch;
  const bodies: string[] = [];
  try {
    globalThis.fetch = async (url, init) => {
      const value = String(url);
      if (value.includes('cdn.discordapp.com')) {
        assert.equal(init?.redirect, 'manual');
        return new Response(png);
      }
      assert.equal((init!.headers as any).Authorization, 'Bot claude-token');
      if (value.endsWith('/users/@me/channels')) {
        bodies.push(init!.body as string);
        return Response.json({ id: '123' });
      }
      assert.ok(value.endsWith('/channels/123/messages/456'));
      return Response.json(message);
    };
    for (const name of allowed) {
      const request = new Request('https://worker.test/mcp/test-secret', {
        method: 'POST', body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: {
          name, arguments: { identity: 'avery', channel_id: '123', user_id: '789', message_id: '456' },
        } }),
      });
      const response = await worker.fetch(request, {
        MCP_SECRET_PATH: 'test-secret', DISCORD_BOT_TOKEN_AVERY: 'claude-token', ALLOWED_TOOLS: [...allowed].join(','),
      } as any, { waitUntil() {}, passThroughOnException() {} });
      const result = (await response.json() as any).result;
      assert.ok(!result.isError);
      assert.deepEqual(result.content[1], { type: 'image', data: png.toString('base64'), mimeType: 'image/png' });
      assert.match(result.content[0].text, /Source URL: https:/);
      assert.ok(!result.content[0].text.includes(png.toString('base64')));
      assert.ok(!result.content[0].text.includes('test-secret'));
    }
    assert.deepEqual(JSON.parse(bodies[0]), { recipient_id: '789' });
  } finally { globalThis.fetch = original; }
});

test('large images encode without argument-stack overflow and oversized originals use Discord previews', async () => {
  const original = globalThis.fetch;
  try {
    const payload = Buffer.concat([png, Buffer.alloc(350000)]);
    globalThis.fetch = async () => new Response(payload);
    const result = await messageImageResult(message, 0, '123');
    assert.equal(result.content[1].type, 'image');
    if (result.content[1].type === 'image') assert.equal(Buffer.from(result.content[1].data, 'base64').length, payload.length);
    const urls: string[] = [];
    globalThis.fetch = async url => {
      urls.push(String(url));
      if (urls.length === 1) return new Response(png, { headers: { 'Content-Length': String(MAX_IMAGE_BYTES + 1) } });
      return new Response(png);
    };
    const preview = await messageImageResult(message, 0, '123');
    assert.equal(preview.content[1].type, 'image');
    assert.equal(new URL(urls[1]).hostname, 'media.discordapp.net');
    assert.equal(new URL(urls[1]).searchParams.get('width'), '1280');
  } finally { globalThis.fetch = original; }
});

test('embedded and MIME-less images work; indexes, non-images and foreign URLs are rejected', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(png);
    assert.equal((await messageImageResult({ ...message, attachments: [], embeds: [{ image: { url: attachment.url } }] }, 0, '123')).content[1].type, 'image');
    assert.equal((await messageImageResult({ ...message, attachments: [{ url: attachment.url }] }, 0, '123')).content[1].type, 'image');
    for (const index of [-1, 0.5, '0', 3]) await assert.rejects(messageImageResult(message, index, '123'));
    await assert.rejects(messageImageResult({ ...message, attachments: [{ ...attachment, content_type: 'application/pdf' }] }, 0, '123'));
    await assert.rejects(messageImageResult({ ...message, attachments: [{ ...attachment, url: 'http://127.0.0.1/private' }] }, 0, '123'));
    globalThis.fetch = async () => new Response('<html>not an image</html>');
    const failed = await messageImageResult(message, 0, '123');
    assert.equal(failed.isError, true);
    assert.ok(failed.content.every(c => c.type === 'text'));
  } finally { globalThis.fetch = original; }
});

test('a rejected Discord resize falls back to the signed original attachment', async () => {
  const original = globalThis.fetch;
  const urls: string[] = [];
  try {
    globalThis.fetch = async url => {
      urls.push(String(url));
      return new URL(String(url)).hostname === 'media.discordapp.net'
        ? new Response('Forbidden', { status: 403 }) : new Response(png);
    };
    const result = await messageImageResult({ ...message, attachments: [{
      ...attachment, width: 2048, height: 2048,
      proxy_url: 'https://media.discordapp.net/attachments/123/456/test.png?ex=fresh',
    }] }, 0, '123');
    assert.equal(result.content[1].type, 'image');
    assert.equal(urls[1], attachment.url);
  } finally { globalThis.fetch = original; }
});

test('readable history contains everything needed to revisit a sent image', () => {
  const text = formatMessage(message);
  assert.match(text, /message_id: 456, channel_id: 123/);
  assert.match(text, /\[0\] test.png/);
  assert.equal(asToolResult('ordinary response').content[0].type, 'text');
});

test('Discord failure is returned without fetching attachments or leaking credentials', async () => {
  const original = globalThis.fetch;
  let count = 0;
  try {
    globalThis.fetch = async () => { count++; return Response.json({ message: 'Missing Access' }, { status: 403 }); };
    await assert.rejects(handleDiscordTool('private-token', 'discord_fetch_image', { channel_id: '123', message_id: '456' }, allowed), /Missing Access/);
    assert.equal(count, 1);
  } finally { globalThis.fetch = original; }
});
