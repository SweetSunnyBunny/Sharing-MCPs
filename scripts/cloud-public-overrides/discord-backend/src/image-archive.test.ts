import assert from 'node:assert/strict';
import { test } from 'node:test';
import { archiveGeneratedImage } from './image-archive';
import { handleDiscordTool } from './tools';
import { asToolResult } from './image-content';

const IMAGE_ARCHIVE_DIR = 'C:/example/image-archive';
const config = { machineAgentUrl: 'https://machine.test', machineAgentApiKey: 'test-key', imageArchiveDir: IMAGE_ARCHIVE_DIR };
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V8AAAAASUVORK5CYII=';

test('both generation routes archive the exact bytes before Discord delivery', async () => {
  const original = globalThis.fetch;
  try {
    for (const name of ['discord_generate_image_only', 'discord_generate_image']) {
      const calls: string[] = [];
      globalThis.fetch = async (url, init) => {
        calls.push(String(url));
        if (String(url).includes('openai.com')) {
          const body = JSON.parse(init!.body as string);
          assert.equal(body.model, 'gpt-image-2.5-flare');
          return Response.json({ data: [{ b64_json: png }] });
        }
        if (String(url).includes('machine.test')) {
          const body = JSON.parse(init!.body as string);
          assert.equal(body.tool, 'fs_write_binary');
          assert.equal(body.arguments.base64_content, png);
          assert.ok(body.arguments.path.startsWith(`${IMAGE_ARCHIVE_DIR}/gpt_image_`));
          assert.equal((init!.headers as any).Authorization, 'Bearer test-key');
          return Response.json({ ok: true, result: { success: true, path: body.arguments.path } });
        }
        assert.equal(calls.length, 3);
        return Response.json({ id: 'test-message' });
      };
      const result = await handleDiscordTool('test-token', name,
        { identity: '../Claude', prompt: 'test', channel_id: '123', output_format: 'png' },
        new Set([name]), { ...config, openAiApiKey: 'test-openai' });
      const content = asToolResult(result).content;
      const text = content.filter(c => c.type === 'text').map(c => c.text).join('\n');
      assert.ok(text.includes(`Saved to: ${IMAGE_ARCHIVE_DIR}`));
      assert.ok(!text.includes(png));
      assert.ok(content.some(c => c.type === 'image' && c.data === png));
      assert.equal(calls.length, name.endsWith('_only') ? 2 : 3);
    }
  } finally { globalThis.fetch = original; }
});

test('nested filesystem errors warn; retries reuse the path; missing config never claims a save', async () => {
  const original = globalThis.fetch;
  const paths: string[] = [];
  try {
    globalThis.fetch = async (_url, init) => {
      paths.push(JSON.parse(init!.body as string).arguments.path);
      return Response.json({ ok: true, result: { success: false, error: 'disk full' } });
    };
    assert.match(await archiveGeneratedImage(png, 'jpeg', 'Claude', config), /WARNING/);
    assert.equal(paths.length, 2);
    assert.equal(paths[0], paths[1]);
    assert.ok(paths[0].endsWith('.jpg'));
    assert.match(await archiveGeneratedImage(png, 'png', 'Claude'), /NOT saved/);
    assert.match(await archiveGeneratedImage(png, 'png', 'Claude', { ...config, imageArchiveDir: '' }), /NOT saved/);
    assert.equal(paths.length, 2, 'missing directory must not invoke the machine agent');
  } finally { globalThis.fetch = original; }
});

test('configured Windows archive directories retain their path separator', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (_url, init) => {
      const path = JSON.parse(init!.body as string).arguments.path;
      assert.ok(path.startsWith('D:\\Images\\gpt_image_'));
      return Response.json({ ok: true, result: { success: true, path } });
    };
    assert.match(await archiveGeneratedImage(png, 'png', 'example', { ...config, imageArchiveDir: 'D:\\Images\\' }), /^Saved to:/);
  } finally { globalThis.fetch = original; }
});

test('a transport failure retries successfully and separate saves never collide', async () => {
  const original = globalThis.fetch;
  const paths: string[] = [];
  try {
    globalThis.fetch = async (_url, init) => {
      const path = JSON.parse(init!.body as string).arguments.path;
      paths.push(path);
      if (paths.length === 1) throw new Error('temporary disconnect');
      return Response.json({ ok: true, result: { success: true, path } });
    };
    assert.match(await archiveGeneratedImage(png, 'webp', 'Claude', config), /^Saved to:/);
    assert.match(await archiveGeneratedImage(png, 'webp', 'Claude', config), /^Saved to:/);
    assert.equal(paths[0], paths[1]);
    assert.notEqual(paths[1], paths[2]);
  } finally { globalThis.fetch = original; }
});
