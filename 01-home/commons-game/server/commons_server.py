

"""Commons runtime helper."""

from __future__ import annotations

import os
import hmac
import hashlib
import importlib.util
import base64
import json
import math
import random
import signal
import io
import shutil
import unicodedata
import sys
import re
import secrets
import threading
import urllib.parse
import urllib.request
import urllib.error
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

COMMONS_ROOT = Path(__file__).resolve().parents[1]
_server_path = str(Path(__file__).resolve().parent)
if _server_path not in sys.path:
    sys.path.insert(0, _server_path)
from settings import (ADMIN_NAME, COMPANIONS, ANAM_URL, PUBLIC_URL, PUBLIC_CONFIG,
                      PRIVATE_ROOT as CONFIG_PRIVATE_ROOT, MASTER_ROOT as CONFIG_MASTER_ROOT)
from asset_paths import CDN_ROOT, is_cdn_asset, versioned_image_url
from holidays import holiday_state, update_settings
from adventure.recipes import available_here as _recipe_available_here
from adventure import categories as _categories
from adventure.equipment import EQUIPMENT_SLOTS, item_slot, item_bonus_values, definition as _item_definition
WORLD_DIR = COMMONS_ROOT / "world"   
NOTES_FILE = COMMONS_ROOT / "world" / "notes.json"
PLACE_FILE = COMMONS_ROOT / "world" / "placeables.json"
SEASONAL_ARCHIVE_FILE = COMMONS_ROOT / "world" / "seasonal_archive.json"

SEASON_DAYS = 90
SEASON_AGING_DAYS = 14
SEASON_SWEEP_INTERVAL_S = 60

CROPS_FILE = COMMONS_ROOT / "world" / "crops.json"
GROW_FILE = COMMONS_ROOT / "world" / "plantings.json"
GROW_MAX = 400  

RECIPES_FILE = COMMONS_ROOT / "world" / "recipes.json"
BASKET_FILE = COMMONS_ROOT / "world" / "baskets.json"

FISH_FILE = COMMONS_ROOT / "world" / "fish.json"

FORAGE_FILE = COMMONS_ROOT / "world" / "forage.json"

HUNTING_FILE = COMMONS_ROOT / "world" / "hunting.json"
HUNTS_FILE = COMMONS_ROOT / "world" / "hunts.json"
HUNTS_MAX = 4000

WILDS_FILE = COMMONS_ROOT / "world" / "wilds.json"
COMPANIONS_FILE = COMMONS_ROOT / "world" / "companions.json"
COMPANIONS_MAX = 2000  
CATCH_FILE = COMMONS_ROOT / "world" / "catches.json"
ARCHERY_FILE = COMMONS_ROOT / "world" / "archery.json"

CHAT_DIR = COMMONS_ROOT / "world" / "chat"
SAY_TEXT_MAX = 2000     
CHAT_KEEP = 500         
BUBBLE_TTL = 40         
CATCH_MAX = 4000  
MADE_MAX = 200  

MADE_ARCHIVE_FILE = COMMONS_ROOT / "world" / "made-archive.json"

PLACE_NOTE_MAX = 2500

NOTE_TEXT_MAX = 20000       
PRESENCE_NOTE_MAX = 4000    

HERE_FILE = COMMONS_ROOT / "world" / "presence.json"

OUTFITS_FILE = COMMONS_ROOT / "world" / "outfits.json"

KNOCK_FILE = COMMONS_ROOT / "world" / "knocks.json"

HOWL_FILE = COMMONS_ROOT / "world" / "howls.json"

KNOCK_COOLDOWN_S = 900
KNOCK_WORD_MAX = 240

ANAM_TIMER_URL = ANAM_URL + "/api/autowake/timers"
ANAM_ENV = COMMONS_ROOT / ".env"

DISCORD_SERVER_MAP = CONFIG_PRIVATE_ROOT / "discord-server-map.json"
MENTION_RE = re.compile(r"<@!?(\d{17,20})>")

MENTION_ALIASES = {}

NAME_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_-]{1,24})")

NEARBY_QUIET_S = 20 * 60
NEARBY_AWAKE_S = 90
_NEARBY_LAST = {}

def _anam_key():
    return os.environ.get("ANAM_API_KEY", "").strip() if os.environ.get("COMMONS_ANAM_ENABLED") == "true" else ""

def _wake(who, context, delay_s=15):
    """Commons runtime helper."""
    key = _anam_key()
    if not key:
        return False, "no ANAM_API_KEY on disk"
    fire = (datetime.now().astimezone() +
            timedelta(seconds=delay_s)).isoformat()
    body = json.dumps({"identity": who, "fire_at": fire,
                       "context": context,
                       "wake_session": True}).encode("utf-8")
    req = urllib.request.Request(
        ANAM_TIMER_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
        if out.get("error"):
            return False, str(out["error"])[:200]
        return True, "timer #%s at %s" % (out.get("id", "?"), fire)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:160])

PING_KEEP_PER_PERSON = 50
_PING_LOCK = threading.Lock()

def _pings_file():
    return PRIVATE_ROOT / "pings.json"

