"""Push notification subscription endpoints."""

# ANAM GUIDE: PUSH NOTIFICATION SIGNUP API
# What: Lets the browser turn phone/desktop push notifications on or off (saves the subscription in the database; the actual sending happens in services/notifications.py).
# Called by: static/js/settings.js (the notifications toggle) and static/sw.js (the service worker that shows the pushes).
# Edit here when: changing how subscriptions are stored or validated. To change WHAT gets pushed and when, go to services/notifications.py instead.

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import VAPID_PUBLIC_KEY
from db.database import get_db, release_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/vapid-key")
async def get_vapid_key():
    """Return the VAPID public key so the browser can subscribe."""
    return {"publicKey": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def subscribe(request: Request):
    """Save a push subscription from the browser."""
    body = await request.json()
    subscription = body.get("subscription")
    if not subscription or not subscription.get("endpoint"):
        return JSONResponse(status_code=400, content={"error": "Missing subscription"})

    endpoint = subscription["endpoint"]
    sub_json = json.dumps(subscription)
    now = datetime.now(timezone.utc).isoformat()

    db = await get_db()
    try:
        # Upsert — replace if same endpoint exists
        await db.execute(
            "INSERT INTO push_subscriptions (endpoint, subscription_json, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json",
            (endpoint, sub_json, now),
        )
        await db.commit()
    finally:
        await release_db(db)

    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(request: Request):
    """Remove a push subscription."""
    body = await request.json()
    endpoint = body.get("endpoint", "")
    if not endpoint:
        return JSONResponse(status_code=400, content={"error": "Missing endpoint"})

    db = await get_db()
    try:
        await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()
    finally:
        await release_db(db)

    return {"ok": True}
