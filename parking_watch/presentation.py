from datetime import datetime
import math
from zoneinfo import ZoneInfo


LOCAL_ZONE = ZoneInfo("Europe/Luxembourg")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def readable_time(stamp):
    moment = datetime.fromtimestamp(stamp, LOCAL_ZONE)
    return f"{moment.day} {MONTHS[moment.month - 1]}, {moment:%H:%M}"


def parking_lines(spaces, items):
    lines = []
    for space in spaces:
        item = items.get(space["id"], {})
        state = item.get("state")
        observation = item.get("observation")
        if state == "free" and observation not in {None, "free"}:
            state = "unknown"
        marker, label = {"occupied": ("🔴", "ЗАНЯТО"), "free": ("🟢", "СВОБОДНО"),
                         "offline": ("⚠️", "НЕТ СВЯЗИ")}.get(state, ("⚪", "НЕ ПОДТВЕРЖДЕНО"))
        lines.append(f"{marker} {space.get('name', space['id'])}: {label}")
        identity = item.get("vehicle_identity")
        if state == "occupied" and observation in {None, "occupied"} and isinstance(identity, dict):
            confidence = {"high": "ВЫСОКАЯ", "medium": "СРЕДНЯЯ", "low": "НИЗКАЯ"}.get(
                identity.get("confidence"), "НЕ ПОДТВЕРЖДЕНО")
            lines.append(f"  🚙 Сходство с {identity.get('name', 'Эталонный автомобиль')}: уверенность {confidence}")
    return lines


def parking_caption(spaces, items, stamp, photo=True):
    lines = [f"{'📷' if photo else '🕒'} {readable_time(stamp)}"]
    lines.extend(parking_lines(spaces, items))
    return "\n".join(lines)


def fresh_status(status, running, now):
    stamp = status.get("timestamp")
    return (running and isinstance(stamp, (int, float)) and math.isfinite(stamp)
            and 0 <= now - stamp < 10)
