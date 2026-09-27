"""Explicit human-authored Markdown import, separate from NPC generation/seeding.

This module is only called by the local import CLI. It never generates text or
relaxes the NPC importer's exclusion of user-controlled authors.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlparse

from services import world_feed as feed

FIELDS = {
    'When in the story': 'fictional_at', 'Reply to (optional)': 'reply',
    'Image path or URL (optional)': 'image', 'Image description (optional)': 'alt_text',
    'Placement notes (optional; not part of the post)': 'placement_notes', 'Post text': 'body',
}


def parse_posts(text):
    """Remove template framing only; retain spelling, punctuation and line breaks."""
    text = text.lstrip('\ufeff').replace('\r\n', '\n')
    headings = list(re.finditer(r'^## Post (\d+)[ \t]*$', text, re.M))
    if not headings:
        raise feed.WorldFeedError('No numbered posts found in the writing file')
    posts = []
    for i, heading in enumerate(headings):
        section = text[heading.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)]
        labels = list(re.finditer(r'^\*\*([^\n*]+):\*\*', section, re.M))
        post = {'key': heading.group(1)}
        if not labels or labels[-1].group(1) != 'Post text':
            raise feed.WorldFeedError('Post text must be the last field in each post')
        for j, label in enumerate(labels):
            field = FIELDS.get(label.group(1))
            if not field or field in post:
                raise feed.WorldFeedError('Unknown or repeated field in post ' + post['key'])
            post[field] = section[label.end():labels[j + 1].start() if j + 1 < len(labels) else len(section)].strip()
        if not post.get('body'):
            raise feed.WorldFeedError('Empty post ' + post['key'])
        if any(p['key'] == post['key'] for p in posts):
            raise feed.WorldFeedError('Repeated post number ' + post['key'])
        reply = post.get('reply', '')
        if reply:
            url = urlparse(reply)
            if url.scheme != 'https' or url.netloc != 'example.com' or not url.fragment.startswith('thread/'):
                raise feed.WorldFeedError('Reply needs an Anam thread link in post ' + post['key'])
            post['parent_post_id'] = url.fragment.removeprefix('thread/')
        posts.append(post)
    return posts


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def import_authored_history(db, seed, *, apply=False):
    if db.in_transaction:
        raise feed.WorldFeedError('Human history import needs its own transaction')
    if not re.fullmatch(r'[a-z0-9-]{1,80}', seed['batch_id']):
        raise feed.WorldFeedError('Invalid batch identifier')
    digest = fingerprint(seed)
    result = {'applied': apply, 'posts_created': 0, 'posts_existing': 0}
    await db.execute('BEGIN IMMEDIATE')
    try:
        world = await feed.get_world(db, seed['world_id'])
        world_times = (await db.execute_fetchall('SELECT updated_at,updated_at_epoch FROM story_worlds WHERE id=?', (world['id'],)))[0]
        if world['posting_enabled'] or world['metadata'].get('automatic_pulses') or world['metadata'].get('photos', {}).get('enabled'):
            raise feed.WorldFeedError('Pause world activity and photos before importing history')
        profiles = await feed.list_profiles(db, world['id'])
        author = next((p for p in profiles if p['handle'].casefold() == seed['author_handle'].casefold()), None)
        if not author or not author['is_user_controlled'] or not author['is_active']:
            raise feed.WorldFeedError('Human history requires the selected active user-controlled account')
        keys = set()
        for item in seed['posts']:
            key = item['key']
            if not re.fullmatch(r'\d{3,6}', key) or key in keys:
                raise feed.WorldFeedError('Post numbers must be unique and stable')
            keys.add(key)
            if not item['body'] or item['body'] != item['body'].strip() or not item['fictional_at']:
                raise feed.WorldFeedError('Each post needs exact text and a story time')
            order = item['timeline_order']
            if type(order) is not int:
                raise feed.WorldFeedError('Timeline placement must be an integer')
            parent_id = item.get('parent_post_id')
            if parent_id:
                await feed._validate_linked_post(db, world['id'], parent_id, 'Parent')
                row = (await db.execute_fetchall('SELECT COALESCE(timeline_order,created_at_epoch) AS sort_order FROM story_feed_posts WHERE id=?', (parent_id,)))[0]
                if order <= row['sort_order']:
                    raise feed.WorldFeedError('Historical replies must follow their parent')
            elif order >= 0:
                raise feed.WorldFeedError('Standalone history needs a historical timeline placement')
            post_id = seed['batch_id'] + '-' + key
            existing = await db.execute_fetchall('SELECT * FROM story_feed_posts WHERE id=?', (post_id,))
            if existing:
                row = existing[0]
                if (row['world_id'] != world['id'] or row['author_profile_id'] != author['id']
                        or row['origin'] != 'human' or row['body'] != item['body']
                        or json.loads(row['metadata']).get('seed_sha256') != digest):
                    raise feed.WorldFeedConflict('Previously imported human history changed; refusing to overwrite it')
                result['posts_existing'] += 1
                continue
            await feed.create_post(db, world['id'], {
                'id': post_id, 'author_profile_id': author['id'], 'body': item['body'],
                'fictional_at': item['fictional_at'], 'parent_post_id': parent_id,
                'origin': 'human', 'canon_status': 'approved', 'canon_level': 'ambient',
                'media': item.get('media', []),
                'metadata': {'history_batch': seed['batch_id'], 'seed_sha256': digest,
                             'authorship': 'Written verbatim by Owner',
                             'source_file': seed['source_file'], 'source_sha256': seed['source_sha256'],
                             'source_post_number': key, 'source_fields': item.get('source_fields', {}),
                             'placement_note': item.get('placement_note', ''),
                             'pending_image': item.get('pending_image')},
            })
            await db.execute('UPDATE story_feed_posts SET timeline_order=? WHERE id=?', (order, post_id))
            result['posts_created'] += 1
        # This operation changes posts and their derived records, never world configuration.
        await db.execute('UPDATE story_worlds SET updated_at=?,updated_at_epoch=? WHERE id=?',
                         (world_times['updated_at'], world_times['updated_at_epoch'], world['id']))
        if apply:
            await db.commit()
        else:
            await db.rollback()
        return result
    except BaseException:
        await db.rollback()
        raise
