import os
import signal
import subprocess

from .cli import config_load, watch
from .runtime import ROOT


def request_stop(signum, frame):
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, request_stop)
    os.chdir(ROOT)
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    # Start Python directly: a caffeinate parent cannot read CloudStorage under TCC.
    awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    try:
        watch(config_load(ROOT / "config.local.json"), True)
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Сервис остановлен: проверь камеру, Telegram и Связку ключей.") from None
    finally:
        awake.terminate()
        awake.wait(timeout=5)


if __name__ == "__main__":
    main()
