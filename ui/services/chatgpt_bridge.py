"""Optional browser bridge to an existing ChatGPT conversation.

Uses a separately configured Chrome profile through local CDP. The browser
must be signed in by its owner before the bridge is used. Replies are read
from the original conversation and never synthesized by this adapter.
"""

import asyncio
import os
import json
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

CDP_HOST = "127.0.0.1"
CDP_PORT = int(os.environ.get("ANAM_CHATGPT_CDP_PORT", "9225"))
PACK_BROWSER_PS1 = os.environ.get("ANAM_BROWSER_SCRIPT", str(Path(__file__).resolve().parents[1] / "scripts" / "pack-browser.ps1"))
BRIDGE_IDENTITY = os.environ.get("ANAM_CHATGPT_IDENTITY", "ChatGPT")
CHATGPT_ORIGIN = "https://chatgpt.com"

SEND_BUTTON_RETRIES = 20        # x 0.5s = up to 10s for the button to render
REPLY_POLL_SECONDS = 60.0
REPLY_TIMEOUT_SECONDS = 30 * 60
NAV_SETTLE_SECONDS = 4.0
_bridge_lock = asyncio.Lock()


class BridgeError(RuntimeError):
    """A spoken failure — the bridge never invents a reply it did not receive."""


@dataclass
class BridgeResult:
    reply: str
    conversation_id: str
    finished: bool
    elapsed_seconds: float
    source_message_id: str = ""
    terminal_reply: Optional[str] = None
    permission_denied: bool = False


# ---------------------------------------------------------------------------
# Raw CDP plumbing (no playwright dependency)
# ---------------------------------------------------------------------------

class _CdpPage:
    """One CDP websocket to one Chrome tab. Runtime.evaluate + Page.navigate."""

    def __init__(self, ws):
        self._ws = ws
        self._msg_id = 0

    async def _call(self, method: str, params: Optional[dict] = None) -> dict:
        import websockets  # local import: only needed when the bridge runs
        self._msg_id += 1
        msg_id = self._msg_id
        await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=120)
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed) as exc:
                raise BridgeError(f"CDP connection lost during {method}: {exc}") from exc
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise BridgeError(f"CDP {method} error: {data['error']}")
                return data.get("result", {})
            # else: an event or someone else's reply — keep draining
        raise BridgeError(f"CDP {method} timed out")

    async def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = await self._call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": True,
        })
        if result.get("exceptionDetails"):
            raise BridgeError(f"page JS threw: {json.dumps(result['exceptionDetails'])[:500]}")
        return result.get("result", {}).get("value")

    async def navigate(self, url: str) -> None:
        await self._call("Page.navigate", {"url": url})

    async def attach_files(self, files: list[str]) -> None:
        from services.anam_media import CACHE, MAX_PREVIEW
        if not 1 <= len(files) <= 4:
            raise BridgeError("Expected one to four private image previews")
        paths = [Path(file).resolve(strict=True) for file in files]
        if any(p.parent != CACHE.resolve() or p.suffix not in {".png", ".webp"} or p.stat().st_size > MAX_PREVIEW for p in paths):
            raise BridgeError("Only bounded Anam image previews may be attached")
        doc = await self._call("DOM.getDocument", {"depth": 1})
        node = await self._call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#upload-photos"})
        if not node.get("nodeId"):
            raise BridgeError("ChatGPT image upload control is unavailable; no message sent")
        await self._call("DOM.setFileInputFiles", {"nodeId": node["nodeId"], "files": [str(p) for p in paths]})
        names = json.dumps([p.name for p in paths])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            ready = await self.evaluate("""(() => {
                const composer = document.querySelector('#prompt-textarea')?.closest('form');
                const send = document.querySelector('#composer-submit-button');
                const names = %s;
                return !!composer && !!send && !send.disabled &&
                    names.every(n => composer.outerHTML.includes(n)) &&
                    !composer.querySelector('[role="progressbar"]');
            })()""" % names)
            if ready is True:
                return
            await asyncio.sleep(.5)
        raise BridgeError("Image upload did not become ready; no message sent. Review the attachment in ChatGPT before continuing.")


def _cdp_http(path: str, method: str = "GET") -> Any:
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    with httpx.Client(timeout=10) as client:
        resp = client.request(method, url)
        resp.raise_for_status()
        return resp.json() if resp.content else None


