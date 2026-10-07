from pathlib import Path
import os
import platform
import plistlib
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
MONITOR_LABEL = "com.kovallux.parking-watch.monitor"
BOT_LABEL = "com.kovallux.parking-watch.bot"
AUTOSTART = Path.home() / "Library" / "LaunchAgents" / (MONITOR_LABEL + ".plist")
INSTALL_STATE = Path.home() / "Library" / "Application Support" / "ParkingWatch" / "installation.json"


def _loaded_from_root(output):
    return str(ROOT) + "/" in output or chr(34) + str(ROOT) + chr(34) in output


def start_job(label, module, *arguments, restart=False):
    logs = Path.home() / "Library" / "Logs" / "ParkingWatch"
    logs.mkdir(parents=True, exist_ok=True)
    jobs = (AUTOSTART.parent if label in {MONITOR_LABEL, BOT_LABEL} else
            Path.home() / "Library" / "Application Support" / "ParkingWatch" / "jobs")
    jobs.mkdir(parents=True, exist_ok=True)
    job = jobs / (label + ".plist")
    existing = subprocess.run(["/bin/launchctl", "list", label], capture_output=True, text=True)
    if existing.returncode == 0:
        if not _loaded_from_root(existing.stdout) or module not in existing.stdout:
            raise ValueError("A different job already uses this label")
        if not restart:
            return
    settings = {"Label": label, "RunAtLoad": True, "ExitTimeOut": 20,
                "WorkingDirectory": str(ROOT),
                "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                                         **({"PARKING_WATCH_HWACCEL": "videotoolbox"}
                                            if platform.machine() == "x86_64" else {})},
                "KeepAlive": label in {MONITOR_LABEL, BOT_LABEL}, "ThrottleInterval": 30,
                "ProgramArguments": [sys.executable, str(ROOT / "scripts/run.py"), module, *arguments],
                "StandardOutPath": str(logs / (label.rsplit(".", 1)[-1] + ".log")),
                "StandardErrorPath": str(logs / (label.rsplit(".", 1)[-1] + ".error.log"))}
    temporary = job.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(settings))
    temporary.chmod(0o600)
    temporary.replace(job)
    if existing.returncode == 0:
        stopped = subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                                 capture_output=True)
        if stopped.returncode != 0:
            raise ValueError("Could not restart the project job")
        deadline = time.monotonic() + 20
        while subprocess.run(["/bin/launchctl", "list", label], capture_output=True).returncode == 0:
            if time.monotonic() >= deadline:
                raise ValueError("The previous project job did not stop")
            time.sleep(.1)
    result = subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(job)],
                            capture_output=True, cwd=ROOT)
    if result.returncode != 0:
        raise ValueError("Could not start the background project job")


def start_bot(restart=False):
    start_job(BOT_LABEL, "parking_watch.bot", restart=restart)


def start_monitor(restart_bot=False):
    start_job(MONITOR_LABEL, "parking_watch.service", restart=True)
    start_bot(restart=restart_bot)


def stop_monitor():
    existing = subprocess.run(["/bin/launchctl", "list", MONITOR_LABEL], capture_output=True, text=True)
    if existing.returncode != 0:
        return
    if not _loaded_from_root(existing.stdout) or "parking_watch.service" not in existing.stdout:
        raise ValueError("A different job already uses this label")
    result = subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{MONITOR_LABEL}"],
                            capture_output=True)
    if result.returncode != 0:
        raise ValueError("Could not stop the project monitor")


def uninstall():
    """Remove this checkout's launch jobs, retaining project data and Keychain."""
    jobs = {label: AUTOSTART.parent / (label + ".plist") for label in [MONITOR_LABEL, BOT_LABEL]}
    cache = Path.home() / "Library" / "Application Support" / "ParkingWatch" / "jobs"
    for path in cache.glob("com.kovallux.parking-watch.*.plist"):
        jobs[path.stem] = path
    loaded = []
    owned = []
    for label, path in jobs.items():
        if path.exists():
            settings = plistlib.loads(path.read_bytes())
            arguments = settings.get("ProgramArguments", [])
            if settings.get("WorkingDirectory") != str(ROOT) and not any(
                    value.startswith(str(ROOT) + "/") for value in arguments):
                raise ValueError("Autostart belongs to another project folder")
            owned.append(path)
        existing = subprocess.run(["/bin/launchctl", "list", label], capture_output=True, text=True)
        if existing.returncode == 0:
            if not _loaded_from_root(existing.stdout) or "parking_watch." not in existing.stdout:
                raise ValueError("A different job uses this label")
            loaded.append(label)
    for label in loaded:
        result = subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                                capture_output=True)
        if result.returncode != 0:
            raise ValueError("Could not unload the project job")
        deadline = time.monotonic() + 20
        while subprocess.run(["/bin/launchctl", "list", label], capture_output=True).returncode == 0:
            if time.monotonic() >= deadline:
                raise ValueError("The project job did not stop")
            time.sleep(.1)
    for path in owned:
        path.unlink(missing_ok=True)
    from .cli import atomic_json
    atomic_json(INSTALL_STATE, {"installed": False, "root": str(ROOT), "changed_at": time.time()})
