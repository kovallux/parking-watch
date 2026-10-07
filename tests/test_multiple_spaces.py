from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch

from parking_watch.cli import space_configs
from parking_watch.state import Monitor
from parking_watch.telegram_setup import matching_chat
from parking_watch.notify import Telegram, telegram_call, telegram_request
from parking_watch.state import Event
from parking_watch.snapshot_session import next_slot
from parking_watch.vision import MultiVision, Vision


class MultipleSpaceTests(unittest.TestCase):
    def test_space_ids_are_independent_and_unique(self):
        base = {"polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "spaces": [{"id": "left"}, {"id": "right"}]}
        self.assertEqual([s["id"] for s in space_configs(base)], ["left", "right"])
        base["spaces"][1]["id"] = "left"
        with self.assertRaises(ValueError):
            space_configs(base)

    def test_right_vacancy_does_not_change_left_occupancy(self):
        left, right = Monitor(), Monitor()
        for t in range(4):
            left.observe("occupied", t)
            right.observe("occupied", t)
        events = []
        for t in range(4, 16):
            self.assertIsNone(left.observe("occupied", t))
            event = right.observe("free", t)
            if event:
                events.append(event.kind)
        self.assertEqual(events, ["freed"])
        self.assertEqual(left.current, "occupied")

    def test_one_model_pass_classifies_each_space_with_its_own_reference(self):
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:] = 80
        frame[::2, ::2] = 140
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:70, 10:40] = True
        result = SimpleNamespace(boxes=[SimpleNamespace(cls=torch.tensor(0), conf=torch.tensor(.9))],
                                 names={0: "car"}, masks=SimpleNamespace(data=torch.tensor(mask[None])))
        model = Mock()
        model.predict.return_value = [result]
        spaces = [{"id": "left", "polygon": [[.05,.05],[.45,.05],[.45,.8],[.05,.8]]},
                  {"id": "right", "polygon": [[.55,.05],[.95,.05],[.95,.8],[.55,.8]]}]
        multi = MultiVision.__new__(MultiVision)
        multi.spaces = spaces
        multi.visions = []
        for index, config in enumerate(spaces):
            vision = Vision.__new__(Vision)
            vision.config, vision.model = config, model
            vision.references = [frame] if index == 1 else []
            multi.visions.append(vision)
        reports, _ = multi.analyse(frame)
        self.assertEqual(reports["left"]["observation"], "occupied")
        self.assertEqual(reports["right"]["observation"], "free")
        self.assertEqual(model.predict.call_count, 1)


class PairingTests(unittest.TestCase):
    def update(self, ident, text, kind="private"):
        return {"message": {"text": text, "from": {"id": ident}, "chat": {"id": ident, "type": kind}}}

    def test_only_exact_private_challenge_selects_recipient(self):
        updates = [self.update(1, "/start"), self.update(2, "/start nonce", "group"),
                   self.update(3, "/start other"), self.update(4, "/start nonce")]
        self.assertEqual(matching_chat(updates, "nonce"), 4)

    def test_ambiguous_pairing_never_guesses_recipient(self):
        self.assertIsNone(matching_chat([self.update(1, "/start nonce"),
                                        self.update(2, "/start nonce")], "nonce"))

    @patch("parking_watch.notify.threading.Thread")
    def test_simultaneous_space_alerts_do_not_replace_each_other(self, thread):
        left = Telegram(lambda: "free", "dummy-token", 123)
        right = Telegram(lambda: "free", "dummy-token", 123)
        left.submit(Event("freed", "free"), "left freed")
        right.submit(Event("freed", "free"), "right freed")
        self.assertEqual(left.queue.get_nowait()[1], "left freed")
        self.assertEqual(right.queue.get_nowait()[1], "right freed")

    @patch("parking_watch.notify.urlopen", side_effect=ValueError("private-token-in-url"))
    def test_telegram_errors_do_not_include_credentials(self, request):
        with self.assertRaises(ValueError) as failure:
            telegram_call("dummy-token", "getMe")
        self.assertNotIn("private-token", str(failure.exception))

    def test_photo_upload_keeps_jpeg_bytes_and_russian_caption(self):
        photo = b"\xff\xd8camera\xff\xd9"
        request = telegram_request("dummy-token", "sendPhoto", {"chat_id": 123,
                                     "caption": "Основное место: свободно"}, photo)
        self.assertIn(photo, request.data)
        self.assertIn("Основное место: свободно".encode(), request.data)
        self.assertIn('name="photo"', request.data.decode(errors="replace"))
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data"))

    @patch("parking_watch.notify.threading.Thread")
    def test_queued_photo_belongs_to_its_state_change(self, thread):
        sender = Telegram(lambda: "free", "dummy-token", 123)
        sender.submit(Event("freed", "free"), "right freed", b"frame")
        self.assertEqual(sender.queue.get_nowait()[3], b"frame")


class CaptureScheduleTests(unittest.TestCase):
    def test_resume_skips_missed_slots_instead_of_saving_backlog(self):
        slot = datetime(2026, 10, 3, 11, 35, tzinfo=timezone.utc)
        self.assertEqual(next_slot(slot, slot + timedelta(seconds=95), 30),
                         slot + timedelta(seconds=120))


if __name__ == "__main__":
    unittest.main()
