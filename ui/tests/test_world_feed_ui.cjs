/* Run with: node --test tests/test_world_feed_ui.cjs */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
    const context = { apiPath: value => value, localStorage: { getItem: () => null }, document: { addEventListener() {}, getElementById: () => null, querySelectorAll: () => [], body: {} }, URLSearchParams, location: { hash: '' }, history: { pushState() {} }, window: { scrollTo() {}, getSelection: () => '' } };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/world-feed.js'), 'utf8') + '\nthis.feed = WorldFeed;', context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/world-feed-social.js'), 'utf8'), context);
    const feed = context.feed;
    feed.state.world = { id: 'world-a' };
    feed.el = new Proxy({}, { get: () => ({ value: '', classList: { toggle() {} }, style: {}, innerHTML: '', textContent: '' }) });
    const pending = [];
    feed.request = url => new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
    return { feed, pending, context };
}

function dmFixture() {
    const f = fixture(), { feed, context } = f;
    context.crypto = require('node:crypto').webcrypto;
    context.document.querySelector = () => null;
    context.formatRelativeTime = () => 'now';
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/world-feed-dms.js'), 'utf8'), context);
    feed.state.profiles = [
        { id: 'me', display_name: 'Player', handle: 'player', is_user_controlled: true, is_active: true },
        { id: 'npc', display_name: 'Bakugou', handle: 'explosion', is_user_controlled: false, is_active: true },
    ];
    feed.state.activeProfileId = 'me';
    feed.toast = () => {};
    return f;
}

test('photo allowance offers and saves twenty without changing participating accounts', async () => {
    const { feed, context } = fixture();
    const content = { innerHTML: '' };
    feed.el = { 'wf-content': content };
    context.document.getElementById = id => ({
        addEventListener() {}, checked: true, value: id === 'wf-photo-limit' ? '20' : '',
    });
    feed.state.photoData = { settings: { enabled: true, daily_limit: 20, max_daily_limit: 20, profile_ids: ['npc'] },
        visual_references: [{ display_name: 'Player Character', reference_url: '/api/images/file/player.png',
            description: 'Example character with a blue coat.', wardrobe: 'Blue coat.' }] };
    feed.state.profiles = [{ id: 'npc', display_name: 'NPC', handle: 'npc', is_active: true }];
    feed.text = String;
    feed.attr = String;
    feed.renderPhotoJobs = () => {};
    feed.renderPhotos();
    assert.match(content.innerHTML, /value="20" selected>20<\/option>/);
    assert.doesNotMatch(content.innerHTML, /value="21"/);
    assert.match(content.innerHTML, /no catch-up batches/);
    assert.match(content.innerHTML, /Player Character · visual reference/);
    assert.match(content.innerHTML, /src="\/api\/images\/file\/player.png"/);
    assert.match(content.innerHTML, /Example character with a blue coat/);
    let saved;
    feed.request = async (url, options) => { saved = JSON.parse(options.body); return { id: 'world-a' }; };
    feed.loadPhotos = async () => {};
    feed.toast = () => {};
    await feed.savePhotoSettings({ preventDefault() {}, target: {
        querySelector: () => ({}), querySelectorAll: () => [{ value: 'npc' }],
    } });
    assert.deepEqual(saved, { enabled: true, daily_limit: 20, profile_ids: ['npc'] });
});

test('late Home response cannot replace Media results', async () => {
    const { feed, pending } = fixture();
    const home = feed.loadFeed();
    feed.state.view = 'media';
    const media = feed.loadFeed();
    pending[1].resolve({ posts: [{ id: 'media' }] });
    await media;
    pending[0].resolve({ posts: [{ id: 'home' }] });
    await home;
    assert.equal(feed.state.posts[0].id, 'media');
});

test('profile filter and Following use their explicit API parameters', async () => {
    const { feed, pending } = fixture();
    feed.state.view = 'profile';
    feed.state.detailId = 'account-7';
    const profile = feed.loadFeed();
    assert.match(pending[0].url, /author_profile_id=account-7/);
    pending[0].resolve({ posts: [] });
    await profile;
    feed.state.view = 'following';
    feed.state.activeProfileId = 'me';
    const following = feed.loadFeed();
    assert.match(pending[1].url, /following_only=true/);
    assert.match(pending[1].url, /viewer_profile_id=me/);
    pending[1].resolve({ posts: [] });
    await following;
});

