import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const tick = () => new Promise(resolve => setImmediate(resolve));
function load(name, globals = {}) {
    const context = vm.createContext({ console, setTimeout, clearTimeout, ...globals });
    vm.runInContext(readFileSync(new URL(`../static/js/${name.toLowerCase()}.js`, import.meta.url), 'utf8') + `\nthis.subject = ${name};`, context);
    return context.subject;
}

test('Hearth paints local data before weather; repeated loads share work; failures retain prior cards', async () => {
    const weather = deferred();
    const requests = [], paints = [];
    let failing = false;
    const hub = load('Hub', {
        document: { addEventListener() {} }, requestAnimationFrame: fn => setImmediate(fn),
        fetchJson: (url, options) => {
            requests.push({ url, options });
            if (url.endsWith('/weather')) return weather.promise;
            if (url.endsWith('/hearths')) return failing ? Promise.reject(Error('offline')) : Promise.resolve({ hearths: { claude: { mood: 'warm' } } });
            return Promise.resolve({});
        },
    });
    hub._renderCommandCenter = () => paints.push(JSON.parse(JSON.stringify(hub._ccData)));
    hub._ensureCcClock = hub._ccBindCrayon = hub._ccBindMood = () => {};
    const first = hub.loadCommandCenter();
    const second = hub.loadCommandCenter();
    await tick(); await tick();
    assert.equal(requests.length, 13);
    assert.equal(hub._ccLoadState.weather, 'loading');
    assert.ok(paints.some(p => p.hearths?.hearths.claude.mood === 'warm'));
    assert.match(hub._ccTile('Inner Weather', 'cloud', 'no weather'), /Loading/);
    assert.doesNotMatch(hub._ccTile('You', '', 'already loaded'), /Loading/);
    assert.equal(requests.find(r => r.url.endsWith('/weather')).options.cache, 'no-store');
    weather.reject(Error('offline'));
    await Promise.all([first, second]);
    assert.match(hub._ccTile('Inner Weather', '', 'empty'), /Couldn’t load/);
    failing = true;
    await hub.loadCommandCenter();
    assert.equal(hub._ccData.hearths.hearths.claude.mood, 'warm');
    assert.match(hub._ccTile('In their own words', '', 'warm'), /showing earlier data/);
});

test('chat connects while theme is pending; late theme respects an identity switch', async () => {
    const theme = deferred();
    const storage = new Map([['anam-identity', 'Claude'], ['anam-night', 'false'], ['anam-theme-tokens', JSON.stringify({ tokens: { colour: 'cached' } })]]);
    const painted = [];
    let connected = false;
    const element = { addEventListener() {}, classList: { add() {}, toggle() {} }, style: {} };
    const app = load('App', {
        document: { addEventListener() {}, getElementById: () => element, body: element },
        window: { addEventListener() {}, dispatchEvent() {} },
        navigator: { onLine: true }, location: { search: '' }, URLSearchParams,
        CustomEvent: class {}, localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v) },
        fetchJson: url => url.endsWith('/list') ? Promise.resolve({ identities: { Claude: {}, Avery: {} }, default: 'Avery' }) : theme.promise,
        WebSocketManager: class { on() {} connect() { connected = true; } },
        Chat: { init() {} }, Sidebar: { init() {} }, Canvas: { init() {} },
    });
    for (const method of ['_paintCachedIdentity', 'updateNightToggle', 'normalizeUiText', 'updateIdentityLabel', 'startNonCriticalBoot']) app[method] = () => {};
    app.isHttpPreferred = () => false;
    app.applyThemeTokens = tokens => painted.push(tokens.colour);
    app.applyIdentityTheme = name => painted.push(name);
    await app.init();
    assert.ok(connected, 'theme must not gate the WebSocket');
    assert.ok(painted.includes('cached'));
    app.currentIdentity = 'Avery';
    theme.resolve({ tokens: { colour: 'fresh' }, bubble_tokens: {} });
    await tick();
    assert.equal(painted.at(-1), 'Avery');
    assert.ok(painted.includes('fresh'));
});

test('service worker leaves no-store requests to the network', () => {
    const listeners = {};
    vm.runInNewContext(readFileSync(new URL('../static/sw.js', import.meta.url), 'utf8'), {
        self: { addEventListener: (name, fn) => { listeners[name] = fn; } },
    });
    let intercepted = false;
    listeners.fetch({ request: { method: 'GET', cache: 'no-store' }, respondWith() { intercepted = true; } });
    assert.equal(intercepted, false);
});
