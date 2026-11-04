from datetime import datetime
from zoneinfo import ZoneInfo


EAT = ZoneInfo("Africa/Mogadishu")
SOMALI_WEEKDAYS = (
    "Isniin",
    "Talaada",
    "Arbaca",
    "Khamiis",
    "Jumca",
    "Sabti",
    "Axad",
)


def parse_announcement_datetime(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EAT)
    return parsed.astimezone(EAT)


def announcement_datetime_local_input(value):
    parsed = parse_announcement_datetime(value)
    return parsed.strftime("%Y-%m-%dT%H:%M") if parsed else ""


def announcement_datetime_payload(value):
    parsed = parse_announcement_datetime(value)
    if not parsed:
        return None
    return {
        "iso": parsed.isoformat(timespec="minutes"),
        "display": (
            f"{SOMALI_WEEKDAYS[parsed.weekday()]}, "
            f"{parsed.strftime('%B %d, %Y')} · {parsed.strftime('%I:%M %p')} EAT"
        ),
        "timestamp_ms": int(parsed.timestamp() * 1000),
    }