test('thread loads root separately from replies and keeps its cursor', async () => {
    const { feed, pending } = fixture();
    feed.state.view = 'thread';
    feed.state.detailId = 'root';
    feed.beginReply = () => {};
    const ready = feed.loadFeed();
    assert.match(pending[0].url, /posts\/root\/thread/);
    pending[0].resolve({ post: { id: 'root' }, posts: [{ id: 'reply' }], next_cursor: { before_epoch: 1, before_id: 'reply' } });
    await ready;
    assert.equal(feed.state.thread.id, 'root');
    assert.equal(feed.state.posts[0].id, 'reply');
    assert.equal(feed.state.nextCursor.before_id, 'reply');
});

test('profile response cannot paint over a newer navigation', async () => {
    const { feed, pending } = fixture();
    feed.toggleMobileNav = () => {};
    feed.renderCurrentView = () => {};
    const profile = feed.setView('profile', 'old-account');
    const home = feed.setView('home');
    pending[1].resolve({ posts: [{ id: 'home' }] });
    await home;
    pending[0].resolve({ id: 'old-account', world_id: 'world-a' });
    await profile;
    assert.equal(feed.state.view, 'home');
    assert.equal(feed.state.profile, null);
    assert.equal(feed.state.posts[0].id, 'home');
});

test('connection editor assigns follower and followed in the requested direction', async () => {
    const { feed } = fixture();
    feed.state.view = 'followers';
    feed.state.detailId = 'me';
    const writes = [];
    feed.request = async (url, options) => { writes.push({ url, body: JSON.parse(options.body), method: options.method }); };
    feed.setView = async () => {};
    await feed.editConnection('npc', true);
    assert.match(writes[0].url, /follow\/me$/);
    assert.equal(writes[0].body.profile_id, 'npc');
    assert.equal(writes[0].method, 'PUT');
    feed.state.view = 'following-list';
    await feed.editConnection('npc', false);
    assert.match(writes[1].url, /follow\/npc$/);
    assert.equal(writes[1].body.profile_id, 'me');
    assert.equal(writes[1].body.active, false);
});

test('name and avatar share account links, timestamps open the conversation', () => {
    const { feed } = fixture();
    feed.text = String;
    feed.avatarHtml = () => '<span>Avatar</span>';
    feed.linkify = String;
    const html = feed.postHtml({ id: 'post-1', author: { id: 'npc', display_name: 'NPC', handle: 'npc' }, body: 'Hello', fictional_at: 'Before story', canon_level: 'ambient' });
    assert.equal((html.match(/href="#profile\/npc"/g) || []).length, 3);
    assert.match(html, /href="#thread\/post-1"/);
});

test('late failures and payloads from the previous world are ignored', async () => {
    const { feed, pending } = fixture();
    const old = feed.loadFeed();
    ++feed.worldGeneration;
    feed.state.world = { id: 'world-b' };
    pending[0].reject(new Error('old world unavailable'));
    await old;
    assert.equal(feed.state.posts.length, 0);
});

test('older page sends both cursor components and appends without duplicates', async () => {
    const { feed, pending } = fixture();
    feed.state.posts = [{ id: 'new' }];
    feed.state.nextCursor = { before_epoch: 100, before_id: 'new' };
    const older = feed.loadFeed(true);
    assert.match(pending[0].url, /before_epoch=100/);
    assert.match(pending[0].url, /before_id=new/);
    pending[0].resolve({ posts: [{ id: 'new' }, { id: 'old' }], next_cursor: null });
    await older;
    assert.equal(feed.state.posts.map(p => p.id).join(','), 'new,old');
    assert.equal(feed.state.nextCursor, null);
});

test('story time is shown instead of import wall-clock time', () => {
    const { feed } = fixture();
    feed.text = String;
    feed.avatarHtml = () => '';
    feed.linkify = String;
    const html = feed.postHtml({ author: { display_name: 'Example', handle: 'example' }, body: 'Past post', fictional_at: 'Earlier this term', canon_level: 'ambient' });
    assert.match(html, /Earlier this term/);
});


