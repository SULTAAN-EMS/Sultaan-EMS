from datetime import datetime
from zoneinfo import ZoneInfo


EAT = ZoneInfo("Africa/Mogadishu")
RESULT_VISIBILITY_SHIFTS = ("morning", "afternoon")
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


def normalize_result_visibility_shift(value):
    """Return the canonical shift key used by the student-results lock."""
    value = str(value or "").strip().lower()
    return value if value in RESULT_VISIBILITY_SHIFTS else None


def result_portal_shift_enabled(settings, shift):
    """Resolve one shift's visibility without breaking the legacy global key."""
    shift = normalize_result_visibility_shift(shift)
    if not shift:
        value = settings.get("student_results_portal_enabled", "on")
    else:
        value = settings.get(f"student_results_portal_enabled_{shift}")
        if str(value or "").strip().lower() not in {"on", "off"}:
            value = settings.get("student_results_portal_enabled", "on")
    return str(value or "on").strip().lower() not in {"off", "false", "0", "no"}


def result_portal_shift_states(settings):
    """Return the effective open/closed state for both school shifts."""
    return {
        shift: result_portal_shift_enabled(settings, shift)
        for shift in RESULT_VISIBILITY_SHIFTS
    }
