"""One retained Codex messaging process per identity; autonomous jobs are disposable."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time

@dataclass
class Lease:
    client: object
    reused: bool = False
    retain: bool = True

_clients = {}
_locks = {}
_background = {}

@asynccontextmanager
async def acquire(identity, factory, config_key, *, background=False):
    key = identity.casefold()
    lock = asyncio.Lock() if background else _locks.setdefault(key, asyncio.Lock())
    async with lock:
        entry = None if background else _clients.get(key)
        if entry and (entry['config'] != config_key or not entry['client'].is_alive()):
            _clients.pop(key, None)
            await asyncio.to_thread(entry['client'].close)
            entry = None
        reused = entry is not None
        if not entry:
            # A cancelled startup still owns and closes the process it created.
            startup = asyncio.create_task(asyncio.to_thread(factory))
            try:
                client = await asyncio.shield(startup)
            except asyncio.CancelledError:
                client = await startup
                await asyncio.to_thread(client.close)
                raise
            entry = {'client': client, 'config': config_key, 'identity': identity,
                     'started': time.time(), 'busy': False}
            if background:
                _background[id(client)] = entry
            else:
                _clients[key] = entry
        lease = Lease(entry['client'], reused)
        entry['busy'] = True
        try:
            yield lease
        except BaseException:
            lease.retain = False
            raise
        finally:
            entry['busy'] = False
            if background or not lease.retain or not lease.client.is_alive():
                if _clients.get(key) is entry:
                    _clients.pop(key, None)
                _background.pop(id(lease.client), None)
                await asyncio.to_thread(lease.client.close)

def status():
    def describe(entry, kind):
        proc = getattr(entry['client'], 'proc', None)
        return {'identity': entry['identity'], 'kind': kind,
                'pid': getattr(proc, 'pid', None), 'owned_process_tree': bool(getattr(proc, '_anam_job', None)), 'alive': entry['client'].is_alive(),
                'busy': entry['busy'], 'started_at': entry['started']}
    return [describe(e, 'messaging') for e in _clients.values()] + [
        describe(e, 'autowake') for e in _background.values()]

async def shutdown():
    # Explicit server shutdown owns all children. No timer reaps messaging.
    entries = list(_clients.values()) + list(_background.values())
    _clients.clear()
    _background.clear()
    for entry in entries:
        await asyncio.to_thread(entry['client'].close)
