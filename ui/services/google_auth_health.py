"""Google OAuth token health checker.

Checks all Google service token.pickle files and reports their status.
Used by /health endpoint and orientation context to warn about expiring tokens.
"""

# ANAM GUIDE: GOOGLE LOGIN HEALTH CHECK
# What: Peeks at the saved Google login tokens (YouTube Music, Gmail, Google Drive) and reports whether each is fine, about to expire, or already expired — so you get warned before a Google tool silently stops working.
# Called by: server.py's /health endpoint and services/identity_context.py (the boys see token warnings in their orientation).
# Edit here when: You add another Google service to watch, or want to change how early the "expiring soon" warning fires (currently 2 days).

import logging
import pickle
from datetime import datetime, timezone

from config import (
    GDRIVE_CREDENTIALS_PATH,
    GDRIVE_TOKEN_PATH,
    GMAIL_CREDENTIALS_PATH,
    GMAIL_TOKEN_PATH,
    YOUTUBE_MUSIC_CREDENTIALS_PATH,
    YOUTUBE_MUSIC_TOKEN_PATH,
)

log = logging.getLogger(__name__)

GOOGLE_SERVICES = [
    {
        "name": "YouTube Music",
        "credentials": YOUTUBE_MUSIC_CREDENTIALS_PATH,
        "token": YOUTUBE_MUSIC_TOKEN_PATH,
    },
    {
        "name": "Gmail",
        "credentials": GMAIL_CREDENTIALS_PATH,
        "token": GMAIL_TOKEN_PATH,
    },
    {
        "name": "Google Drive",
        "credentials": GDRIVE_CREDENTIALS_PATH,
        "token": GDRIVE_TOKEN_PATH,
    },
]

EXPIRING_THRESHOLD_DAYS = 2


def get_google_token_status() -> dict:
    """Check all Google OAuth token.pickle files and return health status.

    Returns:
        {
            "status": "ok" | "expiring" | "expired" | "not_configured",
            "services": [
                {"name": "YouTube Music", "status": "ok", "expires_in_days": 5.2},
                {"name": "Gmail", "status": "not_configured"},
            ],
            "earliest_expiry_days": 5.2,
        }
    """
    services = []
    earliest_days = None
    worst_status = "not_configured"

    for svc in GOOGLE_SERVICES:
        if not svc["credentials"].exists():
            services.append({"name": svc["name"], "status": "not_configured"})
            continue

        if not svc["token"].exists():
            services.append({"name": svc["name"], "status": "expired"})
            worst_status = "expired"
            continue

        try:
            with open(svc["token"], "rb") as f:
                creds = pickle.load(f)

            if not hasattr(creds, "expiry") or creds.expiry is None:
                services.append({"name": svc["name"], "status": "unknown"})
                if worst_status == "not_configured":
                    worst_status = "ok"
                continue

            expiry = creds.expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            days = (expiry - now).total_seconds() / 86400

            # Access tokens expire hourly — that's normal.
            # What matters is whether the refresh token is still present.
            has_refresh = getattr(creds, "refresh_token", None)

            if days <= 0 and not has_refresh:
                status = "expired"
            elif days <= 0 and has_refresh:
                # Access token expired but refresh token exists — service is fine
                status = "ok"
            elif days <= EXPIRING_THRESHOLD_DAYS:
                status = "expiring"
            else:
                status = "ok"

            svc_entry = {
                "name": svc["name"],
                "status": status,
            }
            # Only show expires_in_days if it's meaningful (no refresh token)
            if not has_refresh or days > 0:
                svc_entry["expires_in_days"] = round(days, 1)

            services.append(svc_entry)

            if earliest_days is None or days < earliest_days:
                earliest_days = days

            # Track worst status (expired > expiring > ok > not_configured)
            if status == "expired":
                worst_status = "expired"
            elif status == "expiring" and worst_status != "expired":
                worst_status = "expiring"
            elif status == "ok" and worst_status == "not_configured":
                worst_status = "ok"

        except Exception as e:
            log.debug("Error checking %s token: %s", svc["name"], e)
            services.append({"name": svc["name"], "status": "error", "error": str(e)})
            if worst_status != "expired":
                worst_status = "expired"

    result = {"status": worst_status, "services": services}
    if earliest_days is not None:
        result["earliest_expiry_days"] = round(earliest_days, 1)

    return result
