"""Bound generated photos without discarding unfinished calibration evidence."""

from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import re
import threading
import time


DEFAULTS = {"enabled": False, "interval_seconds": 3600, "telegram_days": 7,
            "telegram_max_mib": 100, "reviewed_candidate_hours": 1,
            "candidate_max_mib": 512, "capture_days": 7, "capture_max_mib": 256}
PHOTO_NAME = re.compile(r"^\d{8}T\d{6}(?:\.\d+)?[+-]\d{4}(?:-[\w-]+)?\.jpg$")


def settings(config):
    values = {**DEFAULTS, **config.get("data_retention", {})}
    if not isinstance(values["enabled"], bool):
        raise ValueError("Retention enabled must be a boolean")
    for key in DEFAULTS.keys() - {"enabled"}:
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Retention limits must be positive finite numbers")
    return values


@contextmanager
def data_lock(root, blocking=True):
    data = Path(root) / "data"
    if data.is_symlink():
        raise ValueError("Generated data must not be a symlink")
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".retention.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def real_file(path, root):
    """Never follow a file or directory symlink, including a symlinked data root."""
    try:
        relative = path.relative_to(root)
        return path.is_file() and not any((root / Path(*relative.parts[:i])).is_symlink()
                                         for i in range(1, len(relative.parts) + 1))
    except ValueError:
        return False


def pinned_names(config, data):
    names = set()

    def visit(value):
        if isinstance(value, str) and value.lower().endswith((".jpg", ".jpeg", ".png")):
            names.add(Path(value).name)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(config)
    for path in data.glob("*reference*.json"):
        # Reference provenance is deliberately retained, including its original frames.
        visit(read_json(path))
    return names


def rejected_files(review):
    checks = {item["file"]: item for item in review.get("checks", [])}
    return {name for name in review.get("reviewed_files", [])
            if checks.get(name, {}).get("decision") == "rejected"
            and checks[name].get("visual_result") == "occupied"}


def candidate_bytes(root):
    return sum(path.stat().st_size for path in (Path(root) / "data/empty-candidates").glob("*/*.jpg")
               if real_file(path, Path(root)))


