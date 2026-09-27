"""Atomic, additive imports of explicitly commissioned ambient story history.

Existing profiles are never edited. User-controlled characters cannot be authors,
relationship endpoints, or follow initiators. NPCs may follow them, never vice
versa. Stable IDs and content fingerprints make retries safe.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import aiosqlite

from services import world_feed


class _BatchConnection:
    """Keep service-level commits inside the caller's single import transaction."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    async def commit(self):
        pass


async def import_history(db: aiosqlite.Connection, seed: dict[str, Any], *, apply: bool = False) -> dict[str, int | bool]:
    if db.in_transaction:
        raise world_feed.WorldFeedError('History import requires its own clean connection')
    batch = str(seed['batch_id'])
    if not re.fullmatch(r'[a-z0-9-]{1,80}', batch):
        raise world_feed.WorldFeedError('Invalid history batch id')
    digest = hashlib.sha256(json.dumps(seed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    metadata = {
        'history_batch': batch, 'seed_sha256': digest,
        'authorship': 'newly authored ambient backfill, commissioned by Owner',
        'sources': seed.get('sources', []), 'continuity': seed.get('continuity', ''),
    }
    result = {'applied': apply, 'posts_created': 0, 'posts_existing': 0, 'relationships_created': 0, 'relationships_existing': 0}
    result.update(profiles_created=0, profiles_existing=0, follows_created=0, follows_existing=0)
    await db.execute('BEGIN IMMEDIATE')
    try:
        connection = _BatchConnection(db)
        world_id = seed['world_id']
        profiles = {p['handle'].casefold(): p for p in await world_feed.list_profiles(connection, world_id, include_inactive=True)}
        for definition in seed.get('profiles', []):
            if definition.get('is_user_controlled'):
                raise world_feed.WorldFeedError('History cannot create user-controlled profiles')
            handle = world_feed.normalize_handle(definition['handle']).casefold()
            if handle in profiles:
                if profiles[handle]['is_user_controlled']:
                    raise world_feed.WorldFeedError('History cannot claim a user-controlled profile')
                if profiles[handle]['metadata'].get('history_batch') != batch:
                    raise world_feed.WorldFeedConflict(f'New history profile collides with existing @{handle}')
                result['profiles_existing'] += 1
                continue
            profiles[handle] = await world_feed.create_profile(connection, world_id, {
                **definition, 'id': f'{batch}-profile-{handle}',
                'metadata': {**metadata, **definition.get('metadata', {})},
                'is_user_controlled': False,
            })
            result['profiles_created'] += 1

        def character(handle):
            profile = profiles.get(handle.casefold())
            if not profile or profile['is_user_controlled']:
                raise world_feed.WorldFeedError(f'History requires an existing non-user-controlled character: {handle}')
            return profile

        # Validate the whole plan before any content is inserted.
        keys = set()
        orders = {}
        for post in seed.get('posts', []):
            character(post['handle'])
            key = post['key']
            if not re.fullmatch(r'[a-z0-9-]{1,40}', key) or key in keys:
                raise world_feed.WorldFeedError('History post keys must be unique and stable')
            if post.get('parent') and post['parent'] not in keys:
                raise world_feed.WorldFeedError('A historical reply must follow its parent in this batch')
            if not post.get('fictional_at') or not post.get('body', '').strip():
                raise world_feed.WorldFeedError('History needs a story-time label and body')
            order = post.get('timeline_order')
            if order is not None:
                if type(order) is not int or not -2_000_000_000 <= order < 0:
                    raise world_feed.WorldFeedError('Historical timeline order must be a negative integer')
                if post.get('parent') and (orders.get(post['parent']) is None or order <= orders[post['parent']]):
                    raise world_feed.WorldFeedError('Historical replies must be later than their parent')
            orders[key] = order
            keys.add(key)
        for relation in seed.get('relationships', []):
            character(relation['from'])
            character(relation['to'])
        for follow in seed.get('follows', []):
            character(follow['from'])
            if follow['to'].casefold() not in profiles:
                raise world_feed.WorldFeedError(f"Unknown follow target: {follow['to']}")

        for post in seed.get('posts', []):
            post_id = f"{batch}-{post['key']}"
            existing = await db.execute_fetchall('SELECT metadata FROM story_feed_posts WHERE id = ?', (post_id,))
            if existing:
                if json.loads(existing[0]['metadata']).get('seed_sha256') != digest:
                    raise world_feed.WorldFeedConflict(f'History batch changed; refusing to overwrite {post_id}')
                result['posts_existing'] += 1
                continue
            await world_feed.create_post(connection, world_id, {
                'id': post_id, 'author_profile_id': character(post['handle'])['id'],
                'body': post['body'], 'fictional_at': post['fictional_at'],
                'parent_post_id': f"{batch}-{post['parent']}" if post.get('parent') else None,
                'origin': 'ai', 'canon_level': 'ambient', 'canon_status': 'approved',
                'metadata': {**metadata, 'historical_event': post.get('event'),
                             'specific_sources': post.get('sources', []),
                             'knowledge_basis': post.get('knowledge_basis', '')},
            })
            if post.get('timeline_order') is not None:
                await db.execute('UPDATE story_feed_posts SET timeline_order=? WHERE id=?',
                                 (post['timeline_order'], post_id))
            result['posts_created'] += 1
        for relation in seed.get('relationships', []):
            source = character(relation['from'])['id']
            target = character(relation['to'])['id']
            existing = await db.execute_fetchall(
                'SELECT id FROM story_feed_relationships WHERE world_id = ? AND from_profile_id = ? AND to_profile_id = ?',
                (world_id, source, target),
            )
            if existing:
                # Existing authored relationships always win; never overwrite them.
                result['relationships_existing'] += 1
                continue
            await world_feed.upsert_relationship(connection, world_id, {
                'from_profile_id': source, 'to_profile_id': target,
                'relationship_type': relation['relationship_type'],
                'public_summary': relation['public_summary'], 'private_context': relation['private_context'],
                'visibility': 'private', 'metadata': {**metadata, 'specific_source': relation['source']},
            })
            result['relationships_created'] += 1
        for follow in seed.get('follows', []):
            source = character(follow['from'])['id']
            target = profiles[follow['to'].casefold()]['id']
            existing = await db.execute_fetchall('SELECT 1 FROM story_feed_follows WHERE world_id = ? AND follower_profile_id = ? AND followed_profile_id = ?', (world_id, source, target))
            if existing:
                result['follows_existing'] += 1
                continue
            await world_feed.toggle_follow(connection, world_id, source, target)
            result['follows_created'] += 1
        if apply:
            await db.commit()
        else:
            await db.rollback()
        return result
    except BaseException:
        await db.rollback()
        raise
