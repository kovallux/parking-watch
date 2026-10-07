import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import time

from .cli import atomic_json, camera_url, config_load
from .runtime import ROOT
from .stream import Stream


def next_slot(slot, now, interval):
    if slot > now:
        return slot
    missed = int((now - slot).total_seconds() // interval) + 1
    return slot + timedelta(seconds=missed * interval)


def session(config, start, end, interval, output):
    import cv2
    if start.tzinfo is None or end.tzinfo is None or end <= start or interval <= 0:
        raise ValueError("Invalid capture schedule")
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "session.json"
    count, failures, last_digest, identical_since = 0, 0, None, None
    stream = None
    slot = start

    def state(phase):
        atomic_json(state_path, {"phase": phase, "start": start.isoformat(), "end": end.isoformat(),
                                "interval_seconds": interval, "frames_saved": count, "failed_slots": failures,
                                "updated_at": datetime.now(start.tzinfo).isoformat()})

    try:
        state("waiting")
        while datetime.now(start.tzinfo) < start:
            time.sleep(max(0, min(30, (start - datetime.now(start.tzinfo)).total_seconds())))
            state("waiting")
        state("running")
        url = camera_url(config)
        while datetime.now(start.tzinfo) < end:
            now = datetime.now(start.tzinfo)
            if now < slot:
                time.sleep(min(1, (slot - now).total_seconds()))
                continue
            if stream is None:
                stream = Stream(url, config.get("stream_width", 1280), config.get("stream_height", 720))
            deadline = min(time.monotonic() + 15, time.monotonic() + (end - now).total_seconds())
            latest = None
            while time.monotonic() < deadline:
                candidate = stream.get()
                if candidate and time.monotonic() - candidate[0] < 3:
                    latest = candidate
                    break
                time.sleep(.1)
            moment = datetime.now(start.tzinfo)
            if latest is None or moment >= end:
                failures += 1
                stream.close()
                stream = None
            else:
                digest = hashlib.blake2s(latest[1].tobytes()).digest()
                if digest != last_digest:
                    last_digest, identical_since = digest, time.monotonic()
                if time.monotonic() - identical_since > 60:
                    failures += 1
                else:
                    name = moment.strftime("%Y%m%dT%H%M%S%z") + ".jpg"
                    temporary = output / ("temporary-" + name)
                    if not cv2.imwrite(str(temporary), latest[1]):
                        raise ValueError("Could not save camera frame")
                    temporary.replace(output / name)
                    with (output / "index.jsonl").open("a") as log:
                        log.write(json.dumps({"file": name, "captured_at": moment.isoformat(),
                                              "scheduled_at": slot.isoformat(),
                                              "frame_age_seconds": round(time.monotonic()-latest[0], 3)}) + "\n")
                    count += 1
                    print("Сохранён кадр " + moment.strftime("%H:%M:%S"), flush=True)
            state("running")
            slot = next_slot(slot, datetime.now(start.tzinfo), interval)
        state("finished")
    except Exception:
        state("failed")
        raise
    finally:
        if stream:
            stream.close()


def main():
    os.chdir(ROOT)
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--output", default="data/capture-2026-10-03")
    args = parser.parse_args()
    try:
        session(config_load("config.local.json"), datetime.fromisoformat(args.start),
                datetime.fromisoformat(args.end), args.interval, Path(args.output))
    except Exception:
        raise SystemExit("Съёмка остановлена: проверь камеру, внешний диск и Связку ключей.") from None


if __name__ == "__main__":
    main()
