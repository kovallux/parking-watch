import argparse
from datetime import datetime
import json
import os
import re
import subprocess
import time

from .cli import config_load
from .notify import telegram_call, telegram_credentials
from .runtime import AUTOSTART, BOT_LABEL, MONITOR_LABEL, ROOT, start_monitor, stop_monitor, uninstall


def monitor_pid(label=MONITOR_LABEL):
    result = subprocess.run(["/bin/launchctl", "list", label], capture_output=True, text=True)
    match = re.search(r'"PID"\s*=\s*(\d+)', result.stdout)
    return int(match.group(1)) if result.returncode == 0 and match else None


def status_text(status, running, now=None):
    now = time.time() if now is None else now
    lines = ["✅ Сервис запущен." if running else "🔴 Сервис сейчас остановлен."]
    stamp = status.get("timestamp", 0)
    fresh = running and 0 <= now - stamp < 10
    if fresh:
        lines.append("Последняя проверка: " + datetime.fromtimestamp(stamp).astimezone().strftime("%H:%M:%S %Z"))
        for item in status.get("spaces", {}).values():
            value = {"occupied": "занято", "free": "свободно", "offline": "камера недоступна",
                     "unknown": "состояние пока не подтверждено"}.get(item.get("state"), "проверяется")
            if item.get("reason") == "need_empty_reference":
                value = "машина не обнаружена; нужен снимок пустого места"
            lines.append(item.get("name", "Место") + ": " + value + ".")
    elif running:
        lines.append("⚠️ Свежих результатов камеры пока нет: дождись запуска или проверь сеть и камеру.")
    else:
        lines.append("Открой «Запустить.command», чтобы включить контроль.")
    return "\n".join(lines)


def read_status():
    try:
        return json.loads((ROOT / "data/status.json").read_text())
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "install", "uninstall"])
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    try:
        if args.action == "install":
            from .install import install
            install()
            return
        if args.action == "uninstall":
            uninstall()
            print("✅ Монитор и бот остановлены. Автозапуск удалён. Папка проекта, снимки и коды в Связке ключей сохранены.")
            return
        if args.action == "stop":
            stop_monitor()
            print("Контроль остановлен. После следующего входа в macOS он снова запустится автоматически.")
            return
        if args.action == "start":
            started_after = time.time()
            start_monitor()
            print("Запускаю контроль. Ожидание свежего результата — до 30 секунд.", flush=True)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = read_status()
                spaces = status.get("spaces", {})
                if (status.get("timestamp", 0) >= started_after
                        and time.time() - status.get("timestamp", 0) < 3 and spaces
                        and all(item.get("state") in {"occupied", "free"} for item in spaces.values())):
                    break
                time.sleep(.5)
        print(status_text(read_status(), monitor_pid() is not None))
        print("Автозапуск после входа в macOS: " + ("включён." if AUTOSTART.exists() else "не установлен."))
        print("Команда /check в Telegram: " + ("обработчик запущен." if monitor_pid(BOT_LABEL) else "обработчик остановлен."))
        try:
            token, chat = telegram_credentials(config_load(ROOT / "config.local.json"))
            bot = telegram_call(token, "getMe")
            print("Telegram отвечает: @" + bot["username"] + ".")
        except Exception:
            print("⚠️ Telegram не ответил: проверь интернет и настройки бота.")
    except Exception:
        print("Не удалось выполнить действие. Проверь подключение внешнего диска и открой «Проверить работу.command».")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