function socialFixture() {
    const f = fixture(), { feed, context } = f;
    const elements = new Map(), storage = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, { value: '', hidden: true, disabled: false, textContent: '', innerHTML: '', style: {},
            classList: { toggle() {} }, querySelectorAll: () => [], querySelector: () => ({ addEventListener() {} }) });
        return elements.get(id);
    };
    context.document.getElementById = element;
    context.localStorage.setItem = (key, value) => storage.set(key, value);
    context.localStorage.getItem = key => storage.get(key) || null;
    context.crypto = { randomUUID: require('node:crypto').randomUUID };
    feed.el = new Proxy({}, { get: (_, id) => element(id) });
    feed.state.profiles = [{ id: 'me', is_user_controlled: true, is_active: true }];
    feed.state.activeProfileId = 'me';
    element('wf-post-canon').value = 'ambient';
    feed.toast = () => {};
    feed.text = String;
    feed.renderWorldChrome = () => {};
    feed.renderMediaPreview = () => {};
    feed.updateCharCount = () => {};
    feed.renderCurrentView = () => {};
    feed.toggleMobileNav = () => {};
    return { ...f, element, storage };
}

test('late successful submission preserves a newly typed draft', async () => {
    const { feed, pending, element } = socialFixture();
    element('wf-post-text').value = 'First post';
    const posting = feed.submitPost();
    element('wf-post-text').value = 'Next thought';
    pending[0].resolve({ id: 'sent', body: 'First post' });
    await posting;
    assert.equal(element('wf-post-text').value, 'Next thought');
    assert.equal(feed.state.posts[0].id, 'sent');
});

test('network retry keeps the same submission receipt and clears only the sent draft', async () => {
    const { feed, element } = socialFixture();
    element('wf-post-text').value = 'One post';
    const receipts = [];
    feed.request = async (_, options) => {
        receipts.push(JSON.parse(options.body).submission_id);
        if (receipts.length === 1) throw new Error('Response lost');
        return { id: 'one' };
    };
    await feed.submitPost();
    assert.equal(element('wf-post-text').value, 'One post');
    await feed.submitPost();
    assert.equal(receipts[0], receipts[1]);
    assert.equal(element('wf-post-text').value, '');
});

test('likes preserve all loaded pages and suppress a rapid duplicate click', async () => {
    const { feed, pending } = socialFixture();
    feed.state.posts = [{ id: 'new' }, { id: 'old', viewer_liked: false }];
    const cursor = feed.state.nextCursor = { before_epoch: 1, before_id: 'old' };
    feed.loadFeed = () => { throw new Error('Must not reload'); };
    const first = feed.reactPost('old', 'like');
    await feed.reactPost('old', 'like');
    assert.equal(pending.length, 1);
    pending[0].resolve({ active: true, like_count: 1 });
    await first;
    assert.equal(feed.state.posts.length, 2);
    assert.equal(feed.state.nextCursor, cursor);
    assert.equal(feed.state.posts[1].viewer_liked, true);
});

test('new activity shows a banner without replacing the timeline', async () => {
    const { feed, pending, element } = socialFixture();
    feed.state.posts = [{ id: 'old' }];
    feed.loadNotifications = async () => {};
    const polling = feed.pollActivity();
    await new Promise(resolve => setImmediate(resolve));
    pending[0].resolve({ posts: [{ id: 'new' }] });
    await polling;
    assert.equal(element('wf-new-posts').hidden, false);
    assert.equal(feed.state.posts[0].id, 'old');
});

test('history back restores older pages and reading position', async () => {
    const { feed, pending, context } = socialFixture();
    feed.state.posts = [{ id: 'new' }, { id: 'older' }];
    feed.state.nextCursor = { before_epoch: 1, before_id: 'older' };
    context.window.scrollY = 860;
    let scroll;
    context.window.scrollTo = options => { scroll = options.top; };
    const open = feed.setView('thread', 'new');
    pending[0].resolve({ post: { id: 'new', author: { handle: 'npc' } }, posts: [] });
    await open;
    const back = feed.setView('home', '', true);
    pending[1].resolve({ posts: [{ id: 'new' }] });
    await back;
    assert.equal(feed.state.posts.length, 2);
    assert.equal(feed.state.nextCursor.before_id, 'older');
    assert.equal(scroll, 860);
});

