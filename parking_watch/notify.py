import json
import os
import queue
import secrets
import threading
import time
from urllib.request import Request, urlopen


def telegram_credentials(config=None):
    settings = (config or {}).get("telegram", {})
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or settings.get("chat_id")
    if not token and settings.get("keychain_service") and settings.get("keychain_account"):
        from .credentials import keychain_get
        token = keychain_get(settings["keychain_service"], settings["keychain_account"])
    if not token or not chat_id:
        raise ValueError("Configure Telegram before enabling notifications")
    return token, chat_id


def telegram_request(token, method, parameters=None, photo=None):
    if photo is None:
        data = json.dumps(parameters or {}).encode()
        content_type = "application/json"
    else:
        boundary = "ParkingWatch" + secrets.token_hex(16)
        parts = []
        for name, value in (parameters or {}).items():
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                          f'{value}\r\n').encode())
        parts.extend([(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                       'filename="camera.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode(),
                      photo, f'\r\n--{boundary}--\r\n'.encode()])
        data = b"".join(parts)
        content_type = "multipart/form-data; boundary=" + boundary
    return Request(f"https://api.telegram.org/bot{token}/{method}", data=data,
                   headers={"Content-Type": content_type})


def telegram_call(token, method, parameters=None, photo=None):
    request = telegram_request(token, method, parameters, photo)
    try:
        with urlopen(request, timeout=8) as response:
            result = json.load(response)
    except Exception:
        raise ValueError("Telegram request failed") from None
    if not result.get("ok"):
        raise ValueError("Telegram rejected the request")
    return result["result"]


def message(event, name):
    state = "свободно" if event.state == "free" else "занято"
    text = {
        "freed": f"🟢 {name}: место освободилось.",
        "occupied": f"🔴 {name}: место занято.",
        "current": f"{name}: сейчас {state}. После перерыва состояние проверено заново.",
        "restored": f"{name}: контроль восстановлен. Сейчас {state}.",
        "unavailable": f"⚠️ {name}: контроль недоступен. Состояние места неизвестно.",
        "initial": f"{name}: контроль запущен. Сейчас {state}.",
    }[event.kind]
    return text


class Telegram:
    """One latest message, bounded retries; old vacancy alerts are discarded."""

    def __init__(self, current_state, token=None, chat_id=None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        self.current_state = current_state
        self.queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def submit(self, event, text, photo=None):
        if event.kind == "initial":
            return
        try:
            self.queue.get_nowait()
        except queue.Empty:
            pass
        self.queue.put_nowait((event, text, time.monotonic() + 60, photo))

    def _run(self):
        pending = None
        while not self.stop.is_set():
            try:
                pending = self.queue.get(timeout=1)
            except queue.Empty:
                pass
            if pending is None:
                continue
            event, text, expires, photo = pending
            current = self.current_state()
            valid = current in {"offline", "unknown"} if event.kind == "unavailable" else current == event.state
            if time.monotonic() > expires or not valid:
                pending = None
                continue
            request = telegram_request(self.token, "sendPhoto" if photo else "sendMessage",
                                       {"chat_id": self.chat_id,
                                        "caption" if photo else "text": text}, photo)
            try:
                with urlopen(request, timeout=3) as response:
                    if not json.load(response).get("ok"):
                        raise ValueError("Telegram rejected the message")
                pending = None
            except Exception:
                # Do not print exceptions: request URLs contain the bot token.
                print("Telegram: сообщение не доставлено; повтор в течение 60 секунд.", flush=True)
                self.stop.wait(5)

    def close(self):
        self.stop.set()
        self.worker.join(timeout=4)
