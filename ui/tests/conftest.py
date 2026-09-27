import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT)

if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)

# Point the whole suite at a throwaway database BEFORE config is imported
# anywhere. Without this, tests that touch db.database/init_db run real
# migrations against the live data/anam.db — which violates the standing
# never-test-write-the-live-DB rule and, worse, would apply any future
# destructive migration to production mid-test-run.
# Always isolate, even if a launcher inherited production path overrides.
# DATA_DIR also catches import-time log rotation and test-generated media.
_test_runtime = tempfile.TemporaryDirectory(prefix="anam_tests_")
_test_root = Path(_test_runtime.name)
os.environ["ANAM_DATA_DIR"] = str(_test_root)
os.environ["ANAM_DB_PATH"] = str(_test_root / "anam_test.db")


os.environ["ANAM_CANVAS_VAULT_DIR"] = str(_test_root / "canvases")
os.environ["ANAM_IDENTITY_VAULT_DIR"] = str(_test_root / "identities")
os.environ["ANAM_ROLEPLAY_VAULT_DIR"] = str(_test_root / "roleplay")
os.environ["ANAM_DB_BACKUP_DIR"] = str(_test_root / "db-backup")


def pytest_sessionfinish(session, exitstatus):
    """Close every pooled db.database connection once the whole run is done.

    aiosqlite backs each connection with a non-daemon worker Thread. Anam's
    connection pool (db/database.py) only closes connections via
    close_all_db_connections(), which pytest never calls on its own — so any
    test that touched the db left its pooled connections (and their threads)
    open, and the interpreter can't exit until something force-kills it.
    That's what made full-suite runs hang after printing their results.
    """
    import asyncio

    from db.database import close_all_db_connections

    try:
        asyncio.run(close_all_db_connections())
    except Exception:
        pass

    # server imports install file handlers; close them before removing the
    # temporary runtime directory (Windows otherwise keeps anam.log locked).
    import logging

    logging.shutdown()
    _test_runtime.cleanup()
