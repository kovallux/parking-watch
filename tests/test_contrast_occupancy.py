from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from parking_watch.vision import MultiVision, Vision


def detection(label=None, bounds=None, shape=(90, 90)):
    if label is None:
        return SimpleNamespace(boxes=[], names={}, masks=None)
    mask = np.zeros(shape, dtype=np.float32)
    top, bottom, left, right = bounds
    mask[top:bottom, left:right] = 1
    box = SimpleNamespace(cls=SimpleNamespace(item=lambda: 0),
                          conf=SimpleNamespace(item=lambda: .8))
    return SimpleNamespace(boxes=[box], names={0: label}, masks=SimpleNamespace(data=[mask]))


class ContrastOccupancyTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((100, 100, 3), 80, dtype=np.uint8)
        self.frame[::2, ::2] = 140
        self.spaces = [
            {"id": "left", "polygon": [[.05, .05], [.45, .05], [.45, .8], [.05, .8]],
             "contrast_occupancy_fallback": True},
            {"id": "right", "polygon": [[.55, .05], [.95, .05], [.95, .8], [.55, .8]],
             "contrast_occupancy_fallback": True},
        ]

    def analyse(self, extra, references=False, frame=None, raw=None, isolated=None):
        frame = self.frame if frame is None else frame
        multi = MultiVision.__new__(MultiVision)
        multi.spaces = self.spaces
        multi.occupancy_fallback = None
        model = Mock()
        model.predict.side_effect = ([[detection()], [raw or detection()], [extra]]
                                     + [[r] for r in (isolated or [detection(), detection()])])
        multi.visions = []
        for config in self.spaces:
            vision = Vision.__new__(Vision)
            vision.config, vision.model = config, model
            vision.references = [frame.copy()] if references else []
            multi.visions.append(vision)
        crop = (frame[5:95, 5:95].copy(), 5, 5)
        with patch.object(MultiVision, "parking_crop", return_value=crop), \
                patch.object(MultiVision, "contrast_crop", return_value=crop):
            reports, _ = multi.analyse(frame)
        return reports, model

    def test_original_crop_recovers_vehicle_when_contrast_misses_it(self):
        reports, _ = self.analyse(detection(), raw=detection("car", (5, 65, 5, 35)))
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["left"]["detector_view"], "parking_crop")
        self.assertEqual(reports["right"]["observation"], "unknown")

    def test_contrast_pass_is_skipped_when_crop_finds_both_vehicles(self):
        left = detection("car", (5, 65, 5, 35))
        right = detection("car", (5, 65, 55, 85))
        left.boxes += right.boxes
        left.masks.data += right.masks.data
        reports, model = self.analyse(detection(), raw=left)
        self.assertEqual([r["observation"] for r in reports.values()], ["occupied", "occupied"])
        self.assertEqual(model.predict.call_count, 2)

    def test_crop_pedestrian_veto_survives_later_contrast_miss(self):
        reports, _ = self.analyse(detection(), references=True,
                                 raw=detection("person", (5, 65, 55, 75)))
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(reports["right"]["reason"], "partial_obstruction")
        self.assertEqual(reports["right"]["detector_view"], "parking_crop")

    def test_isolated_space_recovers_vehicle_missed_in_shared_views(self):
        reports, model = self.analyse(detection(),
                                     isolated=[detection("car", (5, 65, 5, 35)), detection()])
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["left"]["detector_view"], "space_contrast_crop")
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(model.predict.call_count, 5)
        self.assertEqual(model.predict.call_args.kwargs["imgsz"], 640)

    def test_isolated_obstruction_blocks_original_empty_match(self):
        reports, _ = self.analyse(detection(), references=True,
                                 isolated=[detection(), detection("person", (5, 65, 55, 75))])
        self.assertEqual(reports["left"]["observation"], "free")
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(reports["right"]["reason"], "partial_obstruction")

    def test_isolated_detection_outside_space_does_not_confirm_occupancy(self):
        reports, _ = self.analyse(detection(), references=True,
                                 isolated=[detection("car", (5, 65, 55, 85)), detection()])
        self.assertEqual([r["observation"] for r in reports.values()], ["free", "free"])

    def test_larger_fallback_recovers_only_occupied_space_at_same_confidence(self):
        for space in self.spaces:
            space.update(occupancy_fallback_model="fallback.pt", confidence=.35)
        model = Mock()
        model.predict.side_effect = [[detection("car", (5, 65, 5, 35))], [detection()]]
        with patch("parking_watch.vision.YOLO", return_value=model) as load:
            reports, _ = self.analyse(detection())
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(reports["left"]["detector_view"], "space_fallback_model")
        load.assert_called_once_with("fallback.pt")
        self.assertEqual(model.predict.call_args.kwargs["conf"], .35)

    def test_missing_fallback_does_not_leave_a_free_claim(self):
        for space in self.spaces:
            space["occupancy_fallback_model"] = "missing.pt"
        with patch("parking_watch.vision.YOLO", side_effect=ValueError("missing model")):
            reports, _ = self.analyse(detection(), references=True)
        self.assertEqual([r["observation"] for r in reports.values()], ["unknown", "unknown"])
        self.assertEqual(reports["right"]["reason"], "occupancy_fallback_unavailable")

    def test_poor_image_never_loads_optional_detector(self):
        for space in self.spaces:
            space["occupancy_fallback_model"] = "fallback.pt"
        with patch("parking_watch.vision.YOLO") as load:
            reports, _ = self.analyse(detection(), frame=np.zeros_like(self.frame))
        load.assert_not_called()
        self.assertEqual([r["reason"] for r in reports.values()], ["poor_image", "poor_image"])

    def test_vehicle_found_in_contrast_crop_recovers_only_its_own_space(self):
        reports, _ = self.analyse(detection("car", (5, 65, 5, 35)))
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(reports["left"]["detector_view"], "contrast_crop")

    def test_processed_detector_miss_without_reference_never_confirms_free(self):
        reports, _ = self.analyse(detection())
        self.assertEqual([r["observation"] for r in reports.values()], ["unknown", "unknown"])

    def test_matching_reference_cannot_override_extra_vehicle_evidence(self):
        reports, _ = self.analyse(detection("car", (5, 65, 5, 35)), references=True)
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["right"]["observation"], "free")

    def test_pedestrian_in_extra_view_blocks_original_free_decision(self):
        reports, _ = self.analyse(detection("person", (5, 65, 55, 75)), references=True)
        self.assertEqual(reports["left"]["observation"], "free")
        self.assertEqual(reports["right"]["observation"], "unknown")
        self.assertEqual(reports["right"]["reason"], "partial_obstruction")

    def test_dark_original_stays_unknown_without_contrast_prediction(self):
        reports, model = self.analyse(detection("car", (5, 65, 5, 35)), references=True,
                                     frame=np.zeros_like(self.frame))
        self.assertEqual([r["observation"] for r in reports.values()], ["unknown", "unknown"])
        self.assertEqual(model.predict.call_count, 1)

    def test_reference_difference_uses_original_pixels_and_input_is_unchanged(self):
        before = self.frame.copy()
        reports, _ = self.analyse(detection(), references=True)
        self.assertTrue(np.array_equal(before, self.frame))
        self.assertEqual([r["empty_difference"] for r in reports.values()], [0.0, 0.0])

    def test_contrast_processing_keeps_entire_parking_area_and_original_pixels(self):
        roi = np.zeros((100, 100), dtype=np.uint8)
        roi[25:75, 20:80] = 1
        before = self.frame.copy()
        crop, x, y = MultiVision.contrast_crop(self.frame, [roi])
        self.assertLessEqual(x, 20)
        self.assertLessEqual(y, 25)
        self.assertGreaterEqual(x + crop.shape[1], 80)
        self.assertGreaterEqual(y + crop.shape[0], 75)
        self.assertTrue(np.array_equal(self.frame, before))

    def test_original_crop_keeps_camera_pixels_and_excludes_surroundings(self):
        roi = np.zeros((100, 100), dtype=np.uint8)
        roi[25:75, 20:80] = 1
        crop, x, y = MultiVision.parking_crop(self.frame, [roi])
        self.assertTrue(np.array_equal(crop, self.frame[y:y+crop.shape[0], x:x+crop.shape[1]]))
        self.assertGreater(x, 0)
        self.assertGreater(y, 0)
        self.assertLess(crop.shape[0], self.frame.shape[0])
        crop[:] = 0
        self.assertGreater(self.frame.sum(), 0)


if __name__ == "__main__":
    unittest.main()
