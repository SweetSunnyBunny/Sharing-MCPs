"""[commons:fishing] Fishing paint is independent of the walking grid.

Map Table and browser point tests live in assets/fishing-areas.js. This module
validates saved masks and describes their effective coverage to game/AI readers.
Legacy polygons, bottom-anchored props and named native water remain authored.
"""
from collections import deque
import math
import re

def validate_paint(paint, rows):
    """Accept only a same-size grid: 1 fish, 0 erase, . inherit map water."""
    if (not isinstance(paint, list) or len(paint) != len(rows)
            or any(not isinstance(line, str) or len(line) != len(row)
                   for line, row in zip(paint, rows))):
        raise ValueError('the fishing painting must keep the map width and height')
    if any(ch not in '.01' for line in paint for ch in line):
        raise ValueError('the fishing painting accepts only ., 0 and 1')
    return list(paint)

def has_overrides(room):
    return any(ch in '01' for line in room.get('fishing_paint', []) for ch in line)

def _water_tile(room, x, y):
    tile = (room.get('legend') or {}).get(room['rows'][y][x], {})
    name = tile.get('name', '')
    return bool(room.get('water') and re.search(r'\b(water|river|lake|pond|stream|sea|ocean)\b', name, re.I)
                and not re.search(r'bank|bridge', name, re.I))

def _clip(points, axis, edge, above):
    """Clip a polygon to one tile edge, keeping thin authored shorelines."""
    output = []
    for a, b in zip(points, points[1:] + points[:1]):
        ain = a[axis] >= edge if above else a[axis] <= edge
        bin_ = b[axis] >= edge if above else b[axis] <= edge
        if ain != bin_:
            ratio = (edge - a[axis]) / (b[axis] - a[axis])
            output.append([a[0] + ratio * (b[0] - a[0]), a[1] + ratio * (b[1] - a[1])])
        if bin_:
            output.append(b)
    return output

def _polygon_in_tile(points, x, y):
    if not isinstance(points, list) or len(points) < 3:
        return False
    if any(not isinstance(p, (list, tuple)) or len(p) != 2
           or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p)
           for p in points):
        return False
    clipped = [list(p) for p in points]
    for axis, edge, above in ((0, x, True), (0, x + 1, False), (1, y, True), (1, y + 1, False)):
        clipped = _clip(clipped, axis, edge, above)
        if len(clipped) < 3:
            return False
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(clipped, clipped[1:] + clipped[:1]))
    return abs(area) > 1e-10

def _base_cell(room, x, y, props=None):
    if props is None and _water_tile(room, x, y):
        return True
    for prop in props if props is not None else room.get('props', []):
        if not prop.get('fishable'):
            continue
        px, py, w, h = (prop.get('x', 0), prop.get('y', 0), prop.get('w') or 1, prop.get('h') or 1)
        top = py + 1 - h
        if px < x + 1 and px + w > x and top < y + 1 and top + h > y:
            return True
    return any(_polygon_in_tile(z.get('points'), x, y) for z in room.get('fishing_zones', [])) if props is None else False

def effective_cells(room, props=None):
    """Tiles containing some water, not a claim that a polygon fills each tile.

Passing props checks only those particular fixtures after erase overrides.
This keeps a fully erased old rectangle from advertising 'Fish here'.
"""
    mask = room.get('fishing_paint') or []
    for y, row in enumerate(room.get('rows', [])):
        for x in range(len(row)):
            value = mask[y][x] if y < len(mask) and x < len(mask[y]) else '.'
            if value == '0':
                continue
            if (props is None and value == '1') or _base_cell(room, x, y, props):
                yield x, y

def has_water(room):
    return next(effective_cells(room), None) is not None

def prop_has_water(room, prop):
    return next(effective_cells(room, [prop]), None) is not None

def _bounds(cells, limit=16):
    """Connected extents for compact AI descriptions; holes stay explicitly holes."""
    pending = set(cells)
    areas = []
    while pending:
        first = min(pending, key=lambda p: (p[1], p[0]))
        pending.remove(first)
        queue, group = deque([first]), [first]
        while queue:
            x, y = queue.popleft()
            for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if point in pending:
                    pending.remove(point)
                    queue.append(point)
                    group.append(point)
        xs, ys = [p[0] for p in group], [p[1] for p in group]
        areas.append({'x': min(xs), 'y': min(ys), 'w': max(xs) - min(xs) + 1,
                      'h': max(ys) - min(ys) + 1, 'tiles': len(group)})
    return areas[:limit], len(areas)

def describe(room):
    cells = list(effective_cells(room))
    mask = room.get('fishing_paint') or []
    areas, count = _bounds(cells)
    return {'kind': 'water', 'tile_count': len(cells), 'areas': areas, 'areas_total': count,
            'painted_tiles': sum(line.count('1') for line in mask),
            'erased_tiles': sum(line.count('0') for line in mask),
            'coordinate_system': 'Tiles from the top left; x goes right and y goes down.',
            'description': ('Fishing water is present in these connected extents. Bounds may contain shore or erased holes; '
                            'the room fishing_paint and original water geometry give exact coverage.' if cells else
                            'No fishing water remains in the painted areas.')}
