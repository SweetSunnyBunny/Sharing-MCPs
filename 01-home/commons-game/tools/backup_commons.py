"""Verified private snapshot. Never writes into or restores over the website."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT.parent/'commons-state/private'
sys.path.insert(0,str(ROOT/'server'))
from adventure.backups import create_snapshot, restore_drill, verify_snapshot
from adventure.store import Store

def backup():
    runtime = PRIVATE.parent/'world' if PRIVATE.name == 'private' else ROOT/'world'
    destination=create_snapshot(Store(PRIVATE,runtime),ROOT)
    print(destination)
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--verify',type=Path,help='Verify an existing manifest/checksum snapshot.')
    mode.add_argument('--restore-drill',type=Path,help='Verify and restore only inside a temporary isolated folder.')
    args=parser.parse_args()
    if args.verify:print(json.dumps(verify_snapshot(args.verify),indent=2))
    elif args.restore_drill:print(json.dumps(restore_drill(args.restore_drill),indent=2))
    else:backup()
