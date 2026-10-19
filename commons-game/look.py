"""Commons runtime helper."""
import json, math, sys, os
from server.room_experience import room_experience

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD = os.path.join(HERE, "world")
MARKS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def look(room_id):
    path = os.path.join(WORLD, room_id + ".json")
    with open(path, encoding="utf-8") as f:
        r = json.load(f)

    grid = [list(row) for row in r["rows"]]
    h = len(grid)

    for i, p in enumerate(r.get("props", [])):
        mark = MARKS[i % len(MARKS)]
        px = float(p["x"])
        py = float(p["y"])
        pw = max(float(p.get("w", 1)), 0.0)
        ph = max(float(p.get("h", 1)), 0.0)
        left = math.floor(px)
        right = math.ceil(px + pw)
        top = math.floor(py + 1 - ph)
        bottom = math.ceil(py + 1)
        for gy in range(top, bottom):
            for gx in range(left, right):
                if 0 <= gy < h and 0 <= gx < len(grid[gy]):
                    grid[gy][gx] = mark

    sx = math.floor(float(r["spawn"]["x"]))
    sy = math.floor(float(r["spawn"]["y"]))
    if 0 <= sy < h:
        grid[sy][sx] = "@"

    w = len(grid[0])
    print()
    print(f"  {r['name']}  [{r['id']}]   {w} x {h} tiles, {r['view']} view")
    scene = room_experience(r)
    if scene['description']:
        print(f"  {scene['description']}")
    
    if 'fishing_water' in scene:
        water = scene['fishing_water']
        print(f"  Fishing water: {water['tile_count']} tiles touched; "
              f"{water['painted_tiles']} painted, {water['erased_tiles']} erased.")
        for area in water['areas']:
            print(f"    extent x={area['x']} y={area['y']} w={area['w']} h={area['h']} "
                  f"({area['tiles']} water-containing tiles; bounds can include shore or holes)")
    print("  " + "+" + "-" * w + "+")
    for y, row in enumerate(grid):
        print(f"{y:2d}|" + "".join(row) + "|")
    print("  " + "+" + "-" * w + "+")
    print("   " + "".join(str(x % 10) for x in range(w)))
    print()
    print("  @ = spawn point")
    for i, p in enumerate(r.get("props", [])):
        mark = MARKS[i % len(MARKS)]
        print(f"  {mark} = {p['label']:<18} ({p['x']},{p['y']}) {p.get('w',1)}x{p.get('h',1)}"
              f"  - placed by {p.get('by','?')} {p.get('placed','')}")
        if p.get("note"):
            print(f"      {p['note']}")
        fixture = scene['fixtures'][i]
        print(f"      [{fixture['kind']}] id={fixture['id']}")
        for action in fixture['actions']:
            if action['tool'] == 'commons_move':
                print(f"      {action.get('label', 'Go here')}: commons_move destination={action['destination']}")
            else:
                target = action.get('choices', {}).get('target', '')
                required = ', '.join(action.get('required_fields', []))
                print(f"      {action.get('label', action['action'])}: commons_act action={action['action']}"
                      f" target={target}" + (f"; supply {required}" if required else ''))

    if scene['landmarks']:
        print()
        print("  --- Scenery landmarks ---")
        for landmark in scene['landmarks']:
            print(f"  * {landmark['label']} [{landmark['id']}; {landmark['kind']}]")
            print(f"      {landmark['description']}")
            if landmark.get('navigation'):
                print(f"      Approach: commons_move destination={landmark['id']}")

    notes = []
    npath = os.path.join(WORLD, "notes.json")
    try:
        with open(npath, encoding="utf-8") as nf:
            notes = [n for n in json.load(nf) if n.get("room") == r["id"]]
    except FileNotFoundError:
        print("  [notes] world/notes.json not found -- notes NOT checked")
        notes = None
    except Exception as e:
        print(f"  [notes] could not read world/notes.json ({e}) -- notes NOT checked")
        notes = None

    if notes is not None:
        print()
        print(f"  --- {len(notes)} note(s) pinned here (source: world/notes.json) ---")
        for n in notes:
            stamp = n.get("placed") or "undated"
            print(f"  * ({n.get('x')},{n.get('y')}) by {n.get('by','?')} -- {stamp}")
            body = (n.get("text") or "").strip()
            for line in [body[i:i+92] for i in range(0, len(body), 92)] or ["(no text)"]:
                print(f"      {line}")
            if n.get("img"):
                print(f"      [picture] {n['img']}")
    print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        look(sys.argv[1])
    else:
        for f in sorted(os.listdir(WORLD)):
            if f.endswith(".json"):
                path = os.path.join(WORLD, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        candidate = json.load(fh)
                except (OSError, ValueError):
                    continue
                if isinstance(candidate, dict) and "id" in candidate and "rows" in candidate:
                    look(f[:-5])
