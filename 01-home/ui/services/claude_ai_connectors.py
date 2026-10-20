'Application module.'


from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import httpx

from services.cc_mcp_oauth import _read_all

log = logging.getLogger(__name__)

PROXY_BASE = "https://mcp-proxy.anthropic.com/v1/mcp/"
LIST_URL = "https://api.anthropic.com/v1/mcp_servers?limit=1000"
LIST_BETA = "mcp-servers-2025-12-04"
SESSION_HEADER = "X-Mcp-Client-Session-Id"
CONFIG_MARKER = "claudeAiConnector"

_ID_TTL_SECONDS = 30 * 60
_LIST_TIMEOUT = 20.0

_ids: dict[str, str] = {}
_ids_fetched_at = 0.0
_ids_loaded_from_disk = False
_refresh_lock: asyncio.Lock | None = None


def _cache_path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "claude_ai_connectors.json"


def _load_disk_cache() -> None:
    global _ids_loaded_from_disk
    if _ids_loaded_from_disk:
        return
    _ids_loaded_from_disk = True
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    saved = data.get("ids") if isinstance(data, dict) else None
    if isinstance(saved, dict):
        for name, sid in saved.items():
            if isinstance(name, str) and isinstance(sid, str) and sid:
                _ids.setdefault(name, sid)


def _save_disk_cache() -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ids": _ids, "saved_at": int(time.time())}, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.debug("Could not save claude.ai connector id cache", exc_info=True)


def account_token(*, fresh: bool = False) -> str:
    """The Claude account access token, read from disk (never logged)."""
    oauth = _read_all(use_cache=not fresh).get("claudeAiOauth")
    if isinstance(oauth, dict):
        return str(oauth.get("accessToken") or "")
    return ""


def is_connector(config: dict | None) -> bool:
    return isinstance(config, dict) and bool(config.get(CONFIG_MARKER))


def known_id(display_name: str) -> str:
    _load_disk_cache()
    return _ids.get(display_name, "")


def proxy_url(server_id: str) -> str:
    return f"{PROXY_BASE}{server_id}"


def config_entries(spec: Any) -> dict[str, dict]:
    """Bridge server configs for the connectors named in mcp-servers.json.

    An entry whose id is not known yet carries no url; ``resolve`` fills it in
    at connect time.
    """
    entries: dict[str, dict] = {}
    if not isinstance(spec, dict):
        return entries
    for name, display in spec.items():
        server = str(name or "").strip()
        label = str(display or "").strip()
        if not server or not label or server.startswith("_"):
            continue
        cfg: dict[str, Any] = {"type": "http", CONFIG_MARKER: label}
        sid = known_id(label)
        if sid:
            cfg["url"] = proxy_url(sid)
        entries[server] = cfg
    return entries


async def refresh_ids(*, force: bool = False) -> dict[str, str]:
    """Re-read the account's connector list (at most every 30 minutes)."""
    global _ids_fetched_at, _refresh_lock
    _load_disk_cache()
    if not force and _ids and time.monotonic() - _ids_fetched_at < _ID_TTL_SECONDS:
        return dict(_ids)
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    async with _refresh_lock:
        if not force and _ids and time.monotonic() - _ids_fetched_at < _ID_TTL_SECONDS:
            return dict(_ids)
        token = account_token(fresh=True)
        if not token:
            raise RuntimeError("No Claude account login found for claude.ai connectors")
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": LIST_BETA,
        }
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT) as client:
            resp = await client.get(LIST_URL, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"claude.ai connector list answered HTTP {resp.status_code}")
        found: dict[str, str] = {}
        for item in (resp.json().get("data") or []):
            if not isinstance(item, dict):
                continue
            label = str(item.get("display_name") or "").strip()
            sid = str(item.get("id") or "").strip()
            if label and sid and str(item.get("eligibility_reason") or "connected") == "connected":
                found.setdefault(label, sid)
        _ids.clear()
        _ids.update(found)
        _ids_fetched_at = time.monotonic()
        _save_disk_cache()
        return dict(_ids)


async def resolve(config: dict) -> dict:
    """Return ``config`` with a current proxy url, looking the id up if needed."""
    label = str(config.get(CONFIG_MARKER) or "")
    sid = known_id(label)
    if not sid or time.monotonic() - _ids_fetched_at >= _ID_TTL_SECONDS:
        try:
            await refresh_ids()
        except Exception as exc:
            if not sid:
                raise RuntimeError(f"claude.ai connector '{label}' could not be looked up: {exc}") from exc
            log.warning("claude.ai connector list unavailable; using cached id for %s: %s", label, exc)
        sid = known_id(label) or sid
    if not sid:
        raise RuntimeError(
            f"claude.ai connector '{label}' is not connected on Owner's account "
            "(connect it at claude.ai -> Settings -> Connectors)"
        )
    return {**config, "url": proxy_url(sid)}


def session_headers(existing: dict | None = None) -> dict:
    headers = dict(existing or {})
    headers[SESSION_HEADER] = str(uuid.uuid4())
    return headers


class ClaudeAccountAuth(httpx.Auth):
    """Attach the current Claude account login to every proxy request.

    Claude Code refreshes that login on disk as the boys' sessions run; this
    reads it per request (mtime-cached) and, on a 401, re-reads once in case
    the CLI rotated it a moment ago. It never refreshes the login itself, so
    it can never race the CLI over the refresh token.
    """

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = account_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code != 401:
            return
        fresh = account_token(fresh=True)
        if fresh and fresh != token:
            request.headers["Authorization"] = f"Bearer {fresh}"
            yield request

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = account_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request