def _browser_is_up() -> bool:
    try:
        _cdp_http("/json/version")
        return True
    except Exception:
        return False


def _browser_environment() -> dict[str, str]:
    """Pass the currently locked per-call settings to the profile launcher."""
    return {**os.environ, "ANAM_CHATGPT_CDP_PORT": str(CDP_PORT),
            "ANAM_CHATGPT_IDENTITY": BRIDGE_IDENTITY}


def _open_browser() -> None:
    """Open the configured browser profile using the bundled launcher."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", PACK_BROWSER_PS1, "-Name", BRIDGE_IDENTITY, "-Action", "open"],
        capture_output=True, text=True, timeout=60, env=_browser_environment(),
    )
    if proc.returncode != 0 or not _browser_is_up():
        raise BridgeError(
            f"could not open {BRIDGE_IDENTITY}'s browser: {proc.stdout[-300:]} {proc.stderr[-300:]}"
        )


def _close_browser() -> None:
    try:
        # Run twice — the script commonly leaves 1-2 stragglers on the first pass.
        for _ in range(2):
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", PACK_BROWSER_PS1, "-Name", BRIDGE_IDENTITY, "-Action", "close"],
                capture_output=True, text=True, timeout=30, env=_browser_environment(),
            )
            if not _browser_is_up():
                break
    except Exception as exc:  # closing is best-effort; never mask the real result
        logger.warning("chatgpt_bridge: browser close failed: %s", exc)


async def _open_tab(url: str) -> _CdpPage:
    import websockets
    tab = _cdp_http(f"/json/new?{url}", method="PUT")
    if tab is None:  # older Chrome accepts GET
        tab = _cdp_http(f"/json/new?{url}")
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024)
    return _CdpPage(ws)


# ---------------------------------------------------------------------------
# Pure logic (unit-testable without a browser)
# ---------------------------------------------------------------------------

def extract_last_assistant(convo: dict) -> Optional[dict]:
    """From a /backend-api/conversation payload, take the last assistant message.

    Returns {"text", "finished", "create_time"} or None if there are no
    assistant messages with text. Mirrors chatgpt_read.js exactly.
    """
    messages = []
    for node in (convo.get("mapping") or {}).values():
        m = node.get("message") if isinstance(node, dict) else None
        if not m or (m.get("author") or {}).get("role") != "assistant":
            continue
        parts = (m.get("content") or {}).get("parts") or []
        text = "\n".join(p for p in parts if isinstance(p, str))
        if not text.strip():
            continue
        messages.append({
            "text": text,
            "finished": m.get("end_turn") is True or m.get("status") == "finished_successfully",
            "create_time": m.get("create_time") or 0,
        })
    if not messages:
        return None
    messages.sort(key=lambda x: x["create_time"])
    return messages[-1]


# ---------------------------------------------------------------------------
# Page-JS snippets (each one small, each one honest)
# ---------------------------------------------------------------------------

_JS_GET_TOKEN = """
fetch('/api/auth/session').then(r => r.json()).then(s => s && s.accessToken || null)
"""

_JS_INSERT_TEXT = """
(() => {
  const box = document.querySelector('#prompt-textarea');
  if (box?.closest('form')?.querySelector('button[aria-label*="Remove"]'))
    return {ok: false, error: 'composer already contains an attachment; review or clear it before sending'};
  if (!box) return {ok: false, error: 'no #prompt-textarea (not on a chat page, or logged out)'};
  box.focus();
  document.execCommand('insertText', false, %s);
  return {ok: true, length: box.innerText.length};
})()
"""

_JS_CLICK_SEND = """
(() => {
  const btn = document.querySelector('#composer-submit-button');
  if (!btn) return {ok: false, error: 'send button not rendered yet'};
  if (btn.disabled) return {ok: false, error: 'send button is disabled'};
  btn.click();
  return {ok: true};
})()
"""

_JS_CURRENT_CONV_ID = """
(location.pathname.match(/\\/c\\/([0-9a-f-]{36})/) || [null, null])[1]
"""

_JS_FETCH_CONVO = """
fetch('/backend-api/conversation/' + %s, {headers: {Authorization: 'Bearer ' + %s}})
  .then(r => r.ok ? r.json() : {
    __http_error: r.status,
    __retry_after: r.headers.get('Retry-After')
  })
