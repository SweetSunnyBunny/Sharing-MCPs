"""Push notification service — sends browser push notifications via VAPID/webpush."""


import asyncio
import json
import logging

from pywebpush import webpush, WebPushException

from config import VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CONTACT
from db.database import get_db, release_db

log = logging.getLogger("anam.notifications")


async def send_notification(title: str, body: str, icon: str = "/static/assets/icons/app_icon.png", url: str = "/"):
    """Send a push notification to all subscribed browsers."""
    if not VAPID_PRIVATE_KEY:
        log.debug("No VAPID key configured — skipping push")
        return

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT id, subscription_json FROM push_subscriptions")
    finally:
        await release_db(db)

    if not rows:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "icon": icon,
        "url": url,
    })

    dead = []
    for row_id, sub_json in rows:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=json.loads(sub_json),
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CONTACT},
            )
        except WebPushException as e:
            # 410 Gone or 404 means subscription expired — remove it
            if hasattr(e, "response") and e.response is not None and e.response.status_code in (404, 410):
                dead.append(row_id)
                log.info("Removing expired push subscription %s", row_id)
            else:
                log.warning("Push failed for subscription %s: %s", row_id, e)
        except Exception as e:
            log.warning("Push error for subscription %s: %s", row_id, e)

    if dead:
        db = await get_db()
        try:
            for row_id in dead:
                await db.execute("DELETE FROM push_subscriptions WHERE id = ?", (row_id,))
            await db.commit()
        finally:
            await release_db(db)
