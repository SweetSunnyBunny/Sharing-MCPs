import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for browser transport behavior tests")
def test_mobile_resume_replaces_zombie_socket_and_ignores_stale_callbacks():
    websocket_path = json.dumps(str(ROOT / "static" / "js" / "websocket.js"))
    script = r"""
const fs = require('fs');
const assert = require('assert');

const listeners = {};
global.window = {
    __ANAM_PUBLIC_BASE_URL__: '',
    addEventListener(name, handler) { listeners[`window:${name}`] = handler; },
    location: { href: '', replace() {} },
};
global.document = {
    hidden: false,
    addEventListener(name, handler) { listeners[`document:${name}`] = handler; },
};
global.navigator = { onLine: true, maxTouchPoints: 1 };
global.location = {
    protocol: 'https:',
    host: 'example.com',
    hostname: 'example.com',
    pathname: '/',
    search: '',
    hash: '',
};
global.sessionStorage = {
    getItem() { return null; },
    setItem() {},
    removeItem() {},
};
global.anamApiUrl = () => '';
global.apiPath = path => path;
global.AbortSignal = { timeout() { return {}; } };
global.fetch = async () => ({ ok: true });

let nextTimer = 1;
const timers = new Map();
global.setTimeout = (handler, delay) => {
    const id = nextTimer++;
    timers.set(id, { handler, delay });
    return id;
};
global.clearTimeout = id => timers.delete(id);
global.setInterval = global.setTimeout;
global.clearInterval = global.clearTimeout;
global.console = { log() {}, warn() {}, error() {} };

const clientEvents = [];
global.logClientEvent = (type, detail) => clientEvents.push({ type, detail });

class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        this.sent = [];
        FakeWebSocket.instances.push(this);
    }

    send(payload) { this.sent.push(payload); }

    close(code = 1000, reason = '') {
        this.readyState = FakeWebSocket.CLOSED;
        this.closeArgs = { code, reason };
    }
}
global.WebSocket = FakeWebSocket;

const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');
eval(`${source}\nglobalThis.WebSocketManager = WebSocketManager;`);

function openCurrent(manager) {
    const socket = manager.ws;
    socket.readyState = FakeWebSocket.OPEN;
    socket.onopen();
    return socket;
}

const manager = new WebSocketManager();
manager.connect();
const first = openCurrent(manager);

// Long/tool-heavy streams still send heartbeats, with the wider tolerance.
manager.streaming = true;
manager._startHeartbeat();
timers.get(manager._pingInterval).handler();
assert.equal(first.sent.at(-1), JSON.stringify({ type: 'ping' }));
assert.equal(timers.get(manager._pongTimeout).delay, 30000);
manager._clearPongTimeout();
manager.streaming = false;

// Manual retry must replace an OPEN-looking zombie rather than returning early.
manager.manualReconnect();
assert.equal(FakeWebSocket.instances.length, 2);
const second = openCurrent(manager);
assert.notStrictEqual(second, first);
assert.strictEqual(manager.ws, second);

// A late close from the retired socket cannot erase the replacement.
first.onclose({ code: 1006, reason: '', wasClean: false });
assert.strictEqual(manager.ws, second);
assert.equal(FakeWebSocket.instances.length, 2);

// A live answer is probed first so a healthy stream is not stranded.
manager.streaming = true;
manager._hiddenAt = Date.now();
manager._lastResumeCheckAt = Date.now();
manager._handleResume('visibility');
assert.equal(FakeWebSocket.instances.length, 2);
assert.equal(second.sent.at(-1), JSON.stringify({ type: 'ping' }));
assert.equal(timers.get(manager._pongTimeout).delay, 12000);
second.onmessage({ data: JSON.stringify({ type: 'pong' }) });
manager.streaming = false;

// Returning from any real hidden period replaces the potentially zombie socket.
manager._hiddenAt = Date.now();
manager._lastResumeCheckAt = Date.now();
manager._handleResume('visibility');
assert.equal(FakeWebSocket.instances.length, 3);
const third = manager.ws;
assert.notStrictEqual(third, second);

// That retired socket's eventual close is stale too.
second.onclose({ code: 1006, reason: '', wasClean: false });
assert.strictEqual(manager.ws, third);

// Fallback is idempotent, and a close while paused cannot restart the loop.
openCurrent(manager);
manager.reconnectAttempts = 6;
manager._enterHttpFallback('max-retries');
manager._enterHttpFallback('max-retries');
assert.equal(clientEvents.filter(event => event.type === 'transport_http_fallback').length, 1);
const countBeforePausedClose = FakeWebSocket.instances.length;
third.onclose({ code: 1006, reason: '', wasClean: false });
assert.equal(FakeWebSocket.instances.length, countBeforePausedClose);
assert.equal(manager.reconnectAttempts, 6);

// Replacing a socket mid-answer notifies Chat before loading history again.
const recovery = new WebSocketManager();
let disconnectEvents = 0;
recovery.on('disconnected', () => { disconnectEvents += 1; });
recovery.connect();
openCurrent(recovery);
recovery.streaming = true;
const recoveryOldSocket = recovery.ws;
recovery._replaceConnection('resume-streaming-visibility');
assert.equal(disconnectEvents, 1);
assert.notStrictEqual(recovery.ws, recoveryOldSocket);
""".replace("__SOURCE_PATH__", websocket_path)

    completed = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_server_connection_limit_does_not_collide_with_browser_retry_budget():
    browser = (ROOT / "static" / "js" / "websocket.js").read_text(encoding="utf-8")
    server = (ROOT / "api" / "chat.py").read_text(encoding="utf-8")

    browser_budget = int(re.search(r"maxAutoRetries\s*=\s*(\d+)", browser).group(1))
    server_budget = int(re.search(r"_WS_RATE_LIMIT\s*=\s*(\d+)", server).group(1))

    assert server_budget >= browser_budget * 3
    assert 'log.warning("WebSocket connection rate-limited for %s", client_ip)' in server


def test_disconnect_clears_stream_state_even_before_the_first_delta():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    disconnect = app.split("    onDisconnected() {", 1)[1].split("\n    _historyReloadLimit()", 1)[0]

    assert "if (Chat.isStreaming) {" in disconnect
    assert "if (Chat.isStreaming && Chat.currentStreamContent)" not in disconnect
    assert "Chat._lostStreamPending = true;" in disconnect
    assert "Chat.isStreaming = false;" in disconnect


def test_reconnect_reconciles_history_until_detached_turn_is_saved():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")

    assert "_scheduleStreamRecovery(conversationId)" in app
    assert "Date.now() + (20 * 60 * 1000)" in app
    assert "history.slice(lastUserIndex + 1)" in chat
    assert "App._scheduleStreamRecovery(this._viewConversationId)" in chat
