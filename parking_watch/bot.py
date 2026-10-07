from datetime import datetime
import json
import os
import signal
import time
from zoneinfo import ZoneInfo

from .cli import atomic_json, camera_url, config_load, space_configs
from .manage import monitor_pid, read_status
from .notify import telegram_call, telegram_credentials
from .media import archive_photo
from .presentation import fresh_status, parking_caption
from .runtime import ROOT
from .stream import Stream
from .retention import DataRetention


STATE_PATH = ROOT / "data/telegram-bot-state.json"
LOCAL_ZONE = ZoneInfo("Europe/Luxembourg")


def is_check_command(message, chat_id, username, now=None):
    now = time.time() if now is None else now
    private_chat = (message.get("chat", {}).get("type") == "private"
                    and str(message.get("chat", {}).get("id")) == str(chat_id)
                    and str(message.get("from", {}).get("id")) == str(chat_id))
    text = message.get("text", "").strip().lower()
    date = message.get("date", 0)
    return (private_chat and text in {"check", "/check", "/check@" + username.lower()}
            and isinstance(date, (int, float)) and -5 <= now - date <= 120)


def fresh_photo(config):
    import cv2
    stream = Stream(camera_url(config), config.get("stream_width", 1280), config.get("stream_height", 720))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            latest = stream.get()
            if latest and 0 <= time.monotonic() - latest[0] < 3:
                encoded, jpeg = cv2.imencode(".jpg", latest[1], [cv2.IMWRITE_JPEG_QUALITY, 88])
                if encoded:
                    received_at = time.time() - (time.monotonic() - latest[0])
                    return jpeg.tobytes(), received_at
            time.sleep(.1)
        raise ValueError("No fresh camera frame")
    finally:
        stream.close()


def check_caption(config, status, running, photo_time=None, now=None):
    now = time.time() if now is None else now
    fresh = fresh_status(status, running, now)
    items = status.get("spaces", {}) if fresh else {}
    lines = [parking_caption(space_configs(config), items,
                             photo_time if photo_time is not None else now, photo=photo_time is not None),
             "✅ Сервис работает." if running else "🔴 Монитор парковки остановлен."]
    if not fresh and running:
        lines.append("Свежих результатов анализа пока нет.")
    if any(item.get("reason") == "need_empty_reference" for item in items.values()):
        lines.append("⚪ Для подтверждения свободы нужен эталон пустого места.")
    if photo_time is None:
        lines.append("⚠️ Новый снимок получить не удалось. Проверь камеру и домашнюю сеть.")
    else:
        if any(item.get("reason") == "identical_frames" for item in status.get("spaces", {}).values()):
            lines.append("⚠️ Поток повторяет одинаковое изображение; свежесть снимка не подтверждена.")
    return "\n".join(lines)


def send_check(token, chat_id, source="telegram", request_id=None):
    config = config_load(ROOT / "config.local.json")
    photo, received_at = None, None
    try:
        photo, received_at = fresh_photo(config)
    except Exception:
        pass
    caption = check_caption(config, read_status(), monitor_pid() is not None, received_at)
    photo_file = archive_photo(photo, received_at, "check") if photo else None
    parameters = {"chat_id": chat_id, "caption" if photo else "text": caption}
    if request_id is not None:
        parameters["reply_parameters"] = {"message_id": request_id, "allow_sending_without_reply": True}
        # Multipart fields containing objects must be JSON strings.
        if photo:
            parameters["reply_parameters"] = json.dumps(parameters["reply_parameters"])
    result = telegram_call(token, "sendPhoto" if photo else "sendMessage", parameters, photo=photo)
    return {"source": source, "request_id": request_id, "message_id": result["message_id"],
            "photo_sent": photo is not None, "photo_file": photo_file,
            "at": datetime.now(LOCAL_ZONE).isoformat()}


def handle_update(update, token, chat_id, username, now=None):
    message = update.get("message", {})
    if is_check_command(message, chat_id, username, now):
        return send_check(token, chat_id, request_id=message.get("message_id"))
    return None


def request_stop(signum, frame):
    raise KeyboardInterrupt


def main():
    os.chdir(ROOT)
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    signal.signal(signal.SIGTERM, request_stop)
    config = config_load(ROOT / "config.local.json")
    token, chat_id = telegram_credentials(config)
    retention = DataRetention(config, ROOT)
    username = config["telegram"]["username"]
    try:
        state = json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        state = {"offset": 0}
    print("Бот запущен. Ожидаю /check в подтверждённом личном чате.", flush=True)
    try:
        while True:
            retention.tick()
            try:
                updates = telegram_call(token, "getUpdates", {"offset": state.get("offset", 0),
                                         "timeout": 3, "allowed_updates": ["message"]})
                state["last_poll_at"] = datetime.now(LOCAL_ZONE).isoformat()
                for update in updates:
                    state["offset"] = max(state.get("offset", 0), update["update_id"] + 1)
                    atomic_json(STATE_PATH, state)
                    receipt = handle_update(update, token, chat_id, username)
                    if receipt:
                        state["last_check"] = receipt
                        print("Проверка /check отправлена с фото." if receipt["photo_sent"]
                              else "Проверка /check отправлена без фото: камера недоступна.", flush=True)
                atomic_json(STATE_PATH, state)
            except Exception:
                # Telegram errors may contain the token; never print their details.
                print("Бот: запрос не выполнен; повтор подключения через 5 секунд.", flush=True)
                time.sleep(5)
    except KeyboardInterrupt:
        print("Обработчик команд остановлен.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Бот остановлен: проверь диск, интернет и Связку ключей.") from None
