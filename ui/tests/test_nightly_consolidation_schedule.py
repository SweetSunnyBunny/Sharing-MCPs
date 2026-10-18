"""A public starter must not import another installation's nightly timetable."""
from services.autowake import _NIGHTLY_CONSOLIDATION_SCHEDULE

def test_nightly_schedule_requires_installation_configuration():
    assert _NIGHTLY_CONSOLIDATION_SCHEDULE == []
