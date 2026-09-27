"""Helpers for proxying state APIs to remote cloud hosts."""

# ANAM GUIDE: CLOUD REQUEST HELPER
# What: Tiny helper for calling remote/cloud web APIs (like the hearth-hub worker) and getting JSON back — with a "safe" version that returns None instead of crashing when the internet hiccups.
# Called by: api/hub.py, api/rituals.py, services/house_snapshot.py, services/hub_tasks.py, services/identity_context.py, services/context_hooks.py, services/claude_api.py
# Edit here when: You need to change how Anam talks to remote hosts — timeouts, headers, or error handling for cloud calls.

from __future__ import annotations

import json
from urllib import error as url_error
from urllib import request as url_request


def join_url(base: str, path: str) -> str:
    base = (base or "").rstrip("/")
    path = "/" + path.lstrip("/")
    return f"{base}{path}"


def request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 10.0,
) -> dict:
    url = join_url(base, path)
    headers = {
        "Accept": "application/json",
        "User-Agent": "anam/1.0",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = url_request.Request(url, data=data, headers=headers, method=method.upper())
    with url_request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def safe_request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 10.0,
) -> dict | None:
    try:
        return request_json(base, path, method=method, payload=payload, timeout=timeout)
    except (url_error.URLError, url_error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
