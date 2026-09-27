"""Shared rate limiter instance for use across API routers."""

# ANAM GUIDE: SHARED REQUEST RATE LIMITER
# What: A tiny shared "slow down" guard — one limiter object that API routes use to cap how often the same visitor can hit them.
# Called by: server.py plus api/audio.py, api/auth.py, api/documents.py, api/gifs.py, api/images.py, api/messages.py.
# Edit here when: almost never — the actual limits (like "10/minute") are set on each route in those api/ files, not here.

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
