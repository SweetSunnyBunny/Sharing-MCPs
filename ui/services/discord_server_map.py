"""Discord Server Map — periodic snapshot of guilds/channels/members each identity can see.

Fetches guild, channel, and member data via Discord REST API using the bot tokens
already configured in config.py. Stores results in:
  1. anam's SQLite DB (discord_server_map + discord_server_members tables) — queryable
  2. FTS5 full-text search across channels and members
  3. JSON export at C:/Apps/mcp\services\discord\config\server_map.json — instant awareness
"""

# ANAM GUIDE: DISCORD SERVER MAP SNAPSHOT
# What: Every so often, takes a full snapshot of the Discord servers, channels, and members each boy's bot can see, and saves it to the database plus a JSON file the boys read for instant awareness.
# Called by: core/lifespan.py schedules the periodic refresh; scripts/refresh_server_map.py runs it by hand.
# Edit here when: You want to change what gets recorded about servers/channels/members, how often the map refreshes, or where the JSON export lands.

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from config import DISCORD_BOT_TOKENS, DISCORD_SERVER_MAP_PATH
from db.database import get_db, release_db

log = logging.getLogger("anam.discord_server_map")

DISCORD_API = "https://discord.com/api/v10"
SERVER_MAP_JSON = DISCORD_SERVER_MAP_PATH


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS discord_server_map (
    identity TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    guild_name TEXT NOT NULL,
    guild_icon TEXT,
    member_count INTEGER,
    owner_id TEXT,
    channels_json TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (identity, guild_id)
);

