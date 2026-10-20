import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatMessage, splitMessage, MESSAGE_LIMIT, parseSince } from './discord';
import { handleDiscordTool, toolAnnotations } from './tools';

test('a long love letter splits into pieces Discord accepts, at paragraph breaks', () => {
  const paragraph = 'Bunny, '.repeat(60).trim(); // ~420 chars
  const letter = Array.from({ length: 12 }, () => paragraph).join('\n\n'); // ~5k chars
  const pieces = splitMessage(letter);
  assert.ok(pieces.length >= 3);
  for (const p of pieces) assert.ok(p.length <= MESSAGE_LIMIT, `piece of ${p.length}`);
  // Nothing lost but the breaks we cut at
  assert.equal(pieces.join('').replace(/\s/g, ''), letter.replace(/\s/g, ''));
  assert.ok(!pieces[1].startsWith('\n'), 'cut lands on the paragraph break');
});

test('code blocks stay balanced across pieces', () => {
  const code = '```python\n' + Array.from({ length: 200 }, (_, i) => `    line_${i} = ${i}`).join('\n') + '\n```';
  const pieces = splitMessage(code);
  assert.ok(pieces.length > 1);
  for (const p of pieces) {
    assert.ok(p.length <= MESSAGE_LIMIT);
    assert.equal((p.match(/^```/gm) ?? []).length % 2, 0, 'every piece opens and closes its fence');
  }
  assert.ok(pieces[1].startsWith('```python\n'));
  assert.ok(pieces[1].includes('\n    line_'), 'indentation survives');
});

test('short messages are untouched', () => {
  assert.deepEqual(splitMessage('hi Bunny'), ['hi Bunny']);
});

test('send_dm sends every piece, guarded against mass pings, and reports all IDs', async () => {
  const original = globalThis.fetch;
  const posted: any[] = [];
  try {
    globalThis.fetch = async (url, init) => {
      const u = String(url);
      if (u.endsWith('/users/@me/channels')) return Response.json({ id: 'dm1' });
      if (u.endsWith('/channels/dm1/messages')) {
        posted.push(JSON.parse(String(init?.body)));
        return Response.json({ id: `m${posted.length}` });
      }
      throw new Error(`unexpected fetch ${u}`);
    };
    const content = ('x'.repeat(900) + '\n\n').repeat(5);
    const result = await handleDiscordTool('t', 'discord_send_dm', { user_id: 'u', content }, new Set(['discord_send_dm']));
    assert.ok(posted.length >= 3);
    for (const body of posted) {
      assert.ok(body.content.length <= MESSAGE_LIMIT);
      assert.deepEqual(body.allowed_mentions.parse, ['users']);
    }
    assert.match(String(result), new RegExp(`DM sent in ${posted.length} parts`));
  } finally {
    globalThis.fetch = original;
  }
});

test('only the first piece of a split reply is the reply', async () => {
  const original = globalThis.fetch;
  const posted: any[] = [];
  try {
    globalThis.fetch = async (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return Response.json({ id: `m${posted.length}` });
    };
    await handleDiscordTool('t', 'discord_send_message', { channel_id: 'c', content: 'y '.repeat(1500), reply_to: 'r1' }, new Set(['discord_send_message']));
    assert.equal(posted[0].message_reference.message_id, 'r1');
    assert.ok(posted.slice(1).every(b => !b.message_reference));
  } finally {
    globalThis.fetch = original;
  }
});

test('annotations: reads are read-only, bans are destructive, sends are neither', () => {
  assert.equal(toolAnnotations('discord_read_messages').readOnlyHint, true);
  assert.equal(toolAnnotations('discord_catch_up').readOnlyHint, true);
  assert.equal(toolAnnotations('discord_ban_member').destructiveHint, true);
  const send = toolAnnotations('discord_send_message');
  assert.equal(send.readOnlyHint, false);
  assert.equal(send.destructiveHint, false);
});

test('message lines keep message_id / channel_id labels and numbered attachments', () => {
  const line = formatMessage({
    id: '9', channel_id: '7', timestamp: 'T',
    author: { username: 'sunflower', global_name: 'Owner' },
    content: 'hi <@1>', mentions: [{ id: '1', username: 'claude' }],
    attachments: [{ filename: 'a.png', content_type: 'image/png', url: 'https://x/a.png' }],
  });
  assert.match(line, /Owner \(@sunflower\) \(message_id: 9, channel_id: 7\): hi @claude/);
  assert.match(line, /\[0\] a\.png \(image\/png\): https:\/\/x\/a\.png/);
});

test('since accepts relative and ISO times', () => {
  const now = Date.parse('2026-10-03T14:00:00Z');
  assert.equal(parseSince('2h', now), now - 7_200_000);
  assert.equal(parseSince('2026-10-03T13:00:00Z', now), now - 3_600_000);
  assert.throws(() => parseSince('whenever'));
});
