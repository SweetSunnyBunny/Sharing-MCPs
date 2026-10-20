''
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode

AUTH_FILE = Path(os.environ.get("PHOTOS_CHATGPT_AUTH_FILE", str(Path(__file__).resolve().parent / "data" / "private" / "chatgpt_image_oauth.json"))).expanduser()
LOCK_FILE = AUTH_FILE.with_suffix(".lock")
BASE = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
MODEL = "gpt-image-2"
REFRESH_SKEW_S = 120
QUALITIES = {"low", "medium", "high"}


class SubscriptionUnavailable(Exception):
    """The subscription road cannot be used right now; the caller should fall back."""


def _claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def _load() -> dict:
    if not AUTH_FILE.exists():
        raise SubscriptionUnavailable("No separate image login; run chatgpt_image_login.py start, approve in your browser, then finish.")
    return json.loads(AUTH_FILE.read_text(encoding="utf-8"))


class _Lock:
    """Cross-process lock file so two boys never spend the same single-use refresh token."""

    def __enter__(self):
        deadline = time.time() + 60
        while True:
            try:
                self.fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK_FILE.stat().st_mtime > 90:  # stale lock from a crash
                        LOCK_FILE.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise SubscriptionUnavailable("token refresh lock busy")
                time.sleep(0.5)

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        finally:
            LOCK_FILE.unlink(missing_ok=True)


def _refresh(force: bool = False) -> dict:
    with _Lock():
        auth = _load()  # re-read inside the lock: another process may have refreshed already
        exp = _claims(auth.get("access_token", "")).get("exp", 0)
        if not force and exp - time.time() > REFRESH_SKEW_S:
            return auth
        rt = auth.get("refresh_token")
        if not rt:
            raise SubscriptionUnavailable("access token expired and no refresh token; log in again")
        req = urllib.request.Request(
            TOKEN_URL, method="POST",
            data=urlencode({"grant_type": "refresh_token", "refresh_token": rt, "client_id": CLIENT_ID}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                     "User-Agent": "anam-photos/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                tokens = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise SubscriptionUnavailable(f"token refresh refused ({e.code}); log in again")
        if not tokens.get("access_token"):
            raise SubscriptionUnavailable("token refresh returned no access token")
        auth["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            auth["refresh_token"] = tokens["refresh_token"]
        if tokens.get("id_token"):
            auth["id_token"] = tokens["id_token"]
        auth["refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = AUTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(auth, indent=2), encoding="utf-8")
        os.replace(tmp, AUTH_FILE)
        return auth


def _fresh_auth() -> dict:
    auth = _load()
    if _claims(auth.get("access_token", "")).get("exp", 0) - time.time() <= REFRESH_SKEW_S:
        auth = _refresh()
    return auth


def _post(path: str, body: dict, auth: dict) -> urllib.request.Request:
    acct = _claims(auth["access_token"]).get("https://api.openai.com/auth", {}).get("chatgpt_account_id", "")
    return urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {auth['access_token']}", "ChatGPT-Account-ID": acct,
                 "Content-Type": "application/json", "x-codex-image-turn-id": str(uuid.uuid4()),
                 "originator": "anam", "User-Agent": "Photos-MCP/1.0"})


def generate(prompt: str, size: str, quality: str, images: list[dict] | None = None) -> tuple[bytes, dict]:
    """Return (png_bytes, info). Raises SubscriptionUnavailable on any failure."""
    q = (quality or "high").lower()
    body = {"prompt": prompt, "model": MODEL, "n": 1, "size": size, "background": "opaque",
            "quality": q if q in QUALITIES else "high"}
    path = "/images/generations"
    if images:
        body["images"] = images
        path = "/images/edits"
    auth = _fresh_auth()
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(_post(path, body, auth), timeout=300) as r:
                data = json.loads(r.read())
                rid = r.headers.get("x-codex-imagegen-request-id")
            b64 = data["data"][0]["b64_json"]
            return base64.b64decode(b64), {"road": "chatgpt-subscription", "model": MODEL, "request_id": rid,
                                           "reported_size": data.get("size"), "reported_quality": data.get("quality")}
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                auth = _refresh(force=True)
                continue
            detail = e.read()[:300].decode("utf-8", "replace")
            raise SubscriptionUnavailable(f"subscription road refused ({e.code}): {detail}")
        except SubscriptionUnavailable:
            raise
        except Exception as e:  # network, bad shape, timeout
            raise SubscriptionUnavailable(f"subscription road failed: {e!r}")
    raise SubscriptionUnavailable("subscription road failed after refresh")