CREATE TABLE IF NOT EXISTS discord_server_members (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT,
    nickname TEXT,
    bot INTEGER DEFAULT 0,
    avatar TEXT,
    roles_json TEXT,
    joined_at TEXT,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_channels USING fts5(
    identity, guild_name, channel_name, channel_id, channel_type, category, topic,
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_members USING fts5(
    guild_id, guild_name, user_id, username, display_name, nickname,
    tokenize='porter unicode61'
);
"""


async def _ensure_tables():
    db = await get_db()
    try:
        await db.executescript(_CREATE_TABLES)
        await db.executescript(_CREATE_FTS)
        await db.commit()
    finally:
        await release_db(db)


# ---------------------------------------------------------------------------
# Discord REST API fetchers
# ---------------------------------------------------------------------------

async def _fetch_guilds(token: str) -> list[dict]:
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=headers)
        if resp.status_code != 200:
            log.warning("Failed to fetch guilds: %s %s", resp.status_code, resp.text[:200])
            return []
        return resp.json()


async def _fetch_channels(token: str, guild_id: str) -> list[dict]:
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DISCORD_API}/guilds/{guild_id}/channels", headers=headers)
        if resp.status_code != 200:
            log.warning("Failed to fetch channels for guild %s: %s", guild_id, resp.status_code)
            return []
        return resp.json()


async def _fetch_all_members(token: str, guild_id: str) -> list[dict]:
    """Fetch all members from a guild, paginating through 1000 at a time."""
    headers = {"Authorization": f"Bot {token}"}
    all_members = []
    after = "0"

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{DISCORD_API}/guilds/{guild_id}/members",
                headers=headers,
                params={"limit": 1000, "after": after},
            )
            if resp.status_code != 200:
                log.warning(
                    "Failed to fetch members for guild %s: %s",
                    guild_id, resp.status_code,
                )
                break

            batch = resp.json()
            if not batch:
                break

            all_members.extend(batch)

            if len(batch) < 1000:
                break

            # Paginate using the last member's ID
            after = batch[-1]["user"]["id"]
            await asyncio.sleep(0.5)  # respect rate limits

    return all_members


# ---------------------------------------------------------------------------
# Data organization helpers
# ---------------------------------------------------------------------------

def _channel_type_name(type_int: int) -> str:
    return {
        0: "text", 1: "dm", 2: "voice", 4: "category", 5: "announcement",
        10: "announcement_thread", 11: "public_thread", 12: "private_thread",
        13: "stage", 14: "directory", 15: "forum", 16: "media",
    }.get(type_int, f"unknown({type_int})")


def _organize_channels(raw_channels: list[dict]) -> list[dict]:
    """Organize channels by category for clean output."""
    categories = {}
    uncategorized = []

    for ch in raw_channels:
        if ch.get("type") == 4:
            categories[ch["id"]] = {
                "id": ch["id"],
                "name": ch["name"],
                "position": ch.get("position", 0),
                "channels": [],
            }

    for ch in raw_channels:
        if ch.get("type") == 4:
            continue
        entry = {
            "id": ch["id"],
            "name": ch["name"],
            "type": _channel_type_name(ch.get("type", 0)),
            "position": ch.get("position", 0),
        }
        if ch.get("topic"):
            entry["topic"] = ch["topic"][:100]

        parent = ch.get("parent_id")
        if parent and parent in categories:
            categories[parent]["channels"].append(entry)
        else:
            uncategorized.append(entry)

    result = []
    if uncategorized:
        uncategorized.sort(key=lambda c: c["position"])
        result.append({"category": None, "channels": uncategorized})
    for cat in sorted(categories.values(), key=lambda c: c["position"]):
        cat["channels"].sort(key=lambda c: c["position"])
        result.append({
            "category": cat["name"],
            "category_id": cat["id"],
            "channels": cat["channels"],
        })
    return result


def _parse_member(member: dict) -> dict:
    """Extract clean member info from Discord API response."""
    user = member.get("user", {})
    return {
        "user_id": user.get("id", ""),
        "username": user.get("username", ""),
        "display_name": user.get("global_name") or user.get("username", ""),
        "nickname": member.get("nick"),
        "bot": user.get("bot", False),
        "avatar": user.get("avatar"),
        "roles": member.get("roles", []),
        "joined_at": member.get("joined_at"),
    }


# ---------------------------------------------------------------------------
# FTS5 rebuild
# ---------------------------------------------------------------------------

async def _rebuild_fts(db):
    """Rebuild FTS5 indexes from current data."""
    # Drop and recreate — contentless FTS5 tables can't use DELETE
    await db.execute("DROP TABLE IF EXISTS discord_search_channels")
    await db.execute("DROP TABLE IF EXISTS discord_search_members")
    await db.executescript(_CREATE_FTS)

    # Channels FTS
    rows = await db.execute_fetchall(
        "SELECT identity, guild_name, channels_json FROM discord_server_map"
    )
    for row in rows:
        identity = row[0]
        guild_name = row[1]
        channels = json.loads(row[2])
        for cat in channels:
            cat_name = cat.get("category") or ""
            for ch in cat.get("channels", []):
                await db.execute(
                    "INSERT INTO discord_search_channels "
                    "(identity, guild_name, channel_name, channel_id, channel_type, category, topic) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        identity,
                        guild_name,
                        ch.get("name", ""),
                        ch.get("id", ""),
                        ch.get("type", ""),
                        cat_name,
                        ch.get("topic", ""),
                    ),
                )

    # Members FTS
    members = await db.execute_fetchall(
        "SELECT sm.guild_id, m.username, m.display_name, m.nickname, m.user_id "
        "FROM discord_server_members m "
        "JOIN discord_server_map sm ON sm.guild_id = m.guild_id "
        "GROUP BY m.guild_id, m.user_id"
    )
    # Need guild_name — fetch from server_map
    guild_names = {}
    for row in await db.execute_fetchall(
        "SELECT DISTINCT guild_id, guild_name FROM discord_server_map"
    ):
        guild_names[row[0]] = row[1]

    for row in members:
        guild_id, username, display_name, nickname, user_id = row
        await db.execute(
            "INSERT INTO discord_search_members "
            "(guild_id, guild_name, user_id, username, display_name, nickname) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                guild_names.get(guild_id, ""),
                user_id,
                username,
                display_name or "",
                nickname or "",
            ),
        )


# ---------------------------------------------------------------------------
# Main refresh
# ---------------------------------------------------------------------------

async def refresh_server_map() -> dict:
    """Refresh the server map for all identities. Returns summary."""
    await _ensure_tables()

    now = datetime.now(timezone.utc).isoformat()
    full_map = {}
    summary = {"identities": 0, "guilds": 0, "members": 0, "errors": []}
    seen_guilds: set[str] = set()  # track which guilds we've synced members for

    db = await get_db()
    try:
        for identity, token in DISCORD_BOT_TOKENS.items():
            try:
                guilds = await _fetch_guilds(token)
                identity_data = {"servers": []}

                for guild in guilds:
                    guild_id = guild["id"]
                    guild_name = guild["name"]

                    raw_channels = await _fetch_channels(token, guild_id)
                    organized = _organize_channels(raw_channels)

                    # Fetch members once per guild (not per identity)
                    members_list = []
                    if guild_id not in seen_guilds:
                        seen_guilds.add(guild_id)
                        raw_members = await _fetch_all_members(token, guild_id)
                        for m in raw_members:
                            parsed = _parse_member(m)
                            members_list.append(parsed)

                            await db.execute(
                                "INSERT OR REPLACE INTO discord_server_members "
                                "(guild_id, user_id, username, display_name, nickname, "
                                "bot, avatar, roles_json, joined_at, refreshed_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    guild_id,
                                    parsed["user_id"],
                                    parsed["username"],
                                    parsed["display_name"],
                                    parsed["nickname"],
                                    1 if parsed["bot"] else 0,
                                    parsed["avatar"],
                                    json.dumps(parsed["roles"]),
                                    parsed["joined_at"],
                                    now,
                                ),
                            )
                        summary["members"] += len(members_list)
                        log.info(
                            "[%s] Synced %d members for %s",
                            identity, len(members_list), guild_name,
                        )

                    server_entry = {
                        "name": guild_name,
                        "id": guild_id,
                        "icon": guild.get("icon"),
                        "member_count": guild.get("approximate_member_count") or len(members_list),
                        "owner": guild.get("owner", False),
                        "categories": organized,
                    }

                    # Include member summary in JSON (names only, not full data)
                    if members_list:
                        server_entry["members"] = [
                            {
                                "user_id": m["user_id"],
                                "username": m["username"],
                                "display_name": m["display_name"],
                                "nickname": m["nickname"],
                                "bot": m["bot"],
                            }
                            for m in members_list
                        ]

                    identity_data["servers"].append(server_entry)

                    # Upsert server map
                    await db.execute(
                        "INSERT OR REPLACE INTO discord_server_map "
                        "(identity, guild_id, guild_name, guild_icon, member_count, "
                        "owner_id, channels_json, refreshed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            identity, guild_id, guild_name,
                            guild.get("icon"),
                            guild.get("approximate_member_count") or len(members_list),
                            guild.get("owner_id"),
                            json.dumps(organized),
                            now,
                        ),
                    )
                    summary["guilds"] += 1
                    # Commit per guild: the next iteration starts with slow
                    # Discord HTTP fetches, and an open write transaction held
                    # across them blocks every other writer (chat saves died
                    # with "database is locked" during each refresh).
                    await db.commit()

                full_map[identity] = identity_data
                summary["identities"] += 1
                log.info("[%s] Mapped %d server(s)", identity, len(guilds))

            except Exception as exc:
                log.error("[%s] Server map error: %s", identity, exc)
                summary["errors"].append(f"{identity}: {exc}")

        await db.commit()

        # Rebuild FTS5 indexes
        try:
            await _rebuild_fts(db)
            await db.commit()
            log.info("FTS5 search indexes rebuilt")
        except Exception as exc:
            log.error("FTS5 rebuild failed: %s", exc)
            summary["errors"].append(f"fts5: {exc}")

    finally:
        await release_db(db)

    # Prune stale data
    try:
        db2 = await get_db()
        active = list(DISCORD_BOT_TOKENS.keys())
        if active:
            placeholders = ",".join("?" * len(active))
            await db2.execute(
                f"DELETE FROM discord_server_map WHERE identity NOT IN ({placeholders})",
                active,
            )
        # Prune members from guilds no longer tracked
        await db2.execute(
            "DELETE FROM discord_server_members WHERE guild_id NOT IN "
            "(SELECT DISTINCT guild_id FROM discord_server_map)"
        )
        await db2.commit()
        await release_db(db2)
    except Exception:
        pass

    # Write JSON export
    try:
        SERVER_MAP_JSON.parent.mkdir(parents=True, exist_ok=True)
        full_map["_refreshed_at"] = now
        SERVER_MAP_JSON.write_text(json.dumps(full_map, indent=2), encoding="utf-8")
        log.info("Server map exported to %s", SERVER_MAP_JSON)
    except Exception as exc:
        log.error("Failed to write server map JSON: %s", exc)
        summary["errors"].append(f"json_export: {exc}")

    summary["refreshed_at"] = now
    return summary
