"""Durable gateway claims and bounded results. Expiry never makes a write replayable."""
from contextlib import contextmanager
import json
import re
from pathlib import Path
import sqlite3
import time
import uuid
from config import DATA_DIR

DB_PATH = Path(DATA_DIR) / 'runtime' / 'tool-jobs.db'
OWNER = uuid.uuid4().hex
RESULT_TTL = 7 * 86400
MAX_BYTES = 64_000_000

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, identity TEXT, conversation TEXT, owner TEXT, state TEXT, created REAL, updated REAL, result TEXT)')
        yield db
        db.commit()
    finally:
        db.close()

def claim(job_id, fingerprint, identity, conversation, allow_new=True):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise ValueError('request_id already belongs to a different call')
            return False
        if not allow_new:
            raise ValueError("Gateway is busy; collect running jobs before starting another action")
        now = time.time()
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,NULL)',
                   (job_id, fingerprint, identity, conversation, OWNER, 'running', now, now))
        return True

def finish(job_id, result, state='completed'):
    with connection() as db:
        db.execute('UPDATE jobs SET state=?,result=?,updated=? WHERE id=? AND owner=?',
                   (state, json.dumps(result, ensure_ascii=False), time.time(), job_id, OWNER))
        cutoff = time.time() - RESULT_TTL
        db.execute("UPDATE jobs SET result=NULL,state='expired' WHERE result IS NOT NULL AND updated<?", (cutoff,))
        total = db.execute('SELECT COALESCE(SUM(length(CAST(result AS BLOB))),0) FROM jobs').fetchone()[0]
        if total > MAX_BYTES:
            for row in db.execute('SELECT id,length(CAST(result AS BLOB)) AS bytes FROM jobs WHERE result IS NOT NULL ORDER BY updated').fetchall():
                if total <= MAX_BYTES:
                    break
                db.execute("UPDATE jobs SET result=NULL,state='expired' WHERE id=?", (row['id'],))
                total -= row['bytes']

def read(job_id, identity='', conversation=''):
    with connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('Unknown job. Inspect the target before repeating a write.')
    if identity and row['identity'].casefold() != identity.casefold():
        raise ValueError('Job is not owned by this identity')
    if conversation and row['conversation'] != conversation:
        raise ValueError('Job is not owned by this conversation')
    result = json.loads(row['result']) if row['result'] else None
    if row['state'] == 'expired' or (result is not None and time.time()-row['updated'] > RESULT_TTL):
        raise ValueError('Result expired; the action remains recorded and will not be repeated.')
    if result is None:
        if row['owner'] == OWNER and row['state'] == 'running':
            return {'job_id': job_id, 'status': 'running', 'poll_after_seconds': 5}
        return {'job_id': job_id, 'status': 'uncertain', 'isError': True, 'content': [
            {'type': 'text', 'text': 'Anam restarted before recording completion. A write may have completed; inspect its target. It was not repeated.'}]}
    return {'job_id': job_id, 'status': row['state'], **result}

def text_of(result):
    parts = [b.get('text', '') for b in result.get('content', []) if b.get('type') == 'text']
    if result.get('structuredContent') is not None:
        parts.append(json.dumps(result['structuredContent'], ensure_ascii=False))
    return '\n'.join(parts)

def page(job_id, offset=0, limit=8000, query='', identity='', conversation=''):
    if offset < 0 or not 1 <= limit <= 16000 or len(query) > 500:
        raise ValueError('Invalid result page bounds')
    result = read(job_id, identity, conversation)
    if result['status'] != 'completed':
        return result
    text = text_of(result)
    if query:
        match = re.compile(re.escape(query), re.IGNORECASE).search(text, offset)
        if match is None:
            return {'job_id': job_id, 'found': False, 'total_chars': len(text), 'next_offset': None}
        offset = max(offset, match.start()-200)
    end = min(len(text), offset+limit)
    return {'job_id': job_id, 'text': text[offset:end], 'offset': offset, 'total_chars': len(text),
            'next_offset': end if end < len(text) else None, 'found': True}

def compact(result, max_chars=16000):
    text = text_of(result)
    if len(text) <= max_chars:
        return result
    job_id = result['job_id']
    native = [b for b in result.get('content', []) if b.get('type') != 'text']
    return {**{k:v for k,v in result.items() if k not in ('content','structuredContent')},
            'content': [{'type':'text','text':text[:max_chars] + f'\n[More available: use the Anam result operation with job_id={job_id}, offset={max_chars}]'}, *native],
            'result_ref': {'job_id':job_id, 'total_chars':len(text), 'next_offset':max_chars}}
