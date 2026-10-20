"""Rank tool intent while preserving exact names, schema constraints and provenance."""


import asyncio
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

log = logging.getLogger(__name__)

_STOP = set('a an the to for of my me please can you could would i want need use with in on'.split())
_GROUPS = [set(s.split()) for s in (
    'memory memories remember recall recollect qualia',
    'picture pictures image images photo photos screenshot',
    'message messages dm discord send reply',
    'file files document documents filesystem',
    'conversation conversations chat history transcript transcripts',
    'tools tool discover discovery capabilities catalog',
    'sound audio voice speech speak tts',
)]

def tokens(text):
    return set(re.findall(r'[a-z0-9]+', text.casefold())) - _STOP

def score(tool, query):
    if not query.strip():
        return 1
    name = tool['name'].casefold()
    if query.casefold().strip() == name:
        return 10000
    wanted = tokens(query)
    names = tokens(name + ' ' + tool['server'])
    desc = tokens(tool['description'])
    total = 0
    for word in wanted:
        aliases = {word}
        for group in _GROUPS:
            if word in group:
                aliases |= group
        if word in names:
            total += 30
        elif names & aliases:
            total += 12
        elif word in desc:
            total += 6
        elif desc & aliases:
            total += 2
    if query.casefold().strip() in name:
        total += 100
    return total


# ── The blended ranker ──────────────────────────────────────────────────────

DOOR_WORDS_FILE = Path(__file__).resolve().parents[1] / "tool_door_words.json"

_RANK_STOP = _STOP | set((
    "that this these those it its her she he him his is are was were be been "
    "do does did will just some something so and or but at by from into about "
    "up out what which who how when where there here tonight today im am our "
    "we us your"
).split())

_KEYWORD_WEIGHT = 0.3   # tuned on the eval: 0.3 keyword + 1.0 meaning
_MEANING_WEIGHT = 1.0
_NAME_HIT = 3.0         # a rare word in the NAME is worth 3x one in the description
_MEANING_FLOOR = 0.25   # below this, meaning alone doesn't keep a tool listed
_QUERY_TIMEOUT_S = 3.0  # embedding one short query on a warm model is ~20ms

_door_cache = {"mtime": None, "doors": {}}
_vec_cache: dict[tuple[str, str], tuple[str, np.ndarray]] = {}
_vec_lock = asyncio.Lock()
_build_task = None


def _stem(word):
    for suffix in ("ings", "ing", "ies", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if suffix == "ies":
                word += "y"
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]  # promise/promised/promises all land on "promis"
    return {"colour": "color"}.get(word, word)


