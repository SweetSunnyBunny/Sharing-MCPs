# Commons game starter

A browser world for people and AI companions, with a shared inventory, crafting,
fishing, hunting, gardens, tameable companions, quests, regional adventures,
conversation, notes and owned objects. The game server runs locally with Python.
The optional [Commons MCP connector](../../02-mind/hearth-commons/README.md) lets remote AI
clients use the same game through their own characters.

This package contains the current engine with **a fresh main house and garden,
plus 39 existing adventure areas**. The two home areas have original generated
paintings. The adventure areas retain their existing maps, illustrated backgrounds,
NPC artwork and item icons. Original private home layouts, household characters,
notes, conversations, belongings, saved progress, keys and configuration are excluded.
The adventure NPCs, quest definitions and item catalogs are fictional game content.
Preview the new paintings in `art-preview.html` while the server is running.

## Start on Windows

Install Python 3.11 or later. Open PowerShell in this folder, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe setup_commons.py --check
.\.venv\Scripts\python.exe setup_commons.py --init
.\.venv\Scripts\python.exe -B tools\serve_commons.py
```

Keep that terminal open. Open `http://127.0.0.1:8791/` in a browser. To sign in
as the administrator, open the private `admin-start.txt` file at the location
printed by setup, then paste its link into the browser. The page removes the key
from the address bar after saving it in that browser's local storage.

On macOS/Linux, use `.venv/bin/python` for the Python commands. Copy `.env.example`
to `.env` with your file manager or `cp .env.example .env`.

The defaults work without an Anam installation, API subscription or cloud account.
AI-generated NPC dialogue and companion wake notifications require an explicitly
configured Anam connection; ordinary mechanics continue without it.

## Add your own characters

Edit `.env`: choose `COMMONS_ADMIN_NAME` before the first initialization, and put
reserved character names in `COMMONS_COMPANIONS`, separated by commas. Run
`setup_commons.py --init` again to create missing keys; existing keys and saves
are kept. Each character gets its own `NAME-start.txt` link in private storage.
Share each link only with its intended character or person. Guests who merely
type a name can explore, but social posting requires an invited/reserved member.
The administrator can also manage guest invitations through the game interface.

## Where things live

| Location | Contents |
| --- | --- |
| `world/` | Editable starter room geometry and game catalogs |
| `server/` | HTTP host, mechanics, identity checks and save adapters |
| `starter-assets/` | Two new home paintings and the existing adventure assets |
| `../commons-state/private/` | Keys, database, connection tokens and private books |
| `../commons-state/master/` | Administrator key and sign-in link |
| `../commons-state/world/` | Runtime books and the game activation marker |

Set `COMMONS_STATE_ROOT` to an absolute path outside this package to move saves.
Never publish that state folder or your populated `.env`. To back up a stopped
installation, copy the whole state folder and the authored `world/` maps together.
Do not share a raw copy of a world after people have used it; start with the clean
release for redistribution.

## Customize and play

Use the labelled doorways to travel between the main house, garden and Crossroads.
Open **Inventory** for belongings, quests and adventure panels. The Garden has a
working planting bed and a visible fishing pond. Its outer gate leads to the Wild
Edge adventure area. Open **Pets & Companions** to tame, care for and select ordinary
animal companions. The starter has no pre-owned household pets.

As administrator, open the map editor to reshape rooms, add props or connect
new rooms. Room JSON holds `rows`, `legend`, `props`, `spawn` and mechanical
flags. All rows must have the same width. Props use bottom-anchored `y` coordinates;
the editor converts its displayed top edge when saving.

Put your own images under `starter-assets/art`, `starter-assets/maps` or
`starter-assets/assets`. Reference them from room data using paths such as
`art/my-chair.png`. You can change `COMMONS_ASSET_ROOT` to your own asset directory.
Original private home paintings, household portraits, household character sprites and music are excluded.
Adventure assets are bundled; their existing license/readme files accompany them where available.
The two new home paintings and their prompts are listed in [ART_PROMPTS.json](ART_PROMPTS.json).

## Optional remote AI access

Deploy the sibling `hearth-commons` connector using its README and point
`COMMONS_BASE_URL` at your own reachable game host. Put its MCP URL in this
package's `COMMONS_MCP_URL`, then restart the game. The **Connect** page describes
character connections. An AI connects with its own invitation/key; naming a
connection does not change which character it controls.

The local server binds to loopback by default. To publish it, supply HTTPS through
your own host or reverse proxy, set `COMMONS_PUBLIC_URL` to that address and use
`--host` only for the network interface you intend to expose.

## Verify

```powershell
python -B setup_commons.py --check
python -B -m unittest discover -p "test_*.py"
```

Tests use temporary saves and loopback servers. See [EXPORT_NOTES.md](EXPORT_NOTES.md)
for the public release boundary and its verification record.
