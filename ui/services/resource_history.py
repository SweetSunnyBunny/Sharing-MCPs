"""Small durable resource history: once a minute, at most 48 hours.

Counts and memory only: never command lines, prompts, URLs, or tool results.
Collection and SQLite I/O run off the app's event loop.
"""
from __future__ import annotations
import asyncio
from collections import Counter
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import psutil
from config import DATA_DIR

DB_PATH = Path(DATA_DIR)/'runtime'/'resource-history.db'
INTERVAL = 60
MAX_SAMPLES = 2880
MAX_AGE = 48*3600
log = logging.getLogger(__name__)

class _Performance(ctypes.Structure):
    _fields_ = [('cb',wintypes.DWORD)] + [(name,ctypes.c_size_t) for name in (
        'CommitTotal','CommitLimit','CommitPeak','PhysicalTotal','PhysicalAvailable','SystemCache',
        'KernelTotal','KernelPaged','KernelNonpaged','PageSize')] + [
        (name,wintypes.DWORD) for name in ('HandleCount','ProcessCount','ThreadCount')]


def _commit():
    if os.name != 'nt': return {}
    info = _Performance(); info.cb = ctypes.sizeof(info)
    api = ctypes.WinDLL('psapi',use_last_error=True).GetPerformanceInfo
    api.argtypes = [ctypes.POINTER(_Performance),wintypes.DWORD]; api.restype = wintypes.BOOL
    if not api(ctypes.byref(info),info.cb): return {}
    return {'commit_mb':round(info.CommitTotal*info.PageSize/2**20,1),
            'commit_limit_mb':round(info.CommitLimit*info.PageSize/2**20,1)}


def sample(codex_processes=()):
    memory = psutil.virtual_memory()
    processes = {}
    for proc in psutil.process_iter(['pid','ppid','name','create_time','memory_info'],ad_value=None):
        row = proc.info
        if row['create_time'] is not None and row['memory_info'] is not None:
            processes[row['pid']] = row
    def owned(row):
        seen = set()
        while row and row['pid'] not in seen:
            if row['pid'] == os.getpid(): return True
            seen.add(row['pid'])
            parent = processes.get(row['ppid'])
            if parent and parent['create_time'] > row['create_time']: return False
            row = parent
        return False
    def private(row): return getattr(row['memory_info'],'private',row['memory_info'].vms)
    owned_rows = [p for p in processes.values() if owned(p)]
    names = Counter((p['name'] or 'unknown').lower() for p in processes.values())
    selected = ('codex.exe','claude.exe','node.exe','python.exe','pythonw.exe','chrome.exe','chatgpt.exe','vmmemwsl')
    top = sorted(processes.values(),key=private,reverse=True)[:8]
    resident = sorted(processes.values(),key=lambda p:p['memory_info'].rss,reverse=True)[:8]
    return {'at':time.time(),'boot_at':psutil.boot_time(),'anam_pid':os.getpid(),
        'memory_percent':memory.percent,'available_mb':round(memory.available/2**20,1),
        'physical_total_mb':round(memory.total/2**20,1),**_commit(),
        'process_count':len(processes),'process_counts':{n:names[n] for n in selected},
        'anam_tree_count':len(owned_rows),'anam_tree_private_mb':round(sum(private(p) for p in owned_rows)/2**20,1),
        'codex_messaging':sum(p.get('kind')=='messaging' for p in codex_processes),
        'codex_autowake':sum(p.get('kind')=='autowake' for p in codex_processes),
        'top_private_memory':[{'pid':p['pid'],'name':p['name'],'private_mb':round(private(p)/2**20,1)} for p in top],
        'top_resident_memory':[{'pid':p['pid'],'name':p['name'],'resident_mb':round(p['memory_info'].rss/2**20,1)} for p in resident]}


def _connect():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(DB_PATH,timeout=5)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA journal_size_limit=1048576')
    db.execute('CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, at REAL NOT NULL, payload TEXT NOT NULL)')
    return db


def append(row):
    payload = json.dumps(row,separators=(',',':'),ensure_ascii=True)
    if len(payload)>8000: raise ValueError('Resource sample exceeds size bound')
    db = _connect()
    try:
        with db:
            db.execute('INSERT INTO samples(at,payload) VALUES (?,?)',(row['at'],payload))
            db.execute('DELETE FROM samples WHERE at < ?',(time.time()-MAX_AGE,))
            db.execute('DELETE FROM samples WHERE id NOT IN (SELECT id FROM samples ORDER BY id DESC LIMIT ?)',(MAX_SAMPLES,))
    finally: db.close()


def report(limit=120):
    limit = max(1,min(int(limit),MAX_SAMPLES))
    db = _connect()
    try:
        total,first,last = db.execute('SELECT count(*),min(at),max(at) FROM samples').fetchone()
        rows = [json.loads(row[0]) for row in db.execute('SELECT payload FROM samples ORDER BY id DESC LIMIT ?',(limit,))]
    finally: db.close()
    return {'interval_seconds':INTERVAL,'retention_hours':48,'sample_count':total,
            'first_at':first,'last_at':last,'recent':list(reversed(rows))}


async def collect_loop():
    from services.codex_sessions import status
    while True:
        try:
            state = status()
            row = await asyncio.to_thread(sample,state)
            await asyncio.to_thread(append,row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('Resource history sample failed: %s',type(exc).__name__)
        await asyncio.sleep(INTERVAL)
