"""Run Commons directly, without the shared Pack/Stories host or Aisling."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import commons_server as game
from commons_server import HearthHandler, HearthHTTPServer
from settings import STATE_ROOT, PRIVATE_ROOT, MASTER_ROOT
sys.path.insert(0, str(ROOT))
from setup_commons import initialize

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    initialize()
    game._reroot(PRIVATE_ROOT, MASTER_ROOT, world=STATE_ROOT / "world")
    game.RUNTIME_WORLD_DIR = game.WORLD_DIR
    game.WORLD_DIR = ROOT / 'world' 
    for variable, filename in {
        'CROPS_FILE':'crops.json', 'RECIPES_FILE':'recipes.json', 'FISH_FILE':'fish.json',
        'FORAGE_FILE':'forage.json', 'HUNTING_FILE':'hunting.json', 'WILDS_FILE':'wilds.json',
        'SHINY_FILE':'shinies.json',
    }.items():
        setattr(game, variable, ROOT / 'world' / filename)
    with HearthHTTPServer((args.host, args.port), HearthHandler) as server:
        print(f"Commons: http://{args.host}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
