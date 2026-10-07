import json
from pathlib import Path
import unittest
from unittest.mock import patch

from parking_watch.cli import config_load
from parking_watch.state import Monitor


class MonitorTests(unittest.TestCase):
    def occupied(self):
        monitor = Monitor()
        for t in [0, 1, 2, 3]:
            event = monitor.observe("occupied", t)
        self.assertEqual(event.kind, "initial")
        return monitor

    def test_departure_notifies_once_after_continuous_confirmation(self):
        monitor = self.occupied()
        events = [monitor.observe("free", t) for t in range(4, 20)]
        self.assertEqual([e.kind for e in events if e], ["freed"])
        self.assertEqual(monitor.current, "free")

    def test_brief_detector_miss_does_not_report_vacancy(self):
        monitor = self.occupied()
        for t in [4, 5, 6, 7]:
            self.assertIsNone(monitor.observe("free", t))
        for t in [8, 9, 10, 11]:
            self.assertIsNone(monitor.observe("occupied", t))
        self.assertEqual(monitor.current, "occupied")

    def test_camera_loss_reports_unknown_and_never_free(self):
        monitor = self.occupied()
        events = [monitor.observe("offline", t) for t in range(4, 25)]
        self.assertEqual([e.kind for e in events if e], ["unavailable"])
        self.assertEqual(monitor.current, "offline")
        self.assertEqual(monitor.last_confirmed, "occupied")

    def test_reconnection_rechecks_state_and_reports_current_not_departure_time(self):
        monitor = self.occupied()
        for t in range(4, 22):
            monitor.observe("offline", t)
        self.assertIsNone(monitor.observe("free", 22))
        self.assertEqual(monitor.current, "unknown")
        events = [monitor.observe("free", t) for t in range(23, 35)]
        self.assertEqual([e.kind for e in events if e], ["restored"])
        self.assertEqual(monitor.current, "free")

    def test_time_gap_does_not_count_as_continuous_evidence(self):
        monitor = self.occupied()
        for t in [4, 5, 6]:
            monitor.observe("free", t)
        self.assertEqual(monitor.observe("free", 60).kind, "unavailable")
        for t in range(61, 70):
            self.assertIsNone(monitor.observe("free", t))
        self.assertEqual(monitor.observe("free", 70).kind, "restored")

    def test_unknown_breaks_confirmation_window(self):
        monitor = self.occupied()
        for t in range(4, 13):
            self.assertIsNone(monitor.observe("free", t))
        monitor.observe("unknown", 13)
        for t in range(14, 24):
            event = monitor.observe("free", t)
            self.assertFalse(event and event.state == "free")
        self.assertEqual(monitor.observe("free", 24).state, "free")

    def test_unstable_view_eventually_reports_unavailable(self):
        monitor = Monitor()
        events = [monitor.observe("occupied" if t % 2 else "unknown", t) for t in range(30)]
        self.assertEqual([e.kind for e in events if e], ["unavailable"])

    def test_startup_free_is_initial_status_not_departure(self):
        monitor = Monitor()
        events = [monitor.observe("free", t) for t in range(15)]
        self.assertEqual([e.kind for e in events if e], ["initial"])

    def test_reoccupation_is_not_suppressed_by_cooldown(self):
        monitor = self.occupied()
        for t in range(4, 15):
            monitor.observe("free", t)
        events = [monitor.observe("occupied", t) for t in range(15, 21)]
        self.assertEqual([e.kind for e in events if e], ["occupied"])


class ConfigTests(unittest.TestCase):
    def test_rejects_crossed_parking_corners(self):
        data = {"polygon": [[0, 0], [1, 1], [0, 1], [1, 0]]}
        with patch.object(Path, "read_text", return_value=json.dumps(data)):
            with self.assertRaises(ValueError):
                config_load("test.json")

    def test_rejects_nan_and_reversed_thresholds(self):
        for data in [
            {"polygon": [[0, 0], [1, 0], [1, 1], [0, float("nan")]]},
            {"polygon": [[0, 0], [1, 0], [1, 1], [0, 1]], "free_coverage": 0.8, "occupied_coverage": 0.2}
        ]:
            with patch.object(Path, "read_text", return_value=json.dumps(data)):
                with self.assertRaises(ValueError):
                    config_load("test.json")


if __name__ == "__main__":
    unittest.main()
