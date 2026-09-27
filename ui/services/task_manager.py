"""Background task manager — stores references and logs errors.

All fire-and-forget asyncio.create_task calls should go through
`spawn()` so we get error logging and prevent GC of task objects.
"""

# ANAM GUIDE: BACKGROUND TASK SPAWNER
# What: Tiny safety net for "fire and forget" background jobs — starts them, keeps a
#       reference so Python doesn't lose them, and writes any crash to the log.
# Called by: Almost everything that kicks off background work — chat pipeline, autowake,
#            hub, echo relay, connection registry, and more (they all call spawn()).
# Edit here when: You want to change how background-job crashes are logged. You will
#                 almost never need to touch this file — it's 30 lines of plumbing.

import asyncio
import logging

log = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(coro, *, name: str | None = None) -> asyncio.Task:
    """Create a background task with error logging and reference tracking."""
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task):
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(
            "Background task %r failed: %s",
            task.get_name(),
            exc,
            exc_info=exc,
        )
