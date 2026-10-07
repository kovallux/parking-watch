import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from parking_watch.reference_candidates import ReferenceCandidates
from parking_watch.state import Monitor


class CandidateCaptureTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory()
        os.chdir(self.temporary.name)
        self.config = {"empty_candidate_capture": {"enabled": True, "space_ids": ["left"]}}
        self.spaces = [{"id": "left", "empty_references": []},
                       {"id": "right", "empty_references": ["verified-right.jpg"]}]
        self.frame = np.full((40, 60, 3), 128, dtype=np.uint8)
        self.report = {"observation": "unknown", "reason": "need_empty_reference",
                       "vehicle_coverage": 0, "obstruction_coverage": 0}
        self.capture = ReferenceCandidates(self.config, self.spaces)

    def tearDown(self):
        os.chdir(self.previous)
        self.temporary.cleanup()

    def records(self):
        path = Path("data/empty-candidates/left/index.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_detector_miss_saves_review_material_without_declaring_vacancy(self):
        self.capture.consider(self.frame, {"left": self.report}, 100, .2)
        record = self.records()[0]
        self.assertFalse(record["verified_empty"])
        self.assertEqual(self.spaces[0]["empty_references"], [])
        image = cv2.imread(str(Path("data/empty-candidates/left") / record["file"]))
        np.testing.assert_array_equal(image, self.frame)
        monitor = Monitor()
        events = [monitor.observe(record["report"]["observation"], t) for t in range(30)]
        self.assertFalse(any(event and event.state == "free" for event in events))

    def test_pair_is_separated_by_30_seconds_even_after_restart(self):
        self.capture.consider(self.frame, {"left": self.report}, 100, .2)
        restarted = ReferenceCandidates(self.config, self.spaces)
        restarted.consider(self.frame, {"left": self.report}, 129.99, .2)
        self.assertEqual(len(self.records()), 1)
        restarted.consider(self.frame, {"left": self.report}, 130, .2)
        self.assertEqual([item["captured_at_unix"] for item in self.records()], [100, 130])

    def test_occupied_obstructed_dark_and_frozen_frames_are_rejected(self):
        for reason in ["vehicle_in_space", "partial_obstruction", "poor_image", "identical_frames"]:
            report = {**self.report, "reason": reason}
            self.capture.consider(self.frame, {"left": report}, 100, .2)
        self.assertEqual(self.records(), [])

    def test_old_frame_and_unrequested_space_are_rejected(self):
        self.capture.consider(self.frame, {"left": self.report}, 100, 3)
        self.capture.consider(self.frame, {"right": self.report}, 130, .2)
        self.assertEqual(self.records(), [])
        self.assertFalse(Path("data/empty-candidates/right").exists())

    def test_capture_stops_when_verified_reference_is_installed(self):
        self.spaces[0]["empty_references"] = ["verified-left.jpg"]
        capture = ReferenceCandidates(self.config, self.spaces)
        capture.consider(self.frame, {"left": self.report}, 100, .2)
        self.assertEqual(capture.status()["enabled_spaces"], [])
        self.assertEqual(self.records(), [])

    def test_disk_error_does_not_stop_monitor_or_change_parking_status(self):
        with patch.object(Path, "write_bytes", side_effect=OSError("Disk unavailable")):
            self.capture.consider(self.frame, {"left": self.report}, 100, .2)
        self.assertEqual(self.capture.status()["error"], "candidate_storage_failed")
        self.assertEqual(self.records(), [])
        self.assertEqual(self.spaces[0]["empty_references"], [])

    def test_unreadable_candidate_index_does_not_prevent_monitor_startup(self):
        self.capture.consider(self.frame, {"left": self.report}, 100, .2)
        with patch.object(Path, "read_text", side_effect=OSError("Disk unavailable")):
            restarted = ReferenceCandidates(self.config, self.spaces)
        self.assertEqual(restarted.status()["error"], "candidate_index_unavailable")

    def test_storage_limit_keeps_existing_unreviewed_frame_and_pauses_new_capture(self):
        self.capture.consider(self.frame, {"left": self.report}, 100, .2)
        first = self.records()[0]
        self.config["data_retention"] = {"enabled": True, "candidate_max_mib": .0001}
        limited = ReferenceCandidates(self.config, self.spaces)
        limited.consider(self.frame, {"left": self.report}, 130, .2)
        self.assertEqual(limited.status()["error"], "candidate_storage_limit_reached")
        self.assertEqual(self.records(), [first])
        self.assertTrue((Path("data/empty-candidates/left") / first["file"]).exists())
        self.assertEqual(self.spaces[0]["empty_references"], [])


if __name__ == "__main__":
    unittest.main()