test('search and bookmarks send explicit scoped filters', async () => {
    const { feed, pending } = socialFixture();
    feed.state.view = 'search'; feed.state.detailId = 'tea & cake';
    const search = feed.loadFeed();
    assert.match(pending[0].url, /search=tea/);
    pending[0].resolve({ posts: [] }); await search;
    feed.state.view = 'bookmarks';
    const bookmarks = feed.loadFeed();
    assert.match(pending[1].url, /bookmarks_only=true/);
    pending[1].resolve({ posts: [] }); await bookmarks;
});

test('message button opens a real private thread from the active account', async () => {
    const { feed } = dmFixture();
    let write, route;
    feed.request = async (url, options) => {
        write = { url, method: options.method, body: JSON.parse(options.body) };
        return { id: 'dm-1' };
    };
    feed.setView = async (...args) => { route = args; };
    await feed.startDM(null, 'npc');
    assert.match(write.url, /worlds\/world-a\/dms$/);
    assert.equal(write.method, 'POST');
    assert.deepEqual(write.body, { profile_id: 'me', other_profile_id: 'npc' });
    assert.deepEqual(route, ['dms', 'dm-1']);
});

test('sending a private message refreshes only the open conversation', async () => {
    const { feed, context } = dmFixture();
    feed.state.view = 'dms';
    feed.state.detailId = 'dm-1';
    const input = { value: 'Did you eat lunch?' };
    context.document.getElementById = id => id === 'wf-dm-text' ? input : null;
    let write, refreshed;
    feed.request = async (url, options) => {
        write = { url, method: options.method, body: JSON.parse(options.body) };
        return {};
    };
    feed.loadDMThread = async (threadId, markRead) => { refreshed = [threadId, markRead]; };
    feed.renderDMs = () => {};
    await feed.sendDM({ preventDefault() {}, submitter: { disabled: false } });
    assert.match(write.url, /dms\/dm-1\/messages$/);
    assert.equal(write.method, 'POST');
    assert.equal(write.body.profile_id, 'me');
    assert.equal(write.body.body, 'Did you eat lunch?');
    assert.match(write.body.submission_id, /^[a-f0-9]{32}$/);
    assert.equal(input.value, '');
    assert.deepEqual(refreshed, ['dm-1', true]);
});

test('private-message history renders its timestamp with the shared formatter', () => {
    const { feed } = dmFixture();
    feed.avatarHtml = () => '';
    feed.linkify = String;
    feed.text = String;
    const me = feed.state.profiles[0], npc = feed.state.profiles[1];
    const html = feed.dmConversationHtml({
        thread: { participants: [me, npc] },
        messages: [{
            body: 'QA only', created_at: new Date().toISOString(),
            sender: { ...me, avatar_url: '', accent_color: '#D9485F' },
        }],
    }, me);
    assert.match(html, /QA only/);
    assert.doesNotMatch(html, /timeAgo is not a function/);
});

test('DM retry retains receipt and suppresses a double tap', async () => {
    const { feed, context } = dmFixture();
    feed.state.view = 'dms'; feed.state.detailId = 'one';
    const input = { value: 'Keep me' };
    context.document.getElementById = id => id === 'wf-dm-text' ? input : null;
    const event = { preventDefault() {}, submitter: { disabled: false } };
    let reject, writes = [];
    feed.request = (url, options) => { writes.push(JSON.parse(options.body)); return new Promise((_, fail) => { reject = fail; }); };
    const sending = feed.sendDM(event);
    await feed.sendDM(event);
    assert.equal(writes.length, 1);
    reject(new Error('Lost response')); await sending;
    feed.request = async (url, options) => { writes.push(JSON.parse(options.body)); };
    feed.loadDMThread = async () => {}; feed.renderDMs = () => {};
    await feed.sendDM(event);
    assert.equal(writes[0].submission_id, writes[1].submission_id);
    assert.equal(input.value, '');
});

