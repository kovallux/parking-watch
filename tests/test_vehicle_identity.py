from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from parking_watch.vehicle_identity import VehicleIdentity


class VehicleIdentityTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((120, 180, 3), 60, dtype=np.uint8)
        self.frame[30:80, 50:130] = [130, 150, 170]
        self.frame[45:65, 65:100] = [30, 40, 50]
        self.roi = np.zeros((120, 180), dtype=np.uint8)
        self.roi[20:100, 30:150] = 1
        self.mask = np.zeros_like(self.roi, dtype=bool)
        self.mask[30:80, 50:130] = True

    def matcher(self, similarity):
        matcher = VehicleIdentity.__new__(VehicleIdentity)
        matcher.name, matcher.high, matcher.medium = "Reference vehicle", .98, .95
        matcher.references = [np.array([1., 0.]), np.array([1., 0.])]
        vector = np.array([similarity, np.sqrt(1 - similarity ** 2)])
        matcher.embeddings = Mock(return_value=[vector, vector])
        return matcher

    def test_similarity_levels_do_not_claim_identity_probability(self):
        for score, level in [(.995, "high"), (.96, "medium"), (.93, "low")]:
            with self.subTest(score=score):
                result = self.matcher(score).match(self.frame, self.roi, self.mask)
                self.assertEqual(result["confidence"], level)
                self.assertFalse(result["score_is_identity_probability"])

    def test_missing_vehicle_cannot_be_identified_from_background(self):
        matcher = self.matcher(.999)
        result = matcher.match(self.frame, self.roi, np.zeros_like(self.mask))
        self.assertEqual(result["confidence"], "unknown")
        matcher.embeddings.assert_not_called()

    def test_different_ground_and_lights_do_not_change_vehicle_crop(self):
        changed = self.frame.copy()
        changed[~self.mask] = [250, 10, 100]
        before = changed.copy()
        first = VehicleIdentity.vehicle_crop(self.frame, self.roi, self.mask)
        second = VehicleIdentity.vehicle_crop(changed, self.roi, self.mask)
        self.assertTrue(np.array_equal(first, second))
        self.assertTrue(np.array_equal(changed, before))

    def test_vehicle_outside_this_space_is_not_used_for_identity(self):
        roi = np.zeros_like(self.roi)
        roi[:, :20] = 1
        self.assertIsNone(VehicleIdentity.vehicle_crop(self.frame, roi, self.mask))

    def test_neighbouring_vehicle_does_not_enter_selected_crop(self):
        other = self.mask.copy()
        other[30:80, 150:175] = True
        original = VehicleIdentity.vehicle_crop(self.frame, self.roi, self.mask)
        with_neighbour = VehicleIdentity.vehicle_crop(self.frame, self.roi, other)
        self.assertTrue(np.array_equal(original, with_neighbour))

    def test_unconfirmed_reference_is_rejected_before_loading_model(self):
        with self.assertRaises(ValueError):
            VehicleIdentity({"verified_by_user": False}, SimpleNamespace())

    def test_embeddings_include_reverse_parking_orientation(self):
        matcher = VehicleIdentity.__new__(VehicleIdentity)
        matcher.device = "cpu"
        first = Mock(); first.cpu.return_value.numpy.return_value = np.array([1., 0.])
        second = Mock(); second.cpu.return_value.numpy.return_value = np.array([0., 1.])
        matcher.model = Mock()
        matcher.model.embed.return_value = [first, second]
        crop = self.frame[30:80, 50:130]
        matcher.embeddings(crop)
        images = matcher.model.embed.call_args.args[0]
        self.assertTrue(np.array_equal(images[0], crop))
        self.assertTrue(np.array_equal(images[1], crop[::-1, ::-1]))
