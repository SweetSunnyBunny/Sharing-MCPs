import unittest
from unittest.mock import patch

from services.process_health import collect_process_health
from services.scheduler_health import get_scheduler_health, scheduler_heartbeat


class OperationalHealthTests(unittest.TestCase):
    def test_process_snapshot_has_low_cost_core_metrics(self):
        snapshot = collect_process_health()
        self.assertGreater(snapshot["pid"], 0)
        self.assertGreaterEqual(snapshot["threads"], 1)
        self.assertEqual(len(snapshot["gc_counts"]), 3)

    def test_scheduler_heartbeat_job_is_side_effect_free(self):
        self.assertIsNone(scheduler_heartbeat())
        self.assertIn("last_success_at", get_scheduler_health())


if __name__ == "__main__":
    unittest.main()
