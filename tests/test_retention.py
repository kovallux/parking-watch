import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from parking_watch.retention import DataRetention, cleanup, data_lock


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = 2_000_000_000
        self.config = {"spaces": [{"id": "left", "empty_references": []}],
                       "empty_candidate_capture": {"enabled": True, "space_ids": ["left"]},
                       "data_retention": {"enabled": True}}

    def tearDown(self):
        self.temporary.cleanup()

    def photo(self, relative, age=7200, size=10):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        os.utime(path, (self.now - age, self.now - age))
        return path

    def metadata(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def candidates(self, verdicts):
        records, checks, paths = [], [], []
        for number, (result, age) in enumerate(verdicts, 1):
            name = f"20261003T1200{number:02d}.000000+0200.jpg"
            paths.append(self.photo(f"data/empty-candidates/left/{name}", age))
            records.append({"file": name, "captured_at_unix": self.now - age, "verified_empty": False})
            if result:
                checks.append({"file": name, "visual_result": result,
                               "decision": "rejected" if result == "occupied" else "pending"})
        folder = self.root / "data/empty-candidates/left"
        (folder / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        self.metadata("data/left-empty-calibration-review.json",
                      {"checks": checks, "reviewed_files": [c["file"] for c in checks]})
        return paths, records

    def test_only_old_visually_rejected_candidates_are_removed_and_index_is_compacted(self):
        paths, records = self.candidates([("occupied", 7200), (None, 7200), ("empty", 7200),
                                          ("ambiguous", 7200), ("occupied", 30)])
        result = cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertFalse(paths[0].exists())
        self.assertTrue(all(p.exists() for p in paths[1:]))
        index = [json.loads(line) for line in (paths[0].parent / "index.jsonl").read_text().splitlines()]
        self.assertEqual(index, records[1:])
        self.assertEqual(result["groups"]["candidates"]["files"], 1)

    def test_preview_is_read_only_including_indexes(self):
        paths, _ = self.candidates([("occupied", 7200)])
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = cleanup(self.config, self.root, self.now)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["files"], [str(paths[0].relative_to(self.root))])

    def test_configured_reference_provenance_and_unpaired_frame_override_deletion(self):
        paths, _ = self.candidates([("occupied", 7200)] * 3)
        self.config["vehicle_identity"] = {"references": [{"file": str(paths[0])}]}
        self.metadata("data/left-empty-reference.json", {"source": str(paths[1].relative_to(self.root))})
        review_path = self.root / "data/left-empty-calibration-review.json"
        review = json.loads(review_path.read_text())
        review["last_unpaired_file"] = paths[2].name
        review_path.write_text(json.dumps(review))
        self.assertEqual(cleanup(self.config, self.root, self.now, dry_run=False)["files"], [])
        self.assertTrue(all(p.exists() for p in paths))

    def test_telegram_retention_expires_old_files_and_enforces_size_oldest_first(self):
        old = self.photo("data/telegram-photos/20260901T120001.000000+0200-check.jpg", 8 * 86400, 40)
        middle = self.photo("data/telegram-photos/20261003T120001.000000+0200-check.jpg", 200, 40)
        newest = self.photo("data/telegram-photos/20261003T120002.000000+0200-check.jpg", 100, 40)
        self.config["data_retention"]["telegram_max_mib"] = 60 / 1024 ** 2
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertFalse(old.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists())

    def test_unfinished_calibration_pins_capture_series_even_after_retention_deadline(self):
        path = self.photo("data/capture-2026-10-03/20261003T120001+0200.jpg", 10 * 86400)
        self.metadata("data/capture-2026-10-03/session.json", {"phase": "finished"})
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertTrue(path.exists())
        self.config["spaces"][0]["empty_references"] = ["data/empty-left.jpg"]
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertFalse(path.exists())

    def test_running_capture_session_is_kept(self):
        self.config["spaces"][0]["empty_references"] = ["data/empty-left.jpg"]
        path = self.photo("data/capture-2026-10-03/20261003T120001+0200.jpg", 10 * 86400)
        self.metadata("data/capture-2026-10-03/session.json", {"phase": "running"})
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertTrue(path.exists())

    def test_legacy_list_manifest_is_supported_without_discarding_unreviewed_frames(self):
        paths, _ = self.candidates([("occupied", 7200)])
        folder = self.root / "data/left-review-20261003T1201"
        self.metadata(str(folder.relative_to(self.root) / "manifest.json"), [{"file": paths[0].name}])
        self.photo(str(folder.relative_to(self.root) / "contact-1.jpg"))
        os.utime(folder, (self.now - 7200, self.now - 7200))
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertFalse(folder.exists())

    def test_review_contacts_are_removed_only_when_every_frame_is_rejected(self):
        paths, _ = self.candidates([("occupied", 7200), (None, 7200)])
        for number, source in enumerate(paths, 1):
            folder = self.root / f"data/left-review-20261003T120{number}"
            self.metadata(str(folder.relative_to(self.root) / "manifest.json"), {"records": [{"file": source.name}]})
            self.photo(str(folder.relative_to(self.root) / "contact-1.jpg"))
            os.utime(folder, (self.now - 7200, self.now - 7200))
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertFalse((self.root / "data/left-review-20261003T1201").exists())
        self.assertTrue((self.root / "data/left-review-20261003T1202/contact-1.jpg").exists())

    def test_unknown_files_logs_config_and_symbolic_links_are_not_deleted(self):
        unknown = self.photo("data/telegram-photos/user-keeps-this.jpg", 10 * 86400)
        log = self.metadata("data/events.jsonl", {"state": "occupied"})
        link = unknown.parent / "20260901T120001.000000+0200-check.jpg"
        link.symlink_to(unknown)
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertTrue(unknown.exists())
        self.assertTrue(log.exists())
        self.assertTrue(link.is_symlink())
        (self.root / "data/empty-candidates").symlink_to(unknown.parent, target_is_directory=True)
        cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertTrue(unknown.exists())

    def test_corrupt_review_metadata_aborts_before_any_deletion(self):
        paths, _ = self.candidates([("occupied", 7200)])
        old = self.photo("data/telegram-photos/20260901T120001.000000+0200-check.jpg", 10 * 86400)
        (self.root / "data/left-empty-calibration-review.json").write_text("broken")
        with self.assertRaises(ValueError):
            cleanup(self.config, self.root, self.now, dry_run=False)
        self.assertTrue(paths[0].exists())
        self.assertTrue(old.exists())

    def test_background_cleanup_failure_does_not_stop_service_or_expose_error_details(self):
        manager = DataRetention(self.config, self.root)
        with patch("parking_watch.retention.cleanup", side_effect=OSError("private-url")):
            manager.tick()
            manager.worker.join(2)
        self.assertEqual(manager.status(), {"enabled": True, "error": "data_cleanup_failed"})
        self.assertNotIn("private-url", json.dumps(manager.status()))

    def test_shared_lock_defers_cleanup_until_writer_finishes(self):
        manager = DataRetention(self.config, self.root)
        with data_lock(self.root), patch("parking_watch.retention.cleanup") as sweep:
            manager.tick()
            manager.worker.join(2)
            sweep.assert_not_called()
        manager.next_run = 0
        manager.tick()
        manager.worker.join(2)
        self.assertTrue((self.root / "data/retention-status.json").exists())

    def test_disabled_cleanup_does_not_create_files_or_modify_data(self):
        manager = DataRetention({}, self.root)
        manager.tick()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