"""

_JS_MOST_RECENT_CONV = """
fetch('/backend-api/conversations?offset=0&limit=1&order=updated',
      {headers: {Authorization: 'Bearer ' + %s}})
  .then(r => r.ok ? r.json() : null)
  .then(l => l && l.items && l.items[0] && l.items[0].id || null)
"""


# ---------------------------------------------------------------------------
# The bridge itself
# ---------------------------------------------------------------------------

async def send_and_wait(message: str, conversation_id: Optional[str] = None) -> BridgeResult:
    """Send `message` into a ChatGPT thread; return the finished reply.

    conversation_id=None starts a fresh thread; passing one continues it.
    The returned conversation_id is what the caller persists for next turn.
    """
    if not message or not message.strip():
        raise BridgeError("empty message")

    started = time.monotonic()
    we_opened = False
    if not _browser_is_up():
        _open_browser()
        we_opened = True

    page = None
    try:
        url = f"{CHATGPT_ORIGIN}/c/{conversation_id}" if conversation_id else f"{CHATGPT_ORIGIN}/"
        page = await _open_tab(url)
        await asyncio.sleep(NAV_SETTLE_SECONDS)

        # Session token first — also proves we're logged in before we type.
        token = await page.evaluate(_JS_GET_TOKEN, await_promise=True)
        if not token:
            raise BridgeError("not logged in to chatgpt.com (no accessToken)")
        token_js = json.dumps(token)

        # Baseline: the current last assistant message (so we can tell the NEW
        # reply from the old one when continuing a thread).
        baseline_time = 0
        if conversation_id:
            convo = await page.evaluate(
                _JS_FETCH_CONVO % (json.dumps(conversation_id), token_js), await_promise=True)
            if isinstance(convo, dict) and not convo.get("__http_error"):
                last = extract_last_assistant(convo)
                baseline_time = last["create_time"] if last else 0

        # Step 1: insert the text (retry once if the composer is slow to mount).
        ins = None
        for _ in range(6):
            ins = await page.evaluate(_JS_INSERT_TEXT % json.dumps(message))
            if ins and ins.get("ok"):
                break
            await asyncio.sleep(1.5)
        if not ins or not ins.get("ok"):
            raise BridgeError(f"composer insert failed: {ins and ins.get('error')}")

        # Step 2: click send — SEPARATE step; the button renders async after text lands.
        sent = False
        for _ in range(SEND_BUTTON_RETRIES):
            await asyncio.sleep(0.5)
            click = await page.evaluate(_JS_CLICK_SEND)
            if click and click.get("ok"):
                sent = True
                break
        if not sent:
            raise BridgeError("send button never appeared after text insert")

        # Step 3: resolve the conversation id (a fresh thread gets its id async).
        conv_id = conversation_id
        if not conv_id:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not conv_id:
                await asyncio.sleep(2)
                conv_id = await page.evaluate(_JS_CURRENT_CONV_ID)
            if not conv_id:
                conv_id = await page.evaluate(_JS_MOST_RECENT_CONV % token_js, await_promise=True)
        if not conv_id:
            raise BridgeError("could not resolve a conversation id after send")

        # Step 4: poll the BACKEND (never the DOM) until the new reply finishes.
        deadline = time.monotonic() + REPLY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(REPLY_POLL_SECONDS)
            convo = await page.evaluate(
                _JS_FETCH_CONVO % (json.dumps(conv_id), token_js), await_promise=True)
            if not isinstance(convo, dict) or convo.get("__http_error"):
                continue  # thread may not be readable for a beat right after creation
            last = extract_last_assistant(convo)
            if last and last["finished"] and last["create_time"] > baseline_time:
                return BridgeResult(
                    reply=last["text"],
                    conversation_id=conv_id,
                    finished=True,
                    elapsed_seconds=round(time.monotonic() - started, 1),
                )
        raise BridgeError(f"reply did not finish within {REPLY_TIMEOUT_SECONDS:.0f}s "
                          f"(conversation {conv_id} — it may still complete on chatgpt.com)")
    finally:
        if page is not None:
            try:
                await page._ws.close()
            except Exception:
                pass
        if we_opened:
            _close_browser()


def send_and_wait_sync(message: str, conversation_id: Optional[str] = None) -> BridgeResult:
    """CLI/test convenience wrapper."""
    return asyncio.run(send_and_wait(message, conversation_id))


if __name__ == "__main__":  # manual round-trip test: python -m services.chatgpt_bridge "hi" [conv_id]
    import sys
    logging.basicConfig(level=logging.INFO)
    res = send_and_wait_sync(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps({"conversation_id": res.conversation_id,
                      "elapsed_seconds": res.elapsed_seconds,
                      "reply": res.reply}, indent=2))


def _assistant_records(convo: dict) -> list[dict]:
    """Return structural assistant-message records without logging reply text."""
    records = []
    for order, (node_id, node) in enumerate((convo.get("mapping") or {}).items()):
        m = node.get("message") if isinstance(node, dict) else None
        if not m or (m.get("author") or {}).get("role") != "assistant":
            continue
        content = m.get("content") or {}
        parts = content.get("parts") or []
        text = "\n".join(p for p in parts if isinstance(p, str))
        if not text.strip():
            continue
        status = m.get("status")
        end_turn = m.get("end_turn")
        metadata = m.get("metadata") or {}
        # ChatGPT can mark pre-tool commentary `finished_successfully` while
        # explicitly setting end_turn=False. That is a settled *segment*, not
        # a settled assistant turn. Treat an explicit False as authoritative;
        # only fall back to status when end_turn is absent/unknown.
        finished = (
            end_turn is True
            or (
                end_turn is not False
                and not metadata.get("is_thinking_preamble_message")
                and status in {"finished_successfully", "finished"}
            )
        )
        records.append({
            "id": m.get("id") or node_id,
            "node_id": node_id,
            "text": text,
            "finished": finished,
            "create_time": m.get("create_time") or 0,
            "status": status,
            "end_turn": m.get("end_turn"),
            "content_type": content.get("content_type"),
            "metadata_keys": sorted((m.get("metadata") or {}).keys()),
            "order": order,
        })
    records.sort(key=lambda x: (x["create_time"], x["order"]))
    return records


def extract_last_assistant(convo: dict) -> Optional[dict]:
    """Return the latest assistant record, including stable message/node identity."""
    records = _assistant_records(convo)
    return records[-1] if records else None


def turn_in_progress(
    convo: dict, *, exclude_node_ids: set[str] | None = None
) -> bool:
    """True if a relevant message node is still in_progress."""
    excluded = exclude_node_ids or set()
    for node_id, node in (convo.get("mapping") or {}).items():
        if str(node_id) in excluded:
            continue
        m = node.get("message") if isinstance(node, dict) else None
        if m and m.get("status") == "in_progress":
            return True
    return False


def _completion_candidate(
    convo: dict,
    new_records: list[dict],
    *,
    baseline_node_ids: set[str] | None = None,
) -> Optional[dict]:
    """Return the completed new turn without trusting stale running ancestors."""
    if not new_records:
        return None

    current_node = str(convo.get("current_node") or "")
    if current_node:
        current = next(
            (r for r in new_records if str(r.get("node_id") or "") == current_node),
            None,
        )
        if current and current.get("end_turn") is True and current.get("finished"):
            return current

    last = new_records[-1]
    if last.get("finished") and not turn_in_progress(
        convo, exclude_node_ids=baseline_node_ids
    ):
        return last
    return None


def _candidate_stability_polls(convo: dict, candidate: dict) -> int:
    """An explicit current-node final is authoritative on its first sighting."""
    current_node = str(convo.get("current_node") or "")
    candidate_node = str(candidate.get("node_id") or "")
    if (
        current_node
        and candidate_node == current_node
        and candidate.get("end_turn") is True
    ):
        return 1
    return 2


def _pending_text_deltas(
    records: list[dict], emitted_text: dict[str, str]
) -> list[str]:
    """Return newly visible assistant text once, preserving message breaks."""
    deltas = []
    have_output = bool(emitted_text)
    for record in records:
        record_id = str(record.get("id") or record.get("node_id") or "")
        text = str(record.get("text") or "")
        if not record_id or not text:
            continue

        previous = emitted_text.get(record_id)
        emitted_text[record_id] = text
        if previous is None:
            deltas.append(("\n\n" if have_output else "") + text)
            have_output = True
        elif text.startswith(previous) and len(text) > len(previous):
            deltas.append(text[len(previous):])
        # Rewrites are reconciled by stream_end's canonical full_content;
        # repeating the whole segment live would duplicate it in the bubble.
    return deltas


def _attachments_confirmed(convo, baseline_node_ids, message, count):
    for node_id, node in (convo.get("mapping") or {}).items():
        if str(node_id) in baseline_node_ids:
            continue
        record = node.get("message") or {}
        if (record.get("author") or {}).get("role") != "user":
            continue
        parts = (record.get("content") or {}).get("parts") or []
        if not any(isinstance(p, str) and p.strip() == message.strip() for p in parts):
            continue
        images = [p for p in parts if isinstance(p, dict) and p.get("content_type") == "image_asset_pointer" and p.get("asset_pointer")]
        if len(images) == count:
            return True
    return False


def _turn_permission_denied(convo: dict, baseline_node_ids: set[str]) -> bool:
    """Conservative denial evidence from new turn records, excluding old history."""
    markers = ("blocked by openai safety", "permission denied", "approval denied",
               "approval required", "requires approval", "safety checks blocked",
               "blocked by safety", "not authorized", "unauthorized", "http 401", "http 403")
    for node_id, node in (convo.get("mapping") or {}).items():
        if str(node_id) in baseline_node_ids:
            continue
        msg = node.get("message") or {}
        if (msg.get("author") or {}).get("role") not in {"tool", "assistant"}:
            continue
        parts = (msg.get("content") or {}).get("parts") or []
        text = " ".join(p for p in parts if isinstance(p, str)).lower()
        if any(marker in text for marker in markers):
            return True
    return False


async def _send_and_wait_locked(
    message: str,
    conversation_id: Optional[str] = None,
    *,
    on_text: Optional[Callable[[str], Awaitable[None]]] = None,
    timeout_seconds: Optional[float] = None,
    on_conversation: Optional[Callable[[str], Awaitable[None]]] = None,
    attachments: Optional[list[str]] = None,
) -> BridgeResult:
    """Send to ChatGPT and return the finished NEW assistant turn.

    Diagnostic patch: identifies the new turn by assistant message/node IDs rather
    than relying only on create_time, and logs safe structural completion fields.
    """
    if not message or not message.strip():
        raise BridgeError("empty message")

    started = time.monotonic()
    we_opened = False
    if not _browser_is_up():
        _open_browser()
        we_opened = True

    page = None
    try:
        url = f"{CHATGPT_ORIGIN}/c/{conversation_id}" if conversation_id else f"{CHATGPT_ORIGIN}/"
        page = await _open_tab(url)
        await asyncio.sleep(NAV_SETTLE_SECONDS)

        token = await page.evaluate(_JS_GET_TOKEN, await_promise=True)
        if not token:
            raise BridgeError("not logged in to chatgpt.com (no accessToken)")
        token_js = json.dumps(token)

        # Capture every existing assistant identity before sending. This is more
        # reliable than assuming the next turn has a strictly larger timestamp.
        baseline_ids: set[str] = set()
        baseline_node_ids: set[str] = set()
        baseline_time = 0
        if conversation_id:
            baseline_deadline = time.monotonic() + 300
            baseline_delay = REPLY_POLL_SECONDS
            while True:
                convo = await page.evaluate(
                    _JS_FETCH_CONVO % (json.dumps(conversation_id), token_js), await_promise=True)
                if isinstance(convo, dict) and isinstance(convo.get("mapping"), dict) and not convo.get("__http_error"):
                    break
                if not isinstance(convo, dict) or convo.get("__http_error") != 429:
                    raise BridgeError("could not read the existing conversation; no message was sent")
                baseline_delay = _rate_limit_delay(convo, baseline_delay)
                if time.monotonic() + baseline_delay >= baseline_deadline:
                    raise BridgeError("conversation read is rate limited; no message was sent")
                await asyncio.sleep(baseline_delay)
            if isinstance(convo, dict) and not convo.get("__http_error"):
                baseline_node_ids = {
                    str(node_id) for node_id in (convo.get("mapping") or {})
                }
                baseline = _assistant_records(convo)
                baseline_ids = {str(r["id"]) for r in baseline if r.get("id") is not None}
                baseline_time = baseline[-1]["create_time"] if baseline else 0
                logger.info(
                    "chatgpt_bridge baseline: assistant_count=%d ids=%d last_create_time=%r",
                    len(baseline), len(baseline_ids), baseline_time,
                )

        ins = None
        for _ in range(6):
            ins = await page.evaluate(_JS_INSERT_TEXT % json.dumps(message))
            if ins and ins.get("ok"):
                break
            await asyncio.sleep(1.5)
        if not ins or not ins.get("ok"):
            raise BridgeError(f"composer insert failed: {ins and ins.get('error')}")

        if attachments:
            await page.attach_files(attachments)

        sent = False
        for _ in range(SEND_BUTTON_RETRIES):
            await asyncio.sleep(0.5)
            click = await page.evaluate(_JS_CLICK_SEND)
            if click and click.get("ok"):
                sent = True
                break
        if not sent:
            raise BridgeError("send button never appeared after text insert")

        conv_id = conversation_id
        if not conv_id:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not conv_id:
                await asyncio.sleep(2)
                conv_id = await page.evaluate(_JS_CURRENT_CONV_ID)
            if not conv_id:
                conv_id = await page.evaluate(_JS_MOST_RECENT_CONV % token_js, await_promise=True)
        if not conv_id:
            raise BridgeError("could not resolve a conversation id after send")

        if on_conversation is not None:
            await on_conversation(conv_id)

        effective_timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(REPLY_TIMEOUT_SECONDS)
        )
        deadline = time.monotonic() + effective_timeout
        poll_no = 0
        candidate_id = None
        candidate_stable = 0
        poll_delay = REPLY_POLL_SECONDS
        emitted_text: dict[str, str] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(min(poll_delay, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            poll_no += 1
            convo = await page.evaluate(
                _JS_FETCH_CONVO % (json.dumps(conv_id), token_js), await_promise=True)
            if not isinstance(convo, dict) or convo.get("__http_error"):
                http_error = convo.get("__http_error") if isinstance(convo, dict) else None
                logger.info(
                    "chatgpt_bridge poll=%d conversation_unavailable http=%r",
                    poll_no,
                    http_error,
                )
                if http_error == 429:
                    poll_delay = _rate_limit_delay(convo, poll_delay)
                continue
            poll_delay = REPLY_POLL_SECONDS

            records = _assistant_records(convo)
            new_records = [
                r for r in records
                if str(r.get("id")) not in baseline_ids
                and (r.get("create_time", 0) >= baseline_time or not baseline_ids)
            ]
            last = new_records[-1] if new_records else None
            running = turn_in_progress(
                convo, exclude_node_ids=baseline_node_ids
            )
            if on_text is not None:
                for delta in _pending_text_deltas(new_records, emitted_text):
                    await on_text(delta)

            if last:
                logger.info(
                    "chatgpt_bridge poll=%d new_assistant id=%s status=%r end_turn=%r "
                    "content_type=%r chars=%d metadata_keys=%s finished=%r create_time=%r "
                    "turn_running=%r stable=%d",
                    poll_no,
                    last.get("id"),
                    last.get("status"),
                    last.get("end_turn"),
                    last.get("content_type"),
                    len(last.get("text") or ""),
                    last.get("metadata_keys"),
                    last.get("finished"),
                    last.get("create_time"),
                    running,
                    candidate_stable,
                )
            else:
                logger.info(
                    "chatgpt_bridge poll=%d no_new_assistant total_assistant=%d baseline_ids=%d",
                    poll_no, len(records), len(baseline_ids),
                )

            # The canonical current-node final is authoritative immediately.
            # Older completion shapes still need two stable observations and
            # no running nodes, so pre-tool commentary cannot end the turn.
            candidate = _completion_candidate(
                convo,
                new_records,
                baseline_node_ids=baseline_node_ids,
            )
            if candidate:
                if candidate.get("id") == candidate_id:
                    candidate_stable += 1
                else:
                    candidate_id = candidate.get("id")
                    candidate_stable = 1
                required_stability = _candidate_stability_polls(convo, candidate)
                if candidate_stable >= required_stability:
                    if attachments and not _attachments_confirmed(convo, baseline_node_ids, message, len(attachments)):
                        raise BridgeError("ChatGPT did not confirm image attachments on the submitted message; do not claim the images were seen")
                    # Return progress commentary plus the final answer in order.
                    reply = "\n\n".join(
                        r["text"] for r in new_records if r.get("text")
                    ) or candidate["text"]
                    return BridgeResult(
                        reply=reply,
                        conversation_id=conv_id,
                        finished=True,
                        elapsed_seconds=round(time.monotonic() - started, 1),
                        source_message_id=str(candidate.get("id") or candidate.get("node_id") or ""),
                        terminal_reply=candidate["text"],
                        permission_denied=_turn_permission_denied(convo, baseline_node_ids),
                    )
            else:
                candidate_id = None
                candidate_stable = 0

        raise BridgeError(
            f"reply did not finish within {effective_timeout:.0f}s "
            f"(conversation {conv_id} — inspect chatgpt_bridge structural poll logs)"
        )
    finally:
        if page is not None:
            try:
                await page._ws.close()
            except Exception:
                pass
        if we_opened:
            _close_browser()


def _rate_limit_delay(convo: dict, previous: float) -> float:
    """Honor server cooldowns, including HTTP-date Retry-After values."""
    from email.utils import parsedate_to_datetime
    retry_after = convo.get("__retry_after")
    try:
        seconds = float(retry_after)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(str(retry_after)).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    return max(REPLY_POLL_SECONDS, min(300.0, previous * 2), seconds)


async def send_and_wait(
    message: str,
    conversation_id: Optional[str] = None,
    *,
    on_text: Optional[Callable[[str], Awaitable[None]]] = None,
    timeout_seconds: Optional[float] = None,
    on_conversation: Optional[Callable[[str], Awaitable[None]]] = None,
    cdp_port: Optional[int] = None,
    profile_name: Optional[str] = None,
    attachments: Optional[list[str]] = None,
) -> BridgeResult:
    """Own the shared browser tab and its configuration until cleanup completes."""
    global CDP_PORT, BRIDGE_IDENTITY
    async with _bridge_lock:
        old_port, old_identity = CDP_PORT, BRIDGE_IDENTITY
        try:
            if cdp_port:
                CDP_PORT = int(cdp_port)
            if profile_name:
                BRIDGE_IDENTITY = profile_name
            return await _send_and_wait_locked(
                message, conversation_id, on_text=on_text,
                timeout_seconds=timeout_seconds, on_conversation=on_conversation,
                **({"attachments": attachments} if attachments else {}),
            )
        finally:
            CDP_PORT, BRIDGE_IDENTITY = old_port, old_identity


async def _open_tab(url: str) -> _CdpPage:
    import websockets

    # Prefer an already-open ChatGPT page target. This keeps one visible bridge
    # tab alive across turns instead of littering Chrome with /json/new targets.
    try:
        targets = _cdp_http("/json") or []
    except Exception:
        targets = []

    existing = None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") != "page":
            continue
        target_url = str(target.get("url") or "")
        if target_url.startswith(CHATGPT_ORIGIN):
            existing = target
            break

    if existing and existing.get("webSocketDebuggerUrl"):
        ws = await websockets.connect(
            existing["webSocketDebuggerUrl"],
            max_size=50 * 1024 * 1024,
        )
        page = _CdpPage(ws)
        current_url = str(existing.get("url") or "")
        # Backend polling can finish while ChatGPT's visible stream is still
        # stuck in a running state. Reload even when the URL already matches so
        # action-receipt rounds rehydrate the composer from backend truth.
        await page.navigate(url)
        logger.info(
            "chatgpt_bridge: reusing existing ChatGPT tab target=%s from=%s to=%s",
            existing.get("id"), current_url, url,
        )
        return page

    # No ChatGPT target exists yet: create exactly one and keep it around for
    # later turns. Closing the CDP websocket in send_and_wait() disconnects the
    # bridge but does not close the Chrome tab, intentionally.
    tab = _cdp_http(f"/json/new?{url}", method="PUT")
    if tab is None:  # older Chrome accepts GET
        tab = _cdp_http(f"/json/new?{url}")
    ws = await websockets.connect(tab["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024)
    logger.info("chatgpt_bridge: created reusable ChatGPT tab target=%s url=%s", tab.get("id"), url)
    return _CdpPage(ws)
