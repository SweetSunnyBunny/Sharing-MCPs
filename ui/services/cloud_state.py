"""Authenticated cloud service access, reusing Anam's configured MCP endpoints.

Only in-memory caches are used. Cloud failures never open a local memory mirror.
"""
import json
from concurrent.futures import Future
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, urlopen

_cache = {}
_lock = threading.RLock()
_inflight = {}
_cache_generation = 0
_CACHE_LIMIT = 128


class CloudUnavailable(RuntimeError):
    pass


def _endpoint(server):
    from config import MCP_SERVERS_FILE
    candidates = [Path.home()/'.claude.json', Path(os.environ.get('APPDATA', str(Path.home()/'AppData/Roaming')))/'Claude/claude_desktop_config.json']
    explicit = os.environ.get('ANAM_MCP_GLOBAL_CONFIG_PATH')
    if explicit: candidates.insert(0, Path(explicit))
    cfg = {}
    for path in candidates:
        if path.exists():
            found=json.loads(path.read_text(encoding='utf-8-sig')).get('mcpServers',{}).get(server)
            if found is not None:
                cfg=found
                break
    if MCP_SERVERS_FILE.exists():
        cfg={**cfg, **json.loads(MCP_SERVERS_FILE.read_text(encoding='utf-8-sig')).get('mcpServers',{}).get(server,{})}
    url=urlsplit(cfg.get('url',''))
    token=url.path.split('/mcp/',1)[-1] if '/mcp/' in url.path else ''
    if url.scheme!='https' or not url.netloc or not token:
        raise CloudUnavailable(f'{server} has no authenticated cloud endpoint')
    return f'{url.scheme}://{url.netloc}', token


def invalidate_cloud_cache(server='qualia-backend'):
    """Invalidate after local writes; other clients' writes become visible at TTL."""
    global _cache_generation
    with _lock:
        _cache_generation += 1
        for key in list(_cache):
            if key[0] == server:
                del _cache[key]


def _fetch_cloud(server, path, method, payload):
    try:
        base, token = _endpoint(server)
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, data=data, method=method, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            'Accept': 'application/json', 'User-Agent': 'anam/1.0',
        })
        with urlopen(req, timeout=12) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError('Expected cloud object')
        return result
    except Exception as exc:
        raise CloudUnavailable(f'{server} unavailable ({type(exc).__name__})') from None


def request_cloud(server, path, *, method='GET', payload=None, ttl=0, freshness=False):
    key = (server, path)

    def copy_result(entry):
        stamp, fetched_at, result = entry
        copied = json.loads(json.dumps(result))
        if freshness:
            copied['_freshness'] = {
                'fetched_at': fetched_at, 'max_age_seconds': ttl,
                'age_seconds': max(0, int(time.monotonic() - stamp)),
            }
        return copied

    if method != 'GET' or not ttl:
        result = _fetch_cloud(server, path, method, payload)
        if method != 'GET':
            invalidate_cloud_cache(server)
        return result

    with _lock:
        now = time.monotonic()
        old = _cache.get(key)
        if old and now - old[0] < ttl:
            return copy_result(old)
        generation = _cache_generation
        flight_key = (key, generation)
        future = _inflight.get(flight_key)
        owner = future is None
        if owner:
            future = _inflight[flight_key] = Future()
    if not owner:
        return copy_result(future.result(timeout=15))
    try:
        result = _fetch_cloud(server, path, method, payload)
        entry = (time.monotonic(), time.time(), result)
        with _lock:
            # A fetch that began before a write must not repopulate its cache.
            if generation == _cache_generation:
                _cache[key] = entry
                while len(_cache) > _CACHE_LIMIT:
                    del _cache[min(_cache, key=lambda item: _cache[item][0])]
        future.set_result(entry)
        return copy_result(entry)
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        with _lock:
            _inflight.pop(flight_key, None)


def qualia_read(section, **params):
    query = urlencode({k: v for k, v in params.items() if v is not None and v != ''})
    weather = section == 'mind-garden/weather'
    return request_cloud(
        'qualia-backend', '/api/anam/' + section + ('?' + query if query else ''),
        ttl=30 if section == 'snapshot' or weather else 0, freshness=weather,
    )


def hearth_config(name):
    if name not in {'voice','quests'}: raise ValueError('Unknown configuration')
    return request_cloud('hearth-hub','/api/private/config/'+name,ttl=60)


def save_hearth_config(name, value):
    if name not in {'voice','quests'}: raise ValueError('Unknown configuration')
    return request_cloud('hearth-hub','/api/private/config/'+name,method='PUT',payload=value)
