"""Public installation defaults must not seed a private household timetable."""
import asyncio
from services import autowake

def test_empty_schedule_seeds_do_not_open_or_reconcile_database(monkeypatch):
    async def forbidden():
        raise AssertionError("an empty public seed must not touch the installer's schedules")
    monkeypatch.setattr(autowake, "get_db", forbidden)
    assert autowake.DEFAULT_SCHEDULES == []
    assert autowake._NIGHTLY_CONSOLIDATION_SCHEDULE == []
    assert autowake._MORNING_DIGEST_SCHEDULE == []
    assert autowake._RIVER_REVIEW_SCHEDULE == []
    asyncio.run(autowake.seed_default_schedules())
    asyncio.run(autowake.seed_nightly_consolidation_schedules())
    asyncio.run(autowake.seed_morning_digest_schedule())
    asyncio.run(autowake.seed_river_review_schedules())

def test_care_and_outreach_need_installation_configuration():
    assert autowake.CARE_SIGNAL_DEFAULTS["care_signals_enabled"] == "false"
    assert autowake.FAILSAFE_DEFAULTS["failsafe_enabled"] == "false"

def test_inactivity_escalation_needs_opt_in_and_own_work_schedule():
    from services.inactivity_escalation import EscalationConfig
    config = EscalationConfig()
    assert config.enabled is False
    assert config.work_days == ""
