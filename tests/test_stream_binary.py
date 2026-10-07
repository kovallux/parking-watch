from unittest import TestCase
from unittest.mock import patch

from parking_watch.stream import Stream, stream_needs_restart


class StreamBinaryTests(TestCase):
    def test_prefers_bundled_binary_even_when_system_ffmpeg_exists(self):
        with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/local/bundled-ffmpeg"), \
             patch("parking_watch.stream.shutil.which", return_value="/system/ffmpeg") as which, \
             patch("parking_watch.stream.subprocess.Popen") as popen, \
             patch("parking_watch.stream.threading.Thread"):
            Stream("rtsp://example.invalid/camera")
        self.assertEqual(popen.call_args.args[0][0], "/local/bundled-ffmpeg")
        which.assert_not_called()

    def test_system_binary_remains_a_fallback_when_bundle_unavailable(self):
        with patch("imageio_ffmpeg.get_ffmpeg_exe", side_effect=RuntimeError), \
             patch("parking_watch.stream.shutil.which", return_value="/system/ffmpeg"), \
             patch("parking_watch.stream.subprocess.Popen") as popen, \
             patch("parking_watch.stream.threading.Thread"):
            Stream("rtsp://example.invalid/camera")
        self.assertEqual(popen.call_args.args[0][0], "/system/ffmpeg")

    def test_slow_sampling_uses_real_frames_without_duplicate_output(self):
        with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/local/ffmpeg"), \
             patch("parking_watch.stream.subprocess.Popen") as popen, \
             patch("parking_watch.stream.threading.Thread"):
            Stream("rtsp://example.invalid/camera", frame_interval=2)
        args = popen.call_args.args[0]
        self.assertIn("gte(t-prev_selected_t,2)", args[args.index("-vf") + 1])
        self.assertEqual(args[args.index("-fps_mode") + 1], "passthrough")

    def test_frozen_pixels_force_reconnect_even_with_new_arrival_times(self):
        self.assertTrue(stream_needs_restart((100, None), 100, 0, 39, False))
        self.assertFalse(stream_needs_restart((100, None), 100, 0, 99, False))

    def test_two_second_sampling_does_not_force_a_reconnect(self):
        self.assertFalse(stream_needs_restart((98, None), 100, 0, 98, False))
        self.assertTrue(stream_needs_restart((94, None), 100, 0, 94, False))

    def test_occupancy_and_vacancy_confirm_at_two_second_intervals(self):
        from parking_watch.state import Monitor
        monitor = Monitor()
        for t in [0, 2, 4]:
            monitor.observe("occupied", t)
        self.assertEqual(monitor.current, "occupied")
        events = []
        for t in [6, 8, 10, 12, 14, 16]:
            event = monitor.observe("free", t)
            if event:
                events.append(event.kind)
        self.assertEqual(events, ["freed"])
        self.assertEqual(monitor.current, "free")

    def test_analysis_cadence_uses_monotonic_time_and_latest_frame(self):
        from parking_watch.cli import analysis_due
        self.assertFalse(analysis_due((1.5, None), 1.5, 0, 0, 2))
        self.assertTrue(analysis_due((1.9, None), 2, 0, 0, 2))
        self.assertFalse(analysis_due((1.9, None), 4, 1.9, 2, 2))
        self.assertFalse(analysis_due((1, None), 5, 0, 0, 2))
        self.assertFalse(analysis_due(None, 5, 0, 0, 2))
