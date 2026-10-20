'Reloading autowake schedules must not wipe unrelated scheduler jobs.'
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services import scheduler_jobs


def _noop():
    return None


def test_clear_schedule_jobs_only_removes_per_schedule_crons(monkeypatch):
    sched = AsyncIOScheduler()
    monkeypatch.setattr(scheduler_jobs, "scheduler", sched)
    for job_id in (
        "autowake_12",            # per-schedule cron: must go
        "autowake_85",            # per-schedule cron: must go
        "autowake_retry_12_999",  # pending one-shot retry: must stay
        "startup_catchup_12",     # startup catch-up: must stay
        "db_backup",              # lifespan job: must stay
        "oauth_token_refresh",    # lifespan job: must stay
        "timer_check",            # fixed-id job, re-added by load: stays
    ):
        sched.add_job(_noop, "interval", minutes=5, id=job_id)

    removed = scheduler_jobs._clear_schedule_jobs()

    remaining = {job.id for job in sched.get_jobs()}
    assert removed == 2
    assert remaining == {
        "autowake_retry_12_999",
        "startup_catchup_12",
        "db_backup",
        "oauth_token_refresh",
        "timer_check",
    }


def test_trigger_service_accepts_every_condition_the_engine_evaluates():
    """VALID_CONDITION_TYPES must cover every ctype trigger_engine dispatches on."""
    import inspect
    import re

    from services import trigger_engine, trigger_service

    source = inspect.getsource(trigger_engine)
    evaluated = set(re.findall(r'ctype == "([a-z_]+)"', source))
    assert evaluated, "dispatcher pattern changed; update this test"
    missing = evaluated - trigger_service.VALID_CONDITION_TYPES
    assert not missing, f"engine evaluates but service rejects: {sorted(missing)}"
