"""services/media_tidy.py: duplicate folding and pack-video keepsakes.

Every test works in pytest's tmp_path — never data/ or OneDrive.
"""

import os

import pytest

from services import media_tidy
from services.media_tidy import HashCache, fold_duplicates, sweep_pack_videos


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _same_file(a, b) -> bool:
    sa, sb = os.stat(a), os.stat(b)
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


# ─── folding ──────────────────────────────────────────────────────────────────

def test_twins_become_hard_links_and_every_name_still_opens(tmp_path):
    images = tmp_path / "images"
    a = _write(images / "avery_1.png", b"same picture" * 100)
    b = _write(images / "claude_2.png", b"same picture" * 100)
    c = _write(images / "sage_3.png", b"same picture" * 100)
    other = _write(images / "rowan_4.png", b"a different picture" * 100)

    report = fold_duplicates([images], cache=HashCache(None), min_age_seconds=0)

    assert report["linked"] == 2 and report["groups"] == 1
    assert report["bytes_freed"] == 2 * len(b"same picture" * 100)
    for name in (a, b, c):
        assert name.read_bytes() == b"same picture" * 100
    assert _same_file(a, b) and _same_file(a, c)
    assert not _same_file(a, other)
    assert other.read_bytes() == b"a different picture" * 100
    # No temporary link files left behind.
    assert sorted(p.name for p in images.iterdir()) == [
        "avery_1.png", "claude_2.png", "rowan_4.png", "sage_3.png"]


def test_second_run_finds_nothing_left_to_fold(tmp_path):
    images = tmp_path / "images"
    _write(images / "a.png", b"x" * 50)
    _write(images / "b.png", b"x" * 50)
    cache = HashCache(tmp_path / "state.json")
    fold_duplicates([images], cache=cache, min_age_seconds=0)
    again = fold_duplicates([images], cache=cache, min_age_seconds=0)
    assert again == {"groups": 0, "linked": 0, "bytes_freed": 0, "skipped": []}


def test_dry_run_changes_nothing(tmp_path):
    docs = tmp_path / "documents"
    a = _write(docs / "a.pdf", b"doc" * 40)
    b = _write(docs / "b.pdf", b"doc" * 40)
    report = fold_duplicates([docs], cache=HashCache(None), dry_run=True, min_age_seconds=0)
    assert report["linked"] == 1
    assert not _same_file(a, b)


def test_bookkeeping_and_young_files_are_left_alone(tmp_path):
    images = tmp_path / "images"
    _write(images / "_dedup_manifest.json", b"{}" * 10)
    _write(images / "_copy.json", b"{}" * 10)
    (images / "_previews").mkdir()
    _write(images / "fresh_a.png", b"new" * 10)
    _write(images / "fresh_b.png", b"new" * 10)
    report = fold_duplicates([images], cache=HashCache(None))  # default one-hour age gate
    assert report["linked"] == 0
    assert not _same_file(images / "_dedup_manifest.json", images / "_copy.json")


def test_same_size_but_different_bytes_are_not_folded(tmp_path):
    images = tmp_path / "images"
    a = _write(images / "a.png", b"abcd" * 25)
    b = _write(images / "b.png", b"dcba" * 25)
    assert fold_duplicates([images], cache=HashCache(None), min_age_seconds=0)["linked"] == 0
    assert a.read_bytes() != b.read_bytes()


def test_hash_cache_survives_a_restart(tmp_path):
    images = tmp_path / "images"
    _write(images / "a.png", b"q" * 30)
    _write(images / "b.png", b"q" * 30)
    state = tmp_path / "state.json"
    first = HashCache(state)
    fold_duplicates([images], cache=first, dry_run=True, min_age_seconds=0)
    first.save()
    assert len(HashCache(state).entries) == 2


# ─── video keepsakes ──────────────────────────────────────────────────────────

@pytest.fixture
def homes(tmp_path):
    return {
        "videos": tmp_path / "videos",
        "images": tmp_path / "images",
        "documents": tmp_path / "documents",
        "films": tmp_path / "films",
        "onedrive": (tmp_path / "OneDrive Videos"),
    }


