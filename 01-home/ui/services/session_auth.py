"""Session token hashing utilities."""


import hashlib
import re

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_sha256_hex(value: str) -> bool:
    return bool(_SHA256_HEX_RE.fullmatch(value or ""))

