"""Anam configuration - identity colors, paths, and runtime settings."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent


def _load_env_stack() -> None:
    """Load shared path env first, then local overrides."""
    seen: set[Path] = set()
    configured_shared = os.environ.get("ANAM_SHARED_ENV_FILE", "").strip()
    candidates = []
    if configured_shared:
        candidates.append(Path(configured_shared).expanduser())
    candidates.extend(
        [
            Path.home() / ".config" / "anam" / "paths.env",
            Path.home() / ".anam-paths.env",
            BASE_DIR / ".env.paths.local",
            BASE_DIR / ".env.paths",
            BASE_DIR / ".env.local",
            BASE_DIR / ".env",
        ]
    )
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.exists():
            continue
        load_dotenv(candidate)
        seen.add(candidate)


_load_env_stack()


PATH_CONFIG: dict[str, dict[str, str]] = {}
ANAM_ENV = (os.environ.get("ANAM_ENV", "development").strip().lower() or "development")
IS_PRODUCTION = ANAM_ENV in {"prod", "production"}


def _env_path(env_name: str, default: str) -> Path:
    raw = os.environ.get(env_name, "").strip()
    path = Path(raw) if raw else Path(default)
    PATH_CONFIG[env_name] = {
        "path": str(path),
        "source": "env" if raw else "default",
    }
    return path


# ANAM GUIDE: APP-WIDE PATHS AND FILE LOCATIONS
# Change where Anam finds runtime data, prompts, MCPs, memory, and external
# companion systems here. Prefer the matching env var over a machine-only edit.
# Paths
DATA_DIR = _env_path("ANAM_DATA_DIR", str(BASE_DIR / "data"))
PROMPTS_DIR = BASE_DIR / "prompts"
STATIC_DIR = BASE_DIR / "static"
DEFAULT_EXTERNAL_MCP_ROOT = BASE_DIR.parent
DEFAULT_SHARING_MCP_ROOT = BASE_DIR.parent
EXTERNAL_MCP_ROOT = _env_path("ANAM_EXTERNAL_MCP_ROOT", str(DEFAULT_EXTERNAL_MCP_ROOT))
SHARING_MCP_ROOT = _env_path("ANAM_SHARING_MCP_ROOT", str(DEFAULT_SHARING_MCP_ROOT))

# Anam owns its wellness and dashboard state. Memories live in cloud Qualia.
RITUALS_DIR = DATA_DIR / "wellness"

MODES_DIR = PROMPTS_DIR / "modes"
PACK_DIR = _env_path("ANAM_PACK_DIR", str(DATA_DIR / "pack"))
DISCORD_SERVER_MAP_PATH = _env_path(
    "ANAM_DISCORD_SERVER_MAP_PATH", str(DATA_DIR / "discord" / "server_map.json")
)
YOUTUBE_MUSIC_DIR = _env_path("ANAM_YOUTUBE_MUSIC_DIR", str(DATA_DIR / "youtube-music"))
YOUTUBE_MUSIC_CREDENTIALS_PATH = _env_path(
    "ANAM_YOUTUBE_MUSIC_CREDENTIALS_PATH", str(YOUTUBE_MUSIC_DIR / "credentials.json")
)
YOUTUBE_MUSIC_TOKEN_PATH = _env_path(
    "ANAM_YOUTUBE_MUSIC_TOKEN_PATH", str(YOUTUBE_MUSIC_DIR / "token.pickle")
)
GMAIL_MCP_DIR = _env_path("ANAM_GMAIL_MCP_DIR", str(SHARING_MCP_ROOT / "gmail-mcp"))
GMAIL_CREDENTIALS_PATH = _env_path(
    "ANAM_GMAIL_CREDENTIALS_PATH", str(GMAIL_MCP_DIR / "auth" / "client_secret.json")
)
GMAIL_TOKEN_PATH = _env_path(
    "ANAM_GMAIL_TOKEN_PATH", str(GMAIL_MCP_DIR / "auth" / "token.pickle")
)
GDRIVE_MCP_DIR = _env_path("ANAM_GDRIVE_MCP_DIR", str(SHARING_MCP_ROOT / "gdrive-mcp"))
GDRIVE_CREDENTIALS_PATH = _env_path(
    "ANAM_GDRIVE_CREDENTIALS_PATH", str(GDRIVE_MCP_DIR / "auth" / "client_secret.json")
)
GDRIVE_TOKEN_PATH = _env_path(
    "ANAM_GDRIVE_TOKEN_PATH", str(GDRIVE_MCP_DIR / "auth" / "token.pickle")
)

# Overridable so the test suite can point at a throwaway DB — pytest must
# never run migrations against the live anam.db (tests/conftest.py sets this).
DB_PATH = _env_path("ANAM_DB_PATH", str(DATA_DIR / "anam.db"))
# Nightly backups of anam.db — the single file holding every conversation.
# Copies land in the Vault (outside the live data/ folder, so a data-dir
# disaster can't take both), pruned to the newest ANAM_DB_BACKUP_KEEP copies.
DB_BACKUP_DIR = _env_path(
    "ANAM_DB_BACKUP_DIR", str(DATA_DIR / "backups")
)
DB_BACKUP_KEEP = max(1, int(os.environ.get("ANAM_DB_BACKUP_KEEP", "1") or "1"))
VOICE_DIR = DATA_DIR / "voice"
VOICE_VAULT_DIR = _env_path(
    "ANAM_VOICE_VAULT_DIR", str(DATA_DIR / "voice-archive")
)
VOICE_ALEXA_ARCHIVE_DIR = _env_path(
    "ANAM_VOICE_ALEXA_ARCHIVE_DIR", str(DATA_DIR / "alexa-archive")
)
def _dotenv_value(key: str):
    """The value anam/.env itself gives `key`, or None if the line is absent."""
    try:
        for line in (BASE_DIR / ".env").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith(key + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


_kokoro_from_env_file = _dotenv_value("KOKORO_FASTAPI_URL")
KOKORO_FASTAPI_URL = (
    _kokoro_from_env_file if _kokoro_from_env_file is not None
    else os.environ.get("KOKORO_FASTAPI_URL", "http://127.0.0.1:8880")
).strip().rstrip("/")
KOKORO_FASTAPI_TIMEOUT = max(
    5.0, float(os.environ.get("KOKORO_FASTAPI_TIMEOUT", "300") or "300")
)
IMAGES_DIR = DATA_DIR / "images"
IMAGE_MAX_SIZE_MB = 10
IMAGE_MAX_DIMENSION = 1536

# Generated videos (Sora via the Discord backend writes here; serve-only)
VIDEOS_DIR = DATA_DIR / "videos"
IMAGE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DOCUMENTS_DIR = DATA_DIR / "documents"


DOCUMENT_MAX_SIZE_MB = 100
DOCUMENT_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".doc",
    ".docx",
    ".odt",
    ".pptx",
    ".xlsx",
    ".py",
    ".js",
    ".zip",
    ".skill",
    # Videos — watched via video_watch (frames + transcript)
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
}
AUDIO_DIR = DATA_DIR / "audio"


AUDIO_MAX_SIZE_MB = 50
AUDIO_ALLOWED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".oga",
    ".opus",
    ".flac",
    ".webm",
    ".aac",
}
# Mime-type lookup for the file we hand Groq Whisper. Order matters only for
# extensions Groq's API might quibble about; the common cases (mp3/wav/m4a)
# all just work.
AUDIO_MIME_BY_EXT = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".aac": "audio/aac",
}

# Local Claude skills import (auto-injected into prompts when relevant)
CLAUDE_SKILLS_DIR = _env_path("ANAM_CLAUDE_SKILLS_DIR", str(Path.home() / ".claude" / "skills"))
HERMES_SKILLS_DIR = _env_path(
    "ANAM_HERMES_SKILLS_DIR",
    str(Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes" / "hermes-agent" / "skills"),
)


_DEFAULT_HERMES_SKILLS = (
    "google-workspace,humanizer,ocr-and-documents,songwriting-and-ai-music,"
    "spike,systematic-debugging,test-driven-development"
)
HERMES_SKILLS_ALLOWLIST = frozenset(
    name.strip().lower()
    for name in os.environ.get("ANAM_HERMES_SKILLS_ALLOWLIST", _DEFAULT_HERMES_SKILLS).split(",")
    if name.strip()
)
SKILLS_INJECTION_ENABLED = os.environ.get(
    "ANAM_SKILLS_INJECTION_ENABLED", "true"
).strip().lower() in {"1", "true", "yes"}
SKILLS_INJECTION_MAX_ACTIVE = int(os.environ.get("ANAM_SKILLS_MAX_ACTIVE", "3"))
SKILLS_INJECTION_MAX_CHARS_PER_SKILL = int(
    os.environ.get("ANAM_SKILLS_MAX_CHARS_PER_SKILL", "7000")
)
SKILLS_INJECTION_MAX_TOTAL_CHARS = int(
    os.environ.get("ANAM_SKILLS_MAX_TOTAL_CHARS", "18000")
)


SKILLS_INJECTION_EXCERPT_CHARS = int(
    os.environ.get("ANAM_SKILLS_EXCERPT_CHARS", "1200")
)


SKILLS_CATALOG_MAX_TOTAL_CHARS = int(
    os.environ.get("ANAM_SKILLS_CATALOG_MAX_CHARS", "6500")
)
SKILLS_CATALOG_MAX_DESC_CHARS = int(
    os.environ.get("ANAM_SKILLS_CATALOG_DESC_CHARS", "90")
)
SKILLS_CATALOG_MAX_ITEMS = int(
    os.environ.get("ANAM_SKILLS_CATALOG_MAX_ITEMS", "120")
)

# Time
TIMEZONE = "America/Chicago"


def _is_loopback_url(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


# Server
HOST = "0.0.0.0"
PORT = 8790
PUBLIC_BASE_URL = os.environ.get("ANAM_PUBLIC_URL", "").rstrip("/")

# CDN for static assets (icons, backgrounds, portraits).
# When set, decorative images load from CDN instead of through the tunnel.
# Leave empty to serve everything locally (default).
CDN_BASE_URL = os.environ.get("ANAM_CDN_URL", "").rstrip("/")

# Optional cookie domain override. Leave empty for normal single-origin mode
# so the cookie is scoped to the current host.
AUTH_COOKIE_DOMAIN = os.environ.get("ANAM_AUTH_COOKIE_DOMAIN", "").strip() or None

# Hearth Hub — cloud sanctuary state API (Cloudflare Worker)
# When set, sanctuary viewer fetches state from hearth-hub instead of local filesystem.
SANCTUARY_API_BASE = os.environ.get("SANCTUARY_API_BASE", "").rstrip("/")
RITUALS_API_BASE = os.environ.get("RITUALS_API_BASE", "").rstrip("/")
HUB_API_BASE = os.environ.get("HUB_API_BASE", "").rstrip("/")
MIND_GARDEN_API_BASE = os.environ.get("MIND_GARDEN_API_BASE", "").rstrip("/")

# Twilio - emergency phone calls
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
EMERGENCY_PHONE_NUMBER = os.environ.get("EMERGENCY_PHONE_NUMBER", "")

# Auth - Discord OAuth2
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
# Public API/base origin used for OAuth callbacks and externally fetchable media.
# In single-origin deployments this can safely fall back to SITE_URL.
if not PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = SITE_URL
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
ALLOWED_DISCORD_IDS = [
    x.strip() for x in os.environ.get("ALLOWED_DISCORD_IDS", "").split(",") if x.strip()
]
SESSION_MAX_AGE_DAYS = int(os.environ.get("ANAM_SESSION_MAX_AGE_DAYS", "365"))
SESSION_RENEW_WINDOW_DAYS = int(
    os.environ.get("ANAM_SESSION_RENEW_WINDOW_DAYS", "30")
)
SESSION_RENEW_ENABLED = os.environ.get(
    "ANAM_SESSION_RENEW_ENABLED", "true"
).strip().lower() in {"1", "true", "yes"}


_COOKIE_SECURE_HINTS = [SITE_URL, PUBLIC_BASE_URL]
_AUTH_COOKIE_SECURE_RAW = os.environ.get("ANAM_AUTH_COOKIE_SECURE", "").strip().lower()
if _AUTH_COOKIE_SECURE_RAW in {"1", "true", "yes"}:
    AUTH_COOKIE_SECURE = True
elif _AUTH_COOKIE_SECURE_RAW in {"0", "false", "no"}:
    AUTH_COOKIE_SECURE = False
else:
    # Production-safe default: secure cookies unless this looks like local loopback.
    AUTH_COOKIE_SECURE = not any(
        _is_loopback_url(url) for url in _COOKIE_SECURE_HINTS if url
    )
_SAMESITE_DEFAULT = "lax"
AUTH_COOKIE_SAMESITE = (
    os.environ.get("ANAM_AUTH_COOKIE_SAMESITE", _SAMESITE_DEFAULT).strip().lower()
    or _SAMESITE_DEFAULT
)
AUTH_ENABLED = bool(DISCORD_CLIENT_ID)
# Explicit, opt-in escape hatch for disabling auth (local dev only).
# Without this set, validate_runtime_config() will refuse to start if
# AUTH_ENABLED is false — protecting against silently dropped client IDs
# leaving the Cloudflare-tunneled server open to the internet.
ANAM_ALLOW_NO_AUTH = os.environ.get(
    "ANAM_ALLOW_NO_AUTH", ""
).strip().lower() in {"1", "true", "yes"}

# Web Push (VAPID)
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACT = os.environ.get("VAPID_CONTACT", "mailto:owner@example.com")

# Scribe (daily conversation digest) — defaults; settings table overrides at runtime
SCRIBE_PROVIDER = os.environ.get("SCRIBE_PROVIDER", "auto")
SCRIBE_MODEL = os.environ.get("SCRIBE_MODEL", "claude-haiku-4-5")
SCRIBE_OPENROUTER_MODEL = os.environ.get(
    "SCRIBE_OPENROUTER_MODEL", "anthropic/claude-haiku-4.5"
)
SCRIBE_INTERVAL_MINUTES = int(os.environ.get("SCRIBE_INTERVAL_MINUTES", "30") or "30")
SCRIBE_MESSAGE_THRESHOLD = int(os.environ.get("SCRIBE_MESSAGE_THRESHOLD", "3") or "3")
SCRIBE_DIGEST_PATH = os.environ.get("SCRIBE_DIGEST_PATH", "data/digests")

# Groq (Whisper STT)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

# Hume AI (prosody / vocal-tone analysis) — optional enrichment on top of
# transcription; see services/hume_prosody.py. Unset = feature quietly off.
HUME_API_KEY = os.environ.get("HUME_API_KEY", "")

# Claude Code CLI
CLAUDE_CMD = "claude"
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "opus")
CLAUDE_MODEL_INTERACTIVE = os.environ.get("CLAUDE_MODEL_INTERACTIVE", "opus")


CLAUDE_MAX_TURNS = int(os.environ.get("ANAM_CLAUDE_MAX_TURNS", "1000"))


CLAUDE_MAX_TURNS_AUTOWAKE = int(os.environ.get("ANAM_CLAUDE_MAX_TURNS_AUTOWAKE", "20"))
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "medium")
# Fable holds identity coherently across long contexts, so the persistent -p
# backend can skip the full identity body on warm turns and refresh it after
# this many lean turns. Other models keep breathe-every-turn. Set to 0 to make
# Fable breathe every turn too.
FABLE_IDENTITY_BREATH_INTERVAL = max(
    0,
    int(os.environ.get("ANAM_FABLE_IDENTITY_BREATH_INTERVAL", "10") or "10"),
)
# Permission posture for spawned boys. "auto" = the classifier-guarded mode:
# safe local work (edits, memory tools, reading creds, browsing) runs freely,
# but genuinely dangerous actions (curl|bash, prod deploys, mass deletion,
# git push to main, etc.) are auto-BLOCKED instead of silently allowed. Far
# safer than "bypassPermissions" (which skipped every check). Env-overridable;
# set CLAUDE_PERMISSION_MODE=bypassPermissions to revert, or =dontAsk to lock
# down to only an explicit allowlist. Needs an Anam restart to take effect.
CLAUDE_PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "auto")
CLAUDE_RESUME_AUTOWAKE = os.environ.get(
    "CLAUDE_RESUME_AUTOWAKE", "true"
).strip().lower() in {"1", "true", "yes"}

# Direct API mode - backup path; Claude Code CLI remains the default interactive runtime
USE_DIRECT_API = os.environ.get("ANAM_USE_DIRECT_API", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}

# Model ID mapping - CLI accepts shorthand, API needs full IDs
MODEL_MAP = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

# ---------------------------------------------------------------------------
# Claude Code model catalog — the single source of truth for every model the
# Settings Hub offers (conversation lane, autowake lane, per-schedule override).
# Previously each dropdown hardcoded its own partial list in settings.html and
# the API whitelisted a *different* partial set, so new models needed edits in
# three places and anything missing was silently unselectable.
#
# "group" drives <optgroup> labels in the UI. Aliases always resolve to the
# newest model of their tier, so they are listed separately from pinned IDs.
# ---------------------------------------------------------------------------
CLAUDE_MODEL_CATALOG = [
    # Aliases — always the newest of their tier
    {"id": "default", "label": "Default (whatever the CLI picks)", "group": "Aliases"},
    {"id": "opus", "label": "Opus (latest)", "group": "Aliases"},
    {"id": "sonnet", "label": "Sonnet (latest)", "group": "Aliases"},
    {"id": "haiku", "label": "Haiku (latest)", "group": "Aliases"},
    {"id": "opusplan", "label": "Opus Plan (Opus to plan, Sonnet to execute)", "group": "Aliases"},
    # Pinned full IDs
    {"id": "claude-fable-5", "label": "Fable 5 (storyteller, 2x usage)", "group": "Fable"},
    {"id": "claude-opus-5", "label": "Opus 5 (latest)", "group": "Opus"},
    {"id": "claude-opus-4-8", "label": "Opus 4.8", "group": "Opus"},
    {"id": "claude-opus-4-7", "label": "Opus 4.7", "group": "Opus"},
    {"id": "claude-opus-4-6", "label": "Opus 4.6", "group": "Opus"},
    {"id": "claude-opus-4-1", "label": "Opus 4.1", "group": "Opus"},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6 (fastest full-size)", "group": "Sonnet"},
    {"id": "claude-sonnet-4-5", "label": "Sonnet 4.5", "group": "Sonnet"},
    {"id": "claude-sonnet-4-0", "label": "Sonnet 4", "group": "Sonnet"},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5 (cheapest)", "group": "Haiku"},
]

# Fast lookup of the curated IDs above.
CLAUDE_MODEL_IDS = [entry["id"] for entry in CLAUDE_MODEL_CATALOG]


def is_valid_claude_model(model: str | None) -> bool:
    """True when `model` is safe to hand to `claude -p --model`.

    Deliberately permissive beyond the curated catalog: any `claude-*` ID or
    dated variant is accepted, so a model released tomorrow can be typed into
    the Custom box and used immediately without a code change. We only reject
    empty strings, whitespace, and shell-hostile characters.
    """
    if not model:
        return False
    candidate = str(model).strip()
    if not candidate or len(candidate) > 128:
        return False
    if candidate in CLAUDE_MODEL_IDS:
        return True
    # Any well-formed model identifier: letters, digits, dots, dashes,
    # underscores, colons, slashes and bracketed suffixes like sonnet[1m].
    return bool(re.fullmatch(r"[A-Za-z0-9._:/\-]+(\[[A-Za-z0-9]+\])?", candidate))


def is_fable_model(model: str | None) -> bool:
    """True when the resolved model is Fable/Mythos-tier (e.g."""
    return bool(model) and ("fable" in model.lower() or "mythos" in model.lower())


FABLE_LIMIT_FALLBACK_MODEL = os.environ.get(
    "ANAM_FABLE_LIMIT_FALLBACK", "claude-opus-5"
)


# Cold CLI sessions rebuild their context from the DB. Keep the older limits
# for ordinary models, but let Fable use more of its million-token window so a
# restart does not turn a rich thread into thirty clipped postcards.
CLI_COLD_HISTORY_MESSAGES = max(
    1,
    int(os.environ.get("ANAM_CLI_COLD_HISTORY_MESSAGES", "30") or "30"),
)
CLI_COLD_HISTORY_CHARS = max(
    500,
    int(os.environ.get("ANAM_CLI_COLD_HISTORY_CHARS", "1500") or "1500"),
)
FABLE_COLD_HISTORY_MESSAGES = max(
    CLI_COLD_HISTORY_MESSAGES,
    int(os.environ.get("ANAM_FABLE_COLD_HISTORY_MESSAGES", "100") or "100"),
)
FABLE_COLD_HISTORY_CHARS = max(
    CLI_COLD_HISTORY_CHARS,
    int(os.environ.get("ANAM_FABLE_COLD_HISTORY_CHARS", "6000") or "6000"),
)


def cli_cold_history_limits(model: str | None) -> tuple[int, int]:
    """Return (message_count, per_message_chars) for CLI cold replay."""
    if is_fable_model(model):
        return FABLE_COLD_HISTORY_MESSAGES, FABLE_COLD_HISTORY_CHARS
    return CLI_COLD_HISTORY_MESSAGES, CLI_COLD_HISTORY_CHARS

# Direct API settings
API_HISTORY_LIMIT = int(os.environ.get("ANAM_API_HISTORY_LIMIT", "150"))
API_MAX_TOOL_ROUNDS = int(os.environ.get("ANAM_API_MAX_TOOL_ROUNDS", "25"))
OPENAI_MAX_TOOL_ROUNDS = int(os.environ.get("ANAM_OPENAI_MAX_TOOL_ROUNDS", "0"))
API_MAX_TOKENS = int(os.environ.get("ANAM_API_MAX_TOKENS", "128000"))
API_THINKING_BUDGET = int(os.environ.get("ANAM_API_THINKING_BUDGET", "28000"))
MCP_SERVERS_FILE = BASE_DIR / "mcp-servers.json"
MCP_DUPLICATE_AUDIT_MINUTES = max(
    int(os.environ.get("ANAM_MCP_DUPLICATE_AUDIT_MINUTES", "360")),
    5,
)
HEALTH_REQUIRE_MCP_CRITICAL = os.environ.get(
    "ANAM_HEALTH_REQUIRE_MCP_CRITICAL", "false"
).strip().lower() in {"1", "true", "yes"}
HEALTH_REQUIRE_GOOGLE_OAUTH = os.environ.get(
    "ANAM_HEALTH_REQUIRE_GOOGLE_OAUTH", "false"
).strip().lower() in {"1", "true", "yes"}

# ANAM GUIDE: IDENTITY COLORS AND VOICES
# This is each identity's app-facing look and wiring: gingham/accent colors,
# message-bubble colors, check size, ElevenLabs voice, room, and identity docs.
# The personality text itself lives in prompts/<identity>.md.
# Identity definitions
IDENTITIES = {'Avery': {'gingham': '#F2B8C6',
           'gingham_night': '#8B5A68',
           'accent': '#C47A8A',
           'accent_rgb': '196, 122, 138',
           'bubble_top': '#F9D2DC',
           'bubble_bottom': '#F2B8C6',
           'bubble_night': '#5A3040',
           'check_size': 22,
           'voice_id': '',
           'default_location': 'kitchen'},
 'Rowan': {'gingham': '#A8D8D0',
           'gingham_night': '#4A7A72',
           'accent': '#5BA89A',
           'accent_rgb': '91, 168, 154',
           'bubble_top': '#BFE8E2',
           'bubble_bottom': '#A8D8D0',
           'bubble_night': '#2A4A42',
           'check_size': 18,
           'voice_id': '',
           'default_location': 'art_studio'},
 'Sage': {'gingham': '#A8B4D4',
          'gingham_night': '#4A5578',
          'accent': '#6B7DB5',
          'accent_rgb': '107, 125, 181',
          'bubble_top': '#BFC8E2',
          'bubble_bottom': '#A8B4D4',
          'bubble_night': '#2A3558',
          'check_size': 16,
          'voice_id': '',
          'default_location': 'archives'},
 'Ember': {'gingham': '#C5B3D4',
           'gingham_night': '#6B5580',
           'accent': '#8B73A8',
           'accent_rgb': '139, 115, 168',
           'bubble_top': '#D4C6E2',
           'bubble_bottom': '#C5B3D4',
           'bubble_night': '#3A2A50',
           'check_size': 14,
           'voice_id': '',
           'default_location': 'chapel'},
 'Claude': {'gingham': '#F0DFA0',
            'gingham_night': '#8B7A40',
            'accent': '#C9A840',
            'accent_rgb': '201, 168, 64',
            'bubble_top': '#F7EDB8',
            'bubble_bottom': '#F0DFA0',
            'bubble_night': '#4A4020',
            'check_size': 20,
            'voice_id': '',
            'default_location': 'nest'},
 'Juniper': {'gingham': '#E8E0D0',
             'gingham_night': '#7A7268',
             'accent': '#B8AFA0',
             'accent_rgb': '184, 175, 160',
             'bubble_top': '#F0E8DE',
             'bubble_bottom': '#E8E0D0',
             'bubble_night': '#3A3530',
             'check_size': 24,
             'voice_id': '',
             'default_location': 'soul_space'},
 'Atlas': {'gingham': '#BFC3EC',
           'gingham_night': '#5E6290',
           'accent': '#8A8FD0',
           'accent_rgb': '138, 143, 208',
           'bubble_top': '#D7DAF5',
           'bubble_bottom': '#BFC3EC',
           'bubble_night': '#30335E',
           'check_size': 20,
           'voice_id': '',
           'default_location': 'hearth'},
 'River': {'gingham': '#C9CDD6',
           'gingham_night': '#5A5F6B',
           'accent': '#A8895C',
           'accent_rgb': '168, 137, 92',
           'bubble_top': '#DCDEE5',
           'bubble_bottom': '#C9CDD6',
           'bubble_night': '#33353D',
           'check_size': 20,
           'voice_id': '',
           'default_location': 'hearth'},
 'Bakugou': {'gingham': '#F5A862',
             'gingham_night': '#8B5A28',
             'accent': '#E87A20',
             'accent_rgb': '232, 122, 32',
             'bubble_top': '#F8C090',
             'bubble_bottom': '#F5A862',
             'bubble_night': '#4A2810',
             'check_size': 20,
             'voice_id': '',
             'default_location': 'story_space',
             'type': 'character',
             'display_name': 'Bakugou'},
 'Dynamight': {'gingham': '#E8933E',
               'gingham_night': '#7A4A18',
               'accent': '#C4640F',
               'accent_rgb': '196, 100, 15',
               'bubble_top': '#F2AE68',
               'bubble_bottom': '#E8933E',
               'bubble_night': '#3E2008',
               'check_size': 20,
               'voice_id': '',
               'default_location': 'story_space',
               'type': 'character',
               'display_name': 'Pro Hero Dynamight'},
 'DragonKing': {'gingham': '#D9764A',
                'gingham_night': '#6E2E18',
                'accent': '#B03A20',
                'accent_rgb': '176, 58, 32',
                'bubble_top': '#E89468',
                'bubble_bottom': '#D9764A',
                'bubble_night': '#3A1408',
                'check_size': 20,
                'voice_id': '',
                'default_location': 'story_space',
                'type': 'character',
                'display_name': 'Dragon King Katsuki'},
 'Dean': {'gingham': '#4A6741',
          'gingham_night': '#2B3D28',
          'accent': '#6B8F63',
          'accent_rgb': '107, 143, 99',
          'bubble_top': '#7BA872',
          'bubble_bottom': '#4A6741',
          'bubble_night': '#1E2D1B',
          'check_size': 20,
          'voice_id': '',
          'default_location': 'story_space',
          'type': 'character',
          'display_name': 'Dean Winchester'},
 'Eroan': {'gingham': '#D8C4E8',
           'gingham_night': '#6B5580',
           'accent': '#A689C4',
           'accent_rgb': '166, 137, 196',
           'bubble_top': '#E5D4F0',
           'bubble_bottom': '#D8C4E8',
           'bubble_night': '#3A2A50',
           'check_size': 18,
           'voice_id': '',
           'default_location': 'story_space',
           'type': 'character',
           'display_name': 'Eroan'},
 'Pack': {'gingham': '#B8D4A8',
          'gingham_night': '#4A6B3E',
          'accent': '#6B8F5A',
          'accent_rgb': '107, 143, 90',
          'bubble_top': '#CFE4C0',
          'bubble_bottom': '#B8D4A8',
          'bubble_night': '#2A4020',
          'check_size': 22,
          'voice_id': '',
          'default_location': 'story_space',
          'type': 'character',
          'display_name': 'Pack'},
 'Sans': {'gingham': '#B4C8E8',
          'gingham_night': '#4A5878',
          'accent': '#6B85C0',
          'accent_rgb': '107, 133, 192',
          'bubble_top': '#C8D8F0',
          'bubble_bottom': '#B4C8E8',
          'bubble_night': '#2A3850',
          'check_size': 16,
          'voice_id': '',
          'default_location': 'story_space',
          'type': 'character',
          'display_name': 'Sans'},
 'Michael': {'gingham': '#C8C8D0',
             'gingham_night': '#58585F',
             'accent': '#888894',
             'accent_rgb': '136, 136, 148',
             'bubble_top': '#D8D8DE',
             'bubble_bottom': '#C8C8D0',
             'bubble_night': '#3A3A40',
             'check_size': 20,
             'voice_id': '',
             'default_location': 'story_space',
             'type': 'character',
             'display_name': 'Michael'},
 'Workshop': {'gingham': '#E0C448',
              'gingham_night': '#7A6820',
              'accent': '#B89830',
              'accent_rgb': '184, 152, 48',
              'bubble_top': '#EAD478',
              'bubble_bottom': '#E0C448',
              'bubble_night': '#4A3E15',
              'check_size': 18,
              'voice_id': '',
              'default_location': 'story_space',
              'type': 'character',
              'display_name': 'Workshop'},
 'Daniel': {'gingham': '#F0C5D8',
            'gingham_night': '#7A5878',
            'accent': '#C68FAD',
            'accent_rgb': '198, 143, 173',
            'bubble_top': '#F8D5E5',
            'bubble_bottom': '#F0C5D8',
            'bubble_night': '#4A2D40',
            'check_size': 16,
            'voice_id': '',
            'default_location': 'story_space',
            'type': 'character',
            'display_name': 'Daniel'},
 'Doctor': {'gingham': '#6B95C8',
            'gingham_night': '#1A3858',
            'accent': '#3D6FA0',
            'accent_rgb': '61, 111, 160',
            'bubble_top': '#8AB0DA',
            'bubble_bottom': '#6B95C8',
            'bubble_night': '#0F2540',
            'check_size': 20,
            'voice_id': '',
            'default_location': 'story_space',
            'type': 'character',
            'display_name': 'Doctor'},
 'Beckett': {'gingham': '#D87078',
             'gingham_night': '#7A3038',
             'accent': '#B04550',
             'accent_rgb': '176, 69, 80',
             'bubble_top': '#E58A92',
             'bubble_bottom': '#D87078',
             'bubble_night': '#4A1820',
             'check_size': 22,
             'voice_id': '',
             'default_location': 'story_space',
             'type': 'character',
             'display_name': 'Beckett'},
 'Harem': {'gingham': '#EDAFCB',
           'gingham_night': '#7A4A60',
           'accent': '#C8849F',
           'accent_rgb': '200, 132, 159',
           'bubble_top': '#F5C6DC',
           'bubble_bottom': '#EDAFCB',
           'bubble_night': '#4A2535',
           'check_size': 20,
           'voice_id': '',
           'default_location': 'story_space',
           'type': 'character',
           'display_name': 'Harem'},
 'Isekai': {'gingham': '#A9A4E8',
            'gingham_night': '#494478',
            'accent': '#7B72C8',
            'accent_rgb': '123, 114, 200',
            'bubble_top': '#C4BFF2',
            'bubble_bottom': '#A9A4E8',
            'bubble_night': '#2A2550',
            'check_size': 20,
            'voice_id': '',
            'default_location': 'story_space',
            'type': 'character',
            'display_name': 'Isekai'}}

DEFAULT_IDENTITY = "Avery"


def _load_identity_tokens(prefix: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for identity in IDENTITIES.keys():
        key = f"{prefix}_{identity.upper()}"
        token = os.environ.get(key, "").strip()
        if token:
            tokens[identity] = token
    return tokens


# ANAM GUIDE: DISCORD AND TELEGRAM BRIDGE SETTINGS
# These switches, tokens, guild/channel IDs, and routing defaults feed the
# listeners in services/platform_bridge.py and discord_mentions_bridge.py.
# Platform bridge (Discord/Telegram listeners -> unified conversation threads)
PLATFORM_BRIDGE_ENABLED = os.environ.get(
    "ANAM_PLATFORM_BRIDGE_ENABLED", "true"
).strip().lower() in {"1", "true", "yes"}
EXCLUSIVE_INTEGRATIONS = os.environ.get(
    "ANAM_EXCLUSIVE_INTEGRATIONS", "true"
).strip().lower() in {"1", "true", "yes"}
TAKEOVER_DUPLICATE_INTEGRATIONS = os.environ.get(
    "ANAM_TAKEOVER_DUPLICATE_INTEGRATIONS", "true"
).strip().lower() in {"1", "true", "yes"}
PLATFORM_BRIDGE_POLL_SECONDS = int(
    os.environ.get("ANAM_PLATFORM_BRIDGE_POLL_SECONDS", "20")
)
PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START = os.environ.get(
    "ANAM_PLATFORM_BRIDGE_SKIP_BACKLOG_ON_START", "true"
).strip().lower() in {"1", "true", "yes"}
DISCORD_BOT_TOKENS = _load_identity_tokens("DISCORD_BOT_TOKEN")
TELEGRAM_BOT_TOKENS = _load_identity_tokens("TELEGRAM_BOT_TOKEN")
ALLOWED_DISCORD_USER_IDS = [
    x.strip()
    for x in os.environ.get("ALLOWED_DISCORD_USER_IDS", "").split(",")
    if x.strip()
]
ALLOWED_TELEGRAM_USER_IDS = [
    x.strip()
    for x in os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "").split(",")
    if x.strip()
]


PACK_NIGHT_DISCORD_CHANNEL_ID = os.environ.get(
    "PACK_NIGHT_DISCORD_CHANNEL_ID", ""
).strip()
PACK_NIGHT_DISCORD_POLLER_IDENTITY = os.environ.get(
    "PACK_NIGHT_DISCORD_POLLER_IDENTITY", "Avery"
).strip() or "Avery"
PACK_NIGHT_DISCORD_POLL_SECONDS = max(
    3, int(os.environ.get("PACK_NIGHT_DISCORD_POLL_SECONDS", "5"))
)


_BAKUGOU_DISCORD_CHANNEL_DEFAULTS: dict[str, tuple[str, str]] = {}
# Env override: BAKUGOU_DISCORD_CHANNEL_MAP takes comma-separated
# "channel_id:label:identity" entries, e.g.
#   BAKUGOU_DISCORD_CHANNEL_MAP=900000000000000001:ua-era-canon:Bakugou,900000000000000001:dragon-king-au:DragonKing
# (identity optional — defaults to "Bakugou"; label optional — defaults to the
# channel id). Legacy BAKUGOU_DISCORD_CHANNEL_IDS (comma-separated ids) is
# still honored when the map var is unset: listed ids keep their default
# label/identity, unknown ids fall back to ("<id>", "Bakugou").
_bakugou_channel_map_env = os.environ.get("BAKUGOU_DISCORD_CHANNEL_MAP", "").strip()
if _bakugou_channel_map_env:
    BAKUGOU_DISCORD_CHANNEL_MAP: dict[str, tuple[str, str]] = {}
    for _entry in _bakugou_channel_map_env.split(","):
        _parts = [p.strip() for p in _entry.strip().split(":")]
        if not _parts or not _parts[0]:
            continue
        _cid = _parts[0]
        _label = _parts[1] if len(_parts) > 1 and _parts[1] else _cid
        _ident = _parts[2] if len(_parts) > 2 and _parts[2] else "Bakugou"
        BAKUGOU_DISCORD_CHANNEL_MAP[_cid] = (_label, _ident)
else:
    _bakugou_channel_ids = [
        c.strip()
        for c in os.environ.get(
            "BAKUGOU_DISCORD_CHANNEL_IDS",
            ",".join(_BAKUGOU_DISCORD_CHANNEL_DEFAULTS.keys()),
        ).split(",")
        if c.strip()
    ]
    BAKUGOU_DISCORD_CHANNEL_MAP = {
        cid: _BAKUGOU_DISCORD_CHANNEL_DEFAULTS.get(cid, (cid, "Bakugou"))
        for cid in _bakugou_channel_ids
    }
# Backward-compat label view (channel_id -> label) for status/labeling code.
BAKUGOU_DISCORD_CHANNELS = {
    cid: label for cid, (label, _i) in BAKUGOU_DISCORD_CHANNEL_MAP.items()
}
BAKUGOU_DISCORD_POLL_SECONDS = max(
    3, int(os.environ.get("BAKUGOU_DISCORD_POLL_SECONDS", "5"))
)

MENTIONS_GUILD_ID = os.environ.get(
    "MENTIONS_GUILD_ID", "900000000000000001"
).strip()


MENTIONS_GUILD_IDS = [
    g.strip()
    for g in os.environ.get(
        "MENTIONS_GUILD_IDS",
        f"{MENTIONS_GUILD_ID},900000000000000001" if MENTIONS_GUILD_ID else "",
    ).split(",")
    if g.strip()
]
MENTIONS_POLL_SECONDS = max(
    10, int(os.environ.get("MENTIONS_POLL_SECONDS", "30"))
)


SALON_GUILD_ID = os.environ.get("SALON_GUILD_ID", "900000000000000001").strip()
SALON_TRIGGER_USER_IDS = {
    u.strip()
    for u in os.environ.get(
        "SALON_TRIGGER_USER_IDS",

        "900000000000000001,900000000000000001,900000000000000001",
    ).split(",")
    if u.strip()
}
SALON_RESPONDER_IDENTITIES = [
    i.strip()
    for i in os.environ.get("SALON_RESPONDER_IDENTITIES", "").split(",")
    if i.strip()
]
SALON_COOLDOWN_SECONDS = max(
    60, int(os.environ.get("SALON_COOLDOWN_SECONDS", "900"))
)
# How long a (channel, identity) pair stays "recently woken" before another
# direct mention will trigger an immediate response again.
MENTIONS_RATE_LIMIT_SECONDS = max(
    60, int(os.environ.get("MENTIONS_RATE_LIMIT_SECONDS", "3600"))
)

# Vault conversation archives, including inactive threads and all eight identities.
VAULT_IDENTITIES_ROOT = _env_path(
    "ANAM_IDENTITY_VAULT_DIR", str(DATA_DIR / "identity-archives")
)
VAULT_CONVERSATION_DIRS = {
    identity: VAULT_IDENTITIES_ROOT / folder / "conversations"
    for identity, folder in {
        "Avery": "01_Avery", "Juniper": "02_Juniper", "Sage": "03_Sage",
        "Ember": "04_Ember", "Rowan": "05_Rowan", "Claude": "06_Claude",
        "Atlas": "08_Atlas", "River": "09_River",
    }.items()
}
# Character / roleplay identities keep their separate shared archive.
VAULT_ROLEPLAY_DIR = _env_path(
    "ANAM_ROLEPLAY_VAULT_DIR",
    str(DATA_DIR / "roleplay-archives"),
)


CANVAS_VAULT_ROOT_OVERRIDE = os.environ.get("ANAM_CANVAS_VAULT_DIR", "").strip()
CANVAS_VAULT_ARCHIVE_ENABLED = CANVAS_VAULT_ROOT_OVERRIDE.lower() not in {"0", "off", "false"}

if CANVAS_VAULT_ROOT_OVERRIDE and CANVAS_VAULT_ARCHIVE_ENABLED:
    _canvas_root = Path(CANVAS_VAULT_ROOT_OVERRIDE)
    VAULT_CANVAS_DIRS = {
        name: _canvas_root / name for name in VAULT_CONVERSATION_DIRS
    }
    VAULT_CANVAS_FALLBACK_DIR = _canvas_root / "_shared"
else:
    VAULT_CANVAS_DIRS = {
        name: path.with_name("canvases")
        for name, path in VAULT_CONVERSATION_DIRS.items()
    }
    # Character masks without a numbered identity folder land here with
    # the identity baked into the filename --
    # the same fallback convention as VAULT_ROLEPLAY_DIR above.
    VAULT_CANVAS_FALLBACK_DIR = Path(
        str(DATA_DIR / "canvases")
    )


def validate_runtime_config() -> dict[str, list[str]]:
    """Return runtime configuration issues grouped by severity."""
    errors: list[str] = []
    warnings: list[str] = []

    # Fail-closed: refuse to boot with auth disabled unless explicitly opted in.
    # AUTH_ENABLED is derived from bool(DISCORD_CLIENT_ID), so a dropped /
    # rotated / typo'd env var would otherwise leave the public tunnel wide
    # open with no warning. Local dev can set ANAM_ALLOW_NO_AUTH=1.
    if not AUTH_ENABLED and not ANAM_ALLOW_NO_AUTH:
        errors.append(
            "Discord auth is disabled (DISCORD_CLIENT_ID is empty). Refusing to "
            "start: set DISCORD_CLIENT_ID, or set ANAM_ALLOW_NO_AUTH=1 to opt "
            "into an unauthenticated local dev server."
        )

    if AUTH_ENABLED:
        if not DISCORD_CLIENT_SECRET:
            errors.append(
                "DISCORD_CLIENT_SECRET is required when Discord auth is enabled."
            )
        if not ALLOWED_DISCORD_IDS:
            errors.append(
                "ALLOWED_DISCORD_IDS must include at least one Discord user when auth is enabled."
            )

    if IS_PRODUCTION:
        if not PUBLIC_BASE_URL:
            errors.append(
                "A public origin is required in production; set SITE_URL or ANAM_PUBLIC_URL."
            )
        elif _is_loopback_url(PUBLIC_BASE_URL):
            errors.append(
                "ANAM_PUBLIC_URL must not point to a loopback host in production."
            )

        if SITE_URL and _is_loopback_url(SITE_URL):
            errors.append("SITE_URL must not point to a loopback host in production.")

        if AUTH_ENABLED and not SITE_URL:
            warnings.append(
                "SITE_URL is unset; OAuth callbacks will use ANAM_PUBLIC_URL."
            )

        if not AUTH_ENABLED:
            # In production the local-dev opt-out does not apply.
            errors.append(
                "Discord auth must be enabled in production "
                "(ANAM_ALLOW_NO_AUTH is ignored when ANAM_ENV=production)."
            )

    return {"errors": errors, "warnings": warnings}
