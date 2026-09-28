"""Refresh local Discord guild/channel/member metadata and rebuild search indexes.

Uses the app's configured identity tokens. This contacts Discord and writes the
local server-map JSON/database; it is maintenance, not a read-only health check.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env.local", override=True)

DISCORD_API = "https://discord.com/api/v10"
DB_PATH = Path(__file__).parent.parent / "data" / "anam.db"
SERVER_MAP_JSON = Path(
    os.environ.get(
        "ANAM_DISCORD_SERVER_MAP_PATH",
        str(Path(__file__).parent.parent / "data" / "discord" / "server_map.json"),
    )
)

def configured_tokens() -> dict[str, str]:
    """Use the identity registry and credential loading shared by the app."""
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from config import DISCORD_BOT_TOKENS

    return dict(DISCORD_BOT_TOKENS)


def index_summary(connection) -> dict[str, int]:
    """Report index sizes without printing member names or account data."""
    tables = {
        "server mappings": "discord_server_map",
        "members": "discord_server_members",
        "searchable channels": "discord_search_channels",
        "searchable members": "discord_search_members",
    }
    return {
        label: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for label, table in tables.items()
    }


def _channel_type_name(t):
    return {
        0: "text", 1: "dm", 2: "voice", 4: "category", 5: "announcement",
        13: "stage", 15: "forum", 16: "media",
    }.get(t, f"unknown({t})")


def _parse_member(m):
    u = m.get("user", {})
    return {
        "user_id": u.get("id", ""),
        "username": u.get("username", ""),
        "display_name": u.get("global_name") or u.get("username", ""),
        "nickname": m.get("nick"),
        "bot": u.get("bot", False),
        "avatar": u.get("avatar"),
        "roles": m.get("roles", []),
        "joined_at": m.get("joined_at"),
    }


async def fetch_all_members(client, token, guild_id):
    headers = {"Authorization": f"Bot {token}"}
    all_members = []
    after = "0"
    while True:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/members",
            headers=headers,
            params={"limit": 1000, "after": after},
        )
        if resp.status_code != 200:
            print(f"  Members fetch failed: {resp.status_code}")
            break
        batch = resp.json()
        if not batch:
            break
        all_members.extend(batch)
        if len(batch) < 1000:
            break
        after = batch[-1]["user"]["id"]
        await asyncio.sleep(0.5)
    return all_members


def organize_channels(raw):
    cats = {}
    uncat = []
    for ch in raw:
        if ch.get("type") == 4:
            cats[ch["id"]] = {
                "category": ch["name"],
                "position": ch.get("position", 0),
                "channels": [],
            }
    for ch in raw:
        if ch.get("type") == 4:
            continue
        entry = {
            "id": ch["id"],
            "name": ch["name"],
            "type": _channel_type_name(ch.get("type", 0)),
        }
        if ch.get("topic"):
            entry["topic"] = ch["topic"][:100]
        parent = ch.get("parent_id")
        if parent and parent in cats:
            cats[parent]["channels"].append(entry)
        else:
            uncat.append(entry)
    result = []
    if uncat:
        result.append({"category": None, "channels": uncat})
    for c in sorted(cats.values(), key=lambda x: x["position"]):
        result.append({"category": c["category"], "channels": c["channels"]})
    return result


async def main():
    tokens = configured_tokens()
    if not tokens:
        print("No Discord bot tokens are configured; the local map was not changed.")
        return 1

    now = datetime.now(timezone.utc).isoformat()
    full_map = {}
    seen_guilds = set()
    total_members = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for identity, token in tokens.items():
            headers = {"Authorization": f"Bot {token}"}
            resp = await client.get(
                f"{DISCORD_API}/users/@me/guilds", headers=headers
            )
            if resp.status_code != 200:
                print(f"[{identity}] guild fetch failed: {resp.status_code}")
                continue

            guilds = resp.json()
            servers = []

            for g in guilds:
                guild_id = g["id"]
                guild_name = g["name"]

                ch_resp = await client.get(
                    f"{DISCORD_API}/guilds/{guild_id}/channels", headers=headers
                )
                channels = ch_resp.json() if ch_resp.status_code == 200 else []
                organized = organize_channels(channels)

                members_list = []
                if guild_id not in seen_guilds:
                    seen_guilds.add(guild_id)
                    raw = await fetch_all_members(client, token, guild_id)
                    members_list = [_parse_member(m) for m in raw]
                    total_members += len(members_list)
                    print(f"[{identity}] {guild_name}: {len(members_list)} members")

                srv = {"name": guild_name, "id": guild_id, "categories": organized}
                if members_list:
                    srv["members"] = [
                        {
                            "user_id": m["user_id"],
                            "username": m["username"],
                            "display_name": m["display_name"],
                            "nickname": m["nickname"],
                            "bot": m["bot"],
                        }
                        for m in members_list
                    ]
                servers.append(srv)

            full_map[identity] = {"servers": servers}
            print(f"[{identity}] {len(guilds)} server(s) mapped")

    full_map["_refreshed_at"] = now

    # Write JSON
    SERVER_MAP_JSON.parent.mkdir(parents=True, exist_ok=True)
    SERVER_MAP_JSON.write_text(json.dumps(full_map, indent=2), encoding="utf-8")
    print(f"JSON written to {SERVER_MAP_JSON}")

    # Write to DB
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS discord_server_members (
                guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
                username TEXT NOT NULL, display_name TEXT, nickname TEXT,
                bot INTEGER DEFAULT 0, avatar TEXT, roles_json TEXT,
                joined_at TEXT, refreshed_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )
        await db.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_channels USING fts5(
                identity, guild_name, channel_name, channel_type, category, topic,
                content='', tokenize='porter unicode61'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_members USING fts5(
                guild_name, user_id, username, display_name, nickname,
                content='', tokenize='porter unicode61'
            );
            """
        )

        # Upsert server map + members
        for identity, data in full_map.items():
            if identity.startswith("_"):
                continue
            for srv in data["servers"]:
                await db.execute(
                    "INSERT OR REPLACE INTO discord_server_map "
                    "(identity, guild_id, guild_name, member_count, channels_json, refreshed_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        identity, srv["id"], srv["name"],
                        len(srv.get("members", [])),
                        json.dumps(srv["categories"]), now,
                    ),
                )
                for m in srv.get("members", []):
                    await db.execute(
                        "INSERT OR REPLACE INTO discord_server_members "
                        "(guild_id, user_id, username, display_name, nickname, "
                        "bot, roles_json, refreshed_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            srv["id"], m["user_id"], m["username"],
                            m["display_name"], m["nickname"],
                            1 if m["bot"] else 0, "[]", now,
                        ),
                    )

        # Rebuild FTS5 indexes — drop and recreate for clean rebuild
        await db.execute("DROP TABLE IF EXISTS discord_search_channels")
        await db.execute("DROP TABLE IF EXISTS discord_search_members")
        await db.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_channels USING fts5(
                identity, guild_name, channel_name, channel_id, channel_type, category, topic,
                tokenize='porter unicode61'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS discord_search_members USING fts5(
                guild_id, guild_name, user_id, username, display_name, nickname,
                tokenize='porter unicode61'
            );
            """
        )

        rows = await db.execute_fetchall(
            "SELECT identity, guild_name, channels_json FROM discord_server_map"
        )
        for row in rows:
            channels = json.loads(row[2])
            for cat in channels:
                cat_name = cat.get("category") or ""
                for ch in cat.get("channels", []):
                    await db.execute(
                        "INSERT INTO discord_search_channels "
                        "(identity, guild_name, channel_name, channel_id, channel_type, category, topic) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (row[0], row[1], ch.get("name", ""), ch.get("id", ""),
                         ch.get("type", ""), cat_name, ch.get("topic", "")),
                    )

        guild_names = {}
        for row in await db.execute_fetchall(
            "SELECT DISTINCT guild_id, guild_name FROM discord_server_map"
        ):
            guild_names[row[0]] = row[1]

        members = await db.execute_fetchall(
            "SELECT guild_id, user_id, username, display_name, nickname "
            "FROM discord_server_members"
        )
        for row in members:
            await db.execute(
                "INSERT INTO discord_search_members "
                "(guild_id, guild_name, user_id, username, display_name, nickname) "
                "VALUES (?,?,?,?,?,?)",
                (row[0], guild_names.get(row[0], ""), row[1], row[2], row[3] or "", row[4] or ""),
            )

        await db.commit()
        print("DB + FTS5 updated")

    # Reopen read-only so verification cannot change the refreshed indexes.
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        summary = index_summary(connection)
    print("Local index verification:")
    for label, count in summary.items():
        print(f"  {label}: {count}")
    print()
    print(f"DONE: {len(tokens)} identities, {len(seen_guilds)} guilds, {total_members} members")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
