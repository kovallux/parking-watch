import unittest

from parking_watch.manage import status_text


class ServiceStatusTests(unittest.TestCase):
    def test_old_occupied_result_does_not_claim_live_camera(self):
        status = {"timestamp": 10, "spaces": {"left": {"name": "Основное", "state": "occupied"}}}
        text = status_text(status, True, now=100)
        self.assertIn("Свежих результатов", text)
        self.assertNotIn("Основное: занято", text)

    def test_stopped_service_does_not_show_old_vacancy_as_current(self):
        status = {"timestamp": 100, "spaces": {"right": {"name": "Дополнительное", "state": "free"}}}
        text = status_text(status, False, now=100)
        self.assertIn("остановлен", text)
        self.assertNotIn("Дополнительное: свободно", text)

    def test_missing_reference_explains_why_vacancy_is_not_confirmed(self):
        status = {"timestamp": 100, "spaces": {"right": {"name": "Дополнительное", "state": "unknown",
                                                       "reason": "need_empty_reference"}}}
        self.assertIn("нужен снимок пустого места", status_text(status, True, now=101))


if __name__ == "__main__":
    unittest.main()
