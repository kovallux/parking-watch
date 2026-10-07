from datetime import datetime, timezone
from unittest import TestCase

from parking_watch.presentation import parking_caption, parking_lines, readable_time


SPACES = [{"id": "left", "name": "Место у дерева"}, {"id": "right", "name": "Место у входа"}]


class CaptionTests(TestCase):
    def test_date_is_russian_and_uses_luxembourg_time(self):
        stamp = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc).timestamp()
        self.assertEqual(readable_time(stamp), "3 октября, 16:10")

    def test_both_spaces_have_independent_uppercase_states(self):
        stamp = datetime(2026, 10, 3, 14, 10, tzinfo=timezone.utc).timestamp()
        caption = parking_caption(SPACES, {"left": {"state": "occupied"}, "right": {"state": "free"}}, stamp)
        self.assertEqual(caption, "📷 3 октября, 16:10\n🔴 Место у дерева: ЗАНЯТО\n🟢 Место у входа: СВОБОДНО")

    def test_new_obstruction_does_not_keep_showing_previous_free_state(self):
        lines = parking_lines(SPACES, {"left": {"state": "free", "observation": "occupied"}})
        self.assertNotIn("СВОБОДНО", "\n".join(lines))
        self.assertIn("НЕ ПОДТВЕРЖДЕНО", "\n".join(lines))

    def test_unknown_or_missing_state_is_never_reported_as_vacant(self):
        lines = parking_lines(SPACES, {"left": {"state": "unknown"}, "right": {"state": "offline"}})
        self.assertEqual(lines, ["⚪ Место у дерева: НЕ ПОДТВЕРЖДЕНО", "⚠️ Место у входа: НЕТ СВЯЗИ"])

    def test_vehicle_resemblance_is_qualitative_and_separate_from_parking_status(self):
        items = {"right": {"state": "occupied", "observation": "occupied",
                           "vehicle_identity": {"name": "Reference vehicle", "confidence": "low",
                                                "visual_similarity": .94}}}
        lines = parking_lines(SPACES, items)
        self.assertIn("🔴 Место у входа: ЗАНЯТО", lines)
        self.assertIn("  🚙 Сходство с Reference vehicle: уверенность НИЗКАЯ", lines)
        self.assertNotIn("94", "\n".join(lines))

    def test_free_or_uncertain_place_never_keeps_previous_vehicle_identity(self):
        for state, observation in [("free", "free"), ("unknown", "unknown"), ("occupied", "unknown")]:
            items = {"right": {"state": state, "observation": observation,
                               "vehicle_identity": {"name": "Reference vehicle", "confidence": "high"}}}
            self.assertNotIn("Reference vehicle", "\n".join(parking_lines(SPACES, items)))