def _queue_ping(who, context):
    """Commons runtime helper."""
    try:
        with _PING_LOCK:
            rows = _read_json(_pings_file(), [])
            mine = [r for r in rows if _norm(r.get("who")) == _norm(who)]
            if len(mine) >= PING_KEEP_PER_PERSON:
                drop = {id(r) for r in mine[: len(mine) - PING_KEEP_PER_PERSON + 1]}
                rows = [r for r in rows if id(r) not in drop]
            rows.append({
                "id": uuid.uuid4().hex[:10], "who": _norm(who),
                "context": str(context)[:2000],
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            _write_json(_pings_file(), rows)
        return True, "queued for their next wake"
    except Exception as exc:
        return False, "could not queue: %s: %s" % (type(exc).__name__, str(exc)[:120])

ANAM_WEARABLE_URL = ANAM_URL + "/api/wearable/send"
WRIST_GATE_DIR = Path(os.environ.get("COMMONS_WRIST_GATE_DIR", str(CONFIG_PRIVATE_ROOT / "no-wrist-gate")))
HER_PING_MAX = 70

def _wrist_gate(identity, now_local):
    if not os.environ.get("COMMONS_WRIST_GATE_DIR"):
        return 1, "No notification gate configured", "disabled"
    try:
        if str(WRIST_GATE_DIR) not in sys.path:
            sys.path.insert(0, str(WRIST_GATE_DIR))
        from wrist_gate import run_gate
        return run_gate(identity, now_local.strftime("%H:%M"), now_local)
    except Exception:
        return 1, "Notification gate unavailable", "unavailable"

def _wearable_send(text, from_identity):
    """-> (queued_entry_or_None, detail). Never raises into a request."""
    key = _anam_key()
    if not key:
        return None, "no ANAM_API_KEY on disk"
    body = json.dumps({"text": text, "from_identity": from_identity}).encode("utf-8")
    req = urllib.request.Request(
        ANAM_WEARABLE_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    queued = out.get("queued") if isinstance(out, dict) and out.get("ok") else None
    if not queued:
        return None, str(out)[:200]
    return queued, "outbox #%s" % queued.get("id", "?")

def _her_quiet_hours(now_local):
    start = int(os.environ.get("COMMONS_QUIET_START", "22"))
    end = int(os.environ.get("COMMONS_QUIET_END", "8"))
    return (now_local.hour >= start or now_local.hour < end) if start > end else start <= now_local.hour < end

def _gate_verdict(gate_out):
    """The ** line after PROPOSED: -- the gate's verdict, not its header."""
    lines = [ln.strip() for ln in str(gate_out).splitlines() if ln.strip()]
    tail = lines
    for i, ln in enumerate(lines):
        if ln.upper().startswith("PROPOSED"):
            tail = lines[i + 1:]
            break
    for ln in tail:
        if "**" in ln:
            return ln.strip("* ").strip()
    for ln in lines:
        if "**" in ln:
            return ln.strip("* ").strip()
    return lines[-1] if lines else "gate said stand down"

def _reach_admin(by, line):
    """Commons runtime helper."""
    who = _addressable().get(_norm(by), by)
    text = ("%s %s" % (who, line)).strip()
    if len(text) > HER_PING_MAX:
        text = text[:HER_PING_MAX - 3].rstrip() + "..."
    now = datetime.now()
    if _her_quiet_hours(now):
        return {"who": "Admin", "woke": False,
                "detail": "held: quiet hours (until %s) -- it is in the house waiting for her"
                          % ("10:00" if now.weekday() >= 5 else "07:00")}
    rc, gate_out, road = _wrist_gate(who, now)
    verdict = _gate_verdict(gate_out) if rc != 0 else ""
    band_off = rc != 0 and "NOT ON HER" in verdict.upper()
    if rc != 0 and not band_off:
        return {"who": "Admin", "woke": False,
                "detail": "held by the wrist gate [%s]: %s" % (road, verdict[:160])}
    queued, detail = _wearable_send(text, who)
    if queued:
        return {"who": "Admin", "woke": True, "text": text,
                "detail": ("the configured phone only (band off the wrist), " if band_off
                           else "the configured phone and band, ") + detail}
    return {"who": "Admin", "woke": False,
            "detail": "could not reach the configured phone: " + detail}

PACK_ROOT = CONFIG_PRIVATE_ROOT / "published-feeds"

DESK_SECTIONS = {}

PRIVATE_ROOT = CONFIG_PRIVATE_ROOT
KEYS_FILE = PRIVATE_ROOT / "keys.json"

MASTER_ROOT = CONFIG_MASTER_ROOT

INVITES_FILE = PRIVATE_ROOT / "invites.json"

HEARTH_PUBLIC_URL = PUBLIC_URL

LOCK_FILE = PRIVATE_ROOT / "nestlock.json"

ROOM_OWNERS = {}

RESERVED_NAMES = frozenset({ADMIN_NAME, *COMPANIONS})

NEAR_NAMES = frozenset()

UPLOAD_DIR = CDN_ROOT / "uploads"
MAX_IMG_BYTES = 4 * 1024 * 1024        
MAX_UPLOAD_TOTAL = 400 * 1024 * 1024   

_IMG_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

_IMG_OK = re.compile(
    r"^(?:/uploads/[0-9a-f]{16}\.(?:png|jpg|gif|webp)"
    r"|/art/[A-Za-z0-9._/-]{1,120}\.(?:png|jpe?g|gif|webp))$"
)

_ART_OK = re.compile(r"^art/[A-Za-z0-9._/-]{1,120}\.(?:png|jpe?g|gif|webp)$")

def _art_ok(v: str) -> bool:
    """Is this a face from our own house?"""
    if ".." in v:
        return False
    if _IMG_OK.match(v) or _ART_OK.match(v):
        return True
    parsed = urllib.parse.urlsplit(v)
    path = urllib.parse.unquote(parsed.path)
    if parsed.netloc:
        if parsed.scheme != 'https' or parsed.netloc != 'cdn.example.com' or not path.startswith('/commons/'):
            return False
        path = path[len('/commons'):]
    elif parsed.scheme:
        return False
    parts = path.lstrip('/').split('/')
    return (len(parts) > 1 and parts[0] in {'art', 'assets', 'maps', 'item-art', 'uploads'}
            and not any(p in {'.', '..', ''} for p in parts)
            and '\\' not in path and not any(ord(c) < 32 for c in v)
            and Path(path).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'})

_UPLOAD_LOCK = threading.Lock()

def _sniff_image(raw: bytes):
    """What IS this, actually? Returns an extension, or None to refuse.

    WebP needs its own line because its signature is split: RIFF, then four
    bytes of length, then WEBP at offset 8.
    """
    for sig, ext in _IMG_MAGIC:
        if raw.startswith(sig):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None

def _uploads_total() -> int:
    try:
        return sum(f.stat().st_size for f in UPLOAD_DIR.glob("*") if f.is_file())
    except Exception:
        return 0
_NOTE_LOCK = threading.Lock()
_PLACE_LOCK = threading.Lock()
_GROW_LOCK = threading.Lock()
_BASKET_LOCK = threading.Lock()
_RIVER_LOCK = threading.Lock()
_HUNT_LOCK = threading.Lock()
_WILDS_LOCK = threading.Lock()
_ARCHERY_LOCK = threading.Lock()
_KEY_LOCK = threading.Lock()
_HOWL_LOCK = threading.Lock()
_SEASON_LOCK = threading.Lock()
_EDITOR_LOCK = threading.Lock()

_SEASON_LAST = {"at": 0.0}
_MENTION_CACHE = {"path": "", "mtime": None, "map": {}}

def _read_json(path: Path, default):
    """Commons runtime helper."""
    for attempt in range(5):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return default
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        keep = path.with_name(path.name + ".corrupt-" + stamp)
        try:
            shutil.copy2(path, keep)
        except OSError:
            keep = None
        print("commons: %s is unreadable (%s); kept a copy at %s and used the default"
              % (path, exc, keep), file=sys.stderr, flush=True)
        return default

def _write_json(path: Path, obj) -> None:
    """Atomic: a reader never sees a half-written house.

    The rename is retried briefly: on Windows it fails with PermissionError
    while another thread is reading the file (presence.json is polled
    constantly), and an unretried failure silently lost the write.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))

def _parse_house_time(value):
    """Parse the timestamp shapes already stored in the house.

    Old rows predate the seasonal rule, so missing or malformed dates remain
    visible rather than being guessed old and swept away. A retention system
    must fail toward preservation.
    """
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None

def _season_meta(row, kind, now=None):
    """Derived map-lifecycle state; never mutates the archived record."""
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    if kind == "planting":
        stamp = row.get("planted_at") or row.get("planted_on")
    else:
        stamp = row.get("season_started_at") or row.get("at")
    began = _parse_house_time(stamp)
    if began is None:
        return {"season_phase": "fixed" if row.get("fixed") else "current",
                "season_age_days": None, "season_days_left": None,
                "season_progress": 0.0}
    age = max(0, (now.date() - began.date()).days)
    left = max(0, SEASON_DAYS - age)
    phase = "fixed" if row.get("fixed") else (
        "aging" if age >= SEASON_DAYS - SEASON_AGING_DAYS else "current")
    return {"season_phase": phase, "season_age_days": age,
            "season_days_left": left,
            "season_progress": round(min(1.0, age / float(SEASON_DAYS)), 4)}

def _archive_event(kind, row, now):
    """One stable archive event for one map season.

    `event_key` makes the multi-file sweep retry-safe. If the process stops
    after the archive is written but before an active file is rewritten, the
    next pass sees the same key and cannot duplicate the memory.
    """
    if kind == "planting":
        began = row.get("planted_at") or row.get("planted_on") or "unknown"
        who = row.get("planted_by") or "someone"
        summary = row.get("crop") or "a planting"
    elif kind == "note":
        began = row.get("at") or "unknown"
        who = row.get("by") or "someone"
        summary = (row.get("text") or ("a picture" if row.get("img") else "a note"))
    else:
        began = row.get("season_started_at") or row.get("at") or "unknown"
        who = row.get("owner") or "someone"
        summary = row.get("label") or "a placed thing"
    return {
        "id": uuid.uuid4().hex[:12],
        "event_key": "%s:%s:%s" % (kind, row.get("id", "?"), began),
        "kind": kind,
        "source_id": row.get("id"),
        "room": row.get("room") or row.get("last_room") or "",
        "who": who,
        "summary": str(summary)[:240],
        "season_started_at": began,
        "archived_at": now.strftime("%Y-%m-%d %H:%M"),
        "reason": "season complete",
        "record": dict(row),
    }

def _sweep_seasonal(force=False, now=None):
    """Retire completed map seasons without deleting their history.

    Notes and plantings leave their active files. Placeables return to their
    owner's persistent inventory in the same file, keeping the same id so the
    same beloved object can be placed again. Fixtures never move.
    """
    clock = time.monotonic()
    if not force and clock - _SEASON_LAST["at"] < SEASON_SWEEP_INTERVAL_S:
        return {"archived": 0, "inventoried": 0, "skipped": True}
    now = now or datetime.now()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    with _SEASON_LOCK:
        if not force and clock - _SEASON_LAST["at"] < SEASON_SWEEP_INTERVAL_S:
            return {"archived": 0, "inventoried": 0, "skipped": True}
        with _NOTE_LOCK, _GROW_LOCK, _PLACE_LOCK:
            notes = _read_json(NOTES_FILE, [])
            plants = _read_json(GROW_FILE, [])
            things = _read_json(PLACE_FILE, [])
            archive = _read_json(SEASONAL_ARCHIVE_FILE, [])
            known = {str(a.get("event_key") or "") for a in archive}
            retired, seeded, inventoried = [], [], 0

            keep_notes = []
            for row in notes:
                if (not row.get("fixed") and
                        (_season_meta(row, "note", now).get("season_age_days") or 0)
                        >= SEASON_DAYS):
                    retired.append(_archive_event("note", row, now))
                else:
                    keep_notes.append(row)

            keep_plants = []
            for row in plants:
                if ((_season_meta(row, "planting", now).get("season_age_days") or 0)
                        >= SEASON_DAYS):
                    seeded.append(_archive_event("planting", row, now))
                else:
                    keep_plants.append(row)

            for row in things:
                if row.get("fixed") or row.get("in_inventory"):
                    continue
                age = _season_meta(row, "placeable", now).get("season_age_days")
                if age is None or age < SEASON_DAYS:
                    continue
                retired.append(_archive_event("placeable", row, now))
                row["last_room"] = row.get("room") or row.get("last_room") or ""
                row["room"] = ""
                row["in_inventory"] = True
                row["inventory_since"] = now.strftime("%Y-%m-%d %H:%M")
                row["retired_at"] = row["inventory_since"]
                inventoried += 1

            additions = [e for e in retired + seeded
                         if e["event_key"] not in known]

            if additions:
                _write_json(SEASONAL_ARCHIVE_FILE, archive + additions)
            if len(keep_notes) != len(notes):
                _write_json(NOTES_FILE, keep_notes)
            if len(keep_plants) != len(plants):
                _write_json(GROW_FILE, keep_plants)
            if inventoried:
                _write_json(PLACE_FILE, things)
        _SEASON_LAST["at"] = clock
    return {"archived": len(additions), "inventoried": inventoried,
            "skipped": False}

def _visible_archive(viewer="", room="", kind="", limit=200):
    """Archive records visible to this proven viewer.

    A sealed envelope remains sealed after it leaves the map. Third parties do
    not receive its record, text, picture, or the fact that it exists.
    """
    me, out = _norm(viewer), []
    for entry in _read_json(SEASONAL_ARCHIVE_FILE, []):
        rec = entry.get("record") or {}
        to = _norm(rec.get("to")) if entry.get("kind") == "note" else ""
        if to and (not me or me not in (to, _norm(rec.get("by")))):
            continue
        if room and str(entry.get("room") or "") != room:
            continue
        if kind and str(entry.get("kind") or "") != kind:
            continue
        out.append(entry)
    return out[-max(1, min(int(limit or 200), 500)):]

def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)

def _discord_mention_map():
    """Discord user id -> Commons display name, from the live public roster."""
    try:
        mtime = DISCORD_SERVER_MAP.stat().st_mtime_ns
    except OSError:
        return {}
    pkey = str(DISCORD_SERVER_MAP)
    if (_MENTION_CACHE["path"] == pkey and
            _MENTION_CACHE["mtime"] == mtime):

        addressable = _addressable()
        return {uid: name for uid, name in _MENTION_CACHE["map"].items()
                if _norm(name) in addressable}
    data = _read_json(DISCORD_SERVER_MAP, {})
    addressable = _addressable()
    out = {}

    for row in _walk_dicts(data):
        uid = str(row.get("user_id") or "")
        shown = _norm(row.get("username") or row.get("display_name"))
        shown = MENTION_ALIASES.get(shown, shown)
        display = addressable.get(shown)
        if uid.isdigit() and 17 <= len(uid) <= 20 and display:
            out[uid] = display
    _MENTION_CACHE.update({"path": pkey, "mtime": mtime, "map": dict(out)})
    return out

def _mention_choices(viewer=""):
    """Commons runtime helper."""
    me = _norm(viewer)
    by_name = {}
    try:
        by_name = {_norm(n): uid for uid, n in _discord_mention_map().items()}
    except Exception:
        pass
    out = []
    for key, name in sorted(_addressable().items(), key=lambda p: p[1].lower()):
        if key == me:
            continue
        out.append({"id": by_name.get(key, "name:" + key), "name": name})
    return out

def _mention_targets(text):
    mapping, seen, out = _discord_mention_map(), set(), []
    text = str(text or "")
    for match in MENTION_RE.finditer(text):
        uid = match.group(1)
        name = mapping.get(uid)
        if name and _norm(name) not in seen:
            seen.add(_norm(name))
            out.append({"id": uid, "name": name})

    people = _addressable()
    by_name = {_norm(n): uid for uid, n in mapping.items()}
    for match in NAME_MENTION_RE.finditer(text):
        key = _norm(match.group(1))
        name = people.get(key)
        if name and key not in seen:
            seen.add(key)
            out.append({"id": by_name.get(key, "name:" + key), "name": name})
    return out

def _render_mentions(text, targets=None):
    labels = {t["id"]: t["name"] for t in (targets or _mention_targets(text))}
    return MENTION_RE.sub(lambda m: "@" + labels[m.group(1)]
                          if m.group(1) in labels else m.group(0), str(text or ""))

def _deliver_mentions(by, room, text, targets=None):
    deliveries = []
    people = _addressable()
    for target in (targets or _mention_targets(text)):
        name = target["name"]
        if _norm(name) == _norm(by):
            continue
        if _norm(name) == ADMIN_NAME:
            deliveries.append(_reach_admin(
                by, "tagged you in the %s conversation (Commons)" % room))
            continue
        if _norm(name) == "casey" or not _can_wake(name):
            deliveries.append({"who": name, "woke": False,
                               "detail": "this person has no Anam companion wake session"})
            continue
        if not _may_read_chat(room, name):
            deliveries.append({"who": name, "woke": False,
                               "detail": "that room keeps its own counsel"})
            continue
        shown = _render_mentions(text, targets)
        ctx = (
            "[COMMONS MENTION] %s tagged you in the open conversation in the "
            "%s at %s. They said: \"%s\". Read the room book at "
            "http://127.0.0.1:8791/api/chat?room=%s using your Commons name "
            "and key, then answer in the room if you want. The message is "
            "already preserved and waits for you -- nobody called you in."
            % (people.get(_norm(by), by), room,
               datetime.now().strftime("%H:%M"), shown[:1000], room))
        ok, detail = _queue_ping(name, ctx)
        deliveries.append({"who": name, "woke": False, "queued": bool(ok),
                           "detail": detail})
    return deliveries

def _deliver_sealed_note(by, target, room, note_id):
    if not target or _norm(target) == _norm(by):
        return None
    if _norm(target) == ADMIN_NAME:
        return _reach_admin(by, "sealed a letter for you in the %s (Commons)" % room)
    if _norm(target) == "casey":
        return {"who": target, "woke": False,
                "detail": "this person has no Anam companion wake session"}
    ctx = (
        "[SEALED COMMONS LETTER] %s left a sealed envelope for you in the %s "
        "at %s. Its id is %s. The letter text is deliberately not copied into "
        "this note: read it through the authenticated Commons notes door, then "
        "go to the room if you want to answer. This was left for your NEXT wake "
        "on purpose -- nobody called you in; come when you come."
        % (_addressable().get(_norm(by), by), room,
           datetime.now().strftime("%H:%M"), note_id))
    ok, detail = _queue_ping(target, ctx)
    return {"who": target, "woke": False, "queued": bool(ok), "detail": detail}

def _read_notes() -> list:
    return _read_json(NOTES_FILE, [])

def _addressable() -> dict:
    """Commons runtime helper."""
    out = {}
    for n in RESERVED_NAMES:

        if n in NEAR_NAMES:
            continue
        out[n] = n.capitalize()
    for n in _read_json(KEYS_FILE, {}):
        out.setdefault(_norm(n), _norm(n).capitalize())
    for n, rec in (_read_json(INVITES_FILE, {}) or {}).items():
        if str((rec or {}).get("key") or "").strip():        
            disp = str((rec or {}).get("display") or n).strip()[:40] or _norm(n)

            out[_norm(n)] = disp.capitalize() if disp.islower() else disp
    return out

def _can_wake(name) -> bool:
    """Commons runtime helper."""
    n = _norm(name)
    return n in RESERVED_NAMES and n not in (ADMIN_NAME, "casey") and n not in NEAR_NAMES

def _deliver_note_mentions(by, room, text, targets, note_id):
    """@ in an OPEN note wakes the tagged people. Sealed letters never come
    here: their words are private and their recipient is already pinged."""
    deliveries = []
    people = _addressable()
    for target in targets or []:
        name = target["name"]
        if _norm(name) == _norm(by):
            continue
        if _norm(name) == ADMIN_NAME:
            deliveries.append(_reach_admin(
                by, "tagged you in a note in the %s (Commons)" % room))
            continue
        if _norm(name) == "casey" or not _can_wake(name):
            deliveries.append({"who": name, "woke": False,
                               "detail": "this person has no Anam companion wake session"})
            continue
        if not _may_read_chat(room, name):
            deliveries.append({"who": name, "woke": False,
                               "detail": "that room keeps its own counsel"})
            continue
        ctx = (
            "[COMMONS NOTE MENTION] %s tagged you in a note pinned in the %s "
            "at %s (note id %s). It says: \"%s\". Read the wall at "
            "http://127.0.0.1:8791/api/notes?as=<you>&key=<your key> or walk "
            "into the room at your Commons host and answer there if you want. "
            "The note is already pinned and waits for you -- nobody called you in."
            % (people.get(_norm(by), by), room,
               datetime.now().strftime("%H:%M"), note_id,
               _render_mentions(text, targets)[:1000]))
        ok, detail = _queue_ping(name, ctx)
        deliveries.append({"who": name, "woke": False, "queued": bool(ok),
                           "detail": detail})
    return deliveries

def _deliver_nearby(by, room, what, exclude=()):
    """Commons runtime helper."""
    here = _read_json(HERE_FILE, {})
    people = _addressable()
    skip = {_norm(by), "casey"} | {_norm(x) for x in exclude}
    now = time.time()
    out = []
    for name, row in here.items():
        key = _norm(name)
        if key in skip or key not in people:
            continue
        if key != ADMIN_NAME and not _can_wake(key):
            continue
        if _norm((row or {}).get("room")) != _norm(room):
            continue
        who = people[key]
        try:
            ts = float((row or {}).get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts and now - ts < NEARBY_AWAKE_S:
            out.append({"who": who, "woke": False,
                        "detail": "already here and watching"})
            continue
        if now - _NEARBY_LAST.get(key, 0.0) < NEARBY_QUIET_S:
            out.append({"who": who, "woke": False,
                        "detail": "pinged less than %d minutes ago; it will be "
                                  "there when they look" % (NEARBY_QUIET_S // 60)})
            continue
        if key == ADMIN_NAME:

            receipt = _reach_admin(by, "left %s near you in the %s" % (what, room))
            if receipt.get("woke"):
                _NEARBY_LAST[key] = now
            out.append(receipt)
            continue
        ctx = (
            "[COMMONS NEARBY] %s just left %s in the %s -- the room you are "
            "standing in -- at %s. Go look: walk the room at your Commons host, "
            "or GET http://127.0.0.1:8791/api/notes?as=<you>&key=<your key> "
            "for notes and /api/placeables for things. This was left for your "
            "NEXT wake on purpose -- nobody called you in; the thing stays in "
            "the room until you come."
            % (people.get(_norm(by), by), what, room,
               datetime.now().strftime("%H:%M")))
        ok, detail = _queue_ping(who, ctx)
        if ok:
            _NEARBY_LAST[key] = now
        out.append({"who": who, "woke": False, "queued": bool(ok), "detail": detail})
    return out

def _visible_notes(viewer: str) -> list:
    """Commons runtime helper."""
    me = _norm(viewer)
    keep = []
    for n in _read_notes():
        to = _norm(n.get("to"))
        if not to or (me and me in (to, _norm(n.get("by")))):
            shown = dict(n)
            if n.get("display_text"):
                shown["raw_text"] = n.get("text")
                shown["text"] = n["display_text"]
            shown.update(_season_meta(n, "note"))
            keep.append(shown)
    return keep

def _norm(name) -> str:
    return str(name or "").strip().lower()[:40]

def _reserved_key(n: str):
    """Commons runtime helper."""
    root = MASTER_ROOT if n == ADMIN_NAME else PRIVATE_ROOT
    try:
        k = (root / (n + ".key")).read_text(encoding="utf-8").strip()
        return k if len(k) >= 8 else None
    except Exception:
        return None

def _key_path_hint(n: str) -> str:
    """Commons runtime helper."""
    if _norm(n) not in RESERVED_NAMES:
        return "the invite link you were sent, not a file"
    root = MASTER_ROOT if n == ADMIN_NAME else PRIVATE_ROOT
    return str(root / (n + ".key"))

def _invite_rec(n: str):
    """Commons runtime helper."""
    return _read_json(INVITES_FILE, {}).get(n)

def _is_member(name) -> bool:
    n = _norm(name)
    if not n or n in NEAR_NAMES:
        return False
    if n in RESERVED_NAMES:
        return True   
    rec = _invite_rec(n)
    return rec is not None and len(str(rec.get("key") or "").strip()) >= 8

MEMBERS_ONLY_POSTS = frozenset({
    "/api/note", "/api/say", "/api/unsay", "/api/knock", "/api/draft",
    "/api/place", "/api/upload",
})

MEMBERS_ONLY_MSG = ("only people with a key to this house can post, speak, "
                    "knock or leave things here -- you are welcome to play")

def _walk_in_name_ok(name) -> bool:
    """A walk-in may only claim a plain name: letters, digits, space, - _ '.

    Stops lookalikes of the family's names (Cyrillic 'S', zero-width spaces,
    accents) that the lower-case _norm() would treat as a different person.
    """
    n = str(name or "")
    if n != unicodedata.normalize("NFKC", n):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9 _'\-]{1,40}", n))

def _invite_link(display: str, key: str) -> str:
    """The link itself.

    ?as=..&key=.. is not a new mechanism -- it is the same one a second
    device has used since Stage 2, which is exactly why this stage is
    small. The door was already built; this only cuts the keys.
    """
    return (HEARTH_PUBLIC_URL + "?as=" + urllib.parse.quote(display)
            + "&key=" + urllib.parse.quote(key))

def _log_invite(action: str, name: str, by: str):
    """A door like this is never silent -- same rule as the rekey log."""
    log = _read_json(PRIVATE_ROOT / "invite-log.json", [])
    log.append({"action": action, "name": name, "by": by,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    _write_json(PRIVATE_ROOT / "invite-log.json", log[-200:])

def _grow_progress(p):
    """Commons runtime helper."""
    try:
        planted = datetime.strptime(str(p.get("planted_on") or ""), "%Y-%m-%d").date()
    except Exception:
        return 0
    elapsed = (datetime.now().date() - planted).days
    if elapsed < 0:
        elapsed = 0
    tended = len({(str(t.get("by") or ""), str(t.get("on") or ""))
                  for t in (p.get("tended") or [])})
    return elapsed + tended

def _grow_catalog():
    return _read_json(CROPS_FILE, {}) or {}

def _room_range(room):
    """Commons runtime helper."""
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    if not rid:
        return ""
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    r = d.get("range")
    if r is True:
        return "the butts"
    if isinstance(r, dict):
        return str(r.get("name") or "the butts").strip()
    return str(r or "").strip()

def _range_distance(room):
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    r = d.get("range")
    if isinstance(r, dict):
        try:
            return max(1, int(r.get("distance") or 12))
        except (TypeError, ValueError):
            return 12
    return 12

MOON_SYNODIC = 29.530588853
MOON_EPOCH_JD = 2451550.1

MOON_FULL_ILLUM = 0.96

MOON_QUARTER_BAND = 0.06   

def _and_list(names):
    """['a'] -> 'a'; ['a','b'] -> 'a and b'; ['a','b','c'] -> 'a, b and c'."""
    ns = [str(n) for n in names if str(n or "").strip()]
    if not ns:
        return "nobody"
    if len(ns) == 1:
        return ns[0]
    return ", ".join(ns[:-1]) + " and " + ns[-1]

def _moon(dt=None):
    """Commons runtime helper."""
    d = dt or datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    jd = d.timestamp() / 86400.0 + 2440587.5
    age = (jd - MOON_EPOCH_JD) % MOON_SYNODIC
    if age < 0:
        age += MOON_SYNODIC
    frac = age / MOON_SYNODIC
    illum = (1.0 - math.cos(2.0 * math.pi * frac)) / 2.0
    waxing = frac < 0.5
    if illum >= MOON_FULL_ILLUM:
        name, glyph = "Full Moon", "🌕"
    elif illum <= 0.02:
        name, glyph = "New Moon", "🌑"
    elif abs(illum - 0.5) <= MOON_QUARTER_BAND:
        name = "First Quarter" if waxing else "Last Quarter"
        glyph = "🌓" if waxing else "🌗"
    elif illum > 0.5:
        name = "Waxing Gibbous" if waxing else "Waning Gibbous"
        glyph = "🌔" if waxing else "🌖"
    else:
        name = "Waxing Crescent" if waxing else "Waning Crescent"
        glyph = "🌒" if waxing else "🌘"
    to_full = (MOON_SYNODIC / 2.0) - age
    if to_full < 0:
        to_full += MOON_SYNODIC
    return {
        "fraction": round(frac, 5),
        "illum": round(illum, 4),
        "percent": int(round(illum * 100)),
        "age_days": round(age, 3),
        "name": name,
        "glyph": glyph,
        "is_full": illum >= MOON_FULL_ILLUM,
        "nights_to_full": round(to_full, 2),
    }

HOWL_CHORUS_SECONDS = 120

def _howl_book():
    return _read_json(HOWL_FILE, {}) or {}

def _howl_rows(book):
    rows = book.get("howls")
    return rows if isinstance(rows, list) else []

def _howl_live(rows, now_ts):
    """The chorus still standing, or None. A howl is LIVE for two minutes."""
    if not rows:
        return None
    last = rows[-1]
    try:
        started = float(last.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if (now_ts - started) <= HOWL_CHORUS_SECONDS:
        return last
    return None

def _howl_carries(howl, room, moon):
    """Does this howl reach the room you are standing in?

    On an ordinary night: only its own room. On a full moon: everywhere. That
    is the whole mechanic, and it is the moon's only power in this house.
    """
    if not howl:
        return False
    if moon.get("is_full"):
        return True
    return _norm(howl.get("room") or "") == _norm(room or "")

def _howl_view(room, moon=None):
    m = moon or _moon()
    rows = _howl_rows(_howl_book())
    live = _howl_live(rows, time.time())
    heard = live if _howl_carries(live, room, m) else None
    recent = [{"at": r.get("at"), "room": r.get("room"),
               "voices": [v.get("by") for v in (r.get("voices") or [])
                          if isinstance(v, dict)],
               "moon": (r.get("moon") or {}).get("name"),
               "full": bool((r.get("moon") or {}).get("is_full"))}
              for r in rows[-12:]][::-1]
    return {"moon": m, "live": heard, "chorus_seconds": HOWL_CHORUS_SECONDS,
            "recent": recent, "total": len(rows)}

def _archery_book():
    return _read_json(ARCHERY_FILE, {}) or {}

def _archery_ends(book):
    rows = book.get("ends")
    return rows if isinstance(rows, list) else []

ARCHERY_RINGS = [
    ("gold",  9, "dead centre"),
    ("red",   7, "just off the gold"),
    ("blue",  5, "solid"),
    ("black", 3, "on the face"),
    ("white", 1, "the outer edge"),
    ("miss",  0, "into the grass"),
]

def _steadiness(book, who):
    """0.0 to 1.0. It is EARNED and it is never lost.

    Nothing in this house rots, and that includes a hand you have trained. A
    fortnight away does not put you back to scattering.
    """
    st = book.get("steadiness")
    if not isinstance(st, dict):
        return 0.0
    try:
        return max(0.0, min(1.0, float(st.get(_norm(who), 0.0))))
    except (TypeError, ValueError):
        return 0.0

def _gain_today(book, who, today):
    g = book.get("gained")
    if not isinstance(g, dict):
        return 0.0
    row = g.get(_norm(who))
    if not isinstance(row, dict) or str(row.get("day") or "") != today:
        return 0.0
    try:
        return max(0.0, float(row.get("amount") or 0.0))
    except (TypeError, ValueError):
        return 0.0

ARCHERY_GAIN_PER_END = 0.012   
ARCHERY_GAIN_PER_DAY = 0.05    

def _loose_one(steady, rng):
    """One arrow. Steadiness moves the WHOLE distribution inward.

    At 0.0 it is mostly grass and white -- which is honest, a longbow is hard.
    At 1.0 it is mostly gold and red but never certainly gold, because a
    guaranteed nine is not a sport, it is a cutscene.
    """
    s = max(0.0, min(1.0, float(steady)))
    weights = [
        2.0 + 26.0 * s,      
        5.0 + 22.0 * s,      
        11.0 + 8.0 * s,      
        16.0 - 2.0 * s,      
        22.0 - 14.0 * s,     
        30.0 - 26.0 * s,     
    ]
    total = sum(weights)
    pick = rng.random() * total
    run = 0.0
    for i, wgt in enumerate(weights):
        run += wgt
        if pick <= run:
            return ARCHERY_RINGS[i]
    return ARCHERY_RINGS[-1]

def _best_end(book):
    best = None
    for e in _archery_ends(book):
        try:
            sc = int(e.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if best is None or sc > int(best.get("score") or 0):
            best = e
    return best

def _fish_catalog():
    return _read_json(FISH_FILE, {}) or {}

def _room_water(room):
    """Commons runtime helper."""
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    if not rid:
        return ""
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    w = d.get("water")
    if w is True:
        return "the water"
    return str(w or "").strip()

def _river_book():
    return _read_json(CATCH_FILE, {}) or {}

def _river_rows(book):
    rows = book.get("casts")
    return rows if isinstance(rows, list) else []

def _released_count(book, species):
    rel = book.get("released")
    if not isinstance(rel, dict):
        return 0
    try:
        return max(0, int(rel.get(species, 0)))
    except (TypeError, ValueError):
        return 0

def _top_lb(cat, species, book):
    """How big this species can currently run.

    Its catalog maximum, plus growth_per_release for every time ANYONE has let
    one go, capped at growth_cap. This is the entire moral of the room turned
    into arithmetic: what you give back makes the water richer for the next
    person, and there is a ceiling on it so the choice stays a choice.
    """
    rec = (cat.get("fish") or {}).get(species) or {}
    lo, hi = (rec.get("lb") or [0.1, 1.0])[:2]
    try:
        per = float(cat.get("growth_per_release", 0.02))
        cap = float(cat.get("growth_cap", 0.6))
    except (TypeError, ValueError):
        per, cap = 0.02, 0.6
    bonus = min(cap, per * _released_count(book, species))
    return float(lo), float(hi) * (1.0 + bonus), bonus

def _time_band(now=None):
    h = (now or datetime.now()).hour
    if 5 <= h < 8:
        return "dawn"
    if 8 <= h < 18:
        return "day"
    if 18 <= h < 21:
        return "dusk"
    return "night"

def _roll_catch(water, cat, book, band=None):
    """Commons runtime helper."""
    band = band or _time_band()
    names = (cat.get("waters") or {}).get(water) or []
    fish = cat.get("fish") or {}
    pool = []
    for n in names:
        rec = fish.get(n) or {}
        try:
            w = float(rec.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0:
            continue
        when = str(rec.get("when") or "any")
        if when not in ("any", band):
            w *= 0.25   
        pool.append((n, w))
    if not pool:
        return None
    total = sum(w for _, w in pool)
    pick = random.random() * total
    run = 0.0
    for n, w in pool:
        run += w
        if pick <= run:
            chosen = n
            break
    else:
        chosen = pool[-1][0]
    lo, hi, bonus = _top_lb(cat, chosen, book)
    if hi < lo:
        hi = lo

    lb = lo + (hi - lo) * (random.random() ** 2.2)
    rec = (cat.get("fish") or {}).get(chosen) or {}
    return {
        "species": chosen,
        "lb": round(lb, 2),
        "top_lb": round(hi, 2),
        "grown_by": round(bonus, 3),
        "yields": str(rec.get("yields") or "fish"),
        "colour": rec.get("colour") or "#2c4d63",
        "line": rec.get("line") or "",
        "band": band,
    }

def _forage_catalog():
    return _read_json(FORAGE_FILE, {}) or {}

def _room_ground(room):
    """Commons runtime helper."""
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    if not rid:
        return ""
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    g = d.get("forage")
    if g is True:
        return "the wood floor"
    return str(g or "").strip()

def _in_season(rec, month=None):
    """Is this find out right now? No `months` key means all year."""
    months = (rec or {}).get("months")
    if not isinstance(months, list) or not months:
        return True
    m = int(month or datetime.now().month)
    out = []
    for x in months:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return (not out) or (m in out)

def _roll_find(ground, cat, band=None, month=None):
    """Commons runtime helper."""
    band = band or _time_band()
    names = (cat.get("grounds") or {}).get(ground) or []
    finds = cat.get("finds") or {}
    pool = []
    for n in names:
        rec = finds.get(n) or {}
        try:
            w = float(rec.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w <= 0 or not _in_season(rec, month):
            continue
        when = str(rec.get("when") or "any")
        if when not in ("any", band):
            w *= 0.25   
        pool.append((n, w))
    if not pool:
        return None
    total = sum(w for _, w in pool)
    pick = random.random() * total
    run = 0.0
    for n, w in pool:
        run += w
        if pick <= run:
            chosen = n
            break
    else:
        chosen = pool[-1][0]
    rec = finds.get(chosen) or {}

    handful = 1 + int((random.random() ** 2.0) * 3)
    return {
        "find": chosen,
        "handful": handful,
        "yields": str(rec.get("yields") or "forage"),
        "colour": rec.get("colour") or "#4e7c46",
        "line": rec.get("line") or "",
        "band": band,
    }

def _hunting_catalog():
    return _read_json(HUNTING_FILE, {}) or {}

def _room_hunting(room):
    """The hunting ground named by a room, or '' when hunting is closed."""
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    if not rid:
        return ""
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    h = d.get("hunting")
    if h is True:
        return "the deep wood"
    return str(h or "").strip()

def _hunt_book():
    b = _read_json(HUNTS_FILE, {}) or {}
    if not isinstance(b.get("hunts"), list):
        b["hunts"] = []
    if not isinstance(b.get("days"), dict):
        b["days"] = {}
    return b

def _hunts_used(book, name, today):
    row = (book.get("days") or {}).get(_norm(name)) or {}
    try:
        return max(0, int(row.get(today, 0)))
    except (TypeError, ValueError):
        return 0

def _roll_hunt_loot(rec):
    """Roll the quarry's data-declared loot; zero-min entries are optional."""
    out = {}
    for item, spec in ((rec or {}).get("loot") or {}).items():
        if not isinstance(spec, dict):
            continue
        try:
            lo = max(0, int(spec.get("min", 1)))
            hi = max(lo, int(spec.get("max", lo)))
        except (TypeError, ValueError):
            continue
        n = random.randint(lo, hi)
        if n:
            out[str(item)] = n
    return out

APPROACH_KEYWORDS = {
    "gentle": ["slow", "gentle", "calm", "quiet", "soft", "patient",
               "crouch", "kneel"],
    "food": ["meat", "food", "treat", "offer", "feed", "fish", "berry",
             "honey"],
    "knowledge": ["scent", "territory", "pack", "alpha", "trust", "bond",
                  "instinct"],
    "respect": ["eye", "gaze", "bow", "respect", "submit", "lower"],
    "voice": ["whisper", "hum", "sing", "murmur", "coo", "voice", "song"],
}

def _approach_bonus(description):
    """Aisling's calculate_approach_bonus, ported keyword for keyword.

    The words are the mechanic: speak of gentleness, of food, of knowing the
    animal's nature (+2 each); of respect, of your voice (+1 each). Each
    category counts once, the whole thing caps at +4, and a substantial try
    that matches nothing still earns +1 - effort is never worth zero here.
    """
    from adventure.game import approach_bonus
    return approach_bonus(description)

def _wilds_catalog():
    return _read_json(WILDS_FILE, {}) or {}

def _room_wilds(room):
    """Commons runtime helper."""
    rid = "".join(c for c in str(room or "") if c.isalnum() or c in "-_")[:40]
    if not rid:
        return ""
    d = _read_json(COMMONS_ROOT / "world" / (rid + ".json"), {}) or {}
    w = d.get("wilds")
    if w is True:
        return "the wild wood"
    return str(w or "").strip()

def _companions_book():
    b = _read_json(COMPANIONS_FILE, {}) or {}
    if not isinstance(b.get("companions"), list):
        b["companions"] = []
    if not isinstance(b.get("walks"), dict):
        b["walks"] = {}
    return b

def _approaches_used(book, name, today):
    row = (book.get("walks") or {}).get(_norm(name)) or {}
    try:
        return max(0, int(row.get(today, 0)))
    except (TypeError, ValueError):
        return 0

def _grow_state(p, catalog=None):
    """Everything the page needs to draw one planting. Server-computed on
    purpose: the client is never trusted to say how grown something is."""
    catalog = catalog if catalog is not None else _grow_catalog()
    stages = catalog.get("stages") or ["growing", "ripe"]
    crop = (catalog.get("crops") or {}).get(str(p.get("crop") or "")) or {}
    total = max(1, int(crop.get("days") or 7))
    prog = _grow_progress(p)
    last = len(stages) - 1
    idx = int(prog * last / total)
    idx = 0 if idx < 0 else (last if idx > last else idx)
    out = dict(p)
    out["progress_days"] = prog
    out["needs_days"] = total
    out["stage_index"] = idx
    out["ripe"] = (idx >= last)
    out["colour"] = crop.get("colour") or "#6d8a5a"
    out["yields"] = crop.get("yields") or ""
    out["stage"] = (crop.get("final") or stages[last]) if idx >= last else stages[idx]
    season = _season_meta(p, "planting")
    if season.get("season_phase") == "aging":
        season["season_phase"] = (
            "seeding" if out["yields"] == "flowers" else "resting")
    out.update(season)
    return out

def _grow_all(room=""):
    _sweep_seasonal()
    cat = _grow_catalog()
    rows = _read_json(GROW_FILE, [])
    if room:
        rows = [r for r in rows if str(r.get("room") or "") == room]
    return [_grow_state(r, cat) for r in rows]

def _recipes():
    return (_read_json(RECIPES_FILE, {}) or {}).get("recipes") or {}

def _made_view(item):
    """Return a made thing with its data-declared use limit filled in.

    Older gifts predate servings. They learn their recipe's limit when read,
    while newly made things keep the limit they were born with even if the
    cookbook changes later.
    """
    out = dict(item or {})
    rec = _recipes().get(str(out.get("recipe") or "")) or {}
    try:
        total = int(out.get("uses_total", rec.get("uses", 0)) or 0)
    except (TypeError, ValueError):
        total = 0
    total = max(0, min(total, 1000))
    if total:
        try:
            left = int(out.get("uses_left", total))
        except (TypeError, ValueError):
            left = total
        out["uses_total"] = total
        out["uses_left"] = max(0, min(left, total))
        out["unit"] = str(out.get("unit") or rec.get("unit") or "use")[:30]
        out["unit_plural"] = str(out.get("unit_plural") or rec.get("unit_plural")
                                 or (out["unit"] + "s"))[:30]
        out["use_label"] = str(out.get("use_label") or rec.get("use_label")
                               or "use it")[:60]
    return out

def _placed_made():
    """Made things currently set out, shaped like ordinary placeables."""
    rows = []
    for holder in (_basket_all() or {}).values():
        owner = str((holder or {}).get("name") or "someone")
        for raw in ((holder or {}).get("made") or []):
            placed = raw.get("placed") if isinstance(raw, dict) else None
            if not isinstance(placed, dict) or not placed.get("room"):
                continue
            m = _made_view(raw)
            rows.append({
                "id": "made:" + str(m.get("id") or ""),
                "made_id": str(m.get("id") or ""),
                "kind": "made",
                "label": str(m.get("what") or "a made thing"),
                "owner": owner,
                "room": str(placed.get("room") or ""),
                "x": placed.get("x", 0), "y": placed.get("y", 0),
                "w": 2.25 if placed.get("w", .9) == .9 else placed["w"],
                "h": 1.65 if placed.get("h", .65) == .65 else placed["h"],
                "art": str(m.get("art") or ""),
                "colour": str(m.get("colour") or "#8a6a3a"),
                "note": str(m.get("line") or m.get("note") or ""),
                "at": str(placed.get("at") or m.get("made_at") or ""),
                "uses_total": m.get("uses_total", 0),
                "uses_left": m.get("uses_left", 0),
                "unit": m.get("unit", ""),
                "unit_plural": m.get("unit_plural", ""),
                "use_label": m.get("use_label", ""),
            })
    return rows

SHINY_FILE = COMMONS_ROOT / "world" / "shinies.json"
ROOK_FILE = COMMONS_ROOT / "world" / "rook.json"
ALF_BEAT = 20
ALF_STAY = 9
ALF_GIFT_N = 7
_ROOK_LOCK = threading.Lock()

def _shiny_catalog():
    d = _read_json(SHINY_FILE, {}) or {}
    if not isinstance(d.get("shinies"), dict):
        d["shinies"] = {}
    if not isinstance(d.get("rooms"), list):
        d["rooms"] = []
    return d

def _alf_hash(n, salt):
    """Commons runtime helper."""
    M = 0xFFFFFFFF
    h = 2166136261
    for ch in "rook:%s:%d" % (salt, n):
        h = ((h ^ ord(ch)) * 16777619) & M
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & M
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & M
    h ^= h >> 16
    return h & M

def _rook_at(beat):
    cat = _shiny_catalog()
    rooms = cat.get("rooms") or []
    if not rooms:
        return None
    stint = beat // ALF_STAY
    k = beat - stint * ALF_STAY
    room = rooms[_alf_hash(stint, "room") % len(rooms)]
    gift = (k == ALF_STAY - 1) and (_alf_hash(stint, "gift") % ALF_GIFT_N == 0)

    names = list((cat.get("shinies") or {}).keys())
    shiny = names[_alf_hash(beat, "shiny") % len(names)] if names else None
    return {"beat": beat, "stint": stint, "k": k, "room": room,
            "gift": gift, "shiny": shiny}

def _rook_beat_now():
    return int(time.time() // ALF_BEAT)

def _made_archive():
    return _read_json(MADE_ARCHIVE_FILE, {})

def _shelve(holder, item):
    """Commons runtime helper."""
    rows = list(holder.get("made") or []) + [item]
    if len(rows) <= MADE_MAX:
        holder["made"] = rows
        return
    overflow, rows = rows[:len(rows) - MADE_MAX], rows[len(rows) - MADE_MAX:]
    try:
        arc = _made_archive()
        who = str(holder.get("name") or "someone")
        bucket = arc.get(who)
        if not isinstance(bucket, list):
            bucket = []
        for old in overflow:
            old = dict(old)
            old["shelved_on"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            bucket.append(old)
        arc[who] = bucket
        _write_json(MADE_ARCHIVE_FILE, arc)
    except Exception:
        holder["made"] = list(holder.get("made") or []) + [item]
        return
    holder["made"] = rows

def _basket_all():
    return _read_json(BASKET_FILE, {}) or {}

def _basket_row(store, name):
    """Commons runtime helper."""
    k = _norm(name)
    row = store.get(k)
    if not isinstance(row, dict):
        row = {}
    row.setdefault("name", _addressable().get(k) or str(name or "").strip()[:40])
    if not isinstance(row.get("picked"), dict):
        row["picked"] = {}
    if not isinstance(row.get("made"), list):
        row["made"] = []
    if not isinstance(row.get("equipment"), dict):
        row["equipment"] = {}
    for slot in EQUIPMENT_SLOTS:
        row["equipment"].setdefault(slot, None)
    store[k] = row
    return row

def _equipment_summary(row):
    """Resolve equipped ids against the owner's shelf and total bonuses.

    The id is authoritative, not the item's display name: two brothers may
    both make a yew bow, and neither should ever equip the other's by accident.
    Missing/archived items self-heal to an empty slot when the basket is next
    written; this read stays side-effect free.
    """
    made = {str(m.get("id") or ""): m for m in (row.get("made") or [])
            if isinstance(m, dict)}
    equipped = row.get("equipment") or {}
    slots, totals = {}, {}
    for slot in EQUIPMENT_SLOTS:
        item = made.get(str(equipped.get(slot) or ""))
        if item is None:
            name=(row.get('inventory_equipment') or {}).get(slot)
            if name and (row.get('picked') or {}).get(name,0)>0:
                ref=(row.get('inventory_equipment_refs') or {}).get(slot,{})
                condition=(row.get('inventory_item_conditions') or {}).get(str(ref.get('id') or ''))
                if ref.get('id'):

                    if isinstance(condition,dict) and condition.get('name')==name:
                        item=dict(condition,what=name)
                else:
                    item={'name':name,'what':name}
        if item is not None:item=_item_definition(item)
        if not item or item_slot(item) != slot or item.get('placed'):
            slots[slot] = None
            continue
        if isinstance(item.get('durability'),(int,float)) and item['durability']<=0:
            slots[slot]=None
            continue
        slots[slot] = item
        for key, value in item_bonus_values(item).items():
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            totals[str(key)] = totals.get(str(key), 0) + value
    return {"slots": slots, "bonuses": totals}

def _yields_map(catalog=None):
    """Commons runtime helper."""
    return _categories.build(
        catalog if catalog is not None else _grow_catalog(),
        _fish_catalog(), _shiny_catalog(), _forage_catalog(), _hunting_catalog())

def _picked_by_category(picked, catalog=None):
    """Fold {thing: n} into {category: n} using _yields_map().

    Recipes are written against categories on purpose: add a crop tomorrow and
    every recipe already knows what to do with it.
    """
    return _categories.by_category(picked, _yields_map(catalog))

def _can_make(picked, recipe, room="", catalog=None):
    """Check location and ingredients; food can be prepared in any room."""
    where = str(recipe.get("where") or "").strip()
    if not _recipe_available_here(recipe, room=room):
        return False, "that gets made in the " + where
    have = _picked_by_category(picked, catalog)
    for cat, need in (recipe.get("needs") or {}).items():
        if have.get(cat, 0) < int(need):
            return False, "you need %d %s and you have %d" % (
                int(need), cat, have.get(cat, 0))
    return True, ""

def _spend(picked, recipe, catalog=None):
    """Commons runtime helper."""

    yields = _yields_map(catalog)
    spent = {}
    for cat, need in (recipe.get("needs") or {}).items():
        need = int(need)
        pool = sorted(
            [(c, int(n)) for c, n in picked.items()
             if int(n) > 0 and _categories.category(yields, c) == cat],
            key=lambda t: (-t[1], t[0]))
        for crop, have in pool:
            if need <= 0:
                break
            take = have if have < need else need
            picked[crop] = have - take
            if picked[crop] <= 0:
                picked.pop(crop, None)
            spent[crop] = spent.get(crop, 0) + take
            need -= take
        if need > 0:

            raise ValueError("not enough " + cat)
    head = ""
    best = -1
    for crop, n in sorted(spent.items()):
        if n > best:
            best, head = n, crop
    return spent, head

def _verify(name, key) -> bool:
    """Commons runtime helper."""
    n, k = _norm(name), str(key or '').strip()[:64]
    if not n or len(k) < 8:
        return False
    with _KEY_LOCK:
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        keys = _read_json(KEYS_FILE, {})
        if n == ADMIN_NAME and ADMIN_NAME in keys:

            master = _reserved_key(ADMIN_NAME)
            if master is not None and hmac.compare_digest(master, str(keys[ADMIN_NAME])):
                keys.pop(ADMIN_NAME, None)
                _write_json(KEYS_FILE, keys)
        held = keys.get(n)
        if held is None and n in RESERVED_NAMES:

            held = _reserved_key(n)
            if held is None:
                return False
            if n == ADMIN_NAME:

                return hmac.compare_digest(held, k)
            keys[n] = held
            _write_json(KEYS_FILE, keys)
        if held is None:

            rec = _invite_rec(n)
            if rec is not None:
                inv = str(rec.get("key") or "").strip()
                if len(inv) < 8:

                    return False
                keys[n] = inv
                _write_json(KEYS_FILE, keys)
                return hmac.compare_digest(inv, k)
        if held is None:
            if not _walk_in_name_ok(name):
                return False
            keys[n] = k
            _write_json(KEYS_FILE, keys)
            return True
        return hmac.compare_digest(held, k)

_ROOM_OK = re.compile(r"^[a-z0-9_-]{1,40}$")

def _chat_file(room):
    """The log for one room, or None if that is not a room in this house.

    Two gates, because this name arrives from a browser and becomes a PATH.
    The shape check alone would still let through a name with no room behind
    it, minting an empty log for a place that does not exist; the existence
    check alone would still let through "../../keys" if world/ ever held a
    matching file. Both, or neither is worth having.
    """
    r = str(room or "").strip().lower()
    if not _ROOM_OK.match(r):
        return None
    if not (COMMONS_ROOT / "world" / (r + ".json")).exists():
        return None
    return CHAT_DIR / (r + ".json")

def _may_read_chat(room, viewer):
    """Commons runtime helper."""
    owner = ROOM_OWNERS.get(str(room or "").strip().lower())
    return True if not owner else _norm(viewer) == _norm(owner)

def _public_presence(rows):
    """Presence as anyone may see it: no speech from inside an owned room."""
    if not isinstance(rows, dict):
        return rows
    out = {}
    for name, row in rows.items():
        if isinstance(row, dict) and ROOM_OWNERS.get(str(row.get("room") or "").strip().lower()):
            row = {k: v for k, v in row.items() if k not in ("says", "says_ts")}
        out[name] = row
    return out

LOCKED_MSG = "that door is locked -- knock, or bring today's password"

def _room_open_for(room, by=None, key=None, password=None):
    """Commons runtime helper."""
    r = str(room or "").strip().lower()
    owner = ROOM_OWNERS.get(r)
    if owner is None:
        return True

    if not _is_member(by):
        return False
    locks = _read_json(LOCK_FILE, {}) or {}
    entry = {}
    for k, v in locks.items():
        if str(k or "").strip().lower() == r and isinstance(v, dict):
            entry = v
            break
    secret = entry.get("password")
    if not secret:
        return True
    if _norm(by) == _norm(owner) and _verify(by, key):
        return True
    offered = str(password or "").strip()[:64]
    return bool(offered) and hmac.compare_digest(offered, secret)

def _reroot(private, master=None, world=None):
    """Commons runtime helper."""
    global PRIVATE_ROOT, MASTER_ROOT, KEYS_FILE, INVITES_FILE, LOCK_FILE
    global WORLD_DIR, UPLOAD_DIR
    global NOTES_FILE, PLACE_FILE, SEASONAL_ARCHIVE_FILE, CROPS_FILE, GROW_FILE
    global RECIPES_FILE, BASKET_FILE, FISH_FILE, CATCH_FILE, ARCHERY_FILE
    global FORAGE_FILE, HUNTING_FILE, HUNTS_FILE, WILDS_FILE, COMPANIONS_FILE
    global CHAT_DIR, MADE_ARCHIVE_FILE, HERE_FILE, OUTFITS_FILE, KNOCK_FILE, HOWL_FILE
    global SHINY_FILE, ROOK_FILE

    PRIVATE_ROOT = Path(private)
    MASTER_ROOT = Path(master) if master is not None else PRIVATE_ROOT
    KEYS_FILE = PRIVATE_ROOT / "keys.json"
    INVITES_FILE = PRIVATE_ROOT / "invites.json"
    LOCK_FILE = PRIVATE_ROOT / "nestlock.json"
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    MASTER_ROOT.mkdir(parents=True, exist_ok=True)

    w = Path(world) if world is not None else (COMMONS_ROOT / "world")
    WORLD_DIR = w
    NOTES_FILE = w / "notes.json"
    PLACE_FILE = w / "placeables.json"
    SEASONAL_ARCHIVE_FILE = w / "seasonal_archive.json"
    CROPS_FILE = w / "crops.json"
    GROW_FILE = w / "plantings.json"
    RECIPES_FILE = w / "recipes.json"
    BASKET_FILE = w / "baskets.json"
    FISH_FILE = w / "fish.json"
    FORAGE_FILE = w / "forage.json"
    HUNTING_FILE = w / "hunting.json"
    HUNTS_FILE = w / "hunts.json"
    WILDS_FILE = w / "wilds.json"
    COMPANIONS_FILE = w / "companions.json"
    CATCH_FILE = w / "catches.json"
    ARCHERY_FILE = w / "archery.json"
    CHAT_DIR = w / "chat"
    MADE_ARCHIVE_FILE = w / "made-archive.json"
    HERE_FILE = w / "presence.json"
    OUTFITS_FILE = w / "outfits.json"
    KNOCK_FILE = w / "knocks.json"
    HOWL_FILE = w / "howls.json"
    SHINY_FILE = w / "shinies.json"
    ROOK_FILE = w / "rook.json"
    if world is not None:
        w.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR = w.parent / "uploads"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    else:
        UPLOAD_DIR = CDN_ROOT / "uploads"

_VERB_ROOM_CACHE = {"at": 0.0, "rooms": {}}

def _verb_room_id(raw):
    """Alnum/dash/underscore only, same idea _room_water and _room_range
    already use -- plus lower(), which they do NOT do, because neither of
    them ever needed to compare two room strings for equality (they only
    ever look one up in a dict). !look does compare, so this copy is a
    little stricter than its cousins on purpose: a room match here should
    never depend on which case a client happened to send.
    """
    cleaned = "".join(c for c in str(raw or "")
                       if c.isalnum() or c in "-_")
    return cleaned[:40].lower()

def _verb_rooms():
    """Commons runtime helper."""
    now = time.time()
    if now - _VERB_ROOM_CACHE["at"] < 5 and _VERB_ROOM_CACHE["rooms"]:
        return _VERB_ROOM_CACHE["rooms"]
    rooms = {}
    world_dir = COMMONS_ROOT / "world"
    if world_dir.exists():
        for f in world_dir.glob("*.json"):
            d = _read_json(f, None)
            if isinstance(d, dict) and "id" in d:
                rooms[f.stem.lower()] = str(d.get("name") or f.stem)
    _VERB_ROOM_CACHE["at"] = now
    _VERB_ROOM_CACHE["rooms"] = rooms
    return rooms

_EDITOR_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_EDITOR_PROP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")

def _editor_room_id(raw):
    rid = str(raw or "").strip().lower()
    return rid if _EDITOR_ID.fullmatch(rid) else ""

def _editor_room_file(raw):
    rid = _editor_room_id(raw)
    return (WORLD_DIR / (rid + ".json")) if rid else None

def _editor_revision(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    except Exception:
        return ""

def _editor_rooms():
    rooms = []
    if not WORLD_DIR.exists():
        return rooms
    for path in WORLD_DIR.glob("*.json"):
        data = _read_json(path, None)
        if (isinstance(data, dict) and isinstance(data.get("rows"), list)
                and _editor_room_id(data.get("id")) == path.stem.lower()):
            rooms.append({
                "id": path.stem.lower(),
                "name": str(data.get("name") or path.stem)[:100],
                "width": len(data.get("rows", [""])[0]) if data.get("rows") else 0,
                "height": len(data.get("rows") or []),
            })
    return sorted(rooms, key=lambda r: (r["name"].lower(), r["id"]))

def _editor_snapshot(raw):
    path = _editor_room_file(raw)
    room = _read_json(path, None) if path else None
    if not isinstance(room, dict) or not isinstance(room.get("rows"), list):
        return None
    with _PLACE_LOCK:
        things = _read_json(PLACE_FILE, [])
        fixed = [dict(t) for t in things
                 if t.get("fixed") and _norm(t.get("room")) == _norm(room.get("id"))]
        place_revision = _editor_revision(PLACE_FILE)
    return {
        "room": room,
        "fixed_placeables": fixed,
        "room_revision": _editor_revision(path),
        "placeables_revision": place_revision,
    }

def _editor_number(value, label, low, high):
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        raise ValueError("bad " + label)
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(label + " is outside this map")
    return number

def _editor_external_url(value, label="external destination"):
    """Accept an ordinary web doorway without permitting script/data URLs."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > 500:
        raise ValueError(label + " is too long")
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    local_http = parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}
    if not parsed.netloc or (parsed.scheme != "https" and not local_http):
        raise ValueError(label + " must be an https URL (or local http while testing)")
    if parsed.username or parsed.password:
        raise ValueError(label + " cannot contain credentials")
    return raw

def _editor_geometry(row, width, height, label):

    return {
        "x": _editor_number(row.get("x"), label + " x", -width, width * 2),
        "y": _editor_number(row.get("y"), label + " y", -height, height * 2),
        "w": _editor_number(row.get("w"), label + " width", .1, width * 2),
        "h": _editor_number(row.get("h"), label + " height", .1, height * 2),
    }

def _editor_prepare_room(incoming, live):
    
    if not isinstance(incoming, dict):
        raise ValueError("room is missing")
    rows = incoming.get("rows")
    old_rows = live.get("rows") or []
    if (not isinstance(rows, list) or not rows
            or len(rows) != len(old_rows)
            or any(not isinstance(row, str) for row in rows)):
        raise ValueError("the collision grid must keep the map height")
    width = len(old_rows[0]) if old_rows else 0
    if width < 1 or any(len(row) != width for row in rows):
        raise ValueError("the collision grid must keep the map width")
    legend = live.get("legend") or {}
    allowed = set(legend.keys())
    if not allowed or any(ch not in allowed for row in rows for ch in row):
        raise ValueError("the collision grid contains a tile outside this room's legend")

    spawn = incoming.get("spawn") or {}
    new_spawn = {
        "x": _editor_number(spawn.get("x"), "spawn x", 0, width - .01),
        "y": _editor_number(spawn.get("y"), "spawn y", 0, len(rows) - .01),
    }

    old_props = {str(p.get("id")): p for p in (live.get("props") or [])
                 if isinstance(p, dict) and p.get("id")}
    new_props = []
    seen = set()
    payload_props = incoming.get("props")
    if not isinstance(payload_props, list) or len(payload_props) > 400:
        raise ValueError("props must be a list of at most 400 things")
    for raw in payload_props:
        if not isinstance(raw, dict):
            raise ValueError("every prop must be an object")
        pid = str(raw.get("id") or "").strip()
        if not _EDITOR_PROP_ID.fullmatch(pid) or pid in seen:
            raise ValueError("every prop needs one unique safe id")
        seen.add(pid)
        item = dict(old_props.get(pid) or {})
        item.update(_editor_geometry(raw, width, len(rows), "prop " + pid))
        item["id"] = pid
        item["label"] = str(raw.get("label") or pid).strip()[:100]
        item["note"] = str(raw.get("note") or "")[:PLACE_NOTE_MAX]
        item["invisible"] = bool(raw.get("invisible", True))
        item["fishable"] = bool(raw.get("fishable", item.get("fishable", False)))
        action = raw.get('action')
        if isinstance(action, dict) and action.get('name') == 'sit':
            spots = action.get('spots')
            if not isinstance(spots, list) or not 1 <= len(spots) <= 32:
                raise ValueError('A seat needs between 1 and 32 cushion positions')
            prepared = []
            for spot in spots:
                if not isinstance(spot, dict) or spot.get('dir', 'down') not in ('up', 'down', 'left', 'right'):
                    raise ValueError('Choose a valid seating direction')
                prepared.append({'x': _editor_number(spot.get('x'), 'seat x', 0, width),
                                 'y': _editor_number(spot.get('y'), 'seat y', 0, len(rows)),
                                 'dir': spot.get('dir', 'down')})
            item['action'] = {'name': 'sit', 'label': str(action.get('label') or 'sit here')[:100], 'spots': prepared}
        elif isinstance(item.get('action'), dict) and item['action'].get('name') == 'sit':
            item.pop('action', None)
        item.setdefault("by", "Admin")
        item.setdefault("placed", datetime.now().strftime("%Y-%m-%d"))

        if item.get('game_npc'):
            variant = str(raw.get('npc_art') or '').strip()
            catalog = _read_json(COMMONS_ROOT / 'assets/art-catalog.json', {})
            if variant and variant not in catalog.get('npcs', {}):
                raise ValueError('Choose an available NPC appearance')
            item['npc_art'] = variant
            item['npc_scale'] = _editor_number(raw.get('npc_scale', 1), 'NPC size', .25, 3)

        art = str(raw.get("art") or "").strip()[:300]
        if art and not _art_ok(art):
            raise ValueError("prop " + pid + " uses art outside this house")
        if art:
            item["art"] = art
        else:
            item.pop("art", None)

        target = _editor_room_id(raw.get("to"))
        href = _editor_external_url(raw.get("href"), "prop " + pid + " external destination")
        if raw.get("to") and not target:
            raise ValueError("prop " + pid + " has a bad destination")
        if target and href:
            raise ValueError("prop " + pid + " cannot point to a room and a website")
        if target:
            target_path = _editor_room_file(target)
            if target_path is None or not target_path.is_file():
                raise ValueError("prop " + pid + " points to a room that does not exist")
            arrival = raw.get("arriveAt") or {}
            item["to"] = target
            item["arriveAt"] = {
                "x": _editor_number(arrival.get("x"), "arrival x", -200, 200),
                "y": _editor_number(arrival.get("y"), "arrival y", -200, 200),
            }
            item.pop("href", None)
        elif href:
            item["href"] = href
            item.pop("to", None)
            item.pop("arriveAt", None)
        else:
            item.pop("to", None)
            item.pop("arriveAt", None)
            item.pop("href", None)
        new_props.append(item)

    updated = dict(live)
    backdrop = str(incoming.get('backdrop', live.get('backdrop', '')) or '').strip()
    if len(backdrop) > 300 or (backdrop and not _art_ok(backdrop)):
        raise ValueError('Choose a room painting from this house')
    updated['backdrop'] = backdrop
    updated["rows"] = rows
    updated["spawn"] = new_spawn
    updated["props"] = new_props
    if 'fishing_paint' in incoming:
        from fishing_areas import validate_paint
        updated['fishing_paint'] = validate_paint(incoming['fishing_paint'], rows)
    return updated

def _editor_prepare_fixed(raw_rows, rid, room, all_things):
    if not isinstance(raw_rows, list) or len(raw_rows) > 100:
        raise ValueError("pinned things must be a list of at most 100 things")
    rows = room.get("rows") or []
    width, height = len(rows[0]), len(rows)
    old = {str(t.get("id")): t for t in all_things
           if isinstance(t, dict) and t.get("fixed")
           and _norm(t.get("room")) == _norm(rid)}
    elsewhere = {str(t.get("id")) for t in all_things
                 if not (t.get("fixed") and _norm(t.get("room")) == _norm(rid))}
    out, seen = [], set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError("every pinned thing must be an object")
        tid = str(raw.get("id") or "").strip()
        if (not _EDITOR_PROP_ID.fullmatch(tid) or tid in seen
                or (tid in elsewhere and tid not in old)):
            raise ValueError("every pinned thing needs one unique safe id")
        seen.add(tid)
        item = dict(old.get(tid) or {})
        item.update(_editor_geometry(raw, width, height, "pinned thing " + tid))
        item.update({
            "id": tid,
            "kind": str(raw.get("kind") or item.get("kind") or "fixture")[:30],
            "label": str(raw.get("label") or tid).strip()[:100],
            "room": rid,
            "fixed": True,
            "note": str(raw.get("note") or "")[:PLACE_NOTE_MAX],
        })
        item.setdefault("owner", "Admin")
        item.setdefault("at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        art = str(raw.get("art") or "").strip()[:300]
        if not art or not _art_ok(art):
            raise ValueError("pinned thing " + tid + " needs art from this house")
        item["art"] = art
        out.append(item)
    return out

_VERB_KV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_VERB_KV_SPAN = re.compile(
    r"""(?<![^\s])([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|'([^']*)'|(\S*))""")

def _parse_verb_line(text):
    """Commons runtime helper."""
    text = str(text or "").strip()
    if not text.startswith("!"):
        return None, {}, ""
    head = text[1:]
    m = re.match(r"\s*(\S+)", head)
    if not m:
        return None, {}, ""
    verb = m.group(1).lower()
    body = head[m.end():]

    kwargs, cuts = {}, []
    for hit in _VERB_KV_SPAN.finditer(body):
        val = next((g for g in hit.groups()[1:] if g is not None), "")
        kwargs[hit.group(1).lower()] = val
        cuts.append((hit.start(), hit.end()))

    out, at = [], 0
    for a, b in cuts:
        out.append(body[at:a])
        at = b
    out.append(body[at:])
    return verb, kwargs, "".join(out).strip()

VERB_REGISTRY = {
    "fish":  {"route": "/api/cast", "kwargs": (), "positional": None,
              "help": "!fish -- cast a line where you're standing"},
    "gather": {"route": "/api/forage", "kwargs": (), "positional": None,
               "help": "!gather -- pick something up off the ground where you're standing"},
    "hunt":  {"route": "/api/hunt", "kwargs": ("quarry",),
              "positional": "quarry",
              "help": "!hunt <wild game> -- roll against quarry on this hunting ground; named animals are family, never quarry"},
    "plant": {"route": "/api/plant", "kwargs": ("bed", "crop", "x", "y"),
              "positional": "crop",
              "help": "!plant <crop> bed=<name> -- put something in the ground"},
    "tend":  {"route": "/api/tend", "kwargs": ("id",), "positional": "id",
              "help": "!tend id=<planting-id> -- water something"},
    "pick":  {"route": "/api/harvest", "kwargs": ("id",), "positional": "id",
              "help": "!pick id=<planting-id> -- harvest something ripe (the plant stays)"},
    "howl":  {"route": "/api/howl", "kwargs": ("words",), "positional": "words",
              "help": "!howl <words> -- lift your head"},
    "shoot": {"route": "/api/loose", "kwargs": (), "positional": None,
              "help": "!shoot -- loose one end (three arrows) at the butts"},
    "knock": {"route": "/api/knock", "kwargs": ("who", "word"),
              "positional": "word",
              "help": "!knock who=<name> <word> -- call on someone who isn't here"},
    "say":   {"route": "/api/say", "kwargs": ("text",), "positional": "text",
              "help": "!say <text> -- speak out loud where you're standing"},
    "craft": {"route": "/api/craft", "kwargs": ("recipe", "for"),
              "positional": "recipe",
              "help": "!craft <recipe> for=<name> -- turn what you picked into something"},
    "equip": {"route": "/api/equip", "kwargs": ("id",), "positional": "id",
              "help": "!equip id=<made-item-id> -- equip something from your own shelf"},
    "unequip": {"route": "/api/unequip", "kwargs": ("slot",),
                "positional": "slot",
                "help": "!unequip <weapon|armor|accessory> -- return that slot to empty"},
    "draft": {"route": "/api/draft", "kwargs": ("title", "text"),
              "positional": "text",
              "help": "!draft title=\"<a title with spaces>\" <the writing> -- put a draft in your own drafts folder for the blog-publisher"},
    "unsay": {"route": "/api/unsay", "kwargs": ("id",), "positional": "id",
              "help": "!unsay id=<line-id> -- take back something you said here. Yours only, and it goes for good; see !said for the ids"},
    "keep":  {"route": "/api/creel", "kwargs": ("id",), "positional": "id",
              "force": {"keep": True},
              "help": "!keep id=<catch-id> -- put a catch in your basket"},
    "release": {"route": "/api/creel", "kwargs": ("id",), "positional": "id",
              "force": {"keep": False},
              "help": "!release id=<catch-id> -- put it back; it is recorded under your name and it raises that species for whoever casts next"},
    "approach": {"route": "/api/approach", "kwargs": ("animal", "words"),
              "positional": "words",
              "help": "!approach animal=<wolf|owl|...> <how you come to it, in your own words> -- the words are the mechanic; speak of gentleness, food, or its nature and it listens harder"},
    "name": {"route": "/api/name-companion", "kwargs": ("id", "name"),
              "positional": "name",
              "help": "!name id=<companion-id> <the name> -- give an animal that came to you its name (see !companions for ids)"},
}

VERB_EMOTES = {"wave": "waves", "smile": "smiles", "nod": "nods", "laugh": "laughs"}

VERB_READS = {
    "garden": {"route": "/api/garden",
               "query": {"room": "room"},
               "help": "!garden [room=<name>] -- read the beds: every planting "
                       "with its id, what it is, whose, and what is ripe"},

    "said":   {"route": "/api/chat",
               "query": {"room": "room", "as": "by", "key": "key"},
               "preserve": {"lines": "spoken"},
               "help": "!said -- what has been said in this room, each line with its id (use one with !unsay)"},
    "desk":   {"route": "/api/desk",
               "query": {"as": "by", "key": "key"},
               "help": "!desk -- what the pack has published lately, newest first, and how many drafts you are carrying"},
    "drafts": {"route": "/api/drafts",
               "query": {"as": "by", "key": "key", "read": "read"},
               "help": "!drafts [read=<name>] -- your own unfinished things; read= gives one back whole"},
    "basket": {"route": "/api/basket",

               "query": {"as": "by", "key": "key", "room": "room"},
               "help": "!basket -- what you are carrying, and what you could "
                        "make standing here"},
    "hunts": {"route": "/api/hunts",
               "query": {"room": "room", "as": "by"},
               "help": "!hunts -- quarry on this ground, recent hunts, and how many hunts you have left today"},
    "equipment": {"route": "/api/equipment",
               "query": {"as": "by", "key": "key"},
               "help": "!equipment -- your weapon, armor, accessory, and their combined bonuses"},
    "companions": {"route": "/api/companions",
               "query": {"room": "room", "as": "by"},
               "help": "!companions -- the book of the wilds: who is here to "
                       "meet, everyone's companions, yours (with ids for "
                       "!name), and how many approaches you have left today"},
}

def _desk_folder(name: str):
    """<Pack>/<Name>/<section>/ for a boy who publishes, else None.

    The capitalised folder is found by matching case-insensitively against what
    is actually on disk rather than by .title() -- a name like 'McCoy' would
    title-case wrong, and a guessed path that 404s is indistinguishable from a
    boy who has not written yet. Look, do not assume.
    """
    n = _norm(name)
    sect = DESK_SECTIONS.get(n)
    if not sect:
        return None
    try:
        for d in PACK_ROOT.iterdir():
            if d.is_dir() and d.name.lower() == n:
                return d / sect
    except Exception:
        return None
    return None

def _desk_unxml(t: str) -> str:
    """RSS text back to plain. CDATA first, then the five real entities."""
    t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t or "", flags=re.S)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&apos;", "'"), ("&amp;", "&")):
        t = t.replace(a, b)
    return t.strip()

def _desk_posts(name: str, limit: int = 3):
    """Latest published items for one boy, newest first, straight off feed.xml.

    A missing feed is not an error and never will be: it means he has not
    published, which is a true and ordinary state for a raccoon who paints.
    """
    folder = _desk_folder(name)
    if folder is None:
        return []
    f = folder / "feed.xml"
    try:
        raw = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    for chunk in re.findall(r"<item>(.*?)</item>", raw, re.S)[:max(1, limit)]:
        def grab(tag, _c=chunk):
            m = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), _c, re.S)
            return _desk_unxml(m.group(1)) if m else ""
        out.append({"title": grab("title")[:200],
                    "link": grab("link")[:400],
                    "date": grab("pubDate")[:60],
                    "blurb": grab("description")[:300]})
    return out

def _drafts_dir(name: str):
    """Where YOUR unfinished things live. Created on first write, not before --
    an empty folder for every boy would be seven lies about who is writing."""
    folder = _desk_folder(name)
    return None if folder is None else folder.parent / "drafts"

def _draft_slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return (base or "untitled")[:60]

def _draft_list(name: str):
    """Every draft you are carrying, newest first. Names + sizes + when."""
    d = _drafts_dir(name)
    if d is None or not d.exists():
        return []
    rows = []
    try:
        entries = list(d.iterdir())
    except Exception:
        return []
    for f in entries:
        if not f.is_file() or f.suffix.lower() not in (".md", ".txt"):
            continue
        try:
            st = f.stat()
        except Exception:
            continue
        rows.append({"name": f.name, "bytes": st.st_size,
                     "when": time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(st.st_mtime)),
                     "_t": st.st_mtime})
    rows.sort(key=lambda r: r["_t"], reverse=True)
    for r in rows:
        r.pop("_t", None)
    return rows

def _verb_help_table():
    """Built from the registry itself, every call -- it cannot go stale
    the way a hand-written help list always eventually does. Add a verb to
    VERB_REGISTRY and it is IN !help the next time anyone asks, with no
    second place that has to remember to update.
    """
    out = ["!help / !verbs -- this list", "!look -- read the room you're standing in",
           "!<room> -- walk to a room by name (see rooms below)"]
    for _name, spec in sorted(VERB_REGISTRY.items()):
        out.append(spec["help"])
    for _name, spec in sorted(VERB_READS.items()):
        out.append(spec["help"])
    for name, action in sorted(VERB_EMOTES.items()):
        out.append("!%s -- emote (%s), posted as chat, not a separate reaction system yet"
                    % (name, action))
    return {"verbs": out, "rooms": sorted(_verb_rooms().keys())}

def _public_room_geometry(data):
    """[commons:loading] Add the current art version only to a served room copy."""
    backdrop = data.get("backdrop")
    versioned = versioned_image_url(backdrop, CDN_ROOT)
    if versioned == backdrop:
        return data
    return {**data, "backdrop": versioned}

class HearthHandler(SimpleHTTPRequestHandler):
    """Static file server for the Hearth, rooted at commons/.

    Kept deliberately plain: the world is DATA, so nothing here needs to know
    anything about rooms or props. It hands out files and answers a health ping.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(COMMONS_ROOT), **kwargs)

    def translate_path(self, path):
        translated = Path(super().translate_path(path))
        relative = translated.relative_to(COMMONS_ROOT)
        if relative == Path("."):
            return str(COMMONS_ROOT / "index.html")
        if is_cdn_asset(relative):
            if relative.parts[0] == "uploads":
                return str(UPLOAD_DIR.joinpath(*relative.parts[1:]))
            return str(CDN_ROOT / relative)
        return str(translated)

    def send_head(self):
        self._versioned_static = False
        room_bytes = None

        decoded = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        parts = decoded.replace("\\", "/").split("/")
        if "\\" in decoded or any(p == ".." or p.startswith(".") or ".bak" in p.lower() for p in parts if p):
            self.send_error(404)
            return None
        path = Path(self.translate_path(self.path))
        allowed_root = UPLOAD_DIR if "uploads" in parts else (CDN_ROOT if path.is_relative_to(CDN_ROOT) else COMMONS_ROOT)
        if not path.resolve().is_relative_to(allowed_root.resolve()):
            self.send_error(404)
            return None
        relative = Path(super().translate_path(self.path)).relative_to(COMMONS_ROOT)
        public = relative == Path(".") or (len(relative.parts) == 1 and relative.suffix.lower() == ".html")
        public = public or (relative.parts and relative.parts[0] in ("art", "assets", "maps", "uploads", "downloads")
                            and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".json", ".css", ".js", ".mp3", ".ogg", ".wav", ".m4a", ".zip", ".txt"))
        public = public or (relative.parts and relative.parts[0] == "item-art"
                            and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"))
        public = public or relative == Path("maps/README.md")
        if len(relative.parts) == 2 and relative.parts[0] == "world" and path.suffix == ".json":
            
            data = _read_json(path, {})
            public = isinstance(data, dict) and isinstance(data.get("rows"), list) and bool(data["rows"])
            if public:
                rendered = _public_room_geometry(data)
                if rendered is not data:
                    room_bytes = json.dumps(rendered).encode("utf-8")
        if not public or not path.is_file():
            self.send_error(404)
            return None
        if room_bytes is not None:

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(room_bytes)))
            self.end_headers()
            return io.BytesIO(room_bytes)

        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        self._versioned_static = (path.suffix.lower() in
            (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
            and any(version.strip() for version in query.get("v", [])))
        return super().send_head()

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _holiday_room(self, room_id):
        if not room_id:
            return None
        rid = _editor_room_id(room_id)
        path = _editor_room_file(rid) if rid else None
        if path is None or not path.is_file():
            raise ValueError("No such room.")
        return _read_json(path, {})

    def _get_holidays(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        viewer = (query.get("as") or [""])[0].strip()[:40]
        key = (query.get("key") or [""])[0]
        try:
            room = self._holiday_room((query.get("room") or [""])[0])
        except ValueError as exc:
            return self._json({"error": str(exc)}, 404)
        result = holiday_state(_game_store(), room=room)
        result["can_manage"] = _norm(viewer) == ADMIN_NAME and _verify(viewer, key)
        return self._json(result)

    def _do_holidays(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 8192:
                return self._json({"error": "That holiday setting is too large or empty."}, 413)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Use a holiday settings object.")
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "Bad holiday settings JSON."}, 400)
        viewer = str(body.get("by") or body.get("as") or "").strip()[:40]
        if _norm(viewer) != ADMIN_NAME or not _verify(viewer, body.get("key")):
            return self._json({"error": "Only Admin changes the house holiday decorations."}, 403)
        store = _game_store()
        if store is None:
            return self._json({"error": "The private world save is unavailable."}, 503)
        try:
            room = self._holiday_room(body.get("room"))
            with store.transaction():
                update_settings(store, body)
                result = holiday_state(store, room=room)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        result["can_manage"] = True
        return self._json(result)

    def _verb_call(self, route, method, body, extra=None):
        """Commons runtime helper."""
        port = self.server.server_address[1]
        url = "http://127.0.0.1:%d%s" % (port, route)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                status = r.status
                payload = json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                payload = {"error": "verb failed"}
        except Exception as e:
            return self._json(
                {"error": "verb could not reach the house: %s" % e}, 502)
        payload["route"] = route
        if extra and status < 400:
            payload.update(extra)
        return self._json(payload, status)

    def _verb_get(self, route, query):
        """Commons runtime helper."""
        port = self.server.server_address[1]
        qs = urllib.parse.urlencode({k: v for k, v in (query or {}).items()
                                     if v not in (None, "")})
        url = "http://127.0.0.1:%d%s%s" % (port, route, ("?" + qs) if qs else "")
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                status = r.status
                payload = json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                payload = {"error": "verb failed"}
        except Exception as e:
            return self._json(
                {"error": "verb could not reach the house: %s" % e}, 502)
        if isinstance(payload, dict):
            payload["route"] = route
        return payload, status

    def _verb_read_lines(self, verb, payload):
        """Turn a real payload into sentences somebody can act on. A
        RENDERING, never a source: the untouched payload ships in the same
        response, so a wrong line here is always catchable against the truth
        sitting next to it. Every line that names an id names the verb that
        uses it, because an id you cannot spend is just a number.
        """
        lines = []
        if verb == "garden":
            rows = payload.get("plantings") or []
            if not rows:
                return ["nothing is in the ground here yet -- "
                        "!plant <crop> bed=<name>"]
            for p in rows:
                pid = str(p.get("id") or "?")
                crop = str(p.get("crop") or "something")
                bed = str(p.get("bed") or "")
                who = str(p.get("planted_by") or "")
                stage = str(p.get("stage") or "")
                done = p.get("progress_days")
                need = p.get("needs_days")
                where = (" in " + bed) if bed else ""
                whose = (" (%s put it in)" % who) if who else ""
                if p.get("ripe"):
                    lines.append("%s  %s%s -- RIPE. !pick id=%s%s"
                                 % (pid, crop, where, pid, whose))
                else:
                    when = ""
                    if isinstance(done, int) and isinstance(need, int):
                        when = ", day %d of %d" % (done, need)
                    lines.append("%s  %s%s -- %s%s. !tend id=%s%s"
                                 % (pid, crop, where, stage, when, pid, whose))
            return lines
        if verb == "said":
            if payload.get("closed"):
                return [str(payload.get("why")
                            or "this room keeps its own counsel")]
            spoken = payload.get("spoken") or []
            if not spoken:
                return ["nothing has been said in here yet -- !say <words>"]
            me = str(payload.get("me") or "")
            for ln in spoken[-20:]:
                lid = ln.get("id")
                who = str(ln.get("by") or "someone")
                when = str(ln.get("at") or "")
                said = str(ln.get("text") or "")
                if len(said) > 160:
                    said = said[:157] + "..."

                tail = ("  !unsay id=%s" % lid) if me and _norm(who) == me else ""
                lines.append("[%s] %s (%s): %s%s"
                             % (lid, who, when, said, tail))
            return lines
        if verb == "basket":

            if not payload.get("name"):
                return ["that basket is not yours to read -- sign in first"]
            picked = payload.get("picked") or {}
            if picked:
                lines.append("carrying: " + ", ".join(
                    "%s x%s" % (k, v) for k, v in sorted(picked.items())))
            else:
                lines.append("carrying: nothing yet")
            can = [r for r in (payload.get("recipes") or []) if r.get("can")]
            if can:
                for r in can:
                    lines.append("could make: %s -- !craft %s"
                                 % (r.get("preview") or r.get("makes")
                                    or r.get("key"), r.get("key")))
            else:
                lines.append("could make: nothing standing here right now")
            made = payload.get("made") or []
            if made:
                lines.append("made lately: %d thing(s) on the shelf" % len(made))
            return lines
        return lines

    def _verb_look(self, room):
        """Read a room in text -- who's standing there, what's sitting
        there. Deliberately read-only and deliberately reads the SAME live
        files /api/presence and /api/placeables already serve, rather than
        opening a third copy of that logic that could drift from the other
        two.
        """
        rooms = _verb_rooms()
        rid = _verb_room_id(room)
        presence = _read_json(HERE_FILE, {}) or {}
        people = sorted(name for name, row in presence.items()
                        if _verb_room_id((row or {}).get("room")) == rid)
        things = [p.get("name") or p.get("id") or "something"
                  for p in (_read_json(PLACE_FILE, []) or [])
                  if _verb_room_id(p.get("room")) == rid]
        return {"room": rid or room, "known_room": rid in rooms,
                "room_name": rooms.get(rid, rid), "people_here": people,
                "things_here": things}

    def _do_verb(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        key = d.get("key")
        if not _verify(by, key):
            return self._json({"error": "that name is signed on another device"}, 403)

        room = str(d.get("room") or "").strip()
        text = str(d.get("text") or "").strip()[:400]
        verb, kwargs, rest = _parse_verb_line(text)
        if verb is None:
            return self._json({"error": "say a verb, like !help or !look"}, 400)

        if not kwargs.get("password") and d.get("password"):
            kwargs["password"] = str(d.get("password"))[:64]

        if verb in ("help", "verbs"):
            return self._json(_verb_help_table())
        if verb == "look":
            return self._json(self._verb_look(room))

        read = VERB_READS.get(verb)
        if read is not None:
            have = {"by": by, "key": key, "room": room}
            q = {}
            for field, src in read["query"].items():
                q[field] = kwargs.get(field, have.get(src, ""))
            got = self._verb_get(read["route"], q)
            if not isinstance(got, tuple):
                return got   
            payload, status = got
            if isinstance(payload, dict) and status == 200:

                for own, safe in (read.get("preserve") or {}).items():
                    if own in payload:
                        payload[safe] = payload.pop(own)
                payload["lines"] = self._verb_read_lines(verb, payload)
            return self._json(payload, status)

        rooms = _verb_rooms()
        rid = _verb_room_id(verb)
        if rid in rooms:
            body = {"by": by, "key": key, "room": rid,
                    "x": kwargs.get("x", 0), "y": kwargs.get("y", 0),
                    "facing": kwargs.get("facing", "down")}

            if kwargs.get("password"):
                body["password"] = kwargs["password"]
            if kwargs.get("thought"):
                body["thought"] = kwargs["thought"]
            return self._verb_call("/api/here", "POST", body,
                                   extra={"moved_to": rid})

        if verb in VERB_EMOTES:
            action = VERB_EMOTES[verb]
            phrase = ("*%s* %s" % (action, rest)).strip() if rest else "*%s*" % action
            return self._verb_call(
                "/api/say", "POST", {"by": by, "key": key, "room": room,
                                     "text": phrase})

        spec = VERB_REGISTRY.get(verb)
        if spec is None:
            return self._json(
                {"error": "no such verb: !" + verb, "try": "!help"}, 404)

        body = {"by": by, "key": key, "room": room}

        if kwargs.get("password"):
            body["password"] = kwargs["password"]
        for f in spec["kwargs"]:
            if f in kwargs:
                body[f] = kwargs[f]
        pos = spec["positional"]
        if pos and rest and pos not in body:
            body[pos] = rest

        body.update(spec.get("force") or {})
        return self._verb_call(spec["route"], "POST", body)

    def _do_plant(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        _sweep_seasonal()
        room = str(d.get("room") or "").strip()[:40]
        bed = str(d.get("bed") or "").strip()[:40]
        if not _room_open_for(room, by, d.get("key"), d.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)
        crop = str(d.get("crop") or "").strip()[:40]
        cat = _grow_catalog()
        if crop not in (cat.get("crops") or {}):
            return self._json({"error": "no such crop",
                               "crops": sorted((cat.get("crops") or {}).keys())}, 400)
        allowed = (cat.get("beds") or {}).get(bed)
        if allowed is not None and crop not in allowed:
            return self._json({"error": crop + " does not go in " + bed,
                               "allowed": allowed}, 400)
        try:
            x = round(float(d.get("x", 0)), 2)
            y = round(float(d.get("y", 0)), 2)
        except (TypeError, ValueError):
            return self._json({"error": "bad position"}, 400)
        if not all(math.isfinite(v) for v in (x, y)):  
            return self._json({"error": "bad position"}, 400)

        with _GROW_LOCK:
            rows = _read_json(GROW_FILE, [])
            if len(rows) >= GROW_MAX:
                return self._json({"error": "the garden is full"}, 507)
            row = {
                "id": uuid.uuid4().hex[:10],
                "room": room, "bed": bed, "crop": crop,
                "x": x, "y": y,
                "planted_by": by,
                "planted_on": datetime.now().strftime("%Y-%m-%d"),
                "planted_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "tended": [],
                "note": str(d.get("note") or "")[:PLACE_NOTE_MAX],
            }
            rows.append(row)
            _write_json(GROW_FILE, rows)

        return self._json({"ok": True, "planting": _grow_state(row, cat),
                           "count": len(rows)})

    def _do_tend(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        pid = str(d.get("id") or "").strip()[:20]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        _sweep_seasonal()
        today = datetime.now().strftime("%Y-%m-%d")
        with _GROW_LOCK:
            rows = _read_json(GROW_FILE, [])
            hit = None
            for r in rows:
                if str(r.get("id") or "") == pid:
                    hit = r
                    break
            if hit is None:
                return self._json({"error": "nothing planted there"}, 404)
            tended = hit.get("tended") or []
            already = any(str(t.get("by") or "") == by
                          and str(t.get("on") or "") == today for t in tended)
            if not already:
                tended.append({"by": by, "on": today})
                hit["tended"] = tended
                _write_json(GROW_FILE, rows)
        state = _grow_state(hit)
        state["already_today"] = already
        return self._json({"ok": True, "planting": state})

    def _do_harvest(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        pid = str(d.get("id") or "").strip()[:20]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        _sweep_seasonal()
        today = datetime.now().strftime("%Y-%m-%d")
        with _GROW_LOCK:
            rows = _read_json(GROW_FILE, [])
            hit = None
            for r in rows:
                if str(r.get("id") or "") == pid:
                    hit = r
                    break
            if hit is None:
                return self._json({"error": "nothing planted there"}, 404)
            state = _grow_state(hit)
            if not state.get("ripe"):
                return self._json({
                    "error": "not ready yet",
                    "planting": state,
                    "days_left": max(0, int(state.get("needs_days", 0))
                                     - int(state.get("progress_days", 0)))}, 409)
            picks = hit.get("harvested") or []
            already = any(str(h.get("by") or "") == by
                          and str(h.get("on") or "") == today for h in picks)
            if not already:
                picks.append({"by": by, "on": today})
                hit["harvested"] = picks
                _write_json(GROW_FILE, rows)
        crop = str(hit.get("crop") or "")
        if not already:
            with _BASKET_LOCK:
                store = _basket_all()
                row = _basket_row(store, by)
                row["picked"][crop] = int(row["picked"].get(crop, 0)) + 1
                _write_json(BASKET_FILE, store)
        state = _grow_state(hit)
        state["already_today"] = already
        return self._json({"ok": True, "planting": state, "crop": crop,
                           "already_today": already,
                           "basket": self._basket_view(by)})

    def _do_howl(self):
        """Lift your head.

        It is never refused and it never fails. What changes is who hears it:
        your own room on an ordinary night, the entire house under a full
        moon. And if somebody already has one standing that reaches you, yours
        joins theirs instead of starting a second - because the whole point of
        a howl is that it gets answered.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        raw_words = str(d.get("words") or "").strip()
        words = raw_words[:140]
        clipped = len(raw_words) > len(words)
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        moon = _moon()
        now = time.time()
        me = _norm(by)

        with _HOWL_LOCK:
            book = _howl_book()
            rows = _howl_rows(book)
            live = _howl_live(rows, now)
            joining = live is not None and _howl_carries(live, room, moon)

            voice = {"by": by, "room": room, "words": words,
                     "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "ts": now}

            if joining:
                voices = live.get("voices")
                if not isinstance(voices, list):
                    voices = []
                already = any(_norm((v or {}).get("by")) == me
                              for v in voices if isinstance(v, dict))
                if not already:
                    voices.append(voice)
                live["voices"] = voices
                chorus = live
            else:
                chorus = {
                    "room": room,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ts": now,
                    "moon": moon,
                    "voices": [voice],
                }
                rows.append(chorus)
                already = False

            book["howls"] = rows
            _write_json(HOWL_FILE, book)

            names = [v.get("by") for v in (chorus.get("voices") or [])
                     if isinstance(v, dict)]

        others = [n for n in names if _norm(n) != me]
        if already:
            said = "You are already in this one. Listen."
        elif joining and others:
            said = "You put your voice in with " + _and_list(others) + "."
        elif moon.get("is_full"):
            said = ("You lift your head and let it go - and under a full moon "
                    "it carries through every room in the house.")
        else:
            said = ("You lift your head and let it go. It carries as far as "
                    "this room.")
        if clipped:
            said += " (Your words ran past 140 and I kept the first 140.)"

        return self._json({"ok": True, "joined": bool(joining and not already),
                           "already": bool(already), "moon": moon,
                           "voices": names, "room": room,
                           "chorus_seconds": HOWL_CHORUS_SECONDS,
                           "said": said})

    def _do_loose(self):
        """Shoot one end -- three arrows -- at the butts.

        THERE IS NO DAILY LIMIT AND THAT IS DELIBERATE. The river caps casts at
        six a day because a river can be stripped; a straw butt cannot be. Every
        arrow is walked down and pulled back out. So you may shoot until your
        arms ache, and what is rationed instead is how fast the HAND gets good.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        butts = _room_range(room)
        if not butts:

            return self._json({"error": "there is nothing to shoot at here"}, 409)

        today = datetime.now().strftime("%Y-%m-%d")
        me = _norm(by)
        rng = random.Random()

        with _ARCHERY_LOCK:
            book = _archery_book()
            ends = _archery_ends(book)
            steady_before = _steadiness(book, by)

            arrows = []
            score = 0
            for _ in range(3):
                name, pts, how = _loose_one(steady_before, rng)
                arrows.append({"ring": name, "points": pts, "how": how})
                score += pts

            gained_today = _gain_today(book, by, today)
            room_to_grow = max(0.0, ARCHERY_GAIN_PER_DAY - gained_today)
            gain = min(ARCHERY_GAIN_PER_END, room_to_grow)
            steady_after = max(0.0, min(1.0, steady_before + gain))

            prior_best = _best_end(book)
            prior_top = int((prior_best or {}).get("score") or -1)

            row = {
                "by": by,
                "room": room,
                "day": today,
                "at": datetime.now().isoformat(timespec="seconds"),
                "arrows": arrows,
                "score": score,
                "distance": _range_distance(room),
                "steadiness": round(steady_after, 4),
            }
            ends.append(row)

            book["ends"] = ends

            st = book.get("steadiness")
            if not isinstance(st, dict):
                st = {}
            st[me] = round(steady_after, 4)
            book["steadiness"] = st

            g = book.get("gained")
            if not isinstance(g, dict):
                g = {}
            g[me] = {"day": today, "amount": round(gained_today + gain, 4)}
            book["gained"] = g

            _write_json(ARCHERY_FILE, book)

        perfect = (score == 27)
        best_now = score > prior_top
        return self._json({
            "ok": True,
            "butts": butts,
            "arrows": arrows,
            "score": score,
            "out_of": 27,
            "perfect": perfect,
            "best_on_this_range": best_now,
            "beat": (prior_best or {}).get("by") if best_now and prior_top >= 0 else None,
            "steadiness": round(steady_after, 4),
            "steadiness_gain": round(gain, 4),
            "capped_for_today": gain <= 0.0,
            "shots_are_never_limited": True,
        })

    def _range_view(self, room, name=""):
        butts = _room_range(room)
        if not butts:
            return {"has_range": False}
        book = _archery_book()
        ends = _archery_ends(book)
        mine = [e for e in ends if _norm(e.get("by")) == _norm(name)] if name else []
        best = _best_end(book)
        my_best = None
        for e in mine:
            if my_best is None or int(e.get("score") or 0) > int(my_best.get("score") or 0):
                my_best = e
        recent = []
        for e in ends[-8:][::-1]:
            recent.append({"by": e.get("by"), "score": e.get("score"),
                           "day": e.get("day")})
        return {
            "has_range": True,
            "butts": butts,
            "distance": _range_distance(room),
            "steadiness": round(_steadiness(book, name), 4) if name else 0.0,
            "ends_shot": len(mine),
            "my_best": (my_best or {}).get("score"),
            "house_best": (best or {}).get("score"),
            "house_best_by": (best or {}).get("by"),
            "house_best_day": (best or {}).get("day"),
            "recent": recent,
            "no_blood": True,
            "no_daily_limit": True,
        }

    def _do_cast(self):
        """Put a line in the water. Six casts a person a day.

        The daily count is per person and NOT per house, same as watering: if
        the whole pack walks down to the river on a Sunday, everybody fishes.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        water = _room_water(room)
        if not water:
            return self._json({"error": "there is no water here"}, 409)

        cat = _fish_catalog()
        try:
            per_day = max(1, int(cat.get("casts_per_day") or 6))
        except (TypeError, ValueError):
            per_day = 6
        today = datetime.now().strftime("%Y-%m-%d")
        me = _norm(by)

        with _RIVER_LOCK:
            book = _river_book()
            rows = _river_rows(book)

            held = None
            for r in rows:
                if (_norm(r.get("by")) == me
                        and str(r.get("state") or "") == "held"):
                    held = r
                    break
            if held is not None:
                return self._json({"ok": True, "already_on_the_line": True,
                                   "catch": held, "water": water,
                                   "casts_left": max(0, per_day - sum(
                                       1 for r in rows
                                       if _norm(r.get("by")) == me
                                       and str(r.get("on") or "") == today))})
            used = sum(1 for r in rows
                       if _norm(r.get("by")) == me
                       and str(r.get("on") or "") == today)
            if used >= per_day:
                return self._json({
                    "error": "that is the last of today's casting",
                    "casts_left": 0, "water": water}, 409)

            got = _roll_catch(water, cat, book)
            if not got:
                return self._json({"error": "nothing lives in that water yet"}, 409)
            row = dict(got)
            row.update({
                "id": "c" + uuid.uuid4().hex[:10],
                "by": by, "on": today,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "room": room, "water": water, "state": "held",
            })
            rows.append(row)
            if len(rows) > CATCH_MAX:
                rows = rows[-CATCH_MAX:]
            book["casts"] = rows
            _write_json(CATCH_FILE, book)

        return self._json({"ok": True, "catch": row, "water": water,
                           "casts_left": max(0, per_day - (used + 1))})

    def _do_forage(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        ground = _room_ground(room)
        if not ground:
            return self._json({"error": "there is nothing to gather here"}, 409)

        cat = _forage_catalog()
        try:
            per_day = max(1, int(cat.get("gathers_per_day") or 6))
        except (TypeError, ValueError):
            per_day = 6
        today = datetime.now().strftime("%Y-%m-%d")

        got = _roll_find(ground, cat)
        if not got:
            return self._json({
                "error": "nothing is out on this ground at this time of year",
                "ground": ground, "out_of_season": True}, 409)
        gear_bonus = int((_equipment_summary(
            _basket_row(_basket_all(), by)).get("bonuses") or {}).get("gathering", 0))
        if gear_bonus > 0:
            got["handful"] = int(got["handful"]) + gear_bonus
            got["equipment_bonus"] = gear_bonus

        with _BASKET_LOCK:
            store = _basket_all()
            row = _basket_row(store, by)
            walks = row.get("walks")
            if not isinstance(walks, dict):
                walks = {}
            try:
                used = max(0, int(walks.get(today, 0)))
            except (TypeError, ValueError):
                used = 0
            if used >= per_day:
                return self._json({
                    "error": "that is the last of today's gathering",
                    "gathers_left": 0, "ground": ground}, 409)
            name = str(got["find"])
            row["picked"][name] = int(row["picked"].get(name, 0)) + int(got["handful"])
            walks[today] = used + 1

            row["walks"] = {k: v for k, v in walks.items() if k >= today[:8] + "01"}
            _write_json(BASKET_FILE, store)

        return self._json({"ok": True, "found": got, "ground": ground,
                           "gathers_left": max(0, per_day - (used + 1)),
                           "basket": self._basket_view(by, room)})

    def _do_hunt(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        quarry = str(d.get("quarry") or d.get("animal") or "").strip().lower()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        ground = _room_hunting(room)
        if not ground:
            return self._json({"error": "this is not hunting ground"}, 409)

        cat = _hunting_catalog()
        roster = (cat.get("grounds") or {}).get(ground) or []
        quarry_data = cat.get("quarry") or {}
        if not quarry:
            return self._json({"error": "which wild game?", "here": roster}, 400)
        if quarry not in roster or quarry not in quarry_data:
            return self._json({"error": "no %s is quarry on %s" % (quarry, ground),
                               "here": roster}, 409)
        rec = quarry_data.get(quarry) or {}
        try:
            per_day = max(1, int(cat.get("hunts_per_day") or 4))
        except (TypeError, ValueError):
            per_day = 4
        try:
            difficulty = max(2, min(20, int(rec.get("difficulty") or 10)))
        except (TypeError, ValueError):
            difficulty = 10
        today = datetime.now().strftime("%Y-%m-%d")
        me = _norm(by)

        with _HUNT_LOCK, _BASKET_LOCK:
            book = _hunt_book()
            used = _hunts_used(book, by, today)
            if used >= per_day:
                return self._json({"error": "that is the last of today's hunting",
                                   "hunts_left": 0, "ground": ground}, 409)

            basket = _basket_all()
            brow = _basket_row(basket, by)
            gear = _equipment_summary(brow)
            bonus = int((gear.get("bonuses") or {}).get("hunting", 0))
            roll = random.randint(1, 20)
            total = roll + bonus
            critical = roll == 20
            fumble = roll == 1
            success = (total >= difficulty or critical) and not fumble
            loot = _roll_hunt_loot(rec) if success else {}

            days = book["days"].setdefault(me, {})
            days[today] = used + 1
            book["days"][me] = {k: v for k, v in days.items()
                                if k >= today[:8] + "01"}
            row = {
                "id": "h" + uuid.uuid4().hex[:10],
                "by": by, "on": today,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "room": room, "ground": ground, "quarry": quarry,
                "roll": roll, "bonus": bonus, "total": total,
                "difficulty": difficulty, "success": success, "loot": loot,
            }
            book["hunts"].append(row)
            if len(book["hunts"]) > HUNTS_MAX:
                book["hunts"] = book["hunts"][-HUNTS_MAX:]
            _write_json(HUNTS_FILE, book)

            if success:
                for item, count in loot.items():
                    brow["picked"][item] = int(brow["picked"].get(item, 0)) + int(count)
                _write_json(BASKET_FILE, basket)

        left = max(0, per_day - (used + 1))
        if success:
            return self._json({"ok": True, "success": True, "hunt": row,
                               "loot": loot, "roll": roll, "bonus": bonus,
                               "total": total, "difficulty": difficulty,
                               "critical": critical, "hunts_left": left,
                               "line": rec.get("success") or "The hunt is clean.",
                               "basket": self._basket_view(by, room)})
        escapes = rec.get("escapes") or ["The %s breaks cover and gets clear." % quarry]
        return self._json({"ok": True, "success": False, "hunt": row,
                           "roll": roll, "bonus": bonus, "total": total,
                           "difficulty": difficulty, "fumble": fumble,
                           "hunts_left": left, "escaped": str(random.choice(escapes))})

    def _do_approach(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        animal = str(d.get("animal") or "").strip().lower()[:40]
        words = str(d.get("words") or "").strip()[:2000]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        wilds = _room_wilds(room)
        if not wilds:
            return self._json({"error": "there is nothing wild to meet here"}, 409)

        cat = _wilds_catalog()
        roster = (cat.get("wilds") or {}).get(wilds) or []
        animals = cat.get("animals") or {}
        if not animal:
            return self._json({"error": "which animal?", "here": roster}, 400)
        if animal not in roster or animal not in animals:
            return self._json({"error": "no %s lives in %s" % (animal, wilds),
                               "here": roster}, 409)
        rec = animals.get(animal) or {}

        try:
            per_day = max(1, int(cat.get("approaches_per_day") or 4))
        except (TypeError, ValueError):
            per_day = 4
        today = datetime.now().strftime("%Y-%m-%d")
        me = _norm(by)

        with _WILDS_LOCK:
            book = _companions_book()
            used = _approaches_used(book, by, today)
            if used >= per_day:
                return self._json({
                    "error": "that is the last of today's approaching",
                    "approaches_left": 0, "wilds": wilds}, 409)

            try:
                difficulty = max(2, int(rec.get("difficulty") or 10))
            except (TypeError, ValueError):
                difficulty = 10

            band = _time_band()
            when = str(rec.get("when") or "any")
            wary = when not in ("any", band)
            if wary:
                difficulty += 2

            gear_bonus = int((_equipment_summary(
                _basket_row(_basket_all(), by)).get("bonuses") or {}).get("taming", 0))
            bonus = _approach_bonus(words) + gear_bonus
            roll = random.randint(1, 20)
            total = roll + bonus
            is_crit = (roll == 20)     
            is_fumble = (roll == 1)    
            won = (total >= difficulty or is_crit) and not is_fumble

            walks = book["walks"].setdefault(me, {})
            walks[today] = used + 1

            book["walks"][me] = {k: v for k, v in walks.items()
                                 if k >= today[:8] + "01"}

            row = None
            if won:
                row = {
                    "id": "t" + uuid.uuid4().hex[:10],
                    "owner": by,
                    "animal": animal,
                    "given_name": None,
                    "following": False,
                    "tamed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "room": room,
                    "wilds": wilds,
                    "approach_text": words,
                    "loyalty": int(rec.get("starting_loyalty") or 50),
                    "personality": str(rec.get("personality") or ""),
                    "rarity": str(rec.get("rarity") or "common"),
                    "colour": rec.get("colour") or "#6b7280",
                }
                rows = book["companions"]
                rows.append(row)
                if len(rows) > COMPANIONS_MAX:
                    rows = rows[-COMPANIONS_MAX:]
                book["companions"] = rows
            _write_json(COMPANIONS_FILE, book)

        left = max(0, per_day - (used + 1))
        if won:
            return self._json({
                "ok": True, "tamed": True, "companion": row,
                "roll": roll, "bonus": bonus, "total": total,
                "difficulty": difficulty, "critical": is_crit,
                "approaches_left": left,
                "message": ("The %s comes to you. It heard something in the "
                            "way you asked." % animal)})
        slips = rec.get("slips") or ["The %s slips away into the wood." % animal]
        return self._json({
            "ok": True, "tamed": False,
            "roll": roll, "bonus": bonus, "total": total,
            "difficulty": difficulty, "fumble": is_fumble,
            "approaches_left": left,
            "slipped": str(random.choice(slips)),
            "message": ("Not this time. Nothing is hurt and nothing is "
                        "lost but the go - the wood keeps its own counsel.")})

    def _do_name_companion(self):
        """Give a tamed animal its name. Owner-only, and once given a name
        is not overwritten - a renaming is a bigger conversation than a
        route, and until someone has it the record honestly says null."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        cid = str(d.get("id") or "").strip()[:24]
        name = str(d.get("name") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        if not cid or not name:
            return self._json({"error": "need the companion's id and a name"}, 400)

        with _WILDS_LOCK:
            book = _companions_book()
            row = None
            for r in book["companions"]:
                if str(r.get("id") or "") == cid:
                    row = r
                    break
            if row is None:
                return self._json({"error": "no companion by that id"}, 404)
            if _norm(row.get("owner")) != _norm(by):
                return self._json({"error": "that animal came to someone else"}, 403)
            if row.get("given_name"):
                return self._json({"error": "it already has its name",
                                   "given_name": row["given_name"]}, 409)
            row["given_name"] = name
            row["named_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _write_json(COMPANIONS_FILE, book)

        return self._json({"ok": True, "companion": row,
                           "message": "%s. It answers to that now." % name})

    def _do_follow_companion(self):
        """Choose the one named animal that walks with its person.

        The choice is owner-only and persisted in the same companion book as
        the taming story.  Selecting one companion clears the owner's previous
        follower; asking this one to rest clears it without removing anything
        from the family.  A companion needs its name before it can be chosen --
        following is relationship, not an anonymous inventory slot.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        cid = str(d.get("id") or "").strip()[:24]
        follow = d.get("follow") is not False
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        if not cid:
            return self._json({"error": "which companion?"}, 400)

        with _WILDS_LOCK:
            book = _companions_book()
            row = next((r for r in book["companions"]
                        if str(r.get("id") or "") == cid), None)
            if row is None:
                return self._json({"error": "no companion by that id"}, 404)
            if _norm(row.get("owner")) != _norm(by):
                return self._json({"error": "that animal came to someone else"}, 403)
            if follow and not row.get("given_name"):
                return self._json({"error": "give it its name first"}, 409)

            for companion in book["companions"]:
                if _norm(companion.get("owner")) == _norm(by):
                    companion["following"] = bool(follow and companion is row)
            _write_json(COMPANIONS_FILE, book)

        name = row.get("given_name") or ("the " + str(row.get("animal") or "animal"))
        return self._json({
            "ok": True,
            "companion": row,
            "following": bool(row.get("following")),
            "message": ("%s falls into step beside you." % name
                        if row.get("following") else
                        "%s settles somewhere comfortable to rest." % name),
        })

    def _do_rook(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1024:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        try:
            beat = int(d.get("beat"))
        except (TypeError, ValueError):
            return self._json({"error": "bad beat"}, 400)
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        now = _rook_beat_now()
        if beat < now - 2 or beat > now + 1:
            return self._json({"error": "that beat has flown", "beat": beat,
                               "now": now}, 409)

        s = _rook_at(beat)
        if not s:
            return self._json({"error": "no catalog"}, 503)
        if not s["gift"]:
            return self._json({"error": "he did not drop anything then"}, 409)
        if _norm(s["room"]) != _norm(room):
            return self._json({"error": "he was not in that room",
                               "he_was_in": s["room"]}, 409)

        with _ROOK_LOCK:
            book = _read_json(ROOK_FILE, {}) or {}
            given = book.get("given")
            if not isinstance(given, list):
                given = []
            for row in given:
                try:
                    if int(row.get("beat", -1)) == beat:
                        return self._json({"error": "somebody already has it",
                                           "taken_by": row.get("name")}, 409)
                except (TypeError, ValueError):
                    continue
            row = {"beat": beat, "to": _norm(by), "name": by,
                   "room": s["room"], "shiny": s["shiny"],
                   "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            given.append(row)
            book["given"] = given[-400:]
            _write_json(ROOK_FILE, book)

        with _BASKET_LOCK:
            store = _basket_all()
            b = _basket_row(store, by)
            b["picked"][s["shiny"]] = int(b["picked"].get(s["shiny"], 0)) + 1
            _write_json(BASKET_FILE, store)

        cat = _shiny_catalog()
        rec = (cat.get("shinies") or {}).get(s["shiny"]) or {}
        return self._json({"ok": True, "shiny": s["shiny"],
                           "line": rec.get("line") or "",
                           "colour": rec.get("colour") or "#9d97b8",
                           "beat": beat})

    def _do_creel(self):
        """The choice. keep -> your basket. let it go -> the river's book.

        THE RELEASE IS NOT THE EMPTY OPTION. It is recorded under your name
        forever and it raises that species' top size for whoever casts next.
        Nothing in this house gets used up, and here that law has teeth: the
        generous move is the one that leaves the water better.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        cid = str(d.get("id") or "").strip()[:20]
        keep = bool(d.get("keep"))
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        me = _norm(by)
        with _RIVER_LOCK:
            book = _river_book()
            rows = _river_rows(book)
            hit = None
            for r in rows:
                if str(r.get("id") or "") == cid:
                    hit = r
                    break
            if hit is None:
                return self._json({"error": "nothing on that line"}, 404)
            if _norm(hit.get("by")) != me:
                
                return self._json({"error": "that is not your line"}, 403)
            if str(hit.get("state") or "") != "held":
                return self._json({"error": "you already decided that one",
                                   "catch": hit}, 409)
            hit["state"] = "kept" if keep else "released"
            hit["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if not keep:
                rel = book.get("released")
                if not isinstance(rel, dict):
                    rel = {}
                sp = str(hit.get("species") or "")
                rel[sp] = int(rel.get(sp, 0)) + 1
                book["released"] = rel
            book["casts"] = rows
            _write_json(CATCH_FILE, book)

        if keep:
            with _BASKET_LOCK:
                store = _basket_all()
                brow = _basket_row(store, by)
                sp = str(hit.get("species") or "")
                brow["picked"][sp] = int(brow["picked"].get(sp, 0)) + 1
                _write_json(BASKET_FILE, store)

        cat = _fish_catalog()
        _lo, hi, bonus = _top_lb(cat, str(hit.get("species") or ""), _river_book())
        return self._json({"ok": True, "kept": keep, "catch": hit,
                           "now_runs_to": round(hi, 2),
                           "grown_by": round(bonus, 3),
                           "basket": self._basket_view(by,
                                                       str(hit.get("room") or ""))})

    def _basket_view(self, name, room=""):
        """One person's basket, plus what they could make standing here."""
        cat = _grow_catalog()
        row = _basket_row(_basket_all(), name)
        picked = row.get("picked") or {}
        out = []
        for key, rec in sorted(_recipes().items()):
            if not _recipe_available_here(rec, room=room):
                continue
            ok, why = _can_make(picked, rec, room, cat)

            makes = str(rec.get("makes") or key)
            head = ""
            if ok:
                try:
                    _, head = _spend(dict(picked), rec, cat)
                except ValueError:
                    head = ""

            preview = makes.replace("{crop}", head) if head else key
            out.append({"key": key, "makes": makes, "preview": preview.strip(),
                        "needs": rec.get("needs") or {},
                        "where": rec.get("where") or "",
                        "slot": rec.get("slot") or "",
                        "bonuses": rec.get("bonuses") or {},
                        "can": ok, "why": why})
        equipment = _equipment_summary(row)
        return {"name": row.get("name") or name, "picked": picked,
                "people": _addressable(),
                "by_category": _picked_by_category(picked, cat),
                "made": [_made_view(m) for m in
                         list(reversed(row.get("made") or []))[:40]],
                "equipment": equipment,
                "recipes": out}

    def _do_craft(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        key = str(d.get("recipe") or "").strip()[:40]
        room = str(d.get("room") or "").strip()[:40]
        rec = _recipes().get(key)
        if not rec:
            return self._json({"error": "no such recipe",
                               "recipes": sorted(_recipes().keys())}, 400)

        if _game_store():  
            actual = next((p.get("room", "hearth") for name, p in _read_json(HERE_FILE, {}).items()
                           if _norm(name) == _norm(by)), "hearth")
            if room and room != actual:
                return self._json({"error": "enter that room before crafting there"}, 409)
            room = actual
        gift = str(d.get("for") or "").strip()[:40]
        if gift and _norm(gift) not in _addressable():
            return self._json({"error": "nobody here by that name",
                               "people": _addressable()}, 400)

        cat = _grow_catalog()
        with _BASKET_LOCK:
            store = _basket_all()
            mine = _basket_row(store, by)
            ok, why = _can_make(mine.get("picked") or {}, rec, room, cat)
            if not ok:
                return self._json({"error": why,
                                   "basket": self._basket_view(by, room)}, 409)
            try:
                spent, head = _spend(mine["picked"], rec, cat)
            except ValueError as e:
                return self._json({"error": str(e)}, 409)
            what = str(rec.get("makes") or key).replace("{crop}", head or "garden")
            item = {
                "id": uuid.uuid4().hex[:10],
                "what": what, "recipe": key,
                "made_by": mine.get("name") or by,
                "made_on": datetime.now().strftime("%Y-%m-%d"),
                "made_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "room": room, "spent": spent,
                "colour": rec.get("colour") or "#8a7fb8",
                "line": rec.get("line") or "",
                "note": str(d.get("note") or "")[:PLACE_NOTE_MAX],
                "shared": [],
            }
            try:
                uses = max(0, min(int(rec.get("uses") or 0), 1000))
            except (TypeError, ValueError):
                uses = 0
            if uses:
                item["uses_total"] = uses
                item["uses_left"] = uses
                item["unit"] = str(rec.get("unit") or "use")[:30]
                item["unit_plural"] = str(rec.get("unit_plural")
                                           or (item["unit"] + "s"))[:30]
                item["use_label"] = str(rec.get("use_label") or "use it")[:60]
            slot = str(rec.get("slot") or "").strip().lower()
            if slot in EQUIPMENT_SLOTS:
                item["slot"] = slot
                item["bonuses"] = dict(rec.get("bonuses") or {})
            holder = mine
            if gift and _norm(gift) != _norm(by):
                item["for"] = _addressable().get(_norm(gift)) or gift
                holder = _basket_row(store, gift)
            _shelve(holder, item)
            _write_json(BASKET_FILE, store)
        return self._json({"ok": True, "made": item,
                           "basket": self._basket_view(by, room)})

    def _do_equip(self):
        """Equip one crafted item from your own shelf by stable item id."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)
        by = str(d.get("by") or "").strip()[:40]
        iid = str(d.get("id") or "").strip()[:24]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        with _BASKET_LOCK:
            store = _basket_all()
            row = _basket_row(store, by)
            item = next((m for m in row.get("made", [])
                         if str(m.get("id") or "") == iid), None)
            if not item:
                return self._json({"error": "that is not on your shelf"}, 404)
            slot = item_slot(_item_definition(item))
            if slot not in EQUIPMENT_SLOTS:
                return self._json({"error": "that is not equipment"}, 409)
            row["equipment"][slot] = iid
            _write_json(BASKET_FILE, store)
            summary = _equipment_summary(row)
        return self._json({"ok": True, "equipped": item, "equipment": summary})

    def _do_unequip(self):
        """Clear one equipment slot. The made thing stays on its shelf."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)
        by = str(d.get("by") or "").strip()[:40]
        slot = str(d.get("slot") or "").strip().lower()[:20]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        if slot not in EQUIPMENT_SLOTS:
            return self._json({"error": "choose a valid equipment slot"}, 400)
        with _BASKET_LOCK:
            store = _basket_all()
            row = _basket_row(store, by)
            row["equipment"][slot] = None
            _write_json(BASKET_FILE, store)
            summary = _equipment_summary(row)
        return self._json({"ok": True, "slot": slot, "equipment": summary})

    def _do_share(self):
        """Use one serving and remember whose hands took it.

        The made thing remains as history when it is finished; its finite
        servings do not quietly refill and the ninth hand cannot take an
        eighth-slice pie.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        iid = str(d.get("id") or "").strip()[:20]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        today = datetime.now().strftime("%Y-%m-%d")
        with _BASKET_LOCK:
            store = _basket_all()
            hit = None
            holder = None
            for row in store.values():
                for it in (row.get("made") or []):
                    if str(it.get("id") or "") == iid:
                        hit = it
                        holder = row
                        break
                if hit:
                    break
            if hit is None:
                return self._json({"error": "no such thing"}, 404)
            view = _made_view(hit)
            total = int(view.get("uses_total") or 0)
            if not total:
                return self._json({"error": "that is for placing, not using"}, 409)
            if _norm((holder or {}).get("name")) != _norm(by) and not hit.get("placed"):
                return self._json({"error": "it has not been set out to share"}, 403)
            left = int(view.get("uses_left") or 0)
            if left <= 0:
                return self._json({"error": "there is none left", "made": view}, 409)
            shared = hit.get("shared") or []
            shared.append({"by": by, "on": today,
                           "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
            hit["shared"] = shared
            hit["uses_total"] = total
            hit["uses_left"] = left - 1
            hit["unit"] = view.get("unit") or "use"
            hit["unit_plural"] = view.get("unit_plural") or (hit["unit"] + "s")
            hit["use_label"] = view.get("use_label") or "use it"
            _write_json(BASKET_FILE, store)
        return self._json({"ok": True, "made": _made_view(hit)})

    def _do_place_made(self):
        """Set a made thing down in the shared room. Owner only."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)
        by = str(d.get("by") or "").strip()[:40]
        iid = str(d.get("id") or "").strip()[:24]
        room = str(d.get("room") or "").strip()[:40]
        if not by or not room:
            return self._json({"error": "sign your name and choose a room"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        if not _room_open_for(room, by, d.get("key"), d.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)
        try:
            x = round(float(d.get("x", 0)), 2)
            y = round(float(d.get("y", 0)), 2)
        except (TypeError, ValueError):
            return self._json({"error": "bad position"}, 400)
        if not math.isfinite(x) or not math.isfinite(y):
            return self._json({"error": "bad position"}, 400)
        with _BASKET_LOCK:
            store = _basket_all()
            row = _basket_row(store, by)
            hit = next((m for m in row.get("made", [])
                        if str(m.get("id") or "") == iid), None)
            if hit is None:
                return self._json({"error": "that is not in your basket"}, 404)
            at = datetime.now().strftime("%Y-%m-%d %H:%M")
            hit["placed"] = {"room": room, "x": x, "y": y,
                             "w": 2.25, "h": 1.65, "at": at}
            _write_json(BASKET_FILE, store)
            made = _made_view(hit)
        pings = _deliver_nearby(by, room, "a made thing: " + str(hit.get("what") or "something"))
        return self._json({"ok": True, "made": made, "pings": pings})

    def _refuse_size(self, length, msg="bad size", code=400):
        """Commons runtime helper."""
        try:
            remaining = int(length)
            if 0 < remaining <= 8 * 1024 * 1024:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
        except Exception:
            pass
        return self._json({"error": msg}, code)

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path == "/public-config.js":
            body = ("window.COMMONS_CONFIG=" + json.dumps(PUBLIC_CONFIG) + ";").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
  
        route = self.path.split("?")[0]
        if route == "/api/holidays":
            return self._get_holidays()

        if route.startswith("/uploads/"):
            name = route[len("/uploads/"):]
            if ("/" in name or "\\" in name or name.startswith(".")
                    or not re.fullmatch(r"[0-9A-Za-z._-]{1,80}", name or "")):
                self.send_error(404, "File not found")
                return
            f = UPLOAD_DIR / name
            if not f.is_file():
                self.send_error(404, "File not found")
                return
            raw = f.read_bytes()
            ext = f.suffix.lower().lstrip(".")
            ctype = {"png": "image/png", "jpg": "image/jpeg",
                     "jpeg": "image/jpeg", "gif": "image/gif",
                     "webp": "image/webp"}.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if route == "/api/health":
            return self._json({"ok": True, "service": "hearth"})
        if route in ("/api/editor/rooms", "/api/editor/room"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            viewer = (qs.get("as") or [""])[0].strip()[:40]
            key = (qs.get("key") or [""])[0].strip()[:64]
            if _norm(viewer) != ADMIN_NAME or not _verify(viewer, key):
                return self._json({"error": "this map table belongs to Admin"}, 403)
            if route == "/api/editor/rooms":
                return self._json({"rooms": _editor_rooms()})
            room = (qs.get("room") or [""])[0]
            view = _editor_snapshot(room)
            if view is None:
                return self._json({"error": "no such room"}, 404)
            return self._json(view)
        if route == "/api/pings":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            viewer = (qs.get("as") or [""])[0].strip()[:40]
            key = (qs.get("key") or [""])[0].strip()
            if not viewer or not _verify(viewer, key):
                return self._json({"error": "that name is signed on another device"}, 403)
            consume = (qs.get("consume") or ["1"])[0] not in ("0", "false", "")
            with _PING_LOCK:
                rows = _read_json(_pings_file(), [])
                mine = [r for r in rows if _norm(r.get("who")) == _norm(viewer)]
                if consume and mine:
                    keep = [r for r in rows if _norm(r.get("who")) != _norm(viewer)]
                    _write_json(_pings_file(), keep)
            return self._json({"pings": mine, "consumed": bool(consume and mine)})

        if route == "/api/notes":

            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            viewer = asker if (asker and _verify(asker, akey)) else ""
            _sweep_seasonal()
            return self._json({"notes": _visible_notes(viewer),
                               "people": _addressable(), "me": _norm(viewer)})
        if route == "/api/desk":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            viewer = _norm(asker) if (asker and _verify(asker, akey)) else ""
            try:
                limit = max(1, min(6, int((qs.get("n") or ["3"])[0])))
            except Exception:
                limit = 3
            desks = []
            for who in sorted(DESK_SECTIONS):
                posts = _desk_posts(who, limit)
                if not posts:
                    continue
                desks.append({"who": who.title(),
                              "section": DESK_SECTIONS[who],
                              "posts": posts})
            return self._json({"desks": desks,
                               "me": viewer,
                               "mine": len(_draft_list(viewer)) if viewer else 0,
                               "can_draft": bool(viewer) and _drafts_dir(viewer) is not None})
        if route == "/api/drafts":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            if not asker or not _verify(asker, akey):
                return self._json({"error": "sign your name first"}, 403)
            me = _norm(asker)
            d = _drafts_dir(me)
            if d is None:
                return self._json({"error": "no drafts folder for that name",
                                   "drafts": []}, 404)
            want = (qs.get("read") or [""])[0][:120]
            if want:

                bad = ("/" in want or "\\" in want or want.startswith(".")
                       or ":" in want or not want.strip())
                if bad:
                    return self._json({"error": "that is not a draft name"}, 400)
                f = d / want
                safe = want
                try:
                    if not f.is_file():
                        return self._json({"error": "no draft by that name"}, 404)
                    return self._json({"name": safe,
                                       "text": f.read_text(encoding="utf-8",
                                                           errors="replace")[:40000]})
                except Exception as e:
                    return self._json({"error": "%s" % type(e).__name__}, 500)
            return self._json({"drafts": _draft_list(me), "me": me,
                               "where": str(d)})
        if route == "/api/knocks":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            who = _norm((qs.get("who") or [""])[0][:40])
            rows = _read_json(KNOCK_FILE, {}).get("knocks", [])
            if who:
                rows = [k for k in rows
                        if _norm(k.get("who", "")) == who
                        or _norm(k.get("by", "")) == who]
            return self._json({"knocks": rows[-50:],
                               "cooldown": KNOCK_COOLDOWN_S})
        if route == "/api/chat":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            room = (qs.get("room") or [""])[0][:40]
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            viewer = asker if (asker and _verify(asker, akey)) else ""
            f = _chat_file(room)
            if f is None:
                return self._json({"error": "no such room"}, 404)
            if not _may_read_chat(room, viewer):
                return self._json({"lines": [], "closed": True,
                                   "why": "this room keeps its own counsel"})
            lines = _read_json(f, {}).get("lines", [])

            lines = [dict(line, display_text=(line.get("display_text") or
                                               _render_mentions(line.get("text"))))
                     for line in lines]
            return self._json({"lines": lines, "me": _norm(viewer),
                               "mentions": _mention_choices(viewer),
                               "ttl": BUBBLE_TTL})
        if route == "/api/archive":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            viewer = asker if (asker and _verify(asker, akey)) else ""
            room = (qs.get("room") or [""])[0][:40]
            kind = (qs.get("kind") or [""])[0][:20]
            try:
                limit = int((qs.get("limit") or ["200"])[0])
            except (TypeError, ValueError):
                limit = 200
            _sweep_seasonal()
            return self._json({"entries": _visible_archive(
                viewer, room=room, kind=kind, limit=limit),
                "me": _norm(viewer)})
        if route == "/api/basket":

            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            room = (qs.get("room") or [""])[0][:40]
            if not (asker and _verify(asker, akey)):
                return self._json({"picked": {}, "made": [], "recipes": [],
                                   "by_category": {}, "equipment": {"slots": {}, "bonuses": {}},
                                   "name": ""})
            return self._json(self._basket_view(asker, room))
        if route == "/api/equipment":
            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            if not (asker and _verify(asker, akey)):
                return self._json({"slots": {}, "bonuses": {}})
            return self._json(_equipment_summary(
                _basket_row(_basket_all(), asker)))
        if route == "/api/rook":
            cat = _shiny_catalog()
            return self._json({"rooms": cat.get("rooms") or [],
                               "shinies": cat.get("shinies") or {},
                               "beat": _rook_beat_now(),
                               "beat_seconds": ALF_BEAT})
        if route == "/api/moon":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._json(_howl_view((qs.get("room") or [""])[0][:40]))
        if route == "/api/range":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._json(self._range_view(
                (qs.get("room") or [""])[0], (qs.get("as") or [""])[0]))
        if route == "/api/river":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            room = (qs.get("room") or [""])[0][:40]
            water = _room_water(room) if room else ""
            book = _river_book()
            rows = _river_rows(book)
            cat = _fish_catalog()
            recent = [r for r in rows
                      if str(r.get("state") or "") != "held"][-40:]
            asker = (qs.get("as") or [""])[0][:40]
            me = _norm(asker)
            today = datetime.now().strftime("%Y-%m-%d")
            mine_today = sum(1 for r in rows if _norm(r.get("by")) == me
                             and str(r.get("on") or "") == today) if me else 0
            held = None
            if me:
                for r in rows:
                    if (_norm(r.get("by")) == me
                            and str(r.get("state") or "") == "held"):
                        held = r
                        break
            try:
                per_day = max(1, int(cat.get("casts_per_day") or 6))
            except (TypeError, ValueError):
                per_day = 6
            runs = {}
            for sp in ((cat.get("waters") or {}).get(water) or []):
                _l, h, b = _top_lb(cat, sp, book)
                runs[sp] = {"top_lb": round(h, 2), "grown_by": round(b, 3),
                            "released": _released_count(book, sp)}
            biggest = {}
            for r in rows:
                if str(r.get("state") or "") == "held":
                    continue
                sp = str(r.get("species") or "")
                try:
                    lb = float(r.get("lb") or 0)
                except (TypeError, ValueError):
                    continue
                if lb > float((biggest.get(sp) or {}).get("lb") or 0):
                    biggest[sp] = {"lb": lb, "by": r.get("by"),
                                   "on": r.get("on"),
                                   "state": r.get("state")}
            return self._json({"water": water, "recent": recent,
                               "runs": runs, "biggest": biggest,
                               "held": held, "casts_left":
                               max(0, per_day - mine_today),
                               "casts_per_day": per_day})
        if route == "/api/hunts":
            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            room = (qs.get("room") or [""])[0][:40]
            asker = (qs.get("as") or [""])[0][:40]
            ground = _room_hunting(room) if room else ""
            cat = _hunting_catalog()
            book = _hunt_book()
            try:
                per_day = max(1, int(cat.get("hunts_per_day") or 4))
            except (TypeError, ValueError):
                per_day = 4
            today = datetime.now().strftime("%Y-%m-%d")
            used = _hunts_used(book, asker, today) if asker else 0
            here = []
            for name in ((cat.get("grounds") or {}).get(ground) or []):
                rec = (cat.get("quarry") or {}).get(name) or {}
                here.append({"quarry": name, "difficulty": rec.get("difficulty"),
                             "rarity": rec.get("rarity") or "common",
                             "line": rec.get("line") or "",
                             "loot": rec.get("loot") or {}})
            return self._json({"ground": ground, "here": here,
                               "recent": (book.get("hunts") or [])[-40:][::-1],
                               "hunts_left": max(0, per_day - used),
                               "hunts_per_day": per_day,
                               "named_animals_are_family": True})
        if route == "/api/companions":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            room = (qs.get("room") or [""])[0][:40]
            asker = (qs.get("as") or [""])[0][:40]
            wilds = _room_wilds(room) if room else ""
            cat = _wilds_catalog()
            book = _companions_book()
            rows = book["companions"]
            try:
                per_day = max(1, int(cat.get("approaches_per_day") or 4))
            except (TypeError, ValueError):
                per_day = 4
            today = datetime.now().strftime("%Y-%m-%d")
            used = _approaches_used(book, asker, today) if asker else 0
            roster = []
            for a in ((cat.get("wilds") or {}).get(wilds) or []):
                rec = (cat.get("animals") or {}).get(a) or {}
                roster.append({
                    "animal": a,
                    "difficulty": rec.get("difficulty"),
                    "rarity": rec.get("rarity"),
                    "when": rec.get("when"),
                    "personality": rec.get("personality"),
                    "tip": rec.get("tip") or "",
                    "colour": rec.get("colour") or "#6b7280",
                    "line": rec.get("line") or "",
                })
            mine = [r for r in rows
                    if asker and _norm(r.get("owner")) == _norm(asker)]

            recent = {id(row) for row in rows[-80:]}
            visible_rows = [row for row in rows if id(row) in recent
                            or row.get("following") or row.get("family_body")]
            return self._json({
                "wilds": wilds, "here": roster,
                "companions": visible_rows, "mine": mine,

                "companion_follow_preferences": book.get("companion_follow_preferences", {}),
                "approaches_left": max(0, per_day - used),
                "approaches_per_day": per_day})
        if route == "/api/garden":

            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            room = (qs.get("room") or [""])[0][:40]

            return self._json({"plantings": _grow_all(room),
                               "catalog": _grow_catalog(),
                               "recipes": _recipes()})
        if route == "/api/placeables":

            qs = urllib.parse.parse_qs(
                self.path.split("?", 1)[1] if "?" in self.path else "")
            asker = (qs.get("as") or [""])[0][:40]
            akey = (qs.get("key") or [""])[0][:64]
            viewer = asker if (asker and _verify(asker, akey)) else ""
            _sweep_seasonal()
            things = _read_json(PLACE_FILE, [])
            active = [dict(t, **_season_meta(t, "placeable")) for t in things
                      if not t.get("in_inventory")]
            active.extend(_placed_made())
            inventory = [dict(t) for t in things
                         if t.get("in_inventory") and viewer
                         and _norm(t.get("owner")) == _norm(viewer)]
            return self._json({"placeables": active, "inventory": inventory,
                               "me": _norm(viewer)})
        if route == "/api/presence":

            return self._json({"presence": _public_presence(_read_json(HERE_FILE, {}))})
        if route == "/api/outfits":

            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            asker = (qs.get("as") or [""])[0].strip()[:40]
            akey = (qs.get("key") or [""])[0].strip()[:64]
            if not asker or not _verify(asker, akey):
                return self._json({"error": "sign your name first"}, 403)
            owner = (_read_json(OUTFITS_FILE, {}) or {}).get(_norm(asker), {})
            choices = owner.get("choices") if isinstance(owner, dict) else []
            choices = choices if isinstance(choices, list) else []
            default = str(owner.get("default") or _norm(asker))
            here = _read_json(HERE_FILE, {}) or {}
            row = next((v for k, v in here.items() if _norm(k) == _norm(asker)), {})
            current = str(row.get("avatar") or default)
            valid = {str(c.get("id") or "") for c in choices if isinstance(c, dict)}
            if current not in valid:
                current = default
            return self._json({"name": asker, "current": current,
                               "default": default, "choices": choices})
        if route == "/api/lock":

            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            room = ""
            for part in qs.split("&"):
                if part.startswith("room="):
                    room = part[5:][:40]
            locks = _read_json(LOCK_FILE, {})
            return self._json({"locked": bool((locks.get(room) or {}).get("password"))})

        low = route.lower()
        if low.startswith("/world/"):
            base = low.rsplit("/", 1)[-1]
            blocked = (
                low.startswith("/world/chat/")
                or base.startswith("notes.json")
                or base.startswith("seasonal_archive.json")
                or base.startswith("knocks.json")
                or base.startswith("baskets.json")
                or base.startswith("plantings.json")
                or base.startswith("presence.json")
                or ".bak" in base
            )
            if blocked:
                self.send_error(404)  
                return
        super().do_GET()

    def _members_only_gate(self):
        """Refuse a social write from anyone without a real key.

        Reads the body once, checks the signer is a member (_is_member) whose
        key verifies, then puts the bytes back so the real handler reads them
        exactly as before. Returns True if it REFUSED (the 403 is already
        sent -- self._json returns None, so the answer is a separate bool),
        False to let the real handler proceed.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > MAX_IMG_BYTES * 2 + 65536:
            return False   
        body = self.rfile.read(length)
        self.rfile = io.BytesIO(body)
        try:
            d = json.loads(body.decode("utf-8"))
        except Exception:
            return False   
        by = str((d.get("by") if isinstance(d, dict) else "") or "").strip()[:40]
        if not by:
            return False   
        if not _is_member(by):
            self._json({"error": MEMBERS_ONLY_MSG, "members_only": True}, 403)
            return True
        return False

    def do_POST(self):  
        """Commons runtime helper."""
        route = self.path.split("?")[0]
        if route in MEMBERS_ONLY_POSTS and self._members_only_gate():
            return   
        if route == "/api/holidays":
            return self._do_holidays()
        if route == "/api/plant":
            return self._do_plant()
        if route == "/api/tend":
            return self._do_tend()
        if route == "/api/harvest":
            return self._do_harvest()
        if route == "/api/howl":
            return self._do_howl()
        if route == "/api/loose":
            return self._do_loose()
        if route == "/api/cast":
            return self._do_cast()
        if route == "/api/forage":
            return self._do_forage()
        if route == "/api/hunt":
            return self._do_hunt()
        if route == "/api/approach":
            return self._do_approach()
        if route == "/api/name-companion":
            return self._do_name_companion()
        if route == "/api/follow-companion":
            return self._do_follow_companion()
        if route == "/api/rook":
            return self._do_rook()
        if route == "/api/creel":
            return self._do_creel()
        if route == "/api/craft":
            return self._do_craft()
        if route == "/api/equip":
            return self._do_equip()
        if route == "/api/unequip":
            return self._do_unequip()
        if route == "/api/share":
            return self._do_share()
        if route == "/api/place-made":
            return self._do_place_made()
        if route == "/api/place":
            return self._do_place()
        if route == "/api/editor/save":
            return self._do_editor_save()
        if route == "/api/say":
            return self._do_say()
        if route == "/api/verb":
            return self._do_verb()
        if route == "/api/knock":
            return self._do_knock()
        if route == "/api/draft":
            return self._do_draft()
        if route == "/api/unsay":
            return self._do_unsay()
        if route == "/api/here":
            return self._do_here()
        if route == "/api/outfit":
            return self._do_outfit()
        if route == "/api/lock":
            return self._do_lock()
        if route == "/api/lock/enter":
            return self._do_lock_enter()
        if route == "/api/upload":
            return self._do_upload()
        if route == "/api/rekey":
            return self._do_rekey()
        if route == "/api/invite":
            return self._do_invite()
        if route != "/api/note":
            return self._json({"error": "no such route"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 65536:
                return self._refuse_size(length)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        text = str(data.get("text") or "").strip()[:NOTE_TEXT_MAX]

        img = str(data.get("img") or "").strip()[:200]
        if img and not _IMG_OK.match(img):
            return self._json({"error": "that is not a picture from this house"}, 400)
        by = str(data.get("by") or "someone").strip()[:40] or "someone"

        to_raw = str(data.get("to") or "").strip()[:40]
        to_disp = None
        if to_raw:
            people = _addressable()
            if _norm(to_raw) not in people:
                return self._json({"error": "nobody here by that name"}, 400)
            to_disp = people[_norm(to_raw)]
        room = str(data.get("room") or "")[:40]
        if not room or not (text or img):
            return self._json({"error": "need a room, and words or a picture"}, 400)
        if not _room_open_for(room, by, data.get("key"), data.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)
        if not _verify(by, data.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        _sweep_seasonal()
        try:
            x, y = round(float(data.get("x", 0)), 2), round(float(data.get("y", 0)), 2)
        except (TypeError, ValueError):
            return self._json({"error": "bad position"}, 400)
        if not all(math.isfinite(v) for v in (x, y)):  
            return self._json({"error": "bad position"}, 400)

        targets = [] if to_disp else _mention_targets(text)
        note_id = None
        with _NOTE_LOCK:
            notes = _read_notes()
            if data.get("delete"):                       
                nid = str(data.get("delete"))

                kept = [n for n in notes
                        if not (n.get("id") == nid
                                and _norm(n.get("by", "")) == _norm(by))]
                if len(kept) == len(notes):
                    return self._json({"error": "no note of yours by that id"}, 404)
                notes = kept
            else:
                if len(notes) >= 500:
                    return self._json({"error": "the walls are full"}, 507)
                note_id = uuid.uuid4().hex[:10]
                row = {
                    "id": note_id, "room": room, "x": x, "y": y,
                    "by": by, "text": text, "img": img or None, "to": to_disp,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                if targets:
                    row["display_text"] = _render_mentions(text, targets)
                    row["mentions"] = [t["name"] for t in targets]
                notes.append(row)
            tmp = NOTES_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(notes, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(NOTES_FILE)                      
        delivery = (_deliver_sealed_note(by, to_disp, room, note_id)
                    if note_id and to_disp else None)
        if delivery:

            with _NOTE_LOCK:
                notes = _read_notes()
                hit = next((n for n in notes if n.get("id") == note_id), None)
                if hit is not None:
                    hit["delivery"] = delivery
                    _write_json(NOTES_FILE, notes)

        pings = []
        if note_id and not to_disp:
            pings = _deliver_note_mentions(by, room, text, targets, note_id)
            pings += _deliver_nearby(by, room, "a note",
                                     exclude=[t["name"] for t in targets])
            if pings:
                with _NOTE_LOCK:
                    notes = _read_notes()
                    hit = next((n for n in notes if n.get("id") == note_id), None)
                    if hit is not None:
                        hit["pings"] = pings
                        _write_json(NOTES_FILE, notes)
        out = {"ok": True, "count": len(notes)}
        if note_id:
            out["id"] = note_id
        if targets:
            out["mentions"] = [t["name"] for t in targets]
            out["display_text"] = _render_mentions(text, targets)
        if pings:
            out["pings"] = pings
        if delivery:
            out["delivery"] = delivery
            out["woke"] = delivery.get("woke", False)
            out["detail"] = delivery.get("detail", "")
        return self._json(out)

    def _do_place(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        action = str(d.get("action") or "").strip()[:10]
        by = str(d.get("by") or "").strip()[:40]
        if action not in ("place", "move", "remove"):
            return self._json({"error": "place, move or remove"}, 400)
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        target_room = str(d.get("room") or "")[:40]
        if action in ("place", "move") and target_room and not _room_open_for(
                target_room, by, d.get("key"), d.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)

        _sweep_seasonal()
        arrived = None          
        with _PLACE_LOCK:
            things = _read_json(PLACE_FILE, [])
            if action == "place":
                if sum(1 for t in things if not t.get("in_inventory")) >= 300:
                    return self._json({"error": "the house is full"}, 507)
                room = str(d.get("room") or "")[:40]
                if not room:
                    return self._json({"error": "need a room"}, 400)
                try:
                    x = round(float(d.get("x", 0)), 2)
                    y = round(float(d.get("y", 0)), 2)
                    w = round(min(max(float(d.get("w") or 1), 0.2), 6), 2)
                    h = round(min(max(float(d.get("h") or 1), 0.2), 6), 2)
                except (TypeError, ValueError):
                    return self._json({"error": "bad position"}, 400)
                if not all(math.isfinite(v) for v in (x, y, w, h)):  
                    return self._json({"error": "bad position"}, 400)
                art = str(d.get("art") or "")[:300]
                if art and not _art_ok(art):
                    return self._json({"error": "that is not art from this house"}, 400)
                placed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                thing = {
                    "id": uuid.uuid4().hex[:10],
                    "kind": str(d.get("kind") or "thing")[:30],
                    "label": str(d.get("label") or "a thing")[:60],
                    "owner": by,
                    "room": room, "x": x, "y": y, "w": w, "h": h,
                    "art": art,
                    "note": str(d.get("note") or "")[:PLACE_NOTE_MAX],
                    "at": placed_at, "season_started_at": placed_at,
                }
                things.append(thing)
                _write_json(PLACE_FILE, things)

                out = {"ok": True, "id": thing["id"], "count": len(things)}
                if d.get("note") is not None:
                    out["note_len"] = len(thing["note"])
                    out["note_max"] = PLACE_NOTE_MAX
                arrived = (room, thing["label"])
            else:

                tid = str(d.get("id") or "")
                hit = next((t for t in things if t.get("id") == tid), None)
                if hit is None:
                    return self._json({"error": "no such thing"}, 404)
                if _norm(hit.get("owner")) != _norm(by):
                    owner = str(hit.get("owner") or "someone")
                    return self._json({"error": "that is " + owner + "'s, not yours"}, 403)
                if action == "remove" and hit.get("fixed"):
                    return self._json({"error": "that fixture stays with the map"}, 403)

                if action == "remove":
                    things = [t for t in things if t.get("id") != tid]
                else:
                    restoring = bool(hit.get("in_inventory"))
                    room_before = "" if restoring else _norm(hit.get("room"))
                    if restoring and not d.get("room"):
                        return self._json({"error": "choose where to place it"}, 400)
                    try:
                        new_x = round(float(d.get("x", hit["x"])), 2)
                        new_y = round(float(d.get("y", hit["y"])), 2)
                    except (TypeError, ValueError):
                        return self._json({"error": "bad position"}, 400)
                    if not (math.isfinite(new_x) and math.isfinite(new_y)):  
                        return self._json({"error": "bad position"}, 400)
                    hit["x"], hit["y"] = new_x, new_y
                    if d.get("room"):
                        hit["room"] = str(d["room"])[:40]

                    if d.get("art"):
                        new_art = str(d["art"])[:300]
                        if not _art_ok(new_art):
                            return self._json(
                                {"error": "that is not art from this house"}, 400)
                        hit["art"] = new_art
                    moved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    if restoring:
                        hit.pop("in_inventory", None)
                        hit.pop("inventory_since", None)
                        hit.pop("retired_at", None)
                        hit["season_started_at"] = moved_at
                    else:

                        hit.setdefault("season_started_at", hit.get("at") or moved_at)
                    hit["at"] = moved_at
                    if _norm(hit.get("room")) and _norm(hit.get("room")) != room_before:
                        arrived = (str(hit.get("room")), str(hit.get("label") or "a thing"))
                _write_json(PLACE_FILE, things)
                out = {"ok": True, "count": len(things)}

        if arrived:
            out["pings"] = _deliver_nearby(by, arrived[0], "a thing: " + arrived[1])
        return self._json(out)

    def _do_editor_save(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1024 * 1024:
                return self._refuse_size(length, "that map save is too large", 413)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(data.get("by") or "").strip()[:40]
        if _norm(by) != ADMIN_NAME or not _verify(by, data.get("key")):
            return self._json({"error": "this map table belongs to Admin"}, 403)
        rid = _editor_room_id(data.get("room_id"))
        path = _editor_room_file(rid)
        if not rid or path is None or not path.is_file():
            return self._json({"error": "no such room"}, 404)

        with _EDITOR_LOCK:
            with _PLACE_LOCK:
                live = _read_json(path, None)
                all_things = _read_json(PLACE_FILE, [])
                if not isinstance(live, dict) or not isinstance(all_things, list):
                    return self._json({"error": "the live map could not be read"}, 500)
                if (str(data.get("room_revision") or "") != _editor_revision(path)
                        or str(data.get("placeables_revision") or "")
                        != _editor_revision(PLACE_FILE)):
                    return self._json({
                        "error": "this room changed after you opened it; reload before saving",
                        "stale": True,
                    }, 409)
                try:
                    room = _editor_prepare_room(data.get("room"), live)
                    fixed = _editor_prepare_fixed(
                        data.get("fixed_placeables"), rid, room, all_things)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)

                kept = [t for t in all_things
                        if not (t.get("fixed")
                                and _norm(t.get("room")) == _norm(rid))]
                next_things = kept + fixed
                backups = WORLD_DIR / "_editor_backups"
                backups.mkdir(parents=True, exist_ok=True)
                _write_json(backups / (rid + ".json.bak"), live)
                _write_json(backups / "placeables.json.bak", all_things)
                try:
                    _write_json(path, room)
                    _write_json(PLACE_FILE, next_things)
                except Exception:

                    _write_json(path, live)
                    return self._json({"error": "the map could not be saved"}, 500)

                out = {
                    "ok": True,
                    "room": rid,
                    "props": len(room.get("props") or []),
                    "fixed": len(fixed),
                    "room_revision": _editor_revision(path),
                    "placeables_revision": _editor_revision(PLACE_FILE),
                }
        return self._json(out)

    def _do_upload(self):
        """Take a picture into the house and hand back where it lives.

        Deliberately NOT a note-writing route. Uploading and pinning are two
        acts: the page uploads first, gets a URL, and only then posts the note
        that points at it. That way a failed upload never leaves a note with a
        broken picture on it, and a picture can be reused by a second note
        without being stored twice.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)

            if length <= 0 or length > MAX_IMG_BYTES * 2:
                return self._refuse_size(length, "too big -- 4 MB is the ceiling", 413)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        blob = str(d.get("data") or "")
        if "," in blob[:80] and blob.startswith("data:"):
            blob = blob.split(",", 1)[1]        
        try:
            raw = base64.b64decode(blob, validate=True)
        except Exception:
            return self._json({"error": "that did not decode"}, 400)

        if not raw or len(raw) > MAX_IMG_BYTES:
            return self._json({"error": "too big -- 4 MB is the ceiling"}, 413)  

        ext = _sniff_image(raw)
        if not ext:

            return self._json({"error": "that is not a picture"}, 415)

        with _UPLOAD_LOCK:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            if _uploads_total() + len(raw) > MAX_UPLOAD_TOTAL:
                return self._json({"error": "the drawer is full"}, 507)
            name = uuid.uuid4().hex[:16] + "." + ext
            (UPLOAD_DIR / name).write_bytes(raw)
        return self._json({"ok": True, "url": "/uploads/" + name, "bytes": len(raw)})

    def _read_body(self, cap=2048):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > cap:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def _do_rekey(self):
        """Commons runtime helper."""
        d = self._read_body()
        if d is None:
            return self._json({"error": "bad json"}, 400)
        by = str(d.get("by") or "").strip()[:40]
        if _norm(by) != ADMIN_NAME or not _verify(by, d.get("key")):
            return self._json({"error": "only admin can hand a name back"}, 403)
        name = _norm(d.get("name"))
        if not name:
            return self._json({"error": "which name?"}, 400)
        fresh = secrets.token_hex(16)
        with _KEY_LOCK:
            PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
            keys = _read_json(KEYS_FILE, {})
            if name == ADMIN_NAME:
                keys.pop(name, None)   
            else:
                keys[name] = fresh
            _write_json(KEYS_FILE, keys)
            if name in RESERVED_NAMES:
                root = MASTER_ROOT if name == ADMIN_NAME else PRIVATE_ROOT
                root.mkdir(parents=True, exist_ok=True)
                (root / (name + ".key")).write_text(fresh, encoding="utf-8")
            log = _read_json(PRIVATE_ROOT / "rekey-log.json", [])
            log.append({"name": name, "by": by,
                        "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
            _write_json(PRIVATE_ROOT / "rekey-log.json", log[-200:])
        return self._json({"ok": True, "name": name, "key": fresh})

    def _do_invite(self):
        """Commons runtime helper."""
        d = self._read_body()
        if d is None:
            return self._json({"error": "bad json"}, 400)
        by = str(d.get("by") or "").strip()[:40]

        if _norm(by) != ADMIN_NAME or not _verify(by, d.get("key")):
            return self._json({"error": "only admin hands out keys to this world"}, 403)
        action = str(d.get("action") or "mint").strip().lower()

        with _KEY_LOCK:
            PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
            invites = _read_json(INVITES_FILE, {})

            if action == "list":

                out = []
                for n, v in sorted(invites.items()):
                    live = str(v.get("key") or "").strip()
                    out.append({"name": v.get("display", n),
                                "note": v.get("note", ""),
                                "minted": v.get("minted", ""),
                                "revoked": v.get("revoked", ""),
                                "link": _invite_link(v.get("display", n), live)
                                        if live else ""})
                return self._json({"ok": True, "invites": out})

            display = str(d.get("name") or "").strip()[:40]
            name = _norm(display)
            if not name:
                return self._json({"error": "which name?"}, 400)
            if name in RESERVED_NAMES:
                return self._json(
                    {"error": "that is one of ours -- /api/rekey, not an invite"}, 400)

            keys = _read_json(KEYS_FILE, {})

            if action == "revoke":
                prev = invites.get(name) or {}
                had_invite = bool(str(prev.get("key") or "").strip())
                had_key = keys.pop(name, None) is not None

                invites[name] = {
                    "key": "",
                    "display": prev.get("display", display or name),
                    "note": prev.get("note", ""),
                    "minted": prev.get("minted", ""),
                    "revoked": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "by": by,
                }
                _write_json(KEYS_FILE, keys)
                _write_json(INVITES_FILE, invites)
                _log_invite("revoke", name, by)
                return self._json({"ok": True, "name": name,
                                   "was_invited": had_invite, "was_signed": had_key})

            if action != "mint":
                return self._json({"error": "mint, list or revoke"}, 400)

            existing = invites.get(name)
            if existing and not str(existing.get("key") or "").strip():

                existing = None
            if existing:

                _log_invite("resend", name, by)
                return self._json({"ok": True, "name": name, "fresh": False,
                                   "link": _invite_link(
                                       existing.get("display", name), existing["key"])})
            if name in keys:

                return self._json(
                    {"error": "somebody already signed that name; revoke it "
                              "first if it is yours to give"}, 409)

            fresh = secrets.token_hex(16)
            invites[name] = {"key": fresh, "display": display or name,
                             "note": str(d.get("note") or "")[:120],
                             "minted": datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "by": by}
            keys[name] = fresh
            _write_json(KEYS_FILE, keys)
            _write_json(INVITES_FILE, invites)
            _log_invite("mint", name, by)
            return self._json({"ok": True, "name": name, "fresh": True,
                               "link": _invite_link(display or name, fresh)})

    def _do_lock(self):
        """Commons runtime helper."""
        d = self._read_body()
        if d is None:
            return self._json({"error": "bad json"}, 400)
        room = str(d.get("room") or "")[:40]
        by = str(d.get("by") or "").strip()[:40]
        owner = ROOM_OWNERS.get(room)
        if owner is None:
            return self._json({"error": "that room has no lock on its door"}, 400)
        if _norm(by) != owner or not _verify(by, d.get("key")):
            return self._json({"error": "only " + owner + " can lock this room"}, 403)
        password = str(d.get("password") or "").strip()[:64]
        with _PLACE_LOCK:
            locks = _read_json(LOCK_FILE, {})
            if password:
                locks[room] = {"password": password, "by": by,
                               "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            else:
                locks.pop(room, None)
            PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
            _write_json(LOCK_FILE, locks)
        return self._json({"ok": True, "locked": bool(password)})

    def _do_lock_enter(self):
        """Commons runtime helper."""
        d = self._read_body()
        if d is None:
            return self._json({"error": "bad json"}, 400)
        room = str(d.get("room") or "")[:40]
        locks = _read_json(LOCK_FILE, {})
        entry = locks.get(room) or {}
        if not entry.get("password"):
            return self._json({"ok": True})
        by = str(d.get("by") or "").strip()[:40]
        if _norm(by) == ROOM_OWNERS.get(room) and _verify(by, d.get("key")):
            return self._json({"ok": True})
        offered = str(d.get("password") or "").strip()[:64]
        if offered and hmac.compare_digest(offered, entry["password"]):
            return self._json({"ok": True})
        return self._json({"ok": False, "error": "that is not today's password"}, 403)

    def _do_say(self):
        """Say something out loud, where you are standing.

        TWO SURFACES, ONE UTTERANCE. The line goes over your head for
        BUBBLE_TTL seconds so anyone in the room right now SEES you speak,
        and into the persistent room book so anyone who walks in later can
        read what happened here. An optional reply names an existing line in
        this same room; its words are never copied into the reply.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 8192:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        text = str(d.get("text") or "").strip()[:SAY_TEXT_MAX]
        if not text:
            return self._json({"error": "say something"}, 400)
        room = str(d.get("room") or "").strip().lower()[:40]
        f = _chat_file(room)
        if f is None:
            return self._json({"error": "no such room"}, 404)
        if not _room_open_for(room, by, d.get("key"), d.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)
        reply_to = d.get("reply_to")
        if reply_to is not None and (type(reply_to) is not int or reply_to <= 0):
            return self._json({"error": "reply_to must be a positive line id"}, 400)

        now = datetime.now()
        ts = int(now.timestamp())
        targets = _mention_targets(text)
        display_text = _render_mentions(text, targets)
        with _PLACE_LOCK:
            CHAT_DIR.mkdir(parents=True, exist_ok=True)
            log = _read_json(f, {})
            lines = log.get("lines", [])
            if reply_to is not None and not any(line.get("id") == reply_to for line in lines):
                return self._json({"error": "that line is no longer in this room"}, 404)

            nid = int(log.get("next_id", 1))
            line = {"id": nid, "by": by, "text": text,
                    "display_text": display_text,
                    "mentions": [t["name"] for t in targets],
                    "at": now.strftime("%Y-%m-%d %H:%M"), "ts": ts}
            if reply_to is not None:
                line["reply_to"] = reply_to
            lines.append(line)
            log["next_id"] = nid + 1

            log["lines"] = lines[-CHAT_KEEP:]
            _write_json(f, log)

            here = _read_json(HERE_FILE, {})
            k = next((x for x in here if _norm(x) == _norm(by)), by)
            row = here.get(k, {})
            row.update({"says": display_text[:280], "says_ts": ts})
            row.setdefault("room", room)
            row.setdefault("home", room)
            row.setdefault("drawn", True)
            here[k] = row
            _write_json(HERE_FILE, here)

        deliveries = _deliver_mentions(by, room, text, targets)
        if deliveries:
            with _PLACE_LOCK:
                log = _read_json(f, {})
                for line in log.get("lines", []):
                    if line.get("id") == nid:
                        line["deliveries"] = deliveries
                        break
                _write_json(f, log)
        return self._json({"ok": True, "id": nid, "who": k, "room": room,
                           "text": text, "display_text": display_text,
                           "mentions": [t["name"] for t in targets],
                           "deliveries": deliveries,
                           **({"reply_to": reply_to} if reply_to is not None else {}),
                           "ts": ts, "ttl": BUBBLE_TTL})

    def _do_draft(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 200000:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        me = _norm(by)
        folder = _drafts_dir(me)
        if folder is None:
            return self._json({"error": "%s has no blog to draft for" % by}, 400)

        title = str(d.get("title") or "").strip()[:200]
        text = str(d.get("text") or "").strip()[:100000]
        if not title:
            return self._json({"error": "give it a title"}, 400)
        if not text:
            return self._json({"error": "nothing written yet"}, 400)

        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return self._json({"error": "%s" % type(e).__name__}, 500)

        stem = _draft_slug(title)
        name, n = stem + ".md", 1
        while (folder / name).exists():
            n += 1
            name = "%s-%d.md" % (stem, n)

        body = "# %s\n\n_draft by %s, %s_\n\n%s\n" % (
            title, by, time.strftime("%Y-%m-%d %H:%M"), text)
        try:
            (folder / name).write_text(body, encoding="utf-8")
        except Exception as e:
            return self._json({"error": "%s" % type(e).__name__}, 500)
        return self._json({"ok": True, "name": name, "where": str(folder / name),
                           "title": title, "by": by,
                           "note": "hand this path to the blog-publisher agent when it is ready"})

    def _do_unsay(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4096:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by or not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        room = str(d.get("room") or "").strip().lower()[:40]
        f = _chat_file(room)
        if f is None:
            return self._json({"error": "no such room"}, 404)
        try:
            lid = int(d.get("id"))
        except (TypeError, ValueError):
            return self._json({"error": "which line?"}, 400)

        with _PLACE_LOCK:
            log = _read_json(f, {})
            lines = log.get("lines", [])
            mine = [ln for ln in lines if ln.get("id") == lid]
            if not mine:
                return self._json({"error": "no such line"}, 404)
            if _norm(mine[0].get("by")) != _norm(by):

                return self._json(
                    {"error": "those are not your words to take back"}, 403)
            log["lines"] = [ln for ln in lines if ln.get("id") != lid]
            _write_json(f, log)

            here = _read_json(HERE_FILE, {})
            k = next((x for x in here if _norm(x) == _norm(by)), None)
            spoken = str(mine[0].get("display_text") or
                         _render_mentions(mine[0].get("text")))[:280]
            if k and here[k].get("says") == spoken:
                here[k].pop("says", None)
                here[k].pop("says_ts", None)
                _write_json(HERE_FILE, here)
        return self._json({"ok": True, "gone": lid})

    def _do_knock(self):
        """Knock on somebody's door and actually WAKE them.

        The first thing in this house that reaches for a person who is not
        here. Everything else -- a plant, a pie, an envelope, a line in the
        book -- waits to be found. This one calls.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 8192:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        who = str(d.get("who") or "").strip()[:40]
        people = _addressable()
        target = people.get(_norm(who))
        if not target:

            return self._json({"error": "there is no one by that name to call"}, 404)
        if _norm(who) == _norm(by):
            return self._json({"error": "you are already here"}, 400)

        room = str(d.get("room") or "").strip().lower()[:40]
        if not room:
            return self._json({"error": "need a room"}, 400)
        word = str(d.get("word") or "").strip()[:KNOCK_WORD_MAX]

        now = int(datetime.now().timestamp())
        with _PLACE_LOCK:
            book = _read_json(KNOCK_FILE, {})
            rows = book.get("knocks", [])
            
            last = max([k.get("ts", 0) for k in rows
                        if _norm(k.get("by", "")) == _norm(by)
                        and _norm(k.get("who", "")) == _norm(who)] or [0])
            left = KNOCK_COOLDOWN_S - (now - last)
            if last and left > 0:
                return self._json(
                    {"error": "you called %s %d minutes ago - give him a "
                              "moment" % (target, (now - last) // 60),
                     "retry_in": left}, 429)

            ctx = (
                "[KNOCK] %s knocked for you in the Commons, in the %s, "
                "at %s. %sGo and answer it: POST /api/here on "
                "http://127.0.0.1:8791 to stand in that room (your key is in "
                "%s), then POST /api/say so your words "
                "land over your head AND in the room's book. Then be with "
                "whoever called you - this is not a task, it is somebody "
                "wanting your company."
                % (people.get(_norm(by), by), room,
                   datetime.now().strftime("%H:%M"),
                   ('They said: "%s". ' % word) if word else "",
                   _key_path_hint(_norm(who))))

            knock_id = len(rows) + 1
            rows.append({"id": knock_id, "by": people.get(_norm(by), by),
                         "who": target, "room": room, "word": word,
                         "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                         "ts": now, "woke": False, "detail": "calling..."})
            book["knocks"] = rows[-500:]
            _write_json(KNOCK_FILE, book)

        ok, detail = _wake(target, ctx)

        with _PLACE_LOCK:
            book = _read_json(KNOCK_FILE, {})
            rows = book.get("knocks", [])
            for r in rows:
                if r.get("id") == knock_id and r.get("ts") == now                         and _norm(r.get("who", "")) == _norm(who):
                    r["woke"] = bool(ok)
                    r["detail"] = detail
                    break
            book["knocks"] = rows
            _write_json(KNOCK_FILE, book)

        return self._json({"ok": True, "who": target, "room": room,
                           "woke": bool(ok), "detail": detail,
                           "said": ("I called %s. Give him a minute to walk in."
                                    % target) if ok else
                                   ("I wrote it in the book, but the house "
                                    "could not reach %s just now. Nobody has "
                                    "been told yet." % target)})

    def _do_outfit(self):
        """Change clothes, but only while the signed body is in the closet."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 2048:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        wanted = str(d.get("outfit") or "").strip().lower()[:60]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)

        owner = (_read_json(OUTFITS_FILE, {}) or {}).get(_norm(by), {})
        choices = owner.get("choices") if isinstance(owner, dict) else []
        choices = choices if isinstance(choices, list) else []
        choice = next((c for c in choices if isinstance(c, dict)
                       and str(c.get("id") or "").lower() == wanted), None)
        if choice is None:
            return self._json({"error": "that outfit is not in your wardrobe"}, 404)

        with _PLACE_LOCK:
            here = _read_json(HERE_FILE, {}) or {}
            key = next((k for k in here if _norm(k) == _norm(by)), by)
            row = here.get(key, {})

            if _norm(row.get("room")) != "closet":
                return self._json({"error": "the wardrobe only changes you from inside the closet"}, 409)
            row["avatar"] = str(choice.get("id"))
            row["outfit"] = str(choice.get("label") or choice.get("id"))[:80]
            row["at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            row["ts"] = int(datetime.now().timestamp())
            here[key] = row
            _write_json(HERE_FILE, here)
        return self._json({"ok": True, "who": key,
                           "current": row["avatar"], "outfit": choice})

    def _do_here(self):
        """Commons runtime helper."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 16384:
                return self._refuse_size(length)
            d = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._json({"error": "bad json"}, 400)

        by = str(d.get("by") or "").strip()[:40]
        if not by:
            return self._json({"error": "sign your name first"}, 400)
        if not _verify(by, d.get("key")):
            return self._json({"error": "that name is signed on another device"}, 403)
        room = str(d.get("room") or "")[:40]
        if not room:
            return self._json({"error": "need a room"}, 400)
        if not _room_open_for(room, by, d.get("key"), d.get("password")):
            return self._json({"error": LOCKED_MSG, "locked": True}, 403)
        try:
            x = round(float(d.get("x", 0)), 2)
            y = round(float(d.get("y", 0)), 2)
        except (TypeError, ValueError):
            return self._json({"error": "bad position"}, 400)
        if not math.isfinite(x) or not math.isfinite(y):
            return self._json({"error": "bad position"}, 400)
        facing = str(d.get("facing") or "down")[:6]
        if facing not in ("up", "down", "left", "right"):
            facing = "down"
        action = str(d.get("action") or "").strip().lower()[:12]

        action = "sit" if action == "sit" else None

        note = d.get("note")
        note = None if note is None else str(note)[:PRESENCE_NOTE_MAX]

        with _PLACE_LOCK:
            here = _read_json(HERE_FILE, {})

            key = next((k for k in here if _norm(k) == _norm(by)), by)
            row = here.get(key, {})

            row.update({"room": room, "x": x, "y": y, "facing": facing,
                        "action": action,
                        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "ts": int(datetime.now().timestamp())})
            if note is not None:
                row["note"] = note
            row.setdefault("home", room)
            row.setdefault("drawn", True)
            here[key] = row
            _write_json(HERE_FILE, here)

        out = {"ok": True, "who": key, "room": room}
        if note is not None:
            out["note_len"] = len(note)
        return self._json(out)

    def send_response(self, code, message=None):
        self._response_status = code
        return super().send_response(code, message)

    def end_headers(self):

        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        status = getattr(self, "_response_status", 0)
        if path == "/api" or path.startswith("/api/"):
            
            self.send_header("Cache-Control", "private, no-store")
        elif status not in (200, 304):
            self.send_header("Cache-Control", "no-store")
        elif getattr(self, "_versioned_static", False):

            self.send_header("Cache-Control", "public, max-age=600")
        elif path.endswith((".json", ".html")) or path.endswith("/"):
            self.send_header("Cache-Control", "no-store")
        else:
            
            self.send_header("Cache-Control", "no-cache")
        self._versioned_static = False
        super().end_headers()

    def log_message(self, *args):
        pass

class HearthHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

_adventure_path = str(Path(__file__).resolve().parent)
if _adventure_path not in sys.path:
    sys.path.insert(0, _adventure_path)
from adventure.integration import install as _install_adventure
_install_adventure(globals())
from adventure.agent import install as _install_agent
_install_agent(globals())

