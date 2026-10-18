"""Reviewed visual identity and source-bound depiction, separate from authorship."""


from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from services import world_feed as feed

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_FILE = ROOT / 'prompts/visual_reference.json'

CHARACTER_GUIDANCE = """
character_references is a trusted roster of NPC visual identities. Return
depicted_profile_ids: an array of their exact profile_id values, containing ONLY
people actually visible in the image (including a visible author). Use [] for an
object-only picture. A mention in the caption, photographer, favorite hero, or
off-camera person is NOT a depicted person. Player uses includes_player instead.
At most four people total including Player. For every recognizable person, select
their reference; do not invent a lookalike or bypass selection with 'a blond boy'.
The renderer will receive the actual selected reference pictures, so you CAN plan
selfies and expressive character moments without guessing their facial features.
Honor appearance_notes. Do not contradict a reference with invented hair, eyes,
skin, facial structure, horns, scars, body type or other distinguishing features.
References establish appearance only, never co-presence, relationships or events.
Keep the current story era; no future injuries, costumes or adult time-skip designs.
Fan icons and organization logos do not establish the account owner's appearance.
If a subject has no supplied visual identity, avoid inventing a recognizable face.
"""


def character_reference(profile, *, require_renderable=False):
    """Account-owned art, never a planner-supplied path or a fan's favorite hero.

    With ``require_renderable`` a profile whose reference picture (explicit
    reference_url or avatar fallback) would fail local_reference() is omitted,
    so the planner is never offered someone the renderer can't draw.
    """
    if profile['is_user_controlled'] or not profile['is_active']:
        return None
    metadata = profile.get('metadata') or {}
    raw = metadata.get('visual_reference') or {}
    if not isinstance(raw, dict):
        raw = {}
    notes = raw.get('description', '')
    notes = notes.strip()[:2000] if isinstance(notes, str) else ''
    url = raw.get('reference_url', '')
    url = url.strip() if isinstance(url, str) else ''
    # Only person account types get automatic avatar fallback. Generic character
    # accounts may be organizations; they can opt in through visual_reference.
    use_avatar = raw.get('use_avatar', profile['account_type'] in {
        'student', 'hero', 'teacher', 'villain', 'civilian'})
    if metadata.get('fan_only') or profile['account_type'] in {'fan', 'news'}:
        use_avatar = raw.get('use_avatar') is True
    if not url and use_avatar is True:
        url = profile.get('avatar_url', '')
    if not url and not notes:
        return None
    if require_renderable and url and not _renderable(url):
        return None
    return {'profile_id': profile['id'], 'handle': profile['handle'],
            'display_name': profile['display_name'], 'reference_url': url,
            'appearance_notes': notes}


def _renderable(url):
    try:
        local_reference(url)
    except feed.WorldFeedError:
        return False
    return True


async def character_roster(db, world_id, *, renderable_only=True):
    """NPC visual identities in this world.

    The default (planner/UI) roster only lists people whose reference picture
    can actually be rendered. render_characters passes renderable_only=False so
    a picture that broke after planning fails loudly with the specific
    "restore it" error instead of a vague "no visual identity".
    """
    return [ref for profile in await feed.list_profiles(db, world_id)
            if (ref := character_reference(profile, require_renderable=renderable_only))]


def validate_characters(plan, roster, *, required=False):
    if required and 'depicted_profile_ids' not in plan:
        raise feed.WorldFeedError('Photo plan is missing depicted_profile_ids (use [] for nobody)')
    ids = plan.get('depicted_profile_ids', [])
    if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids))
            or len(ids) + int(bool(plan.get('includes_player'))) > 4):
        raise feed.WorldFeedError('Select up to four distinct visual subjects, including Player')
    allowed = {r['profile_id'] for r in roster}
    if any(i not in allowed for i in ids):
        raise feed.WorldFeedError('A depicted character has no visual identity in this world')
    return ids


def local_reference(url):
    """Resolve uploaded art only. No remote fetch, traversal or arbitrary files."""
    from api.images import IMAGES_DIR
    from PIL import Image

    parsed = urlsplit(url)
    prefix = '/api/images/file/'
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not parsed.path.startswith(prefix):
        raise feed.WorldFeedError('Character references must use an uploaded Anam image')
    name = unquote(parsed.path[len(prefix):])
    if not name or '/' in name or '\\' in name or ':' in name or name in {'.', '..'}:
        raise feed.WorldFeedError('Invalid character reference image')
    path = (IMAGES_DIR / name).resolve()
    if path.parent != IMAGES_DIR.resolve() or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise feed.WorldFeedError('Character reference image is missing or too large; restore it before rendering')
    try:
        with Image.open(path) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('Unsupported format')
            image.verify()
    except (OSError, ValueError) as exc:
        raise feed.WorldFeedError('Character reference is not a valid PNG, JPEG or WebP image') from exc
    return path


