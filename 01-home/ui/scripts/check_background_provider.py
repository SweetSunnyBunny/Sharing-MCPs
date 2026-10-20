"""Live smoke check of the selected background provider; writes no memories/cards.

Run from the repo root: python scripts/check_background_provider.py [--research]
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.database import close_all_db_connections
from services.background_generation import generate_background_text, resolve_background_provider


async def main():
    try:
        provider, model, _options = await resolve_background_provider(identity="Claude")
        research = "--research" in sys.argv
        reply = await generate_background_text(
            "Search the official Python documentation for sqlite3.Connection.backup. "
            "Open the documentation and return its direct link and one sentence describing the method."
            if research else 'Return exactly {"ok": true}.',
            identity="Claude", research=research,
            system_prompt="You are a background provider smoke check. Return only the requested result.",
        )
        if not research:
            assert json.loads(reply) == {"ok": True}, reply
        print(json.dumps({"provider": provider, "model": model, "research": research, "reply": reply}))
    finally:
        await close_all_db_connections()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
