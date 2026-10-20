"""Read-only post-restart check of World Feed paging, accounts and threads."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / 'data/runtime/world-feed-verification.json'
URL = 'http://127.0.0.1:8790/api/world-feed/worlds/example-world/feed'


def record(status, **details):
    payload = {'status': status, 'at': datetime.now(timezone.utc).isoformat(), **details}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECEIPT.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(temporary, RECEIPT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    headers = {'Authorization': 'Bearer ' + os.environ['ANAM_API_KEY']}

    def read(params, url=URL):
        request = Request(url + '?' + urlencode(params), headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    record('waiting_for_updated_backend')
    deadline = time.monotonic() + args.timeout_seconds
    last_error = 'Updated cursor contract not served yet'
    while time.monotonic() < deadline:
        try:
            first = read({'limit': 2})
            cursor = first.get('next_cursor')
            if cursor and 'before_id' in cursor:
                second = read({'limit': 2, **cursor})
                ids = [post['id'] for post in first['posts'] + second['posts']]
                if len(ids) != len(set(ids)) or not second['posts']:
                    raise ValueError('Served pagination repeated or lost the next page')
                ordering = [(post['created_at_epoch'], post['id']) for post in first['posts'] + second['posts']]
                if ordering != sorted(ordering, reverse=True):
                    raise ValueError('Served cursor order is inconsistent')
                base = 'http://127.0.0.1:8790/api/world-feed'
                profile = read({}, base + '/profiles/' + first['posts'][0]['author']['id'])
                if not {'followers', 'following', 'posts', 'viewer_follows', 'follows_viewer'} <= profile.keys():
                    raise ValueError('Account detail contract not served yet')
                connections = read({}, base + '/profiles/' + profile['id'] + '/connections')
                thread = read({}, base + '/posts/' + first['posts'][0]['id'] + '/thread')
                if 'profiles' not in connections or thread['post']['id'] != first['posts'][0]['id'] or 'posts' not in thread:
                    raise ValueError('Account connections or thread contract is incomplete')
                photos = read({}, base + '/worlds/example-world/photos')
                if not {'settings', 'jobs'} <= photos.keys():
                    raise ValueError('Photo studio backend not served yet')
                if photos['settings'].get('daily_limit_scope') != 'automatic_only':
                    raise ValueError('Updated automatic-only photo budget is not served yet')
                if photos['settings'].get('max_daily_limit') != 20:
                    raise ValueError('Twenty-photo allowance is not served yet')
                if photos['settings'].get('review_limit') != max(2, photos['settings']['daily_limit']):
                    raise ValueError('Photo review backlog does not match the daily allowance')
                record('verified', check='Live pagination, accounts, threads and photo studio endpoints respond correctly', sampled_post_ids=ids, profile_id=profile['id'], photo_settings=photos['settings'])
                return 0
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
        time.sleep(2)
    record('failed', reason=last_error)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
