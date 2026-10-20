"""One-shot local game dialogue using the same isolated Anam provider lane.

Commons can use this during an Anam upgrade without interrupting active chats.
The JSON request and response travel over pipes, never command-line arguments.
"""
import asyncio
import contextlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')

async def main():
    from api.game_npc import Dialogue, npc
    from db.database import close_all_db_connections
    try:
        request=json.loads(sys.stdin.buffer.read(131072))
        with contextlib.redirect_stdout(sys.stderr):
            result=await npc(Dialogue(**request))
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
        sys.stdout.buffer.flush()
    finally:
        await close_all_db_connections()

if __name__=='__main__':
    asyncio.run(main())
