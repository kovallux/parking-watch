from unittest import TestCase
from unittest.mock import patch
import tempfile
from pathlib import Path

from parking_watch.bot import check_caption, handle_update, is_check_command, send_check


CONFIG = {"spaces": [{"id": "left", "name": "Основное"}, {"id": "right", "name": "Дополнительное"}]}


def message(chat=123, sender=123, kind="private", text="/check", date=100):
    return {"chat": {"id": chat, "type": kind}, "from": {"id": sender},
            "text": text, "date": date, "message_id": 7}


class CommandTests(TestCase):
    def test_only_owners_private_fresh_check_is_accepted(self):
        for text in ["/check", "check", "/check@parking_bot"]:
            self.assertTrue(is_check_command(message(text=text), 123, "parking_bot", now=101))
        for item in [message(chat=9), message(sender=9), message(kind="group"),
                     message(text="/check@other_bot"), message(date=1), message(text="/check restart")]:
            self.assertFalse(is_check_command(item, 123, "parking_bot", now=200))

    @patch("parking_watch.bot.send_check")
    def test_other_users_commands_cannot_trigger_camera_capture(self, send):
        self.assertIsNone(handle_update({"message": message(chat=9)}, "token", 123, "parking_bot", now=101))
        send.assert_not_called()

    def test_stopped_or_stale_monitor_does_not_report_old_vacancy(self):
        status = {"timestamp": 100, "spaces": {"right": {"state": "free"}}}
        for running, now in [(False, 101), (True, 200)]:
            text = check_caption(CONFIG, status, running, photo_time=now, now=now)
            self.assertNotIn("🟢", text)
            self.assertIn("НЕ ПОДТВЕРЖДЕНО", text)

    def test_spaces_are_reported_independently(self):
        status = {"timestamp": 100, "spaces": {"left": {"state": "occupied"}, "right": {"state": "free"}}}
        text = check_caption(CONFIG, status, True, 101, now=101)
        self.assertIn("🔴 Основное: ЗАНЯТО", text)
        self.assertIn("🟢 Дополнительное: СВОБОДНО", text)

    @patch("parking_watch.bot.telegram_call", return_value={"message_id": 10})
    @patch("parking_watch.bot.monitor_pid", return_value=None)
    @patch("parking_watch.bot.read_status", return_value={})
    @patch("parking_watch.bot.fresh_photo", side_effect=ValueError("secret-url"))
    @patch("parking_watch.bot.config_load", return_value=CONFIG)
    def test_camera_failure_returns_status_without_secret_or_old_photo(self, config, photo, status, pid, call):
        receipt = send_check("token", 123, request_id=7)
        self.assertFalse(receipt["photo_sent"])
        self.assertEqual(call.call_args.args[1], "sendMessage")
        self.assertIn("остановлен", call.call_args.args[2]["text"])
        self.assertNotIn("secret-url", call.call_args.args[2]["text"])

    @patch("parking_watch.bot.telegram_call", return_value={"message_id": 10})
    @patch("parking_watch.bot.monitor_pid", return_value=42)
    @patch("parking_watch.bot.read_status", return_value={})
    @patch("parking_watch.bot.fresh_photo", return_value=(b"actual-jpeg", 100))
    @patch("parking_watch.bot.config_load", return_value=CONFIG)
    def test_check_uploads_the_new_photo_and_replies_to_requested_message(self, config, photo, status, pid, call):
        with tempfile.TemporaryDirectory() as folder, patch("parking_watch.media.ROOT", Path(folder)):
            receipt = send_check("token", 123, request_id=7)
            self.assertEqual((Path(folder) / receipt["photo_file"]).read_bytes(), b"actual-jpeg")
        self.assertTrue(receipt["photo_sent"])
        self.assertEqual(call.call_args.args[1], "sendPhoto")
        self.assertEqual(call.call_args.kwargs["photo"], b"actual-jpeg")
        self.assertIn('"message_id": 7', call.call_args.args[2]["reply_parameters"])
