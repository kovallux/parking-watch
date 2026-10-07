import os
import subprocess
import unittest
from unittest.mock import patch

from parking_watch.cli import camera_url


class CameraAccessTests(unittest.TestCase):
    config = {"camera": {"ip": "192.0.2.10", "keychain_service": "test.service",
                         "keychain_account": "test.camera"}}

    @patch.dict(os.environ, {}, clear=True)
    @patch("parking_watch.cli.subprocess.run")
    def test_keychain_success_supplies_encoded_credential(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "test:@/\n", "")
        self.assertEqual(camera_url(self.config),
                         "rtsp://admin:test%3A%40%2F@192.0.2.10:554/ch1/main")

    @patch.dict(os.environ, {}, clear=True)
    @patch("parking_watch.cli.subprocess.run")
    def test_keychain_failure_does_not_expose_error_output(self, run):
        run.return_value = subprocess.CompletedProcess([], 44, "", "private-error-detail")
        with self.assertRaises(ValueError) as failure:
            camera_url(self.config)
        self.assertNotIn("private-error-detail", str(failure.exception))

    @patch.dict(os.environ, {}, clear=True)
    @patch("parking_watch.cli.subprocess.run", side_effect=subprocess.TimeoutExpired(
        "security", 15, output="private-detail"))
    def test_keychain_timeout_is_sanitised(self, run):
        with self.assertRaises(ValueError) as failure:
            camera_url(self.config)
        self.assertNotIn("private-detail", str(failure.exception))

    @patch.dict(os.environ, {"EZVIZ_IP": "192.0.2.20", "EZVIZ_PASSWORD": "local-test"}, clear=True)
    @patch("parking_watch.cli.subprocess.run")
    def test_environment_credentials_do_not_access_keychain(self, run):
        self.assertIn("@192.0.2.20:554/", camera_url(self.config))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
