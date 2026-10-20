// Live filesystem smoke check; OpenAI response is a fixture (no generation fee).
// Never posts to Discord. Run explicitly with node --import tsx.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handleDiscordTool } from './tools';
import { asToolResult } from './image-content';

async function main() {
  const machineAgentUrl = process.env.MACHINE_AGENT_URL?.trim();
  const imageArchiveDir = process.env.IMAGE_ARCHIVE_DIR?.trim();
  const key = process.env.MACHINE_AGENT_API_KEY?.trim()
    || (process.env.MACHINE_AGENT_KEY_FILE ? readFileSync(process.env.MACHINE_AGENT_KEY_FILE, 'utf8').trim() : '');
  if (!machineAgentUrl || !imageArchiveDir || !key) {
    throw new Error('Set MACHINE_AGENT_URL, IMAGE_ARCHIVE_DIR, and MACHINE_AGENT_API_KEY (or MACHINE_AGENT_KEY_FILE).');
  }
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V8AAAAASUVORK5CYII=';
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => String(url).startsWith('https://api.openai.com/')
    ? Response.json({ data: [{ b64_json: png }] }) : realFetch(url, init);
  try {
    const result = await handleDiscordTool('unused', 'discord_generate_image_only',
      { identity: 'archive_verification_cloud', prompt: 'Fixture smoke check', output_format: 'png' },
      new Set(['discord_generate_image_only']), {
        openAiApiKey: 'fixture-only', machineAgentUrl, machineAgentApiKey: key, imageArchiveDir,
      });
    const text = asToolResult(result).content.filter(c => c.type === 'text').map(c => c.text).join('\n');
    const path = text.split('\n').find(line => line.startsWith('Saved to: '))?.slice(10);
    assert.ok(path, text);
    assert.deepEqual(readFileSync(path), Buffer.from(png, 'base64'));
    console.log(`Verified cloud archive bytes on disk: ${path}`);
  } finally { globalThis.fetch = realFetch; }
}
main().catch(error => { console.error(error instanceof Error ? error.message : 'Live image archive verification failed'); process.exitCode = 1; });
