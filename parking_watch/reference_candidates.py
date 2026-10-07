from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .retention import candidate_bytes, data_lock, settings as retention_settings


class ReferenceCandidates:
    """Retain real frames for visual review; never declare them empty references."""

    def __init__(self, config, spaces):
        settings = config.get("empty_candidate_capture", {})
        wanted = settings.get("space_ids", []) if settings.get("enabled") else []
        self.targets = {space["id"]: space for space in spaces
                        if space["id"] in wanted and not space.get("empty_references")}
        self.root = Path("data/empty-candidates")
        self.last_capture = {}
        self.saved = {key: 0 for key in self.targets}
        self.error = None
        self.retry_at = 0
        self.retention = retention_settings(config)
        for key in self.targets:
            index = self.root / key / "index.jsonl"
            if index.exists():
                try:
                    lines = index.read_text().splitlines()
                except OSError:
                    self.error = "candidate_index_unavailable"
                    continue
                for line in lines:
                    try:
                        record = json.loads(line)
                        stamp = record["captured_at_unix"]
                        if not isinstance(stamp, (int, float)):
                            continue
                        self.last_capture[key] = stamp
                        self.saved[key] += 1
                    except (ValueError, KeyError, TypeError):
                        continue

    def consider(self, frame, reports, captured_at, frame_age):
        if frame is None or not 0 <= frame_age < 3 or captured_at < self.retry_at:
            return
        try:
            for key, space in self.targets.items():
                report = reports.get(key, {})
                if (report.get("observation") != "unknown"
                        or report.get("reason") != "need_empty_reference"
                        or report.get("vehicle_coverage", 1) > space.get("free_coverage", .03)
                        or report.get("obstruction_coverage", 1) > .03):
                    continue
                if captured_at - self.last_capture.get(key, float("-inf")) < 30:
                    continue
                import cv2
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
                if not ok:
                    raise OSError("Candidate JPEG unavailable")
                moment = datetime.fromtimestamp(captured_at, ZoneInfo("Europe/Luxembourg"))
                name = moment.strftime("%Y%m%dT%H%M%S.%f%z") + ".jpg"
                folder = self.root / key
                record = {"file": name, "space_id": key, "captured_at": moment.isoformat(),
                          "captured_at_unix": captured_at, "frame_age_seconds": round(frame_age, 3),
                          "verified_empty": False, "report": report}
                with data_lock(Path.cwd(), blocking=False) as acquired:
                    if not acquired:
                        # Cleanup runs in the background; do not stall camera analysis.
                        self.retry_at = captured_at + 1
                        return
                    photo = jpeg.tobytes()
                    if (self.retention["enabled"] and candidate_bytes(Path.cwd()) + len(photo)
                            > self.retention["candidate_max_mib"] * 1024 ** 2):
                        self.error = "candidate_storage_limit_reached"
                        self.retry_at = captured_at + 30
                        return
                    folder.mkdir(parents=True, exist_ok=True)
                    temporary = folder / (name + ".tmp")
                    temporary.write_bytes(photo)
                    temporary.replace(folder / name)
                    with (folder / "index.jsonl").open("a") as index:
                        index.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.last_capture[key] = captured_at
                self.saved[key] += 1
                self.error = None
        except Exception:
            # Candidate storage must not stop the normal camera monitor.
            self.error = "candidate_storage_failed"
            self.retry_at = captured_at + 30

    def status(self):
        return {"enabled_spaces": list(self.targets), "interval_seconds": 30,
                "saved_candidates": self.saved.copy(), "last_capture": self.last_capture.copy(),
                "error": self.error}
