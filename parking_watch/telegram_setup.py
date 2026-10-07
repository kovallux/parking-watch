import json
import re
import secrets
import subprocess
import time

from .cli import atomic_json
from .credentials import keychain_set
from .notify import telegram_call
from .runtime import ROOT, start_monitor


def dialog(prompt, secret=False):
    script = '''on run argv
    if item 2 of argv is "secret" then
        set response to display dialog (item 1 of argv) with title "Parking Watch — Telegram" default answer "" with hidden answer buttons {"Отмена", "Продолжить"} default button "Продолжить"
        return text returned of response
    else
        display dialog (item 1 of argv) with title "Parking Watch — Telegram" buttons {"Отмена", "Готово"} default button "Готово"
        return "ok"
    end if
end run'''
    result = subprocess.run(["/usr/bin/osascript", "-e", script, prompt, "secret" if secret else "normal"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError("Настройка отменена")
    return result.stdout.strip()


def matching_chat(updates, challenge):
    ids = {update.get("message", {}).get("chat", {}).get("id") for update in updates
           if update.get("message", {}).get("text") == "/start " + challenge
           and update.get("message", {}).get("chat", {}).get("type") == "private"
           and update.get("message", {}).get("from", {}).get("id")
           == update.get("message", {}).get("chat", {}).get("id")}
    ids.discard(None)
    return next(iter(ids)) if len(ids) == 1 else None


def main():
    try:
        token = dialog("Скопируй токен, который выдал BotFather, и вставь сюда.", secret=True)
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{25,100}", token):
            raise ValueError("Токен не похож на токен BotFather")
        bot = telegram_call(token, "getMe")
        challenge = "parking_" + secrets.token_urlsafe(12)
        url = f"https://t.me/{bot['username']}?start={challenge}"
        subprocess.run(["/usr/bin/open", url], check=True)
        chat_id = None
        for attempt in range(3):
            dialog(f"Открылся бот @{bot['username']}. Нажми Start / Начать в Telegram, затем нажми «Готово» здесь.")
            for _ in range(3):
                updates = telegram_call(token, "getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
                chat_id = matching_chat(updates, challenge)
                if chat_id is not None:
                    break
                time.sleep(1)
            if chat_id is not None:
                break
        if chat_id is None:
            raise ValueError("Не получена команда Start по открытой ссылке")
        service, account = "com.kovallux.parking-watch.telegram", "bot-token"
        keychain_set(service, account, token)
        path = ROOT / "config.local.json"
        config = json.loads(path.read_text())
        config["telegram"] = {"keychain_service": service, "keychain_account": account,
                              "chat_id": chat_id, "username": bot["username"]}
        atomic_json(path, config)
        telegram_call(token, "sendMessage", {"chat_id": chat_id,
            "text": "✅ Parking Watch: Telegram подключён.\nКонтроль использует настроенные области и проверенные пустые эталоны каждого места."})
        atomic_json(ROOT / "data/telegram-setup.json", {"configured": True, "test_message_sent": True,
                                                       "username": bot["username"]})
        start_monitor(restart_bot=True)
        atomic_json(ROOT / "data/telegram-setup.json", {"configured": True, "test_message_sent": True,
                                                       "monitor_started": True, "username": bot["username"]})
        dialog("Telegram подключён. Тестовое сообщение отправлено, контроль обоих мест запущен. Проверь чат с ботом.")
    except Exception as error:
        known = {"Настройка отменена", "Токен не похож на токен BotFather",
                 "Не получена команда Start по открытой ссылке"}
        explanation = str(error) if str(error) in known else "Не удалось завершить настройку. Проверь токен, интернет и доступ к Связке ключей."
        print(explanation, flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