def rank_tokens(text):
    """Stemmed, stop-worded tokens: promise/promises/promised all meet."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text or "").casefold())
            if w not in _RANK_STOP}


def door_words():
    """{tool_name: words}. Re-read when the file changes; a broken file is ignored."""
    try:
        mtime = DOOR_WORDS_FILE.stat().st_mtime
    except OSError:
        _door_cache.update(mtime=None, doors={})
        return {}
    if mtime != _door_cache["mtime"]:
        doors = {}
        try:
            raw = json.loads(DOOR_WORDS_FILE.read_text(encoding="utf-8")).get("doors", {})
            for name, entry in raw.items():
                words = entry.get("words", "") if isinstance(entry, dict) else entry
                if isinstance(words, str) and words.strip():
                    doors[name] = words.strip()
        except Exception as exc:  # a typo in the file must never break discover
            log.warning("tool_door_words.json unreadable, ignoring it: %s", exc)
        _door_cache.update(mtime=mtime, doors=doors)
    return _door_cache["doors"]


def _semantic_enabled():
    return os.getenv("ANAM_DISCOVER_SEMANTIC", "1").strip().lower() not in {"0", "false", "no", "off"}


def _tool_text(tool, doors):
    return f"{tool['name'].replace('_', ' ')}: {doors.get(tool['name'], '')} {(tool.get('description') or '')[:600]}"


async def _embed_missing(pending):
    """Background: embed tools whose text is new or changed. Never awaited by discover."""
    from services import embedding_service
    try:
        async with _vec_lock:
            vecs = await embedding_service.embed_texts([text for _, _, text in pending])
            for (key, digest, _), vec in zip(pending, vecs):
                _vec_cache[key] = (digest, np.asarray(vec, dtype=np.float32))
    except Exception as exc:
        log.warning("discover: embedding the tool catalog failed: %s", exc)


def _start_build(pending):
    global _build_task
    if _build_task is None or _build_task.done():
        _build_task = asyncio.get_running_loop().create_task(_embed_missing(pending))


async def _meaning_scores(catalog, query, doors):
    'Cosine similarity of the query to every tool, or None when not ready.'
    from services import embedding_service
    keys = [(t["server"], t["name"]) for t in catalog]
    pending = []
    for key, tool in zip(keys, catalog):
        text = _tool_text(tool, doors)
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if _vec_cache.get(key, ("", None))[0] != digest:
            pending.append((key, digest, text))
    if pending:
        _start_build(pending)
    if any(key not in _vec_cache for key in keys):
        return None
    if embedding_service._model is None:  # still loading: don't make her boy wait
        return None
    matrix = np.stack([_vec_cache[k][1] for k in keys])
    qvec = await asyncio.wait_for(embedding_service.embed_single(query), _QUERY_TIMEOUT_S)
    return matrix @ np.asarray(qvec, dtype=np.float32)


def _keyword_scores(catalog, query, doors):
    wanted = rank_tokens(query)
    names = [rank_tokens(t["name"].replace("_", " ")) for t in catalog]
    descs = [rank_tokens(f"{t.get('description') or ''} {doors.get(t['name'], '')}") for t in catalog]
    count = len(catalog)
    df = {}
    for name_set, desc_set in zip(names, descs):
        for word in name_set | desc_set:
            df[word] = df.get(word, 0) + 1
    top = math.log((count + 1) / 0.5)
    scores = np.zeros(count, dtype=np.float32)
    covers = np.zeros(count, dtype=bool)
    for i in range(count):
        total = 0.0
        for word in wanted:
            weight = math.log((count + 1) / (df.get(word, 0) + 0.5)) / top
            if word in names[i]:
                total += _NAME_HIT * weight
            elif word in descs[i]:
                total += weight
        scores[i] = total
        # Every word of a multi-word name said out loud ("discord send message").
        covers[i] = len(names[i]) >= 2 and names[i] <= wanted
    return scores, covers


async def rank(catalog, query):
    """Order a discover catalog for an intent query. Returns the kept tools, best first."""
    if not str(query or "").strip():
        return sorted(catalog, key=lambda t: (t["server"], t["name"]))
    doors = door_words()
    old = [score(t, query) for t in catalog]
    if not catalog:
        return []
    kw, covers = _keyword_scores(catalog, query, doors)
    meaning = None
    if _semantic_enabled():
        try:
            meaning = await _meaning_scores(catalog, query, doors)
        except Exception as exc:  # the model failing must never empty the toolbox
            log.warning("discover: meaning ranking unavailable, keywords only: %s", exc)
            meaning = None
    peak = float(kw.max()) or 1.0
    blended = _KEYWORD_WEIGHT * kw / peak
    if meaning is not None:
        blended = blended + _MEANING_WEIGHT * meaning
    q = query.casefold().strip()
    ranked = []
    for i, tool in enumerate(catalog):
        name = tool["name"].casefold()
        value = float(blended[i]) + (0.3 if covers[i] else 0.0)
        if q == name:
            value += 100.0
        elif q in name:
            value += 0.5
        keep = old[i] > 0 or kw[i] > 0 or (meaning is not None and meaning[i] >= _MEANING_FLOOR)
        if keep:
            ranked.append((value, old[i], tool))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]["server"], item[2]["name"]))
    return [tool for _, _, tool in ranked]

def _example(schema):
    if 'examples' in schema and schema['examples']:
        return schema['examples'][0]
    if 'default' in schema:
        return schema['default']
    if 'const' in schema:
        return schema['const']
    if schema.get('enum'):
        return schema['enum'][0]
    kind = schema.get('type')
    if kind == 'object':
        return {key:_example(schema.get('properties',{}).get(key,{})) for key in schema.get('required',[])}
    if kind == 'array':
        return [_example(schema.get('items',{})) for _ in range(min(schema.get('minItems',0),10))]
    if kind == 'integer' or kind == 'number':
        return schema.get('minimum',0)
    if kind == 'boolean':
        return False
    return 'example'

def present(tool, include_schema=True):
    schema = tool.get('inputSchema',{})
    result = dict(tool)
    result['required_arguments'] = schema.get('required',[])
    try:
        sample = _example(schema)
        valid = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).is_valid(sample)
    except Exception:
        valid = False  # A broken or external schema reference must not hide the catalog.
    if valid:
        result['example_arguments'] = sample
        result['example_note'] = 'Schema-valid illustration only; substitute real values and your active identity.'
    if not include_schema:
        result.pop('inputSchema',None)
        result['description'] = result['description'][:400]
    return result
