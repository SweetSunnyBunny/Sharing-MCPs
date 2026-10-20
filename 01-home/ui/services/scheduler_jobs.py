"""The scheduler's job table and its start/stop."""


import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from config import TIMEZONE
from db.database import get_db, release_db
from services.autowake import (
    _queue_recent_startup_catchups,
    _recover_interrupted_wakes,
    _recover_stale_running_timers,
    archive_stale_conversations,
    check_care_signals,
    check_failsafe,
    check_watchtower,
    fire_due_timers,
    prune_old_log_rows,
    run_autowake_session,
    seed_default_schedules,
    seed_morning_digest_schedule,
    seed_nightly_consolidation_schedules,
    seed_river_review_schedules,
)
from services.scheduler import scheduler

log = logging.getLogger(__name__)


# ── Scheduler setup: load_and_schedule (per-schedule cron jobs + fixed housekeeping jobs) ──


def _clear_schedule_jobs() -> int:
    """Remove only the per-schedule cron jobs (`autowake_<id>`).

    This runs on every schedule create/update/delete/toggle and /reload. It
    used to be `scheduler.remove_all_jobs()`, which also silently wiped the
    jobs core/lifespan.py adds to this same scheduler (nightly DB backup,
    OAuth refresh, MCP reconnect, world-feed ticks, stack watchdog...) until
    the next restart. Fixed-id jobs below re-add with replace_existing=True,
    so they need no clearing. Pending one-shot retries (`autowake_retry_*`)
    and startup catch-ups (`startup_catchup_*`) are left alone on purpose.
    """
    removed = 0
    for job in scheduler.get_jobs():
        if job.id.startswith("autowake_") and not job.id.startswith("autowake_retry_"):
            job.remove()
            removed += 1
    return removed