test('a late DM response cannot refresh another conversation or erase its draft', async () => {
    const { feed, context } = dmFixture();
    feed.state.view = 'dms'; feed.state.detailId = 'one';
    let input = { value: 'First conversation' };
    context.document.getElementById = id => id === 'wf-dm-text' ? input : null;
    let complete;
    feed.request = () => new Promise(resolve => { complete = resolve; });
    feed.loadDMThread = () => { throw new Error('Must not refresh another conversation'); };
    const sending = feed.sendDM({ preventDefault() {} });
    feed.state.detailId = 'two'; input = { value: 'Second conversation draft' };
    complete({}); await sending;
    assert.equal(input.value, 'Second conversation draft');
});

test('attachments snapshot a live FileList before resetting and allow the same file again', async () => {
    const { feed } = fixture();
    let files = [{ name: 'phone-photo.png' }], uploaded = [];
    const liveList = { [Symbol.iterator]: function* () { yield* files; } };
    const input = { set value(_) { files = []; } };
    const submit = { disabled: false };
    feed.el = { 'wf-media-input': input, 'wf-submit-post': submit };
    feed.renderMediaPreview = () => {}; feed.saveDraft = () => {};
    feed.uploadImage = async file => { uploaded.push(file.name); return { url: '/api/images/file/photo.png' }; };
    await feed.addPostMedia(liveList);
    assert.deepEqual(uploaded, ['phone-photo.png']);
    assert.equal(feed.state.media.length, 1);
    assert.equal(submit.disabled, false);
    files = [{ name: 'phone-photo.png' }];
    await feed.addPostMedia(liveList);
    assert.equal(uploaded.length, 2);
});

test('attachment cap reports clearly and late uploads cannot enter another world', async () => {
    const { feed } = fixture();
    feed.el = { 'wf-media-input': { value: '' }, 'wf-submit-post': { disabled: false } };
    let notice, finish;
    feed.toast = message => { notice = message; };
    feed.state.media = [1, 2, 3, 4];
    feed.uploadImage = () => { throw new Error('Must not upload a fifth image'); };
    await feed.addPostMedia([{ name: 'extra.png' }]);
    assert.match(notice, /four images/);
    feed.state.media = [];
    feed.uploadImage = () => new Promise(resolve => { finish = resolve; });
    const upload = feed.addPostMedia([{ name: 'old-world.png' }]);
    feed.worldGeneration++;
    finish({ url: '/api/images/file/old-world.png' });
    await upload;
    assert.equal(feed.state.media.length, 0);
    assert.equal(feed.el['wf-submit-post'].disabled, false);
});

test('a DM response from a previous account is discarded', async () => {
    const { feed, pending } = dmFixture();
    feed.state.view = 'dms'; feed.state.detailId = 'one';
    const loading = feed.loadDMThread('one');
    feed.state.activeProfileId = 'different';
    pending[0].resolve({ thread: { id: 'one' }, messages: [] });
    await loading;
    assert.equal(feed.state.dmThread, null);
});

test('notification counts stay visible and accessible on both navigation surfaces', async () => {
    const { feed, context } = fixture();
    const desktop = {}, mobile = {}, buttons = [{}, {}];
    feed.el = { 'wf-notice-badge': desktop };
    context.document.getElementById = id => id === 'wf-notice-mobile-badge' ? mobile : null;
    context.document.querySelectorAll = () => buttons.map(b => ({ setAttribute: (key, value) => b[key] = value }));
    feed.activeProfile = () => ({ id: 'me' });
    feed.state.activeProfileId = 'me';
    feed.latestRequest = async () => ({ notifications: [], unread_count: 57 });
    await feed.loadNotifications(false);
    assert.equal(desktop.textContent, '57');
    assert.equal(mobile.textContent, '57');
    assert.equal(buttons[0]['aria-label'], 'Open notifications, 57 unread');
    feed.activeProfile = () => null;
    await feed.loadNotifications(false);
    assert.equal(mobile.textContent, '');
    assert.equal(buttons[1]['aria-label'], 'Open notifications');
});
