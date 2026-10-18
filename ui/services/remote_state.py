"""Helpers for proxying state APIs to remote cloud hosts."""


from __future__ import annotations

import json
import logging
from urllib import error as url_error
from urllib import request as url_request

log = logging.getLogger(__name__)


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
    except (url_error.URLError, url_error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        log.info("Remote state %s %s unavailable: %s", method, path, exc)
        return None