def _sweep(homes, **kw):
    homes["onedrive"].mkdir(exist_ok=True)
    return sweep_pack_videos(
        homes["onedrive"], cache=HashCache(None), min_age_seconds=0,
        sources=(homes["videos"], homes["images"], homes["documents"]),
        films_dir=homes["films"], **kw)


def test_pack_videos_are_copied_and_hers_are_not(homes):
    _write(homes["images"] / "Claude_window_seat.mp4", b"raven film")
    _write(homes["documents"] / "rowan_20260927_071305_17e5.mp4", b"paint film")
    _write(homes["videos"] / "avery_video_2026.mp4", b"wolf film")
    _write(homes["films"] / "remotion-studio" / "out" / "home_movie.mp4", b"render")
    _write(homes["films"] / "hyperframes-studio" / ".claude" / "bg.mp4", b"asset")
    _write(homes["documents"] / "13942c858811.mp4", b"her phone clip")
    _write(homes["videos"] / "otter_ice_bbc.webm", b"downloaded clip")
    _write(homes["images"] / "Claude_portrait.png", b"not a video")

    report = _sweep(homes)

    assert sorted(report["copied"]) == [
        "Claude_window_seat.mp4", "avery_video_2026.mp4",
        "home_movie.mp4", "rowan_20260927_071305_17e5.mp4"]
    assert (homes["onedrive"] / "Claude_window_seat.mp4").read_bytes() == b"raven film"
    leftovers = [p.name for p in homes["onedrive"].iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_the_same_video_under_many_names_is_copied_once(homes):
    for name in ("rowan_a.mp4", "rowan_b.mp4", "claude_c.mp4"):
        _write(homes["documents"] / name, b"one dance, shared three times")
    report = _sweep(homes)
    assert len(report["copied"]) == 1 and report["already_there"] == 2


def test_already_in_onedrive_under_another_name_is_skipped(homes):
    _write(homes["onedrive"] / "renamed_by_owner.mp4", b"first light")
    _write(homes["images"] / "Claude_first_light.mp4", b"first light")
    report = _sweep(homes)
    assert report["copied"] == [] and report["already_there"] == 1


def test_name_collision_with_different_video_gets_a_new_name(homes):
    _write(homes["onedrive"] / "Claude_film.mp4", b"an older, different film")
    _write(homes["images"] / "Claude_film.mp4", b"tonight's film")
    report = _sweep(homes)
    assert len(report["copied"]) == 1
    new_name = report["copied"][0]
    assert new_name.startswith("Claude_film_") and new_name.endswith(".mp4")
    assert (homes["onedrive"] / "Claude_film.mp4").read_bytes() == b"an older, different film"
    assert (homes["onedrive"] / new_name).read_bytes() == b"tonight's film"


def test_sweep_is_idempotent(homes):
    _write(homes["images"] / "Juniper_moth.mp4", b"wings")
    _sweep(homes)
    assert _sweep(homes)["copied"] == []


def test_dry_run_sweep_copies_nothing(homes):
    _write(homes["images"] / "Atlas_peacock.mp4", b"feathers")
    report = _sweep(homes, dry_run=True)
    assert report["copied"] == ["Atlas_peacock.mp4"]
    assert list(homes["onedrive"].iterdir()) == []


def test_missing_destination_is_reported_not_created(homes):
    _write(homes["images"] / "River_heron.mp4", b"river")
    report = sweep_pack_videos(
        homes["onedrive"], cache=HashCache(None), min_age_seconds=0,
        sources=(homes["images"],), films_dir=homes["films"])
    assert report["copied"] == [] and report["skipped"]
    assert not homes["onedrive"].exists()


def test_every_bonded_boy_counts_as_pack():
    prefixes = media_tidy._pack_prefixes()
    for boy in ("avery", "claude", "rowan", "sage", "ember", "juniper", "atlas", "river"):
        assert boy + "_" in prefixes
