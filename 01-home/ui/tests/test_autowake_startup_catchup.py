import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from services import autowake


class _FakeDb:
    def __init__(self, schedules, statuses=None):
        self.schedules = schedules
        self.statuses = statuses or {}

    async def execute_fetchall(self, sql, params=()):
        if "FROM autowake_schedule" in sql:
            return self.schedules
        if "FROM autowake_log" in sql:
            status = self.statuses.get(params[0])
            return [(status,)] if status else []
        raise AssertionError(sql)


class _FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


def _install_fakes(monkeypatch, fake_db, fake_scheduler):
    async def fake_get_db():
        return fake_db

    async def fake_release_db(_db):
        return None

    monkeypatch.setattr(autowake, "get_db", fake_get_db)
    monkeypatch.setattr(autowake, "release_db", fake_release_db)
    monkeypatch.setattr(autowake, "scheduler", fake_scheduler)


def test_startup_catchup_queues_only_recent_missing_occurrence(monkeypatch):
    fake_db = _FakeDb(
        schedules=[
            (61, 22, 0),
            (62, 22, 15),
            (63, 22, 30),
        ]
    )
    fake_scheduler = _FakeScheduler()
    _install_fakes(monkeypatch, fake_db, fake_scheduler)

    queued = asyncio.run(
        autowake._queue_recent_startup_catchups(
            now=datetime(2026, 9, 6, 22, 15, 7, tzinfo=ZoneInfo("America/Chicago")),
            delay_seconds=20,
        )
    )

    assert queued == [62]
    assert fake_scheduler.jobs[0][1]["id"] == "startup_catchup_62"
    assert fake_scheduler.jobs[0][1]["kwargs"] == {"retry_of": 1}


def test_startup_catchup_skips_running_or_completed_occurrence(monkeypatch):
    fake_db = _FakeDb(
        schedules=[(62, 22, 15), (63, 22, 15)],
        statuses={62: "running", 63: "completed"},
    )
    fake_scheduler = _FakeScheduler()
    _install_fakes(monkeypatch, fake_db, fake_scheduler)

    queued = asyncio.run(
        autowake._queue_recent_startup_catchups(
            now=datetime(2026, 9, 6, 22, 15, 7, tzinfo=ZoneInfo("America/Chicago"))
        )
    )

    assert queued == []
    assert fake_scheduler.jobs == []


def test_startup_catchup_handles_a_restart_across_midnight(monkeypatch):
    fake_db = _FakeDb(schedules=[(70, 23, 59), (71, 1, 0)])
    fake_scheduler = _FakeScheduler()
    _install_fakes(monkeypatch, fake_db, fake_scheduler)

    queued = asyncio.run(
        autowake._queue_recent_startup_catchups(
            now=datetime(2026, 9, 7, 0, 3, 0, tzinfo=ZoneInfo("America/Chicago"))
        )
    )

    assert queued == [70]


def _wake_log_db(rows):
    """A real in-memory autowake_log, so the UPDATE statements actually run."""
    import aiosqlite

    async def build():
        db = await aiosqlite.connect(":memory:")
        await db.execute(
            "CREATE TABLE autowake_log (id INTEGER PRIMARY KEY, schedule_id INTEGER, "
            "started_at_epoch INTEGER, completed_at TEXT, completed_at_epoch INTEGER, status TEXT)"
        )
        await db.executemany(
            "INSERT INTO autowake_log (id, schedule_id, started_at_epoch, status) VALUES (?, ?, ?, ?)",
            rows,
        )
        await db.commit()
        return db

    return build


def test_restart_marks_running_wakes_interrupted_so_catchup_retries_them(monkeypatch):
    now = datetime(2026, 9, 6, 22, 15, 7, tzinfo=ZoneInfo("America/Chicago"))
    crossed = int(now.replace(second=0).timestamp())  # schedule 62 fired at 22:15

    async def scenario():
        db = await _wake_log_db([
            (1, 62, crossed, "running"),      # killed mid-wake by the restart
            (2, 61, crossed - 900, "completed"),
        ])()

        class _Schedules:
            async def execute_fetchall(self, sql, params=()):
                if "FROM autowake_schedule" in sql:
                    return [(61, 22, 0), (62, 22, 15)]
                return await db.execute_fetchall(sql, params)

            def __getattr__(self, name):
                return getattr(db, name)

        fake_scheduler = _FakeScheduler()
        _install_fakes(monkeypatch, _Schedules(), fake_scheduler)

        before = await autowake._queue_recent_startup_catchups(now=now, delay_seconds=20)
        marked = await autowake._recover_interrupted_wakes()
        after = await autowake._queue_recent_startup_catchups(now=now, delay_seconds=20)
        statuses = dict(await db.execute_fetchall("SELECT id, status FROM autowake_log"))
        await db.close()
        return before, marked, after, statuses

    before, marked, after, statuses = asyncio.run(scenario())
    assert before == []            # a 'running' row used to hide the wake forever
    assert marked == 1
    assert after == [62]
    assert statuses == {1: "interrupted", 2: "completed"}


def test_close_wake_log_only_touches_a_row_still_running(monkeypatch):
    async def scenario():
        db = await _wake_log_db([(1, 62, 100, "running"), (2, 62, 200, "completed")])()
        _install_fakes(monkeypatch, db, _FakeScheduler())
        await autowake._close_wake_log(1, "failed")
        await autowake._close_wake_log(2, "failed")
        await autowake._close_wake_log(None, "failed")
        rows = await db.execute_fetchall("SELECT id, status, completed_at IS NOT NULL FROM autowake_log ORDER BY id")
        await db.close()
        return rows

    assert asyncio.run(scenario()) == [(1, "failed", 1), (2, "completed", 0)]
