"""Shared holiday decoration seasons, independent of weather/gameplay rolls."""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import os
ZONE = ZoneInfo(os.environ.get('COMMONS_TIMEZONE', 'UTC'))
SETTINGS_KEY = 'settings:holidays'

KEEPSAKES_FILE = Path(__file__).resolve().parents[1] / 'assets' / 'holiday-keepsakes.json'
OUTDOOR_ROOMS = frozenset({
    'hearth', 'garden', 'woods', 'roof', 'wild-edge',
    'village-square', 'wizards-tower-outside', 'canyon-overlook', 'dusty-river',
    'saloon-district', 'dt-town-square', 'dt-campfire',
    'dt-abandoned-mine', 'dt-open-plains', 'dt-plains-north', 'dt-plains-north2',
    'enchanted-forest-main', 'enchanted-forest-north', 'enchanted-forest-right',
    'fey-wilds-main', 'fey-wilds-north1', 'fey-wilds-north2',
})
THEMES = {
    'halloween': ('Halloween', 'Small glowing pumpkins, autumn wreaths and leaf garlands decorate the edges.'),
    'christmas': ('Christmas', 'Evergreen wreaths, warm fairy lights and small trees with gifts decorate the edges.'),
    'valentines': ("Valentine's Day", 'Heart bunting, rose wreaths and small flower arrangements decorate the edges.'),
    'st_patricks': ("Saint Patrick's Day", 'Shamrock garlands, green wreaths and small clover pots decorate the edges.'),
    'easter': ('Easter', 'Pastel egg garlands, blossom wreaths and little spring baskets decorate the edges.'),
}

