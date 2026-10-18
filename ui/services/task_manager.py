"""Background task manager — stores references and logs errors.

All fire-and-forget asyncio.create_task calls should go through
`spawn()` so we get error logging and prevent GC of task objects.
"""


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
