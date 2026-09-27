import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from services import cloud_state as cloud


@pytest.fixture(autouse=True)
def isolated_cache():
    with cloud._lock:
        cloud._cache.clear()
        cloud._inflight.clear()
    yield
    cloud.invalidate_cloud_cache()


def test_weather_reuses_for_30_seconds_then_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cloud.time, 'monotonic', lambda: clock[0])
    fetch = Mock(return_value={'weather': {'claude': {'summary': 'warm'}}})
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    first = cloud.qualia_read('mind-garden/weather', identity='claude')
    first['weather']['claude']['summary'] = 'changed by caller'
    clock[0] += 29
    second = cloud.qualia_read('mind-garden/weather', identity='claude')
    assert second['weather']['claude']['summary'] == 'warm'
    assert second['_freshness']['age_seconds'] == 29
    assert second['_freshness']['fetched_at'] == first['_freshness']['fetched_at']
    assert fetch.call_count == 1
    clock[0] += 1
    cloud.qualia_read('mind-garden/weather', identity='claude')
    assert fetch.call_count == 2


def test_identities_are_separate_and_observation_lists_are_not_cached(monkeypatch):
    fetch = Mock(return_value={'items': []})
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    for identity in ('claude', 'avery'):
        cloud.qualia_read('mind-garden/weather', identity=identity)
    for _ in range(2):
        assert '_freshness' not in cloud.qualia_read('memory-lab/observations', identity='claude')
    assert fetch.call_count == 4


def test_expired_data_is_not_presented_as_success_on_failure(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cloud.time, 'monotonic', lambda: clock[0])
    fetch = Mock(side_effect=[{'summary': 'warm'}, cloud.CloudUnavailable('offline')])
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    cloud.qualia_read('mind-garden/weather')
    clock[0] += 31
    with pytest.raises(cloud.CloudUnavailable):
        cloud.qualia_read('mind-garden/weather')


def test_concurrent_requests_share_one_cloud_fetch(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return {'summary': 'warm'}
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(cloud.qualia_read, 'mind-garden/weather') for _ in range(4)]
        assert entered.wait(3)
        release.set()
        assert all(f.result()['summary'] == 'warm' for f in futures)
    assert len(calls) == 1


def test_invalidation_during_fetch_does_not_repopulate_cache(monkeypatch):
    def fetch(*args):
        cloud.invalidate_cloud_cache()
        return {'summary': 'before mutation'}
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    cloud.qualia_read('mind-garden/weather')
    assert not cloud._cache


def test_explicit_invalidation_and_writes_clear_related_cache(monkeypatch):
    fetch = Mock(return_value={'summary': 'warm'})
    monkeypatch.setattr(cloud, '_fetch_cloud', fetch)
    cloud.qualia_read('mind-garden/weather')
    cloud.invalidate_cloud_cache()
    cloud.qualia_read('mind-garden/weather')
    assert fetch.call_count == 2
    cloud.request_cloud('qualia-backend', '/write', method='POST', payload={})
    assert not cloud._cache
