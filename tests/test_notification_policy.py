import unittest

from parking_watch.notification_policy import NotificationPolicy
from parking_watch.state import Monitor


class NotificationPolicyTests(unittest.TestCase):
    def ready(self):
        monitor, policy = Monitor(), NotificationPolicy()
        for now in range(4):
            event = monitor.observe("occupied", now)
            self.assertIsNone(policy.observe(monitor.current, "occupied", event, now))
        return monitor, policy

    def advance(self, monitor, policy, observation, now):
        event = monitor.observe(observation, now)
        return event, policy.observe(monitor.current, observation, event, now)

    def test_repeated_short_lighting_misses_and_recoveries_are_silent(self):
        monitor, policy = self.ready()
        raw, sent = [], []
        for now in range(4, 254):
            observation = "unknown" if (now - 4) % 25 < 20 else "occupied"
            event, notification = self.advance(monitor, policy, observation, now)
            if event:
                raw.append(event.kind)
            if notification:
                sent.append(notification.kind)
        self.assertGreater(raw.count("unavailable"), 5)
        self.assertGreater(raw.count("restored"), 5)
        self.assertEqual(sent, [])
        self.assertEqual(monitor.last_confirmed, "occupied")

    def test_five_minutes_of_instability_warns_once_despite_brief_recoveries(self):
        monitor, policy = self.ready()
        notices = []
        for now in range(4, 1000):
            observation = "unknown" if (now - 4) % 25 < 20 else "occupied"
            _, notification = self.advance(monitor, policy, observation, now)
            if notification:
                notices.append((now, notification.kind))
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][1], "uncertain")
        self.assertGreaterEqual(notices[0][0], 304)

    def test_confirmed_departure_after_uncertainty_is_not_delayed(self):
        monitor, policy = self.ready()
        for now in range(4, 26):
            _, notice = self.advance(monitor, policy, "unknown", now)
            self.assertIsNone(notice)
        notices = []
        for now in range(26, 42):
            _, notice = self.advance(monitor, policy, "free", now)
            if notice:
                notices.append((now, notice))
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], 36)
        self.assertEqual(notices[0][1].state, "free")

    def test_departure_and_reoccupation_are_never_affected_by_diagnostic_cooldown(self):
        monitor, policy = self.ready()
        for now in range(4, 309):
            self.advance(monitor, policy, "unknown", now)
        self.assertTrue(policy.uncertain_warning_latched)
        notices = []
        for now in range(309, 321):
            _, notice = self.advance(monitor, policy, "free", now)
            if notice:
                notices.append(notice)
        for now in range(321, 326):
            _, notice = self.advance(monitor, policy, "occupied", now)
            if notice:
                notices.append(notice)
        self.assertEqual([notice.state for notice in notices], ["free", "occupied"])

    def test_actual_camera_loss_still_warns_after_fifteen_seconds(self):
        monitor, policy = self.ready()
        notices = []
        for now in range(4, 25):
            _, notice = self.advance(monitor, policy, "offline", now)
            if notice:
                notices.append((now, notice))
        self.assertEqual([(now, notice.kind, notice.state) for now, notice in notices],
                         [(19, "unavailable", "offline")])
        recovered = []
        for now in range(25, 30):
            _, notice = self.advance(monitor, policy, "occupied", now)
            if notice:
                recovered.append(notice.kind)
        self.assertEqual(recovered, ["restored"])

    def test_camera_loss_is_not_hidden_by_an_existing_recognition_warning(self):
        monitor, policy = self.ready()
        for now in range(4, 309):
            self.advance(monitor, policy, "unknown", now)
        notices = []
        for now in range(309, 330):
            _, notice = self.advance(monitor, policy, "offline", now)
            if notice:
                notices.append(notice)
        self.assertEqual([(notice.kind, notice.state) for notice in notices],
                         [("unavailable", "offline")])

    def test_unknown_status_never_becomes_a_vacancy(self):
        monitor, policy = self.ready()
        for now in range(4, 400):
            _, notice = self.advance(monitor, policy, "unknown", now)
            self.assertNotEqual(monitor.current, "free")
            if notice:
                self.assertEqual(notice.state, "unknown")

    def test_healthy_period_and_half_hour_limit_control_repeated_diagnostics(self):
        monitor, policy = self.ready()
        notices = []
        for now in range(4, 2400):
            observation = "occupied" if 320 <= now < 390 else "unknown"
            _, notice = self.advance(monitor, policy, observation, now)
            if notice:
                notices.append((now, notice.kind))
        self.assertEqual(notices, [(304, "uncertain"), (2104, "uncertain")])

    def test_other_space_changes_are_independent_of_lighting_instability(self):
        left, left_policy = self.ready()
        right, right_policy = self.ready()
        left_notices, right_notices = [], []
        for now in range(4, 20):
            _, left_notice = self.advance(left, left_policy, "unknown", now)
            _, right_notice = self.advance(right, right_policy, "free", now)
            if left_notice:
                left_notices.append(left_notice)
            if right_notice:
                right_notices.append(right_notice.state)
        self.assertEqual(left_notices, [])
        self.assertEqual(right_notices, ["free"])