async def render_characters(db, world, plan):
    roster = await character_roster(db, world['id'], renderable_only=False)
    ids = validate_characters(plan, roster)
    by_id = {r['profile_id']: r for r in roster}
    result = []
    for profile_id in ids:
        ref = by_id[profile_id]
        item = {'profile_id': profile_id, 'handle': ref['handle'],
                'label': ref['display_name'] + ' (@' + ref['handle'] + ')',
                'prompt': ref['appearance_notes'], 'is_player': False}
        if ref['reference_url']:
            path = local_reference(ref['reference_url'])
            item.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        result.append(item)
    player = render_reference(world, plan)
    if player:
        result.append({**player, 'handle': plan['visual_subject'],
                       'label': 'Player Character', 'is_player': True})
    if sum(Path(r['path']).stat().st_size for r in result if r.get('path')) > 20 * 1024 * 1024:
        raise feed.WorldFeedError('Combined character references exceed the image service limit')
    return result


def reference(world):
    if not REFERENCE_FILE.is_file():
        return None
    data = json.loads(REFERENCE_FILE.read_text(encoding='utf-8'))
    if world['id'] != data['world_id'] or world['story_branch'] != data['story_branch']:
        return None
    return data


def mentions(text, names):
    return any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I) for name in names)


async def context(db, world, author, idea):
    ref = reference(world)
    if not ref:
        return None
    profiles = await feed.list_profiles(db, world['id'])
    subject = next((p for p in profiles if p['handle'].casefold() == ref['handle'].casefold() and p['is_user_controlled']), None)
    if not subject:
        return None
    aliases = list(dict.fromkeys(ref['aliases'] + [subject['handle'], subject['display_name']]))
    beats = []
    if world['fictional_now'] == ref['beats_checkpoint']:
        beats = [dict(b) for b in ref['established_beats'] if author['handle'] in b['authors']]
    # Only approved current posts from this author or Player can seed new moments.
    # Other people's gossip, private memories and historical imports are not witnesses.
    rows = await db.execute_fetchall(
        "SELECT id,body,fictional_at FROM story_feed_posts WHERE world_id=? "
        "AND author_profile_id IN (?,?) AND canon_status='approved' AND fictional_at=? "
        "AND json_extract(metadata,'$.history_batch') IS NULL "
        "ORDER BY created_at_epoch DESC,id DESC LIMIT 16",
        (world['id'], author['id'], subject['id'], world['fictional_now']),
    )
    for row in rows:
        if mentions(row['body'], aliases):
            beats.append({'id': 'post:' + row['id'], 'fictional_at': row['fictional_at'],
                          'scene': row['body'], 'source': row['id']})
    return {'reference': {k: ref[k] for k in ('handle', 'display_name', 'description', 'wardrobe', 'render_constraints', 'reference_url')},
            'aliases': aliases, 'beats': beats,
            'explicit_request': bool(idea.strip() and mentions(idea, aliases))}


def validate_plan(plan, context):
    """Return trusted provenance, never a model-supplied local file path."""
    text = ' '.join(plan[k] for k in ('caption', 'image_prompt', 'alt_text'))
    includes = plan.get('includes_player', False)
    if type(includes) is not bool:
        raise feed.WorldFeedError('Photo subject selection must be true or false')
    if not includes:
        if context and mentions(text, context['aliases']):
            raise feed.WorldFeedError('A Player photo must select her visual reference')
        return {}
    if not context:
        raise feed.WorldFeedError('No Player visual reference is configured for this story')
    if context['explicit_request']:
        return {'visual_subject': context['reference']['handle'], 'visual_basis': 'storyteller_request'}
    beat = next((b for b in context['beats'] if b['id'] == plan.get('source_beat_id')), None)
    if not beat:
        raise feed.WorldFeedError('A Player photo needs a supplied story beat or an explicit request')
    return {'visual_subject': context['reference']['handle'], 'visual_basis': beat['id'],
            'visual_source_hash': hashlib.sha256(json.dumps(beat, sort_keys=True).encode()).hexdigest(),
            'fictional_at': beat['fictional_at']}


def remaining_protected(names, context, authorized=False):
    if not authorized or not context:
        return names
    aliases = {name.casefold() for name in context['aliases']}
    return [name for name in names if name.casefold() not in aliases]


def public_reference(world):
    ref = reference(world)
    return {k: ref[k] for k in ('handle', 'display_name', 'description', 'wardrobe',
                               'render_constraints', 'reference_url')} if ref else None


def render_reference(world, plan):
    if not plan.get('visual_subject'):
        return None
    ref = reference(world)
    if not ref or plan['visual_subject'] != ref['handle']:
        raise feed.WorldFeedError('The photo visual reference no longer matches this story')
    path = (ROOT / ref['reference_image']).resolve()
    if not path.is_relative_to((ROOT / 'data/images').resolve()) or not path.is_file():
        raise feed.WorldFeedError('Player reference image is unavailable; restore it before rendering')
    from PIL import Image
    with Image.open(path) as img:
        img.verify()
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'prompt': '\n'.join((ref['description'], ref['wardrobe'], ref['render_constraints']))}
