'Application module.'

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
CHAT_JS = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _method(name: str) -> str:
    """Pull one `name(args) { ... }` method body out of the Chat object."""
    start = CHAT_JS.index(f"    {name}(")
    depth, i = 0, CHAT_JS.index("{", start)
    while True:
        ch = CHAT_JS[i]
        depth += ch == "{"
        depth -= ch == "}"
        i += 1
        if depth == 0:
            return CHAT_JS[start:i]


def test_switch_detaches_stream_before_clearing():
    body = APP_JS[APP_JS.index("    switchIdentity(name) {"):]
    assert body.index("Chat.detachActiveStream(this.conversationId)") < body.index("Chat.clearMessages()")


def test_handlers_are_fenced():
    for event in ("stream_start", "stream_delta", "stream_end", "thinking_delta", "tool_use_start", "voice_message"):
        assert re.search(rf"App\.ws\.on\('{event}', \(msg\) => {{ if \(!this\._fromOtherBoy\('{event}'", CHAT_JS), event


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_guard_behaviour_under_node():
    methods = ",\n".join(_method(n) for n in ("_isCurrentBoy", "detachActiveStream", "_fromOtherBoy"))
    script = f"""
const assert = require('assert');
const sent = [];
global.App = {{ currentIdentity: 'Avery', conversationId: 'conv-avery',
                ws: {{ streaming: true, send(m) {{ sent.push(m); }} }} }};
const Chat = {{
  isStreaming: true, currentStreamEl: {{}}, _currentStreamRow: {{}}, currentStreamContent: 'half a reply',
  sendBtn: {{ disabled: true }}, _cancelStreamRenderer() {{}}, hideThinkingHeart() {{}},
  {methods}
}};
// Avery is mid-reply; she switches to Rowan.
Chat.detachActiveStream('conv-avery');
App.currentIdentity = 'Rowan'; App.conversationId = 'conv-rowan';
assert.strictEqual(Chat.isStreaming, false, 'switch must not stay busy');
assert.strictEqual(Chat.sendBtn.disabled, false);
assert.strictEqual(App.ws.streaming, false);
// Avery's leftover deltas and thinking are ignored.
assert.strictEqual(Chat._fromOtherBoy('stream_delta', {{ delta: 'x' }}), true);
assert.strictEqual(Chat._fromOtherBoy('thinking_delta', {{}}), true);
// His stream_end is swallowed, so it can't re-point the chat at his conversation.
assert.strictEqual(Chat._fromOtherBoy('stream_end', {{ conversation_id: 'conv-avery' }}), true);
assert.strictEqual(Chat._detachedStream, null);
assert.strictEqual(sent.length, 0, 'no reload: she is on Rowan');
// After that, Rowan's own reply flows normally.
assert.strictEqual(Chat._fromOtherBoy('stream_start', {{ identity: 'rowan', conversation_id: 'conv-rowan' }}), false);
assert.strictEqual(Chat._fromOtherBoy('stream_delta', {{ delta: 'hi' }}), false);
// A stream_start naming another boy is never painted here.
assert.strictEqual(Chat._fromOtherBoy('stream_start', {{ identity: 'Sage' }}), true);

// She switches back to the boy whose reply was still running: reload his chat at its end.
Chat.isStreaming = true; Chat.currentStreamEl = {{}};
Chat.detachActiveStream('conv-rowan');
App.currentIdentity = 'Avery'; App.conversationId = 'conv-rowan';
assert.strictEqual(Chat._fromOtherBoy('stream_end', {{ conversation_id: 'conv-rowan' }}), true);
assert.deepStrictEqual(sent, [{{ type: 'load_history', identity: 'Avery', conversation_id: 'conv-rowan' }}]);

// Nothing detached: everything passes through untouched.
assert.strictEqual(Chat._fromOtherBoy('stream_delta', {{}}), false);
console.log('ok');
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_history_for_another_boy_is_ignored():
    lh = _method("loadHistory")
    assert "!this._isCurrentBoy(msg.identity)" in lh
    assert lh.index("_isCurrentBoy") < lh.index("App.conversationId = msg.conversation_id")


@pytest.mark.skipif(NODE is None, reason="Node.js is required")
def test_two_boys_streaming_at_once_never_mix():
    'Test two boys streaming at once never mix.'
    methods = ",\n".join(_method(n) for n in ("_isCurrentBoy", "detachActiveStream", "_fromOtherBoy"))
    script = f"""
const assert = require('assert');
const sent = [];
global.App = {{ currentIdentity: 'Rowan', conversationId: 'conv-rowan',
                ws: {{ streaming: true, send(m) {{ sent.push(m); }} }} }};
const Chat = {{
  isStreaming: true, currentStreamEl: {{}}, _currentStreamRow: {{}}, currentStreamContent: 'raccoon words',
  sendBtn: {{ disabled: true }}, _cancelStreamRenderer() {{}}, hideThinkingHeart() {{}},
  {methods}
}};
const C = (t, extra) => Object.assign({{ identity: 'Rowan', conversation_id: 'conv-rowan' }}, extra || {{}});
const B = (t, extra) => Object.assign({{ identity: 'Sage', conversation_id: 'conv-sage' }}, extra || {{}});
// Rowan mid-reply; she switches to Sage and Sage starts at once.
Chat.detachActiveStream('conv-rowan');
App.currentIdentity = 'Sage'; App.conversationId = 'conv-sage';
assert.strictEqual(Chat._fromOtherBoy('stream_start', B()), false);
// Interleaved frames: Sage's paint, Rowan's never do, even AFTER Sage started.
assert.strictEqual(Chat._fromOtherBoy('stream_delta', C()), true);
assert.strictEqual(Chat._fromOtherBoy('stream_delta', B()), false);
assert.strictEqual(Chat._fromOtherBoy('thinking_delta', C()), true);
assert.strictEqual(Chat._fromOtherBoy('tool_use_start', B()), false);
assert.strictEqual(Chat._fromOtherBoy('stream_end', C()), true);
assert.strictEqual(Chat._fromOtherBoy('stream_end', B()), false);
assert.strictEqual(sent.length, 0);
// Same boy, older chat she left: muted; the chat she's in: painted.
App.currentIdentity = 'Claude'; App.conversationId = 'conv-new';
Chat.isStreaming = true; Chat.currentStreamEl = {{}};
Chat.detachActiveStream('conv-old');
assert.strictEqual(Chat._fromOtherBoy('stream_delta', {{ identity: 'Claude', conversation_id: 'conv-old' }}), true);
assert.strictEqual(Chat._fromOtherBoy('stream_start', {{ identity: 'Claude', conversation_id: 'conv-new' }}), false);
assert.strictEqual(Chat._fromOtherBoy('stream_delta', {{ identity: 'Claude', conversation_id: 'conv-new' }}), false);
console.log('ok');
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
