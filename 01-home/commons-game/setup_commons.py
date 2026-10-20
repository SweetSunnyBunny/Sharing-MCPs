"""Check the starter or initialize this installation's private identity keys."""
import argparse
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'server'))


def check():
    rooms = {}
    for path in (ROOT / 'world').glob('*.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and 'rows' in value:
            if not value['rows'] or len({len(row) for row in value['rows']}) != 1:
                raise ValueError('Invalid room grid: ' + path.name)
            rooms[value['id']] = value
    for room in rooms.values():
        for prop in room.get('props', []):
            if prop.get('to') and prop['to'] not in rooms:
                raise ValueError('Missing doorway destination: ' + prop['to'])
    if 'hearth' not in rooms:
        raise ValueError('The entry room is missing.')
    print(f'Starter checked: {len(rooms)} rooms; all doorway destinations exist.')
    return rooms


def initialize():
    from settings import ADMIN_NAME, COMPANIONS, MASTER_ROOT, PRIVATE_ROOT, PUBLIC_URL, STATE_ROOT
    if STATE_ROOT.is_relative_to(ROOT):
        raise ValueError('COMMONS_STATE_ROOT must be outside the public package folder.')
    for directory in (PRIVATE_ROOT, MASTER_ROOT, STATE_ROOT / 'world'):
        directory.mkdir(parents=True, exist_ok=True)
    for name in sorted({ADMIN_NAME, *COMPANIONS}):
        if not name or len(name) > 40 or not all(c.isalnum() or c in '_-' for c in name):
            raise ValueError('Configured companion names must be simple names of 1 to 40 characters.')
        directory = MASTER_ROOT if name == ADMIN_NAME else PRIVATE_ROOT
        keyfile = directory / (name + '.key')
        if not keyfile.exists():
            with keyfile.open('x', encoding='utf-8') as output:
                output.write(secrets.token_hex(32))
        key = keyfile.read_text(encoding='utf-8').strip()
        if len(key) < 16:
            raise ValueError('Existing key is invalid; it was left untouched: ' + str(keyfile))
        # Links contain a credential. Keep them alongside private keys, never in the web folder.
        linkfile = directory / (name + '-start.txt')
        linkfile.write_text(PUBLIC_URL + '?' + urlencode({'as': name, 'key': key}) + '\n', encoding='utf-8')
    # Runtime books and SQLite stay outside the web root. Authored maps remain editable in world/.
    for filename in ('crops.json', 'fish.json', 'forage.json', 'hunting.json', 'recipes.json',
                     'wilds.json', 'shinies.json', 'game-config.json'):
        target = STATE_ROOT / 'world' / filename
        if not target.exists():
            with target.open('x', encoding='utf-8') as output:
                output.write((ROOT / 'world' / filename).read_text(encoding='utf-8'))
    print('Private state initialized without replacing existing keys or game files.')
    print('Open your administrator sign-in link from: ' + str(MASTER_ROOT / (ADMIN_NAME + '-start.txt')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init', action='store_true', help='Create private state and local sign-in links.')
    parser.add_argument('--check', action='store_true', help='Validate starter rooms without creating state.')
    args = parser.parse_args()
    check()
    if args.init:
        initialize()


if __name__ == '__main__':
    main()
