from datetime import datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import time

from .cli import atomic_json, camera_url, config_load
from .credentials import keychain_get, keychain_set
from .notify import telegram_call, telegram_credentials
from .runtime import INSTALL_STATE, ROOT, start_monitor


def ask_secret(prompt):
    script = '''on run argv
        set response to display dialog (item 1 of argv) with title "Установка Parking Watch" default answer "" with hidden answer buttons {"Отмена", "Сохранить"} default button "Сохранить"
        return text returned of response
    end run'''
    result = subprocess.run(["/usr/bin/osascript", "-e", script, prompt], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("Настройка отменена")
    return result.stdout.strip()


def configure_credentials(config):
    for section, prompt in [
        ("camera", "Введи код проверки камеры. Он сохранится в Связке ключей этого Mac."),
        ("telegram", "Вставь токен своего Telegram-бота. Он сохранится в Связке ключей этого Mac.")
    ]:
        settings = config[section]
        service, account = settings["keychain_service"], settings["keychain_account"]
        try:
            keychain_get(service, account)
        except ValueError:
            secret = ask_secret(prompt)
            if section == "telegram":
                bot = telegram_call(secret, "getMe")
                if settings.get("username") and bot.get("username") != settings["username"]:
                    raise ValueError("Токен относится к другому боту")
            keychain_set(service, account, secret)


def install():
    os.chdir(ROOT)
    config = config_load(ROOT / "config.local.json")
    configure_credentials(config)
    camera_url(config)
    token, _ = telegram_credentials(config)
    telegram_call(token, "getMe")
    from .vision import MultiVision
    from .cli import space_configs
    from imageio_ffmpeg import get_ffmpeg_exe
    executable = get_ffmpeg_exe()
    check = subprocess.run([executable, "-version"], capture_output=True, timeout=10)
    if check.returncode != 0:
        raise ValueError("Не запускается обработчик камеры")
    MultiVision(space_configs(config))
    before = time.time()
    start_monitor(restart_bot=True)
    atomic_json(INSTALL_STATE, {"installed": True, "root": str(ROOT),
                               "architecture": platform.machine(), "changed_at": time.time()})
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status = json.loads((ROOT / "data/status.json").read_text())
            if status.get("timestamp", 0) >= before and time.time() - status["timestamp"] < 3:
                if status.get("spaces") and any(space.get("observation") in {"occupied", "free", "unknown"}
                                               for space in status["spaces"].values()):
                    print("✅ Установлено. Монитор и Telegram запущены. Автозапуск после входа в macOS включён.")
                    return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.5)
    print("Автозапуск установлен, но свежий анализ ещё не подтверждён. Открой «Проверить работу.command»: возможна долгая загрузка, нагрузка на Mac или недоступность камеры.")


def main():
    try:
        install()
    except Exception as error:
        known = {"Настройка отменена", "Токен относится к другому боту", "Не запускается обработчик камеры"}
        print(str(error) if str(error) in known else
              "Установка не завершена. Проверь интернет, доступ к камере и Связке ключей.", flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
