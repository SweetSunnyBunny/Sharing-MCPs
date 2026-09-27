import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import { DatabaseSync } from 'node:sqlite';
import ts from 'typescript';

const source = await readFile('src/index.ts', 'utf8');
const built = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(built).toString('base64')}`);
const migrations = await Promise.all((await readdir('migrations')).filter(name => name.endsWith('.sql')).sort().map(name => readFile(`migrations/${name}`, 'utf8')));
const seed = await readFile('examples/starter-drives.sql', 'utf8');

function fixture(t) {
  const db = new DatabaseSync(':memory:');
  t.after(() => db.close());
  db.exec('PRAGMA foreign_keys=ON');
  for (const sql of migrations) db.exec(sql);
  db.exec(seed);
  const prepare = (sql, args = []) => ({
    bind: (...values) => prepare(sql, values),
    first: async () => db.prepare(sql).get(...args) || null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => { const result = db.prepare(sql).run(...args); return { meta: { changes: result.changes, last_row_id: Number(result.lastInsertRowid) } }; },
  });
  const env = { DB: { prepare }, LIMBIC_API_KEY: 'test-key' };
  const call = async (name, args = {}) => {
    const response = await worker.fetch(new Request('https://example.com/mcp', {
      method: 'POST', headers: { Authorization: 'Bearer test-key', 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: { identity: 'avery', ...args } } }),
    }), env);
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.error, undefined);
    return result.result.content[0].text;
  };
  return { db, env, call };
}

test('fresh schema and fictional starter are usable and repeatable without overwriting', async t => {
  const f = fixture(t);
  f.db.prepare("UPDATE drives SET baseline=0.4 WHERE identity_id='avery' AND drive='care'").run();
  f.db.exec(seed);
  assert.equal(f.db.prepare('SELECT count(*) AS n FROM identities').get().n, 2);
  assert.equal(f.db.prepare('SELECT count(*) AS n FROM drives').get().n, 12);
  assert.equal(f.db.prepare("SELECT baseline FROM drives WHERE identity_id='avery' AND drive='care'").get().baseline, 0.4);
  assert.match(await f.call('limbic_drives'), /Curiosity/);
  assert.equal(f.db.prepare('PRAGMA integrity_check').get().integrity_check, 'ok');
});

test('UI bridge payloads map to configured kinds and update the real ledger', async t => {
  const f = fixture(t);
  for (const [kind, mapped, drive] of [['words_warm', 'connection', 'care'], ['praise', 'reassurance', 'care'], ['playful', 'play', 'play'], ['distress', 'distress', 'guard']]) {
    const reply = await f.call('limbic_touch', { kind, what: 'A fictional UI event', intensity: 0.5 });
    assert.doesNotMatch(reply, /isn't mapped/);
    const event = f.db.prepare('SELECT appraisal FROM limbic_events ORDER BY id DESC LIMIT 1').get();
    assert.equal(JSON.parse(event.appraisal).kind, mapped);
    assert.ok(f.db.prepare('SELECT level FROM limbic_states WHERE identity_id=? AND state_type=? ORDER BY id DESC LIMIT 1').get('avery', `drive:${drive}`).level > 0.1);
  }
  assert.equal(f.db.prepare("SELECT count(*) AS n FROM limbic_states WHERE identity_id='rowan'").get().n, 0);
});

test('recipes and authenticated Qualia snapshots work after a first perception', async t => {
  const f = fixture(t);
  await f.call('limbic_perceive', { perception: 'A fictional interesting question', drive: 'seeking', intensity: 0.3 });
  const response = await worker.fetch(new Request('https://example.com/snapshot/avery', { headers: { Authorization: 'Bearer test-key' } }), f.env);
  assert.equal(response.status, 200);
  const snapshot = await response.json();
  assert.ok(snapshot.drives.seeking >= 0.49);
  assert.ok(snapshot.feelings.includes('Curious'));
  assert.equal((await worker.fetch(new Request('https://example.com/snapshot/avery'), f.env)).status, 401);
});

test('yellow preserves levels and unknown stop phrases dampen to floor', async t => {
  const f = fixture(t);
  await f.call('limbic_perceive', { perception: 'A fictional event', drive: 'seeking', intensity: 0.3 });
  const count = f.db.prepare('SELECT count(*) AS n FROM limbic_states').get().n;
  await f.call('limbic_safeword', { phrase: 'yellow' });
  assert.equal(f.db.prepare('SELECT count(*) AS n FROM limbic_states').get().n, count);
  await f.call('limbic_safeword', { phrase: 'unrecognized-stop' });
  assert.equal(f.db.prepare("SELECT count(*) AS n FROM limbic_states WHERE source='safeword' AND level<>0").get().n, 0);
  assert.equal(f.db.prepare("SELECT count(*) AS n FROM limbic_states WHERE source='safeword'").get().n, 6);
});