def easter_date(year):
    """Gregorian computus (Oudin), https://aa.usno.navy.mil/faq/easter."""
    c = year // 100
    n = year % 19
    k = (c - 17) // 25
    i = (c - c // 4 - (c - k) // 3 + 19 * n + 15) % 30
    i -= (i // 28) * (1 - (i // 28) * (29 // (i + 1)) * ((21 - n) // 11))
    j = (year + year // 4 + i + 2 - c + c // 4) % 7
    l = i - j
    month = 3 + (l + 40) // 44
    day = l + 28 - 31 * (month // 4)
    return date(year, month, day)

def _window(ident, start, end, holiday):
    name, description = THEMES[ident]
    return {'id': ident, 'name': name, 'start': start.isoformat(),
            'end': end.isoformat(), 'holiday_date': holiday.isoformat(),
            'description': description, 'snow': ident == 'christmas'}

def calendar(year):
    """Six annual decoration windows, with inclusive end dates."""
    easter = easter_date(year)
    easter_start = max(date(year, 3, 18), easter - timedelta(days=21))
    return [
        _window('valentines', date(year, 2, 1), date(year, 2, monthrange(year, 2)[1]), date(year, 2, 14)),
        _window('st_patricks', date(year, 3, 1), easter_start - timedelta(days=1), date(year, 3, 17)),
        _window('easter', easter_start, date(year, 4, 30), easter),
        _window('halloween', date(year, 10, 1), date(year, 11, 14), date(year, 10, 31)),
        _window('christmas', date(year, 11, 15), date(year + 1, 1, 10), date(year, 12, 25)),
    ]

def local_date(now=None):
    if now is None:
        return datetime.now(timezone.utc).astimezone(ZONE).date()
    if isinstance(now, datetime):
        return (now.replace(tzinfo=ZONE) if now.tzinfo is None else now.astimezone(ZONE)).date()
    if isinstance(now, date):
        return now
    raise TypeError('Use a date or datetime for the holiday clock.')

def room_is_outdoors(room):
    """Room authors can override the conservative known outdoor room list."""
    if isinstance(room, str):
        return room in OUTDOOR_ROOMS
    if not isinstance(room, dict):
        return False
    if isinstance(room.get('holiday_outdoors'), bool):
        return room['holiday_outdoors']
    if isinstance(room.get('outdoors'), bool):
        return room['outdoors']
    if isinstance(room.get('outdoor'), bool):
        return room['outdoor']
    return room.get('id') in OUTDOOR_ROOMS

def load_keepsakes(path=None):
    """Seasonal keepsakes from the manifest. A missing or broken file means none,
    never a broken holiday state: decorations are a gift, not a dependency."""
    try:
        raw = json.loads(Path(path or KEEPSAKES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    rows = raw.get('keepsakes') if isinstance(raw, dict) else None
    return [dict(row) for row in (rows or [])
            if isinstance(row, dict) and row.get('holiday') in THEMES
            and isinstance(row.get('room'), str) and row.get('art')]

def _room_id(room):
    if isinstance(room, str):
        return room
    if isinstance(room, dict):
        return room.get('id')
    return None

def holiday_state(store=None, now=None, room=None, keepsakes=None):
    today = local_date(now)
    this_year = calendar(today.year)
    windows = calendar(today.year - 1) + this_year + calendar(today.year + 1)
    stamp = today.isoformat()
    scheduled = next((dict(row) for row in windows if row['start'] <= stamp <= row['end']), None)
    upcoming = next((dict(row) for row in windows if row['start'] > stamp), None)
    settings = store.read(SETTINGS_KEY, {}) if store is not None else {}
    if not isinstance(settings, dict):
        settings = {}
    mode = settings.get('mode', 'auto')
    held = settings.get('holiday') or settings.get('held_holiday')
    if mode not in ('auto', 'off', 'hold') or (mode == 'hold' and (not isinstance(held, str) or held not in THEMES)):
        mode, held = 'auto', None
    current = scheduled
    if mode == 'off':
        current = None
    elif mode == 'hold':
        current = next(dict(row) for row in this_year if row['id'] == held)
        if scheduled and scheduled['id'] == held:
            current = dict(scheduled)
    season = ('winter' if today.month in (12, 1, 2) else 'spring' if today.month in (3, 4, 5)
              else 'summer' if today.month in (6, 7, 8) else 'autumn')
    outdoors = room_is_outdoors(room) if room is not None else None
    snow = bool(current and current['id'] == 'christmas' and outdoors is not False)
    description = current['description'] if current else 'No holiday decorations are up.'
    if snow:
        description += ' Gentle Christmas snow falls outdoors.'
    pieces = []
    if current:
        rid = _room_id(room)
        pieces = [row for row in (load_keepsakes() if keepsakes is None else keepsakes)
                  if row['holiday'] == current['id'] and (rid is None or row['room'] == rid)]
    if pieces and room is not None:
        description += ' Out for the season here: ' + '; '.join(
            str(row.get('label') or 'a keepsake') for row in pieces) + '.'
    return {'date': stamp, 'timezone': str(ZONE), 'season': season,
            'mode': mode, 'held_holiday': held if mode == 'hold' else None,
            'current': current, 'scheduled': scheduled, 'next': upcoming,
            'calendar': this_year, 'can_manage': False, 'keepsakes': pieces,
            'snow': snow, 'outdoors': outdoors, 'description': description,
            'seasonal_weather': {'name': 'Christmas snow', 'description': 'Gentle snow falls outdoors.',
                                 'visual_only': True} if snow else None}

def update_settings(store, body):
    """Host authenticates the owner; validation precedes the shared save."""
    if not isinstance(body, dict):
        raise ValueError('Choose an automatic, off, or held holiday setting.')
    mode = body.get('mode')
    if mode not in ('auto', 'off', 'hold'):
        raise ValueError('Choose auto, off, or hold.')
    held = body.get('holiday') or body.get('held_holiday')
    if mode == 'hold' and (not isinstance(held, str) or held not in THEMES):
        raise ValueError('Choose an available holiday decoration season.')
    settings = {'mode': mode, 'holiday': held if mode == 'hold' else None,
                'updated_at': datetime.now(timezone.utc).isoformat()}
    store.write(SETTINGS_KEY, settings)
    return holiday_state(store)
