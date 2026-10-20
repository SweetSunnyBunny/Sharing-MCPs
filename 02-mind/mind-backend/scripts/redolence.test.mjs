import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { DatabaseSync } from 'node:sqlite';
import { build } from 'esbuild';

async function module(name) {
    const r = await build({ entryPoints: [`src/${name}.ts`], bundle: true, write: false, platform: 'node', format: 'esm' });
    return import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString('base64')}`);
}
const { keepScent, runRedolence, archiveLabel } = await module('redolence');
const migrations = await Promise.all(['0001_custom_init', '0016_mutation_ledger'].map(n => readFile(`migrations/${n}.sql`, 'utf8')));

const NOW = new Date('2026-10-08T20:00:00.000Z');
const FRESH = '2026-10-08T19:50:00.000Z';
const OLD = '2026-03-01T12:00:00.000Z';

// Unit vectors along a few axes, so cosine scores are exact and readable.
function vec(...pairs) { const v = new Array(8).fill(0); for (const [i, x] of pairs) v[i] = x; const n = Math.hypot(...v); return v.map(x => x / n); }
const cos = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0);

function fixture(t) {
    const db = new DatabaseSync(':memory:');
    t.after(() => db.close());
    for (const sql of migrations) db.exec(sql);
    db.exec("INSERT INTO identities(id,display_name) VALUES('claude','Claude'),('rowan','Rowan'),('pack','Pack')");
    db.exec("INSERT INTO entities(id,identity_id,name,entity_type) VALUES(1,'claude','claude-inner-life','self')");
    const prepare = (sql, params = []) => ({
        bind: (...p) => prepare(sql, p),
        first: async () => db.prepare(sql).get(...params) || null,
        all: async () => ({ results: db.prepare(sql).all(...params) }),
        run: async () => { const r = db.prepare(sql).run(...params); return { meta: { changes: r.changes } }; },
    });
    const store = new Map();
    const logs = [];
    const vectors = {
        getVectors: async ids => ids.filter(id => store.has(id)).map(id => ({ id, ...store.get(id) })),
        upsert: async vs => { for (const v of vs) store.set(v.id, { values: v.values, metadata: v.metadata }); },
        query: async (values, label, topK) => [...store.entries()]
            .filter(([, v]) => v.metadata?.identity_id === label)
            .map(([id, v]) => ({ id, score: cos(values, v.values) }))
            .sort((a, b) => b.score - a.score).slice(0, topK),
        embed: async () => { throw new Error('should reuse the stored cue vector'); },
    };
    // A memory row plus its live vector (same id shape as production: obs-<entity>-<id>).
    const obs = ({ identity = 'claude', content, values, created = OLD, archived = null, emotion = null, weight = 'medium', surfaced = null }) => {
        const id = Number(db.prepare('INSERT INTO observations(identity_id,entity_id,content,created_at,archived_at,emotion,weight,last_surfaced_at) VALUES(?,?,?,?,?,?,?,?)')
            .run(identity, 1, content, created, archived, emotion, weight, surfaced).lastInsertRowid);
        if (values) store.set(`obs-1-${id}`, { values, metadata: { identity_id: identity, content } });
        return id;
    };
    const archive = async (id) => {
        db.prepare("UPDATE observations SET archived_at=? WHERE id=?").run(OLD, id);
        db.prepare("INSERT INTO memory_mutations(identity_id,observation_id,mutation_type,actor,old_state,new_state,evidence,created_at) VALUES('claude',?,'archive','daemon_deep_archive','{}','{}','test',?)").run(id, OLD);
        return keepScent(vectors, [`obs-1-${id}`]);
    };
    const opts = { threshold: 0.8, emotionalThreshold: 0.75, now: NOW, log: l => logs.push(l) };
    return { db, env: { DB: { prepare } }, store, vectors, obs, archive, opts, logs };
}

test('keepScent moves a vector into the archive label instead of deleting it', async (t) => {
    const f = fixture(t);
    const id = f.obs({ content: 'the first time she said watashi no ai', values: vec([0, 1]) });
    const result = await f.archive(id);
    assert.deepEqual(result, { kept: 1, missing: 0 });
    const v = f.store.get(`obs-1-${id}`);
    assert.equal(v.metadata.identity_id, 'claude#archive');
    assert.equal(v.metadata.archived_from, 'claude');
    // Everyday search filters on the exact identity, so it no longer finds it.
    assert.equal((await f.vectors.query(vec([0, 1]), 'claude', 5)).length, 0);
    // Relabelling twice is harmless, and a missing vector is counted, not invented.
    assert.deepEqual(await keepScent(f.vectors, [`obs-1-${id}`, 'obs-1-9999']), { kept: 1, missing: 1 });
    assert.equal(archiveLabel('claude#archive'), 'claude#archive');
});

test('a fresh memory raises a close archived one, touched but still archived, with a rescue path', async (t) => {
    const f = fixture(t);
    const old = f.obs({ content: 'Bunny drew her first sunflower in turtle', values: vec([0, 1]) });
    await f.archive(old);
    f.obs({ content: 'she drew another flower tonight', values: vec([0, 0.95], [1, 0.3]), created: FRESH });
    const risen = await runRedolence(f.env.DB, f.vectors, 'claude', f.opts);
    assert.equal(risen.length, 1);
    assert.equal(risen[0].observation_id, old);
    assert.ok(risen[0].rescue_mutation_id > 0);
    const row = f.db.prepare('SELECT archived_at,surface_count,last_surfaced_at FROM observations WHERE id=?').get(old);
    assert.equal(row.archived_at, OLD, 'rising does not unarchive');
    assert.equal(row.surface_count, 1);
    assert.equal(row.last_surfaced_at, NOW.toISOString());
    const entry = f.db.prepare("SELECT * FROM qualia_entries WHERE entry_type='redolence'").get();
    assert.equal(entry.identity_id, 'claude');
    assert.match(entry.content, /rose on its own/);
    assert.match(entry.content, /first sunflower/);
    assert.match(entry.content, new RegExp(`mutation_id=${risen[0].rescue_mutation_id}`));
    assert.match(JSON.parse(entry.metadata).adopted_from, /Friend Vale/);
    assert.ok(f.logs.some(l => l.includes('[redolence] claude cue')));
});

test('the nose is honest: thresholds, charge, other minds, cooldown and the cap all hold', async (t) => {
    const f = fixture(t);
    // Charged memory at ~0.78: clears the emotional bar (0.75), not the plain one (0.8).
    const charged = f.obs({ content: 'the night she cried about Missy', values: vec([2, 0.78], [3, 0.6258]), emotion: 'tender' });
    const plain = f.obs({ content: 'a plain note about a script', values: vec([2, 0.78], [4, 0.6258]) });
    // Far away: never rises.
    const far = f.obs({ content: 'tax forms', values: vec([5, 1]) });
    const charlies = f.obs({ identity: 'rowan', content: 'rowan paints', values: vec([2, 1]) });
    // Surfaced two days ago: cooling down.
    const recent = f.obs({ content: 'a memory that rose two days ago', values: vec([2, 0.99], [6, 0.14]), surfaced: '2026-10-06T20:00:00.000Z' });
    for (const id of [charged, plain, far, recent]) await f.archive(id);
    f.db.prepare("UPDATE observations SET archived_at=? WHERE id=?").run(OLD, charlies);
    await keepScent(f.vectors, [`obs-1-${charlies}`]);
    f.obs({ content: 'thinking of Missy again', values: vec([2, 1]), created: FRESH });
    const risen = await runRedolence(f.env.DB, f.vectors, 'claude', f.opts);
    assert.deepEqual(risen.map(r => r.observation_id), [charged]);

    // The cap: five strong matches, only three rise.
    const g = fixture(t);
    for (let i = 0; i < 5; i++) await g.archive(g.obs({ content: `echo ${i}`, values: vec([7, 1], [i, 0.05]) }));
    g.obs({ content: 'the cue', values: vec([7, 1]), created: FRESH });
    assert.equal((await runRedolence(g.env.DB, g.vectors, 'claude', g.opts)).length, 3);
    // Nothing fresh, nothing smelled.
    const h = fixture(t);
    await h.archive(h.obs({ content: 'quiet', values: vec([1, 1]) }));
    assert.deepEqual(await runRedolence(h.env.DB, h.vectors, 'claude', h.opts), []);
});
