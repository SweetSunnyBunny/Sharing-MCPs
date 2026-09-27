"""Redacted connector observations: preserve causes without logging payloads."""
from datetime import datetime, timezone
import threading

_events = {}
_lock = threading.RLock()
HINTS = {
    "healthy": "The latest request completed.",
    "connected_unchecked": "Connected at startup; use Check to verify its current catalog.",
    "not_connected": "No shared connection; the gateway can try a fresh connection.",
    "auth_required": "Refresh the connector's login or credentials.",
    "permission_denied": "Review the denied permission. A retry cannot grant access.",
    "rate_limited": "Wait before trying again; avoid repeated polling.",
    "missing_tool": "Discover this server again and use an exact current tool name.",
    "invalid_arguments": "Check the tool's current argument schema.",
    "stale_connection": "A fresh catalog connection is attempted before dispatch.",
    "unavailable": "Check whether the backing service is running and reachable.",
}


def classify(error):
    code = getattr(getattr(error, "response", None), "status_code", None)
    text = str(error).lower()
    if code == 401 or "401" in text or "unauthorized" in text or "authentication" in text:
        return "auth_required"
    if code == 403 or "403" in text or "permission denied" in text or "approval denied" in text:
        return "permission_denied"
    if code == 429 or "429" in text or "rate limit" in text:
        return "rate_limited"
    if "unknown tool" in text or "tool not found" in text:
        return "missing_tool"
    if "invalid arguments" in text or "validation" in text:
        return "invalid_arguments"
    if any(word in text for word in ("session", "closed", "disconnect", "transport", "broken pipe")):
        return "stale_connection"
    return "unavailable"


def observe(server, *, tool="", stage="catalog", error=None):
    kind = "healthy" if error is None else classify(error)
    event = {"status": kind, "tool": tool, "stage": stage,
             "checked_at": datetime.now(timezone.utc).isoformat(), "hint": HINTS[kind]}
    with _lock:
        prior = _events.get(server, {})
        _events[server] = {**event, "last_failure": event if error is not None else prior.get("last_failure")}


def snapshot():
    from services.anam_tool_gateway import _configured
    from services.mcp_bridge import mcp_bridge
    names = sorted(set(_configured()) | {"anam-context"})
    with _lock:
        rows = []
        for name in names:
            connected = name == "anam-context" or name in mcp_bridge._clients
            default = "connected_unchecked" if connected else "not_connected"
            rows.append({"server": name, "connected": connected, **_events.get(name, {
                "status": default, "hint": HINTS[default], "checked_at": None, "last_failure": None})})
    return {"servers": rows, "hint": "These are Anam connector observations. ChatGPT's separate app permissions and login state are not visible here."}
