
"""Verified private snapshots and a Commons-owned daily backup timer.

SQLite's backup API includes committed WAL data. Restore drills extract into a
temporary directory and inspect the copy; they never replace a live save. The
daemon is enabled only for this checkout's exact canonical live save paths.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile

CANONICAL_ROOT = Path(__file__).resolve().parents[2]
DAY = 86400
RETAIN = 7
_threads = {}
_thread_lock = threading.Lock()
_EXCLUDED_DIRS = frozenset(('migration_backups', 'routine_backups', '__pycache__', '.git'))

def _sha(data):
    return hashlib.sha256(data).hexdigest()

def _json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default

def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

def _marker(private):
    try:
        value = _json(Path(private) / 'routine_backups' / 'status.json', {})
        if not isinstance(value, dict):
            raise ValueError('Invalid routine backup marker.')
        return value
    except (OSError, ValueError):
        return {'error': 'The routine backup status marker could not be read.'}

def _database_summary(path):
    with closing(sqlite3.connect(path)) as db:
        integrity = [row[0] for row in db.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            raise ValueError('The copied SQLite save did not pass its integrity check.')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'documents' not in tables:
            raise ValueError('The copied save has no Commons documents table.')
        docs = [(key, json.loads(body)) for key, body in db.execute('SELECT id,body FROM documents')]
        summary = {'integrity': 'ok', 'documents': len(docs), 'chat_rooms': 0,
                   'chat_lines': 0, 'inventory_owners': 0, 'inventory_units': 0,
                   'made_items': 0, 'players': 0, 'receipts': 0, 'activity_events': 0}
        for key, value in docs:
            if key.startswith('chat:'):
                if not isinstance(value, dict) or not isinstance(value.get('lines', []), list):
                    raise ValueError('A copied room conversation has an invalid structure.')
                summary['chat_rooms'] += 1
                summary['chat_lines'] += len(value.get('lines', []))
            elif key == 'house:baskets.json':
                if not isinstance(value, dict):
                    raise ValueError('The copied inventory document has an invalid structure.')
                summary['inventory_owners'] = len(value)
                for basket in value.values():
                    if not isinstance(basket, dict):
                        raise ValueError('A copied inventory has an invalid structure.')
                    summary['inventory_units'] += sum(_integer(count) for count in basket.get('picked', {}).values())
                    summary['made_items'] += len(basket.get('made', []))
            elif key.startswith('player:'):
                summary['players'] += 1
        if 'receipts' in tables:
            for request, response in db.execute('SELECT request,response FROM receipts'):
                json.loads(request)
                json.loads(response)
                summary['receipts'] += 1
        if 'room_activity_events' in tables:
            summary['activity_events'] = db.execute('SELECT COUNT(*) FROM room_activity_events').fetchone()[0]
        return summary

def _safe_member(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('A backup contains an unsafe member path.')
    return path

def verify_snapshot(archive):
    """Check every byte against its manifest and inspect a separate DB copy."""
    archive = Path(archive)
    with zipfile.ZipFile(archive) as saved:
        names = saved.namelist()
        if len(names) != len(set(names)):
            raise ValueError('A backup contains duplicate member names.')
        for name in names:
            _safe_member(name)
        if 'manifest.json' not in names:
            raise ValueError('This archive predates verified snapshot manifests.')
        manifest = json.loads(saved.read('manifest.json'))
        if manifest.get('format') != 'commons-private-backup-v1':
            raise ValueError('Unknown private backup manifest format.')
        entries = manifest.get('files')
        if not isinstance(entries, dict) or set(entries) != set(names) - {'manifest.json'}:
            raise ValueError('The manifest does not describe every saved file.')
        json_files = 0
        for name, expected in entries.items():
            data = saved.read(name)
            if not isinstance(expected, dict) or expected.get('bytes') != len(data) or expected.get('sha256') != _sha(data):
                raise ValueError('A saved file failed its checksum verification.')
            if name.endswith('.json'):
                json.loads(data.decode('utf-8-sig'))
                json_files += 1
        dbname = 'commons_private/commons-game.sqlite3'
        if dbname not in entries:
            raise ValueError('A verified Commons snapshot must include its SQLite save.')
        with tempfile.TemporaryDirectory(prefix='commons-backup-check-') as temp:
            path = Path(temp) / 'commons-game.sqlite3'
            path.write_bytes(saved.read(dbname))
            summary = _database_summary(path)
        if summary != manifest.get('database'):
            raise ValueError('The copied save does not match its manifest summary.')
    return {'verified': True, 'archive': str(archive.resolve()), 'sha256': _sha(archive.read_bytes()),
            'files': len(entries), 'json_files': json_files, 'database': summary,
            'created_at': manifest['created_at']}

def restore_drill(archive):
    """Restore only into an isolated disposable folder, including private keys."""
    verified = verify_snapshot(archive)
    with tempfile.TemporaryDirectory(prefix='commons-restore-drill-') as temp:
        root = Path(temp).resolve()
        with zipfile.ZipFile(archive) as saved:
            for name in saved.namelist():
                if name == 'manifest.json':
                    continue
                relative = _safe_member(name)
                destination = root.joinpath(*relative.parts)
                if not destination.resolve().is_relative_to(root):
                    raise ValueError('A restored file escaped the disposable folder.')
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(saved.read(name))
        summary = _database_summary(root / 'commons_private' / 'commons-game.sqlite3')
        if summary != verified['database']:
            raise ValueError('The restore drill did not reproduce the saved records.')
        for path in (root / 'world').rglob('*.json'):
            _json(path)
        private_files = sum(path.is_file() for folder in ('commons_private', 'commons_master') for path in (root / folder).rglob('*'))
    return {**verified, 'restore_drill': 'passed', 'isolated': True, 'private_files': private_files}

def create_snapshot(store, root, *, routine=False):
    """Build, verify and atomically publish one archive inside the private root."""
    root = Path(root).resolve()
    private = store.path.parent.resolve()
    if private.is_relative_to(root):
        raise ValueError('Private backups must remain outside the served Commons checkout.')
    destination_dir = private / ('routine_backups' if routine else 'migration_backups')
    destination_dir.mkdir(parents=True, exist_ok=True)
    if not destination_dir.resolve().is_relative_to(private):
        raise ValueError('A private backup directory cannot point outside the private save root.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    prefix = 'commons-routine-' if routine else 'commons-before-adventure-'
    destination = destination_dir / (prefix + stamp + '.zip')
    pending = destination_dir / (destination.name + '.' + uuid.uuid4().hex[:8] + '.partial')
    manifest = {'format': 'commons-private-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'routine': routine, 'files': {}}
    try:
        with tempfile.TemporaryDirectory(prefix='commons-snapshot-') as temp:
            copied = Path(temp) / 'commons-game.sqlite3'

            with store.lock, closing(sqlite3.connect(store.path, timeout=30)) as source, closing(sqlite3.connect(copied)) as target:
                source.backup(target)
            manifest['database'] = _database_summary(copied)
            with zipfile.ZipFile(pending, 'x', zipfile.ZIP_DEFLATED) as saved:
                def add(name, data):
                    saved.writestr(name, data)
                    manifest['files'][name] = {'bytes': len(data), 'sha256': _sha(data)}
                master = private.parent / 'master' if private.name == 'private' else root.parent / 'commons_master'
                directories = [(store.world.resolve(), 'world'), (private, 'commons_private'), (master, 'commons_master')]
                if store.world.resolve() != root / 'world':
                    directories.append((root / 'world', 'authored_world'))
                for directory, label in directories:
                    if not directory.exists():
                        continue
                    for path in sorted(directory.rglob('*')):
                        relative = path.relative_to(directory)
                        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(directory.resolve())
                                or any(part in _EXCLUDED_DIRS for part in relative.parts)
                                or path.name.startswith('commons-game.sqlite3')):
                            continue
                        add(label + '/' + relative.as_posix(), path.read_bytes())
                add('commons_private/commons-game.sqlite3', copied.read_bytes())
                saved.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        restore_drill(pending)
        pending.replace(destination)
        if routine:
            _record_success(private, destination)
            prune_routine(private)
        return destination
    except BaseException:
        if pending.exists():
            pending.unlink()
        raise

def _record_success(private, archive):
    path = Path(private) / 'routine_backups' / 'status.json'
    staging = path.with_name('status-' + uuid.uuid4().hex[:8] + '.partial')
    staging.write_text(json.dumps({'last_success': time.time(), 'archive': archive.name,
        'verified': True, 'restore_drill': 'passed'}), encoding='utf-8')
    staging.replace(path)

def prune_routine(private, retain=RETAIN):
    """Remove only older verified routine archives; manual recovery stays put."""
    if isinstance(retain, bool) or not isinstance(retain, int) or retain < 1:
        raise ValueError('Keep at least one routine backup.')
    directory = (Path(private) / 'routine_backups').resolve()
    archives = sorted(directory.glob('commons-routine-*.zip'), key=lambda path: path.name, reverse=True)
    removed = []
    for path in archives[retain:]:
        if path.is_symlink() or path.parent.resolve() != directory:
            continue
        verify_snapshot(path)
        path.unlink()
        removed.append(path.name)
    return removed

def _is_live(store, root):
    root = Path(root).resolve()
    return (root == CANONICAL_ROOT.resolve() and store.world.resolve() == root / 'world'
            and store.path.resolve() == root.parent / 'commons_private' / 'commons-game.sqlite3')

def status(store, root):
    saved = _marker(store.path.parent)
    with _thread_lock:
        worker = _threads.get(str(store.path.resolve()))
        running = bool(worker and worker[0].is_alive())
    return {'enabled': _is_live(store, root), 'interval_hours': 24, 'retained': RETAIN,
            'daemon_running': running,
            **{key: saved[key] for key in ('last_success', 'verified', 'restore_drill', 'error') if key in saved}}

def _worker(store, root, stop):
    
    while not stop.wait(60):
        marker = {}
        try:
            marker = _marker(store.path.parent)
            last = marker.get('last_success', 0)
            if isinstance(last, (int, float)) and not isinstance(last, bool) and last + DAY > time.time():
                continue
            create_snapshot(store, root, routine=True)
        except Exception as error:
            directory = store.path.parent / 'routine_backups'
            directory.mkdir(parents=True, exist_ok=True)
            marker['error'] = type(error).__name__ + ': snapshot verification did not complete.'
            (directory / 'status.json').write_text(json.dumps(marker), encoding='utf-8')
            
            if stop.wait(3600):
                return

def start_if_live(store, root):
    """Never start for previews/tests, or for any noncanonical save location."""
    if not _is_live(store, root):
        return False
    with _thread_lock:
        key = str(store.path.resolve())
        if key not in _threads or not _threads[key][0].is_alive():
            stop = threading.Event()
            thread = threading.Thread(target=_worker, args=(store, Path(root), stop), name='commons-private-backups', daemon=True)
            _threads[key] = (thread, stop)
            thread.start()
    return True
