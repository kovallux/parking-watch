import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import quote

from .state import Monitor


def space_configs(config):
    defaults = {key: value for key, value in config.items() if key != "spaces"}
    spaces = config.get("spaces")
    if spaces is None:
        return [{**defaults, "id": "main"}]
    if not isinstance(spaces, list) or not spaces:
        raise ValueError("Configure at least one parking space")
    expanded = [{**defaults, **space} for space in spaces]
    ids = [space.get("id") for space in expanded]
    if any(not isinstance(value, str) or not value or not value.replace("-", "").replace("_", "").isalnum()
           for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Parking space IDs must be unique")
    for space in expanded:
        for key in ["model", "device", "imgsz", "confidence", "occupancy_fallback_model"]:
            if space.get(key) != config.get(key):
                raise ValueError("Parking spaces must share the same detector settings")
    return expanded


def validate_space(config):
    polygon = config.get("polygon", [])
    if len(polygon) != 4 or any(len(p) != 2 or any(not isinstance(v, (int, float)) or
            not math.isfinite(v) or not 0 <= v <= 1 for v in p) for p in polygon):
        raise ValueError("Select four normalised parking-space corners")
    turns = []
    for i in range(4):
        a, b, c = polygon[i], polygon[(i + 1) % 4], polygon[(i + 2) % 4]
        turns.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    if not (all(v > 0 for v in turns) or all(v < 0 for v in turns)):
        raise ValueError("Corners must form a convex polygon in boundary order")
    for key in ["free_seconds", "occupied_seconds", "unavailable_seconds"]:
        value = config.get(key, {"free_seconds": 10, "occupied_seconds": 3, "unavailable_seconds": 15}[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Confirmation durations must be positive")
    low, high = config.get("free_coverage", 0.03), config.get("occupied_coverage", 0.2)
    if not 0 <= low < high <= 1:
        raise ValueError("Invalid occupancy thresholds")
    for key, default in [("confidence", 0.35), ("empty_difference_max", 0.08), ("empty_structure_min", 0.4)]:
        if not 0 < config.get(key, default) < 1:
            raise ValueError("Invalid vision threshold")


def config_load(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    interval = config.get("analysis_interval_seconds", 0.5)
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 0 < interval <= 2:
        raise ValueError("Analysis interval must be between 0 and 2 seconds")
    from .retention import settings
    settings(config)
    for space in space_configs(config):
        validate_space(space)
    for item in config.get("spaces", [config]):
        item["empty_references"] = [str((path.parent / reference).resolve())
                                    for reference in item.get("empty_references", [])]
    identity = config.get("vehicle_identity", {})
    for reference in identity.get("references", []):
        reference["file"] = str((path.parent / reference["file"]).resolve())
    return config


def camera_url(config=None):
    value = os.environ.get("CAMERA_RTSP_URL")
    if value:
        if not value.startswith(("rtsp://", "rtsps://")):
            raise ValueError("CAMERA_RTSP_URL must be an RTSP URL")
        return value
    camera = (config or {}).get("camera", {})
    ip = os.environ.get("EZVIZ_IP") or camera.get("ip")
    password = os.environ.get("EZVIZ_PASSWORD")
    if not password and camera.get("keychain_service") and camera.get("keychain_account"):
        try:
            result = subprocess.run([
                "/usr/bin/security", "find-generic-password", "-s", camera["keychain_service"],
                "-a", camera["keychain_account"], "-w"],
                capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Camera credential unavailable in macOS Keychain") from None
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError("Camera credential unavailable in macOS Keychain")
        password = result.stdout.strip()
    if not ip or not password:
        raise ValueError("Set EZVIZ_IP and EZVIZ_PASSWORD, or CAMERA_RTSP_URL")
    return f"rtsp://admin:{quote(password, safe='')}@{ip}:554/ch1/main"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def capture(config, output):
    import cv2
    from .stream import Stream
    stream = Stream(camera_url(config), config.get("stream_width", 1280), config.get("stream_height", 720))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            frame = stream.get()
            if frame and time.monotonic() - frame[0] < 3:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(output, frame[1]):
                    raise ValueError("Could not save captured frame")
                print("Кадр сохранён.")
                return
            time.sleep(0.1)
        raise ValueError("No fresh camera frame within 15 seconds")
    finally:
        stream.close()


def image_check(config, file, output):
    import cv2
    from .vision import MultiVision
    frame = cv2.imread(file)
    if frame is None:
        raise ValueError("Input image cannot be read")
    vision = MultiVision(space_configs(config))
    reports, annotated = vision.analyse(frame)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(output, annotated):
            raise ValueError("Could not save annotated image")
    report = next(iter(reports.values())) if len(reports) == 1 else reports
    print(json.dumps(report, ensure_ascii=False, indent=2))


def analysis_due(latest, now, last_frame_at, last_analysis_at, interval):
    return (latest is not None and 0 <= now - latest[0] < 3
            and latest[0] != last_frame_at and now - last_analysis_at >= interval)


def watch(config, send_notifications):
    spaces = space_configs(config)
    if any(not space.get("calibrated") for space in spaces):
        raise ValueError("Calibrate the area on a real camera frame and set calibrated=true")
    from .notify import Telegram, telegram_credentials
    from .stream import Stream, stream_needs_restart
    from .vision import MultiVision
    from .reference_candidates import ReferenceCandidates
    from .presentation import parking_caption
    from .media import archive_photo
    from .notification_policy import NotificationPolicy
    from .retention import DataRetention
    retention = DataRetention(config, Path.cwd())
    retention.tick()
    url = camera_url(config)
    vision = MultiVision(spaces)
    candidates = ReferenceCandidates(config, spaces)
    monitors = {space["id"]: Monitor(**{key: space[key] for key in ["free_seconds", "occupied_seconds",
                        "unavailable_seconds"] if key in space}) for space in spaces}
    policies = {space["id"]: NotificationPolicy(offline_seconds=space.get("unavailable_seconds", 15))
                for space in spaces}
    credentials = telegram_credentials(config) if send_notifications else None
    notifiers = {key: Telegram(lambda monitor=monitor: monitor.current, *credentials)
                 for key, monitor in monitors.items()} if credentials else {}
    stream, restart_at, last_frame_at = None, 0, None
    last_analysis_at = float("-inf")
    analysis_interval = config.get("analysis_interval_seconds", 0.5)
    last_digest, identical_since = None, None
    last_uncertain_notice_at = None
    event_path = Path("data/events.jsonl")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    print("Контроль запущен. " + ("Telegram включён." if notifiers else "Уведомления только в журнале."), flush=True)
    try:
        while True:
            retention.tick()
            now = time.monotonic()
            if stream is None and now >= restart_at:
                stream = Stream(url, config.get("stream_width", 1280), config.get("stream_height", 720))
                stream_started = now
                last_digest, identical_since, last_frame_at = None, None, None
            latest = None if stream is None else stream.get()
            reports = None
            if analysis_due(latest, now, last_frame_at, last_analysis_at, analysis_interval):
                last_frame_at = latest[0]
                last_analysis_at = now
                digest = hashlib.blake2s(latest[1].tobytes()).digest()
                if digest != last_digest:
                    last_digest, identical_since = digest, now
                if now - identical_since > 60:
                    reports = {space["id"]: {"observation": "unknown", "reason": "identical_frames"} for space in spaces}
                else:
                    reports, _ = vision.analyse(latest[1])
                    if time.monotonic() - latest[0] >= 3:
                        reports = {space["id"]: {"observation": "offline", "reason": "analysis_too_slow"} for space in spaces}
            elif latest is None or now - latest[0] >= 3:
                reports = {space["id"]: {"observation": "offline", "reason": "no_fresh_frame"} for space in spaces}
            if stream is not None and stream_needs_restart(
                    latest, now, stream_started, identical_since, stream.process.poll() is not None):
                stream.close()
                stream, restart_at = None, time.monotonic() + 5
            if reports is not None:
                if latest:
                    frame_age = time.monotonic() - latest[0]
                    candidates.consider(latest[1], reports, time.time() - frame_age, frame_age)
                status = {}
                events = []
                for space in spaces:
                    key = space["id"]
                    monitor, report = monitors[key], reports[key]
                    observed_at = time.monotonic()
                    event = monitor.observe(report["observation"], observed_at)
                    notification = policies[key].observe(monitor.current, report["observation"], event, observed_at)
                    if notification and notification.kind == "uncertain":
                        if last_uncertain_notice_at is not None and observed_at - last_uncertain_notice_at < 1800:
                            event = event or notification
                            notification = None
                        else:
                            last_uncertain_notice_at = observed_at
                    status[key] = {"name": space.get("name", key), "state": monitor.current,
                                   "last_confirmed": monitor.last_confirmed, **report}
                    if event or notification:
                        events.append((space, event, notification))
                for space, raw_event, notification in events:
                    key = space["id"]
                    event = notification or raw_event
                    should_send = notification is not None and key in notifiers
                    photo = None
                    captured_at = time.time()
                    if should_send and event.kind != "unavailable" and latest and time.monotonic() - latest[0] < 3:
                        import cv2
                        encoded, jpeg = cv2.imencode(".jpg", latest[1], [cv2.IMWRITE_JPEG_QUALITY, 88])
                        if encoded:
                            photo = jpeg.tobytes()
                            captured_at = time.time() - (time.monotonic() - latest[0])
                    text = parking_caption(spaces, status, captured_at, photo=photo is not None)
                    if event.kind == "unavailable":
                        text += ("\n⚠️ Камера не передаёт свежие кадры." if event.state == "offline"
                                 else "\n⚠️ Распознавание временно неоднозначно.")
                    elif event.kind == "uncertain":
                        text += "\n⚠️ Распознавание нестабильно уже 5 минут. Свежий кадр можно проверить через /check."
                    elif event.kind == "restored":
                        text += "\n✅ Контроль восстановлен."
                    photo_file = (archive_photo(photo, captured_at, key)
                                  if photo and should_send else None)
                    record = {"timestamp": time.time(), "space_id": key, "kind": event.kind,
                              "state": event.state, "text": text, "photo_file": photo_file,
                              "notification_suppressed": notification is None,
                              "notification_submitted": should_send,
                              "raw_kind": None if raw_event is None else raw_event.kind}
                    with event_path.open("a") as file:
                        file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    print(text, flush=True)
                    if should_send:
                        notifiers[key].submit(event, text, photo)
                atomic_json(Path("data/status.json"), {"timestamp": time.time(), "spaces": status,
                                                       "reference_capture": candidates.status(),
                                                       "data_retention": retention.status()})
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Контроль остановлен.")
    finally:
        if stream:
            stream.close()
        for notifier in notifiers.values():
            notifier.close()
        atomic_json(Path("data/status.json"), {"timestamp": time.time(), "state": "offline", "reason": "stopped"})


def main():
    parser = argparse.ArgumentParser(description="Parking Watch")
    parser.add_argument("--config", default="config.example.json")
    sub = parser.add_subparsers(dest="command", required=True)
    image = sub.add_parser("image")
    image.add_argument("file")
    image.add_argument("--output")
    snapshot = sub.add_parser("capture")
    snapshot.add_argument("--output", default="data/camera.jpg")
    live = sub.add_parser("watch")
    live.add_argument("--notify", action="store_true")
    cleaning = sub.add_parser("cleanup", help="Preview generated-photo cleanup")
    cleaning.add_argument("--apply", action="store_true", help="Delete the selected generated files")
    cleaning.add_argument("--output", help="Save the complete cleanup plan as JSON")
    args = parser.parse_args()
    try:
        config = config_load(args.config)
        if args.command == "image":
            image_check(config, args.file, args.output)
        elif args.command == "capture":
            capture(config, args.output)
        elif args.command == "cleanup":
            from .retention import cleanup, data_lock
            root = Path(args.config).resolve().parent
            with data_lock(root):
                result = cleanup(config, root, dry_run=not args.apply)
            if args.output:
                atomic_json(Path(args.output), result)
            print(json.dumps({key: value for key, value in result.items() if key != "files"},
                             ensure_ascii=False, indent=2))
        else:
            watch(config, args.notify)
    except Exception:
        # Raw camera/model/network errors can contain credentials or local paths.
        raise SystemExit("Не удалось выполнить команду. Проверь конфигурацию, доступ к камере и установку зависимостей.")


if __name__ == "__main__":
    main()
