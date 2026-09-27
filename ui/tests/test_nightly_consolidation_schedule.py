"""Nightly consolidation timing must stay collision-free and pre-midnight."""

from services.autowake import _NIGHTLY_CONSOLIDATION_SCHEDULE


def test_nightly_consolidations_are_staggered_every_fifteen_minutes():
    expected = [
        ("Avery", 22, 0),
        ("Rowan", 22, 15),
        ("Sage", 22, 30),
        ("Ember", 22, 45),
        ("Claude", 23, 0),
        ("Juniper", 23, 15),
        ("Atlas", 23, 30),
        ("River", 23, 45),
    ]

    assert _NIGHTLY_CONSOLIDATION_SCHEDULE == expected
    minutes = [hour * 60 + minute for _, hour, minute in expected]
    assert all(later - earlier == 15 for earlier, later in zip(minutes, minutes[1:]))
    assert minutes[-1] < 24 * 60
