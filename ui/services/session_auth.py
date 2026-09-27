"""Session token hashing utilities."""

# ANAM GUIDE: LOGIN TOKEN HASHER
# What: Two tiny helpers that scramble (hash) login session tokens before they're stored, so the raw token never sits in the database.
# Called by: api/auth.py, api/chat.py, core/middleware.py, db/schema.py
# Edit here when: Almost never — only if the way session tokens are hashed needs to change. Login flow itself lives in api/auth.py.

import hashlib
import re

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_sha256_hex(value: str) -> bool:
    return bool(_SHA256_HEX_RE.fullmatch(value or ""))

