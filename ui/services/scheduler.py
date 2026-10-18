"""The one APScheduler instance for the whole server."""


from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TIMEZONE

scheduler = AsyncIOScheduler(timezone=TIMEZONE)
