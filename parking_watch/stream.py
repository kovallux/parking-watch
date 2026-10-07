import math
import os
import shutil
import subprocess
import threading
import time

import numpy as np


class Stream:
    """Drain video continuously and retain only the latest frame."""

    def __init__(self, url, width=1280, height=720, frame_interval=0.5):
        if not math.isfinite(frame_interval) or not 0 < frame_interval <= 2:
            raise ValueError("Frame interval must be between 0 and 2 seconds")
        # Use the architecture-matched bundled binary validated by the installer.
        # A system Homebrew binary may be broken or stall during startup.
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            executable = get_ffmpeg_exe()
        except Exception:
            executable = shutil.which("ffmpeg")
            if not executable:
                raise ValueError("FFmpeg is not installed") from None
        self.width, self.height = width, height
        self.lock = threading.Lock()
        self.latest = None
        hardware = (["-hwaccel", "videotoolbox"]
                    if os.environ.get("PARKING_WATCH_HWACCEL") == "videotoolbox" else [])
        self.process = subprocess.Popen([
            executable, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-threads", "1", "-filter_threads", "1", *hardware,
            "-rtsp_transport", "tcp", "-timeout", "5000000", "-i", url,
            "-an", "-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{frame_interval:g})',scale={width}:{height}",
            "-fps_mode", "passthrough",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.worker = threading.Thread(target=self._read, daemon=True)
        self.worker.start()

    def _read(self):
        size = self.width * self.height * 3
        while True:
            data = bytearray()
            while len(data) < size:
                chunk = self.process.stdout.read(size - len(data))
                if not chunk:
                    return
                data.extend(chunk)
            frame = np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3).copy()
            with self.lock:
                self.latest = (time.monotonic(), frame)

    def get(self):
        with self.lock:
            return self.latest

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.worker.join(timeout=2)
        self.process.stdout.close()


def stream_needs_restart(latest, now, started, identical_since, process_exited):
    """Recover dead, stalled, or continuously repeated camera streams."""
    return (process_exited or (latest is None and now - started > 15)
            or (latest is not None and now - latest[0] >= 5)
            or (identical_since is not None and now - identical_since > 60))
