/* Newcomer world creation uses only mocked HTTP, without provider calls. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, { value: '', checked: false, hidden: false, disabled: false, textContent: '' });
        return elements.get(id);
    };
    const context = {
        localStorage: { getItem: () => null },
        document: { addEventListener() {}, getElementById: element },
        history: { replaceState() {} },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/world-feed.js'), 'utf8') + '\nthis.feed = WorldFeed;', context);
    const feed = context.feed;
    feed.el = new Proxy({}, { get: (_, id) => element(id) });
    feed.openModal = () => {};
    feed.closeModals = () => {};
    feed.renderWorldPicker = () => {};
    feed.renderWorldChrome = () => {};
    feed.toast = () => {};
    feed.state.world = { id: 'old-world', name: 'Existing World', story_identity: 'Rowan', story_branch: 'chapter-one',
        posting_enabled: true, metadata: { photo_style: 'Ink drawing', activity: { daily_limit: 24, auto_publish: true, scene_reactions: true } } };
    feed.state.worlds = [feed.state.world];
    feed.request = async () => ({ runs: [], settings: { daily_limit: 24 } });
    return { feed, element };
}

test('new-world form clears previous world and leaves automatic activity off', async () => {
    const { feed, element } = fixture();
    await feed.openWorldModal();
    assert.equal(element('wf-world-identity').value, 'Rowan');
    assert.equal(element('wf-world-branch').value, 'chapter-one');
    assert.equal(element('wf-world-photo-style').value, 'Ink drawing');
    await feed.openWorldModal(true);
    assert.equal(feed.editingWorldId, '');
    assert.equal(element('wf-world-name').value, '');
    assert.equal(element('wf-world-identity').value, 'Avery');
    assert.equal(element('wf-world-branch').value, '');
    assert.equal(element('wf-world-photo-style').value, '');
    assert.equal(element('wf-world-posting-enabled').checked, false);
    assert.equal(element('wf-activity-publish').checked, false);
    assert.equal(element('wf-activity-scenes').checked, false);
    assert.equal(element('wf-world-activity-controls').hidden, true);
});

test('create posts independent world then selects it without overwriting existing world', async () => {
    const { feed, element } = fixture();
    const original = feed.state.world;
    await feed.openWorldModal(true);
    element('wf-world-name').value = 'Café Friends';
    element('wf-world-photo-style').value = 'Watercolour';
    let write, selected;
    feed.request = async (url, options) => {
        write = { url, method: options.method, body: JSON.parse(options.body) };
        return { id: 'cafe-friends', ...write.body };
    };
    feed.selectWorld = async id => { selected = id; };
    await feed.saveWorld({ preventDefault() {} });
    assert.equal(write.url, '/api/world-feed/worlds');
    assert.equal(write.method, 'POST');
    assert.equal(write.body.slug, 'cafe-friends');
    assert.equal(write.body.story_identity, 'Avery');
    assert.equal(write.body.story_branch, '');
    assert.equal(write.body.posting_enabled, false);
    assert.deepEqual(write.body.metadata, { photo_style: 'Watercolour' });
    assert.equal(selected, 'cafe-friends');
    assert.equal(feed.state.worlds[0], original);
    assert.equal(feed.state.worlds.length, 2);
});

test('edit sends narrator, blank branch, photo style and existing activity settings through PATCH', async () => {
    const { feed, element } = fixture();
    await feed.openWorldModal();
    element('wf-world-identity').value = 'Sage';
    element('wf-world-branch').value = '';
    element('wf-world-photo-style').value = 'Comic-book';
    let write;
    feed.request = async (url, options) => {
        write = { url, method: options.method, body: JSON.parse(options.body) };
        return { id: 'old-world', ...write.body };
    };
    await feed.saveWorld({ preventDefault() {} });
    assert.equal(write.url, '/api/world-feed/worlds/old-world');
    assert.equal(write.method, 'PATCH');
    assert.equal(write.body.story_identity, 'Sage');
    assert.equal(write.body.story_branch, '');
    assert.equal(write.body.activity_daily_limit, 24);
    assert.equal(write.body.activity_auto_publish, true);
    assert.equal(write.body.activity_scene_reactions, true);
    assert.deepEqual(write.body.metadata, { photo_style: 'Comic-book' });
    assert.equal(feed.state.worlds[0].story_identity, 'Sage');
});

test('duplicate create click is suppressed and a rejected creation preserves the form', async () => {
    const { feed, element } = fixture();
    await feed.openWorldModal(true);
    element('wf-world-name').value = 'My World';
    let reject, calls = 0;
    feed.request = () => { calls++; return new Promise((_, fail) => { reject = fail; }); };
    const first = feed.saveWorld({ preventDefault() {} });
    await feed.saveWorld({ preventDefault() {} });
    assert.equal(calls, 1);
    reject(new Error('A world with that id or slug already exists'));
    await first;
    assert.equal(element('wf-world-name').value, 'My World');
    assert.equal(element('wf-world-submit').disabled, false);
});

test('late old-world activity status cannot enter a new-world form', async () => {
    const { feed, element } = fixture();
    let complete;
    feed.request = () => new Promise(resolve => { complete = resolve; });
    const loading = feed.openWorldModal();
    await feed.openWorldModal(true);
    complete({ runs: [], settings: { daily_limit: 24 } });
    await loading;
    assert.equal(element('wf-activity-status').textContent, '');
});

test('starter page has neutral branding, local back links and model controls', () => {
    const html = fs.readFileSync(path.join(__dirname, '../static/world-feed.html'), 'utf8');
    assert.match(html, /<title>World Feed · Anam<\/title>/);
    assert.match(html, /href="\/" class="wf-back-link"/);
    assert.doesNotMatch(html, /example\.com\/mha|U\.A\. World Feed/);
    for (const id of ['wf-new-world', 'wf-world-identity', 'wf-world-branch', 'wf-world-photo-style']) {
        assert.match(html, new RegExp(`id="${id}"`));
    }
});
