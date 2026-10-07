from types import SimpleNamespace
import unittest

import numpy as np
import torch

from parking_watch.vision import Vision


class VisionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.frame[:] = 80
        self.frame[::2, ::2] = 140
        self.frame[1::2, 1::2] = 140
        self.config = {"polygon": [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]}

    def vision(self, reference=None, label=None, mask=None):
        vision = Vision.__new__(Vision)
        vision.config = self.config
        vision.references = [] if reference is None else [reference]
        boxes = [] if label is None else [SimpleNamespace(cls=torch.tensor(0), conf=torch.tensor(.9))]
        result = SimpleNamespace(boxes=boxes, names={0: label},
                                 masks=None if mask is None else SimpleNamespace(data=torch.tensor(mask[None])))
        vision.model = SimpleNamespace(predict=lambda *a, **kw: [result])
        return vision

    def test_missing_detection_and_no_reference_is_unknown(self):
        report, *_ = self.vision().analyse(self.frame)
        self.assertEqual(report["observation"], "unknown")

    def test_empty_reference_allows_free_observation(self):
        report, *_ = self.vision(self.frame).analyse(self.frame)
        self.assertEqual(report["observation"], "free")

    def test_same_average_brightness_with_different_texture_is_not_free(self):
        roof = self.frame.copy()
        roof[::2, ::2] = 80
        roof[1::2, 1::2] = 80
        roof[::2, 1::2] = 140
        roof[1::2, ::2] = 140
        report, *_ = self.vision(self.frame).analyse(roof)
        self.assertLess(report["empty_difference"], .08)
        self.assertLess(report["empty_structure_similarity"], .4)
        self.assertEqual(report["observation"], "unknown")
        self.assertEqual(report["reason"], "ground_texture_mismatch")

    def test_global_exposure_shift_preserves_empty_ground_texture(self):
        brighter = self.frame + np.uint8(30)
        report, *_ = self.vision(self.frame).analyse(brighter)
        self.assertEqual(report["observation"], "free")
        self.assertAlmostEqual(report["empty_structure_similarity"], 1, places=3)

    def test_pixel_and_structure_matches_must_use_one_reference(self):
        from unittest.mock import patch
        vision = self.vision(self.frame)
        vision.references.append(self.frame.copy())
        with patch.object(Vision, "reference_difference", side_effect=[.01, .15]), \
                patch.object(Vision, "reference_structure_similarity", side_effect=[.1, .95]):
            report, *_ = vision.analyse(self.frame)
        self.assertEqual(report["observation"], "unknown")

    def test_repeated_detector_misses_do_not_emit_vacancy_changes(self):
        from parking_watch.state import Monitor
        from parking_watch.notification_policy import NotificationPolicy
        roof = np.roll(self.frame, 1, axis=0)
        mask = np.zeros(self.frame.shape[:2], dtype=bool)
        mask[25:75, 25:75] = True
        seen = self.vision(self.frame, "car", mask).analyse(roof)[0]["observation"]
        missed = self.vision(self.frame).analyse(roof)[0]["observation"]
        monitor, policy = Monitor(), NotificationPolicy()
        notices = []
        for now in range(1200):
            observation = seen if now % 180 < 30 else missed
            event = monitor.observe(observation, now)
            notice = policy.observe(monitor.current, observation, event, now)
            self.assertNotEqual(monitor.current, "free")
            if notice:
                notices.append(notice)
        self.assertEqual([notice.kind for notice in notices], ["uncertain"])
        self.assertEqual(monitor.last_confirmed, "occupied")

    def test_missed_vehicle_that_changes_ground_image_is_unknown(self):
        occupied = self.frame.copy()
        occupied[25:75, 25:75] = 0
        report, *_ = self.vision(self.frame).analyse(occupied)
        self.assertEqual(report["observation"], "unknown")

    def test_dark_frame_does_not_become_free_even_with_matching_reference(self):
        dark = np.zeros_like(self.frame)
        report, *_ = self.vision(dark).analyse(dark)
        self.assertEqual(report["observation"], "unknown")

    def test_pedestrian_occlusion_is_unknown(self):
        mask = np.zeros((100, 100), dtype=bool)
        mask[25:75, 25:75] = True
        report, *_ = self.vision(self.frame, "person", mask).analyse(self.frame)
        self.assertEqual(report["observation"], "unknown")

    def test_adjacent_vehicle_outside_space_does_not_occupy_it(self):
        mask = np.zeros((100, 100), dtype=bool)
        mask[:, :15] = True
        report, *_ = self.vision(self.frame, "car", mask).analyse(self.frame)
        self.assertEqual(report["observation"], "free")

    def test_vehicle_in_space_takes_precedence_over_matching_reference(self):
        mask = np.zeros((100, 100), dtype=bool)
        mask[25:75, 25:75] = True
        report, *_ = self.vision(self.frame, "car", mask).analyse(self.frame)
        self.assertEqual(report["observation"], "occupied")


if __name__ == "__main__":
    unittest.main()
