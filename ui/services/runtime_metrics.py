"""Bounded local runtime timings; never stores prompts, arguments, or tool output."""
from collections import deque
from threading import Lock
import time

_rows = deque(maxlen=2000)
_lock = Lock()

def record(stage, elapsed_ms, *, identity='', provider='', reused=None, ok=True):
    row = {'at': time.time(), 'stage': stage, 'elapsed_ms': round(elapsed_ms, 2),
           'identity': identity, 'provider': provider, 'ok': bool(ok)}
    if reused is not None:
        row['reused'] = bool(reused)
    with _lock:
        _rows.append(row)

def report():
    with _lock:
        rows = list(_rows)
    groups = {}
    for row in rows:
        key = (row['stage'], row['provider'], row.get('reused'))
        groups.setdefault(key, []).append(row['elapsed_ms'])
    summary = []
    for (stage, provider, reused), values in groups.items():
        values.sort()
        summary.append({'stage': stage, 'provider': provider, 'reused': reused,
                        'count': len(values), 'median_ms': values[len(values)//2],
                        'p95_ms': values[min(len(values)-1, int(len(values)*.95))]})
    return {'window': 'current server process; last 2000 observations', 'summary': summary, 'recent': rows[-100:]}


def measure(stage):
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        async def measured(*args, **kwargs):
            started = time.monotonic()
            ok = False
            try:
                value = await fn(*args, **kwargs)
                ok = True
                return value
            finally:
                ctx = args[0] if args else None
                identity = kwargs.get('identity') or getattr(ctx, 'identity', '')
                record(stage, (time.monotonic()-started)*1000, identity=identity, ok=ok)
        return measured
    return decorate
