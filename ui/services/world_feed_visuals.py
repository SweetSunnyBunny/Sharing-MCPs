"""Reviewed visual identity and source-bound depiction, separate from authorship."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from services import world_feed as feed

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_FILE = ROOT / 'prompts/visual_reference.json'


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
