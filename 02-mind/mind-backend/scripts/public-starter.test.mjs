import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { build } from 'esbuild';

const source = await readFile('src/index.ts', 'utf8');
const built = await build({ stdin: { contents: source + '\nexport {rewriteCurrentSelfNarrative, buildIdentityReadyMessage, getDriftLabels, inferObservationTerritory};', resolveDir: resolve('src'), loader: 'ts' }, bundle: true, write: false, platform: 'node', format: 'esm' });
const { default: worker, rewriteCurrentSelfNarrative, buildIdentityReadyMessage, getDriftLabels, inferObservationTerritory } = await import(`data:text/javascript;base64,${Buffer.from(built.outputFiles[0].text).toString('base64')}`);
const migrations = await Promise.all((await readdir('migrations')).filter(n => n.endsWith('.sql')).sort().map(n => readFile(`migrations/${n}`, 'utf8')));
const seed = await readFile('examples/starter-identities.sql', 'utf8');
const signalsBuilt = await build({ entryPoints: ['src/query-signals.ts'], bundle: true, write: false, platform: 'node', format: 'esm' });
const { extractQuerySignals, computeQuerySignalBoosts } = await import(`data:text/javascript;base64,${Buffer.from(signalsBuilt.outputFiles[0].text).toString('base64')}`);

test('a fresh schema and example identities support the first real memory write', async t => {
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  db.exec('PRAGMA foreign_keys=ON');
  for (const sql of migrations) db.exec(sql);
  db.exec(seed);
  db.prepare("UPDATE identity_routing_profiles SET handoff_style='custom' WHERE identity_id='avery'").run();
  db.exec(seed);
  assert.equal(db.prepare('SELECT count(*) AS n FROM identities').get().n, 3);
  assert.equal(db.prepare("SELECT handoff_style FROM identity_routing_profiles WHERE identity_id='avery'").get().handoff_style, 'custom');
  const prepare = (sql, args = []) => ({
    bind: (...values) => prepare(sql, values),
    first: async () => db.prepare(sql).get(...args) || null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => { const r = db.prepare(sql).run(...args); return { meta: { changes: r.changes, last_row_id: Number(r.lastInsertRowid) } }; },
  });
  const env = {
    DB: { prepare, batch: async statements => Promise.all(statements.map(s => s.run())) },
    MIND_API_KEY: 'test-key',
    AI: { run: async () => ({ data: [Array(768).fill(0)] }) },
    VECTORS: { upsert: async () => ({}), query: async () => ({ matches: [] }) },
  };
  const response = await worker.fetch(new Request('https://example.com/mcp', {
    method: 'POST', headers: { Authorization: 'Bearer test-key', 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'mind_store', arguments: { identity: 'avery', content: 'A fictional first memory from the public starter.' } } }),
  }), env, { waitUntil() {} });
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.error, undefined);
  assert.equal(result.result.isError, undefined);
  assert.equal(db.prepare("SELECT count(*) AS n FROM observations WHERE identity_id='avery'").get().n, 1);
  assert.equal(db.prepare("SELECT count(*) AS n FROM observations WHERE identity_id='rowan'").get().n, 0);
  assert.equal(db.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
  db.prepare("INSERT INTO identities(id,display_name) VALUES('custom-persona','Custom Persona')").run();
  const overview = await worker.fetch(new Request('https://example.com/api/anam/mind-garden/summary', {
    headers: { Authorization: 'Bearer test-key' },
  }), env, { waitUntil() {} });
  assert.equal(overview.status, 200);
  assert.ok((await overview.json()).summaries['Custom Persona']);
});

test('assistant attribution follows explicit role cues rather than example identity names', () => {
  const signals = extractQuerySignals('What did you say?');
  const config = { quoted_phrase: 1, proper_name: 1, temporal: 1, assistant_reference: 1, max_total: 4 };
  assert.equal(computeQuerySignalBoosts(signals, { content: 'An ordinary record', context: 'river' }, config).assistant_reference_matched, false);
  assert.equal(computeQuerySignalBoosts(signals, { content: 'assistant: a prior reply' }, config).assistant_reference_matched, true);
  assert.equal(extractQuerySignals('River said something').assistant_reference.detected, false);
  assert.equal(extractQuerySignals('The assistant replied yesterday').assistant_reference.detected, true);
});

test('public rendering preserves the installation-authored identity instead of assigning a biography', () => {
  const narrative = 'I am a fictional botanist who enjoys careful experiments.';
  for (const identity of ['avery', 'rowan', 'my-own-identity']) {
    assert.equal(rewriteCurrentSelfNarrative(identity, 'custom', narrative, {}), narrative);
    assert.equal(rewriteCurrentSelfNarrative(identity, 'custom', null, {}), null);
    assert.ok(buildIdentityReadyMessage(identity, 'custom').includes(identity));
    assert.equal(getDriftLabels(identity, 'custom').section, 'Drift');
  }
  assert.equal(inferObservationTerritory({ content: 'My friend enjoys gardening.' }), 'kin');
  assert.equal(inferObservationTerritory({ content: 'The river flows beside the atlas exhibit.' }), 'episodic');
});
