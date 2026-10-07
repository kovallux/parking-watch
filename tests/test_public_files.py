import importlib.util
from pathlib import Path
from unittest import TestCase


spec = importlib.util.spec_from_file_location(
    "public_files", Path(__file__).resolve().parents[1] / "scripts/check_public_files.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PublicFilesTests(TestCase):
    def test_local_files_are_excluded_but_template_and_source_are_allowed(self):
        for name in ["data/status.json", "research/notes.md", "config.local.json", ".env.production",
                     ".venv/bin/python", "model.pt", "camera.jpg", "private.pem"]:
            self.assertTrue(checker.private_path(name), name)
        for name in ["config.example.json", ".env.example", "parking_watch/vision.py", "README.md"]:
            self.assertFalse(checker.private_path(name), name)

    def test_token_is_detected_without_returning_its_value(self):
        secret = "12345678:" + "A" * 35
        findings = checker.scan_text("README.md", "token=" + secret)
        self.assertEqual(findings, [("README.md", 1, "telegram-token")])
        self.assertNotIn(secret, repr(findings))

    def test_only_the_approved_documentation_image_is_allowed(self):
        self.assertFalse(checker.private_path("docs/images/telegram-check.png"))
        self.assertTrue(checker.private_path("docs/images/another-camera.png"))
        self.assertTrue(checker.private_path("data/telegram-check.png"))

    def test_credential_url_exception_applies_only_to_exact_test_fixture(self):
        sample = "rtsp://" + "admin:test%3A%40%2F@192.0.2.10:554/ch1/main"
        self.assertFalse(checker.scan_text("tests/test_camera_access.py", sample))
        self.assertTrue(checker.scan_text("README.md", sample))
        self.assertTrue(checker.scan_text("tests/test_camera_access.py",
                                          sample.replace("192.0.2.10", "192.0.2.20")))

    def test_local_paths_and_network_addresses_are_reported(self):
        text = "/Users/" + "example/project\n/Volumes/" + "Example/project\n192.168." + "1.10"
        self.assertEqual(len(checker.scan_text("README.md", text)), 3)

    def test_package_version_is_not_mistaken_for_camera_address(self):
        self.assertFalse(checker.scan_text("uv.lock", 'version = "10.4.0.35"'))