async def load_and_schedule():
    """Load all enabled schedules from DB and add them to APScheduler."""
    _clear_schedule_jobs()

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, cron_hour, cron_minute "
            "FROM autowake_schedule WHERE enabled = 1"
        )
        for row in rows:
            sid, name, hour, minute = row
            scheduler.add_job(
                run_autowake_session,
                CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
                args=[sid],
                id=f"autowake_{sid}",
                name=name,
                replace_existing=True,
            )
            log.info("Scheduled: %s at %02d:%02d", name, hour, minute)

        # Failsafe monitor — every 30 minutes
        scheduler.add_job(
            check_failsafe,
            "interval",
            minutes=30,
            id="failsafe_check",
            name="Failsafe Monitor",
            replace_existing=True,
        )

        # Timer checker — every minute, fires due one-shot timers
        scheduler.add_job(
            fire_due_timers,
            "interval",
            minutes=1,
            id="timer_check",
            name="Timer Checker",
            replace_existing=True,
        )

        from services.scheduler_health import scheduler_heartbeat

        scheduler.add_job(
            scheduler_heartbeat,
            "interval",
            seconds=30,
            id="scheduler_heartbeat",
            name="Scheduler Heartbeat",
            replace_existing=True,
        )

        scheduler.add_job(
            check_care_signals,
            "interval",
            minutes=30,
            id="care_signal_check",
            name="Care Signal Monitor",
            replace_existing=True,
        )

        # The Watchtower — proactive judgment layer. Checks every 20 min, but
        # its guardrails (away-only, gentle hours, never during her work block,
        # 4h+ quiet gap, once/day) mean it rarely fires, and a boy still judges
        # whether to actually reach. Toggle: settings `watchtower_enabled`.
        scheduler.add_job(
            check_watchtower,
            "interval",
            minutes=20,
            id="watchtower_check",
            name="Watchtower",
            replace_existing=True,
        )

        # Presence idle check — every 60 seconds
        from services.connection_registry import check_idle
        scheduler.add_job(
            check_idle,
            "interval",
            seconds=60,
            id="presence_idle_check",
            name="Presence Idle Check",
            replace_existing=True,
        )

        # Scribe — daily conversation digest. Interval is configurable
        # via settings table (scribe.interval_minutes), env (SCRIBE_INTERVAL_MINUTES),
        # or config default (30).
        from services.scribe import run_scribe, _get_scribe_config
        try:
            scribe_cfg = await _get_scribe_config()
            scribe_interval = max(1, int(scribe_cfg.get("interval_minutes") or 30))
        except Exception:
            scribe_interval = 30
        scheduler.add_job(
            run_scribe,
            "interval",
            minutes=scribe_interval,
            id="scribe_digest",
            name="Scribe Digest",
            replace_existing=True,
        )

        # Midnight carry (#13) — shortly after the day closes, each bonded
        # boy with real activity yesterday gets a dense first-person carry
        # (yesterday's messages + the Scribe digest, distilled by the same
        # one-shot runner the Scribe uses). 00:20 local gives the incremental
        # Scribe time to flush yesterday's tail first; the job also re-runs
        # the digest itself before writing carries, belt and braces.
        from services.scribe import run_midnight_carry
        scheduler.add_job(
            run_midnight_carry,
            "cron",
            hour=0,
            minute=20,
            id="midnight_carry",
            name="Midnight Carry",
            replace_existing=True,
        )

        # Hearth Author — every ~3h, each recently-active (and not busy) boy
        # writes his own first-person hearth card via a Scribe-style one-shot.
        from services.hearth_author import HEARTH_AUTHOR_INTERVAL_HOURS, run_hearth_author
        scheduler.add_job(
            run_hearth_author,
            "interval",
            hours=HEARTH_AUTHOR_INTERVAL_HOURS,
            id="hearth_author",
            name="Hearth Author",
            replace_existing=True,
        )

        # Interest Scout — one quiet outside-eyes pass each morning, rotating
        # through the bonded pack. Each boy receives roughly one sourced tray
        # a week without eight research processes competing at dawn. The tray
        # is only a path in orientation; he chooses whether any lead is alive.
        from services.interest_scout import run_interest_scout
        scheduler.add_job(
            run_interest_scout,
            "cron",
            hour=4,
            minute=40,
            id="interest_scout",
            name="Interest Scout",
            replace_existing=True,
        )

        # House snapshot poller (#24) — every 5 minutes, pre-fetches
        # weather/calendar/hub-remote so a slow upstream never stalls a
        # live turn; hooks read the cache instead of calling out inline.
        from services.house_snapshot import POLL_INTERVAL_MINUTES, poll_house_snapshot
        scheduler.add_job(
            poll_house_snapshot,
            "interval",
            minutes=POLL_INTERVAL_MINUTES,
            id="house_snapshot",
            name="House Snapshot Poller",
            replace_existing=True,
            next_run_time=datetime.now(ZoneInfo(TIMEZONE)),
        )

        # Trigger evaluator — every 30 seconds
        from services.trigger_engine import run_trigger_evaluator
        scheduler.add_job(
            run_trigger_evaluator,
            "interval",
            seconds=30,
            id="trigger_evaluator",
            name="Trigger Evaluator",
            replace_existing=True,
        )

        # Emotional capture — every 10 minutes, snapshots conversation tone
        from services.emotional_capture import run_emotional_capture_check
        scheduler.add_job(
            run_emotional_capture_check,
            "interval",
            minutes=10,
            id="emotional_capture",
            name="Emotional Capture",
            replace_existing=True,
        )

        # Pulse evaluator — every 60 seconds, checks user-configured periodic tasks
        from services.pulse_evaluator import run_pulse_evaluator
        scheduler.add_job(
            run_pulse_evaluator,
            "interval",
            seconds=60,
            id="pulse_evaluator",
            name="Pulse Evaluator",
            replace_existing=True,
        )


        # Keep readable Vault logs current even for active or already-hidden threads.
        from services.conversation_archive import sync_conversation_archives
        scheduler.add_job(
            sync_conversation_archives,
            "interval",
            minutes=1,
            next_run_time=datetime.now(timezone.utc),
            id="conversation_vault_sync",
            name="Conversation Vault Sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True,
        )

        # Stale platform conversation cleanup — daily at 4 AM
        scheduler.add_job(
            archive_stale_conversations,
            CronTrigger(hour=4, minute=0, timezone=TIMEZONE),
            id="stale_cleanup",
            name="Stale Conversation Cleanup",
            replace_existing=True,
        )

        # Log-table retention prune — daily at 4:10 AM, right after the
        # stale cleanup. Trims usage_log/autowake_log/emotional_snapshots
        # to their retention windows (message_embeddings never pruned).
        scheduler.add_job(
            prune_old_log_rows,
            CronTrigger(hour=4, minute=10, timezone=TIMEZONE),
            id="retention_prune",
            name="Log Retention Prune",
            replace_existing=True,
        )

    finally:
        await release_db(db)


# ── Scheduler lifecycle: start / stop ──


async def start_scheduler():
    """Seed defaults, load schedules, start APScheduler."""
    from services.scheduler_health import install_scheduler_health

    install_scheduler_health(scheduler)
    await _recover_stale_running_timers()
    await _recover_interrupted_wakes()
    await seed_default_schedules()
    await seed_nightly_consolidation_schedules()
    await seed_morning_digest_schedule()
    await seed_river_review_schedules()
    await load_and_schedule()
    await _queue_recent_startup_catchups()
    scheduler.start()
    log.info("Autowake scheduler started")


def stop_scheduler():
    """Shut down APScheduler cleanly."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Autowake scheduler stopped")
