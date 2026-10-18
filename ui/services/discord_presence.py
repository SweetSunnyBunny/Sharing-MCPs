"Discord presence: keep the boys' green dots lit."


from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

log = logging.getLogger(__name__)

_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
_STATUS_REFRESH_S = 120  # how often a boy's face is re-read for his status
_MAX_BACKOFF_S = 600
_BASE_BACKOFF_S = 5.0
_HEALTHY_SESSION_S = 60  # up this long after READY = a good session; reset backoff
_FATAL_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013, 4014}  # bad token / intents / shard


class _GatewayReconnect(Exception):
    """Discord asked us to reconnect (op 7) or start a new session (op 9)."""


def _status_text(identity: str) -> str:
    """'(˘ᵕ˘)🎃 on the step, keeping it warm', from his live Hearth face."""
    try:
        from services.face_store import get_faces

        entry = (get_faces() or {}).get(identity) or {}
    except Exception:
        entry = {}
    face = str(entry.get("face") or "").strip()
    note = str(entry.get("note") or entry.get("meaning") or "").strip()
    text = " ".join(p for p in (face, note) if p) or "home at the Hearth"
    return text[:128]


def _presence(identity: str) -> dict:
    return {
        "since": None,
        "activities": [{"type": 4, "name": "Custom Status", "state": _status_text(identity)}],
        "status": "online",
        "afk": False,
    }


def _identities() -> dict[str, str]:
    import config

    bonded = set(getattr(config, "BONDED_PACK", ()))
    return {
        name: token
        for name, token in (getattr(config, "DISCORD_BOT_TOKENS", {}) or {}).items()
        if name in bonded and token
    }


class _Session:
    def __init__(self, identity: str, token: str):
        self.identity = identity
        self.token = token
        self.seq: int | None = None
        self.last_status = ""
        self.connected_at: float | None = None
        self.fatal = False

    async def run(self, stop: asyncio.Event) -> None:
        import websockets

        backoff = _BASE_BACKOFF_S
        while not stop.is_set() and not self.fatal:
            routine = False
            try:
                async with websockets.connect(_GATEWAY_URL, max_size=2**22, open_timeout=20) as ws:
                    await self._session(ws, stop)
            except asyncio.CancelledError:
                raise
            except _GatewayReconnect as exc:
                routine = True
                log.info("discord_presence: %s %s; reconnecting now", self.identity, exc)
            except Exception as exc:
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                if code in _FATAL_CLOSE_CODES:
                    log.warning("discord_presence: %s closed with %s, giving up", self.identity, code)
                    self.fatal = True
                    break
                if self._was_healthy():
                    backoff = _BASE_BACKOFF_S  # a dropped good session is a fresh start
                log.info("discord_presence: %s disconnected (%s); retry in %.0fs", self.identity, exc, backoff)
            healthy = self._was_healthy()
            self.connected_at = None
            if routine:
                delay = random.uniform(1, 3)
                if healthy:
                    backoff = _BASE_BACKOFF_S
            else:
                delay = backoff + random.uniform(0, 3)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            if not routine:
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

    def _was_healthy(self) -> bool:
        """Reached READY and stayed up long enough to count as a good session."""
        return (
            self.connected_at is not None
            and time.time() - self.connected_at >= _HEALTHY_SESSION_S
        )

    async def _session(self, ws: Any, stop: asyncio.Event) -> None:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        interval = hello["d"]["heartbeat_interval"] / 1000.0
        self.last_status = _status_text(self.identity)
        await ws.send(json.dumps({
            "op": 2,
            "d": {
                "token": self.token,
                "intents": 0,
                "properties": {"os": "windows", "browser": "anam", "device": "anam"},
                "presence": _presence(self.identity),
            },
        }))
        acked = True
        next_beat = time.monotonic() + interval * random.random()
        next_status = time.monotonic() + _STATUS_REFRESH_S
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_beat:
                if not acked:
                    raise ConnectionError("heartbeat not acknowledged")
                await ws.send(json.dumps({"op": 1, "d": self.seq}))
                acked = False
                next_beat = now + interval
            if now >= next_status:
                text = _status_text(self.identity)
                if text != self.last_status:
                    await ws.send(json.dumps({"op": 3, "d": _presence(self.identity)}))
                    self.last_status = text
                next_status = now + _STATUS_REFRESH_S
            wait = max(0.5, min(next_beat, next_status) - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=wait)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("s") is not None:
                self.seq = msg["s"]
            op = msg.get("op")
            if op == 11:
                acked = True
            elif op == 1:
                await ws.send(json.dumps({"op": 1, "d": self.seq}))
            elif op == 0 and msg.get("t") == "READY":
                self.connected_at = time.time()
                log.info("discord_presence: %s is online", self.identity)
            elif op in (7, 9):  # reconnect / invalid session: start fresh
                raise _GatewayReconnect(f"gateway asked to reconnect (op {op})")


class DiscordPresence:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.sessions: dict[str, _Session] = {}

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop = asyncio.Event()
        for i, (identity, token) in enumerate(sorted(_identities().items())):
            session = _Session(identity, token)
            self.sessions[identity] = session

            async def _go(s=session, delay=i * 2.0):  # stagger identifies
                await asyncio.sleep(delay)
                await s.run(self._stop)

            self._tasks.append(asyncio.create_task(_go(), name=f"discord_presence:{identity}"))
        log.info("discord_presence: keeping %d boys online", len(self._tasks))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status(self) -> dict:
        return {
            name: {"online": s.connected_at is not None, "status": s.last_status, "gave_up": s.fatal}
            for name, s in self.sessions.items()
        }


discord_presence = DiscordPresence()