def cleanup(config, root, now=None, dry_run=True):
    """Caller holds data_lock when applying; a preview never mutates files."""
    root = Path(root).absolute()
    data = root / "data"
    if data.is_symlink():
        raise ValueError("Generated data must not be a symlink")
    now = time.time() if now is None else now
    policy = settings(config)
    pins = pinned_names(config, data)
    selected = {}
    indexes = {}
    rejected_by_space = {}
    protected = {"candidates": 0, "telegram": 0, "captures": 0, "review_artifacts": 0}

    def select(path, group):
        if real_file(path, root) and path.name not in pins:
            selected[path] = group

    def bounded(paths, group, days, max_mib):
        files = sorted((p for p in paths if real_file(p, root)), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if path.name in pins:
                protected[group] += 1
            elif now - path.stat().st_mtime >= days * 86400 or total > max_mib * 1024 ** 2:
                select(path, group)
                total -= path.stat().st_size

    for folder in (data / "empty-candidates").glob("*"):
        if not folder.is_dir() or folder.is_symlink():
            continue
        review = read_json(data / f"{folder.name}-empty-calibration-review.json", {})
        rejected = rejected_files(review)
        rejected.discard(Path(review.get("last_unpaired_file") or "").name)
        rejected_by_space[folder.name] = rejected
        index = folder / "index.jsonl"
        records = [json.loads(line) for line in index.read_text().splitlines() if line.strip()] if index.exists() else []
        indexes[index] = records
        by_name = {item["file"]: item for item in records}
        for path in folder.glob("*.jpg"):
            stamp = by_name.get(path.name, {}).get("captured_at_unix", now)
            if (path.name in rejected and PHOTO_NAME.fullmatch(path.name)
                    and now - max(path.stat().st_mtime, stamp) >= policy["reviewed_candidate_hours"] * 3600
                    and path.name not in pins):
                select(path, "candidates")
            else:
                protected["candidates"] += 1

    bounded((p for p in (data / "telegram-photos").glob("*.jpg") if PHOTO_NAME.fullmatch(p.name)),
            "telegram", policy["telegram_days"], policy["telegram_max_mib"])

    spaces = config.get("spaces", [config])
    wanted = config.get("empty_candidate_capture", {}).get("space_ids", [])
    calibration_pending = any(s.get("id", "main") in wanted and not s.get("empty_references") for s in spaces)
    captures = []
    for folder in data.glob("capture-????-??-??"):
        state = read_json(folder / "session.json", {})
        files = [p for p in folder.glob("*.jpg") if PHOTO_NAME.fullmatch(p.name)]
        if calibration_pending or state.get("phase") not in {"finished", "failed"}:
            protected["captures"] += len(files)
            continue
        captures.extend(files)
        index = folder / "index.jsonl"
        if index.exists():
            indexes[index] = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
    bounded(captures, "captures", policy["capture_days"], policy["capture_max_mib"])

    for space_id, rejected in rejected_by_space.items():
        for folder in data.glob(f"{space_id}-review-*"):
            if folder.is_symlink() or now - folder.stat().st_mtime < policy["reviewed_candidate_hours"] * 3600:
                continue
            manifest = read_json(folder / "manifest.json", {})
            records = manifest if isinstance(manifest, list) else manifest.get("records", [])
            files = list(folder.iterdir())
            if (not manifest or any(item.get("file") not in rejected for item in records)
                    or any(p.name != "manifest.json" and not re.fullmatch(r"contact-\d+\.jpg", p.name) for p in files)):
                protected["review_artifacts"] += len(files)
                continue
            for path in files:
                select(path, "review_artifacts")

    groups = {}
    for path, group in selected.items():
        bucket = groups.setdefault(group, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += path.stat().st_size
    result = {"checked_at": now, "dry_run": dry_run, "groups": groups, "protected": protected,
              "files": [str(p.relative_to(root)) for p in sorted(selected)],
              "bytes": sum(x["bytes"] for x in groups.values()), "error": None}
    if not dry_run:
        for path in selected:
            path.unlink()
        for index, records in indexes.items():
            if not any(p.parent == index.parent and p.suffix == ".jpg" for p in selected):
                continue
            remaining = [r for r in records if index.parent / r["file"] not in selected]
            temporary = index.with_suffix(".retention.tmp")
            temporary.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in remaining))
            temporary.replace(index)
        for folder in {p.parent for p, group in selected.items() if group == "review_artifacts"}:
            if not any(folder.iterdir()):
                folder.rmdir()
    return result


class DataRetention:
    def __init__(self, config, root):
        self.config, self.root = config, Path(root)
        self.policy = settings(config)
        self.next_run = 0
        self.worker = None
        self.result = {"enabled": self.policy["enabled"], "error": None}

    def tick(self):
        if (not self.policy["enabled"] or time.monotonic() < self.next_run
                or self.worker is not None and self.worker.is_alive()):
            return
        self.next_run = time.monotonic() + self.policy["interval_seconds"]
        self.worker = threading.Thread(target=self._sweep, name="photo-retention", daemon=True)
        self.worker.start()

    def _sweep(self):
        try:
            with data_lock(self.root, blocking=False) as acquired:
                if not acquired:
                    self.next_run = time.monotonic() + 30
                    return
                path = self.root / "data/retention-status.json"
                previous = read_json(path, {})
                if time.time() - previous.get("checked_at", 0) < self.policy["interval_seconds"]:
                    self.result = previous
                    return
                result = cleanup(self.config, self.root, dry_run=False)
                result.pop("files")
                self.result = {"enabled": True, **result}
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.result, ensure_ascii=False, indent=2) + "\n")
                temporary.replace(path)
        except Exception:
            # Disk and metadata problems must not stop monitoring or expose credentials.
            self.result = {"enabled": True, "error": "data_cleanup_failed"}

    def status(self):
        return self.result.copy()
