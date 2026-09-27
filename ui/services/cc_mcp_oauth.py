"""Borrow Claude Code's stored MCP sign-ins for hosted connectors.

When Owner signs a hosted MCP connector in through Claude Code (``/mcp`` ->
authenticate), the CLI keeps the OAuth tokens in ``~/.claude/.credentials.json``
under ``mcpOAuth``. Anam's own MCP bridge and tool gateway open their own HTTP
connections, so without this they knock on those servers as a stranger (401).

``ClaudeCodeOAuth`` is an ``httpx.Auth`` that reads the matching sign-in on
every request (cached by file mtime), attaches the bearer, refreshes it with the
stored refresh token when it is about to expire or the server answers 401, and
writes the refreshed token back into the same entry so the CLI and Anam keep
sharing one sign-in. Tokens are never logged.
"""


from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import httpx

log = logging.getLogger(__name__)

_REFRESH_SKEW_MS = 120_000  # refresh when under two minutes remain
_METADATA_TIMEOUT = 15.0
_TOKEN_TIMEOUT = 30.0

_write_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "data": {}}
_token_endpoints: dict[str, str] = {}


def credentials_path() -> Path:
    override = os.environ.get("ANAM_CC_CREDENTIALS_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".claude" / ".credentials.json"


def _norm_url(url: str) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _read_all(path: Path | None = None, *, use_cache: bool = True) -> dict:
    path = path or credentials_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _cache_lock:
        if use_cache and _cache["path"] == str(path) and _cache["mtime"] == mtime:
            return _cache["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    with _cache_lock:
        _cache.update(path=str(path), mtime=mtime, data=data)
    return data


def find_entry(server_name: str, url: str, path: Path | None = None) -> tuple[str, dict] | None:
    """Return ``(key, entry)`` for the stored sign-in matching this server, if any."""
    store = _read_all(path).get("mcpOAuth")
    if not isinstance(store, dict):
        return None
    want_url = _norm_url(url)
    for key, entry in store.items():
        if not isinstance(entry, dict) or not entry.get("accessToken"):
            continue
        name = entry.get("serverName") or str(key).split("|", 1)[0]
        if name == server_name and _norm_url(entry.get("serverUrl", "")) == want_url:
            return key, entry
    return None


def has_signin(server_name: str, url: str) -> bool:
    return find_entry(server_name, url) is not None


def _needs_refresh(entry: dict) -> bool:
    expires_at = entry.get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return False  # no expiry recorded: only a 401 will tell us
    return expires_at - time.time() * 1000 < _REFRESH_SKEW_MS


def _metadata_urls(entry: dict) -> list[str]:
    discovery = entry.get("discoveryState") if isinstance(entry.get("discoveryState"), dict) else {}
    base = (discovery.get("authorizationServerUrl") or entry.get("issuer") or "").rstrip("/")
    if not base:
        return []
    return [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]


def _write_back(key: str, updates: dict, path: Path | None = None) -> None:
    """Merge ``updates`` into one mcpOAuth entry, re-reading the file first so
    nothing the CLI wrote in the meantime is clobbered. Atomic replace."""
    path = path or credentials_path()
    with _write_lock:
        data = _read_all(path, use_cache=False)
        entry = (data.get("mcpOAuth") or {}).get(key)
        if not isinstance(entry, dict):
            return
        entry.update(updates)
        tmp = path.with_name(f"{path.name}.anam-{os.getpid()}.tmp")
        # Same shape the CLI writes: compact JSON, no trailing newline.
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
                time.sleep(0.1 * (attempt + 1))
        with _cache_lock:
            _cache.update(path=None, mtime=None, data={})


def _make_refresh_client() -> httpx.AsyncClient:
    """Separate client for metadata + token calls (seam for tests)."""
    return httpx.AsyncClient()


def _apply_token_response(key: str, entry: dict, body: dict) -> dict | None:
    access = body.get("access_token")
    if not access:
        return None
    updates: dict[str, Any] = {"accessToken": access}
    if body.get("refresh_token"):
        updates["refreshToken"] = body["refresh_token"]
    if isinstance(body.get("expires_in"), (int, float)):
        updates["expiresAt"] = int(time.time() * 1000 + body["expires_in"] * 1000)
    if body.get("scope"):
        updates["scope"] = body["scope"]
    _write_back(key, updates)
    return {**entry, **updates}


class ClaudeCodeOAuth(httpx.Auth):
    """Attach (and keep fresh) the Claude Code sign-in for one hosted MCP server."""

    def __init__(self, server_name: str, url: str):
        self.server_name = server_name
        self.url = url
        self._async_refresh_lock: Any = None

    # -- helpers -----------------------------------------------------------
    def _current(self) -> tuple[str, dict] | None:
        return find_entry(self.server_name, self.url)

    def _refresh_form(self, entry: dict) -> dict:
        form = {"grant_type": "refresh_token", "refresh_token": entry["refreshToken"]}
        if entry.get("clientId"):
            form["client_id"] = entry["clientId"]
        return form

    def _log_refresh_failure(self, detail: str) -> None:
        log.warning("Claude Code sign-in refresh failed for %s: %s", self.server_name, detail)

    # -- async (what fastmcp uses) -----------------------------------------
    async def _atoken_endpoint(self, entry: dict, client: httpx.AsyncClient) -> str | None:
        for meta_url in _metadata_urls(entry):
            if meta_url in _token_endpoints:
                return _token_endpoints[meta_url]
            try:
                resp = await client.get(meta_url, timeout=_METADATA_TIMEOUT)
                if resp.status_code == 200 and resp.json().get("token_endpoint"):
                    _token_endpoints[meta_url] = resp.json()["token_endpoint"]
                    return _token_endpoints[meta_url]
            except (httpx.HTTPError, ValueError):
                continue
        return None

    async def _arefresh(self, key: str, entry: dict) -> dict | None:
        import asyncio

        if self._async_refresh_lock is None:
            self._async_refresh_lock = asyncio.Lock()
        async with self._async_refresh_lock:
            # Someone (the CLI, or a sibling request) may have refreshed already.
            latest = self._current()
            if latest and latest[1].get("accessToken") != entry.get("accessToken") and not _needs_refresh(latest[1]):
                return latest[1]
            if not entry.get("refreshToken"):
                return None
            async with _make_refresh_client() as client:
                token_url = await self._atoken_endpoint(entry, client)
                if not token_url:
                    self._log_refresh_failure("no token endpoint in authorization-server metadata")
                    return None
                try:
                    resp = await client.post(token_url, data=self._refresh_form(entry), timeout=_TOKEN_TIMEOUT)
                except httpx.HTTPError as exc:
                    self._log_refresh_failure(type(exc).__name__)
                    return None
            if resp.status_code != 200:
                self._log_refresh_failure(f"HTTP {resp.status_code}")
                return None
            try:
                body = resp.json()
            except ValueError:
                self._log_refresh_failure("token response was not JSON")
                return None
            refreshed = await asyncio.to_thread(_apply_token_response, key, entry, body)
            if refreshed:
                log.info("Refreshed Claude Code sign-in for hosted MCP server %s", self.server_name)
            return refreshed

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        found = self._current()
        if found is None:
            yield request
            return
        key, entry = found
        if _needs_refresh(entry):
            entry = await self._arefresh(key, entry) or entry
        request.headers["Authorization"] = f"Bearer {entry['accessToken']}"
        response = yield request
        if response.status_code != 401:
            return
        refreshed = await self._arefresh(key, entry)
        if not refreshed or refreshed.get("accessToken") == entry.get("accessToken"):
            return
        request.headers["Authorization"] = f"Bearer {refreshed['accessToken']}"
        yield request

    # -- sync (kept honest: attaches the bearer, never refreshes) ------------
    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        found = self._current()
        if found is not None:
            request.headers["Authorization"] = f"Bearer {found[1]['accessToken']}"
        yield request


def auth_for(server_name: str, url: str, headers: dict | None) -> ClaudeCodeOAuth | None:
    """The auth to use for a hosted server, or None if static headers already
    authenticate it or Claude Code holds no sign-in for it."""
    if headers and any(str(k).lower() == "authorization" for k in headers):
        return None
    if not str(url or "").lower().startswith("https://"):
        return None
    found = find_entry(server_name, url)
    # Only real OAuth registrations (they carry a clientId). Older token-in-URL
    # connectors already authenticate themselves and are left exactly as they were.
    if found is None or not found[1].get("clientId"):
        return None
    return ClaudeCodeOAuth(server_name, url)
