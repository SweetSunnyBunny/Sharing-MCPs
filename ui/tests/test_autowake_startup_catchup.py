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
