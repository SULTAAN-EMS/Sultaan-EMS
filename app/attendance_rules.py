"""Canonical examination-sitting rules shared by Attendance and reporting.

Attendance records created by older versions used a mix of English and Somali
labels.  New records keep the compact API keys, while reporting normalizes both
forms so historical data is interpreted consistently.
"""

from __future__ import annotations


SAT_STATUSES = frozenset({"present", "late"})
NON_SAT_STATUSES = frozenset({"absent", "excused", "sick", "emergency"})

_STATUS_ALIASES = {
    "present": "present",
    "joogto": "present",
    "joogid": "present",
    "late": "late",
    "soo daahid": "late",
    "daahid": "late",
    "absent": "absent",
    "maqan": "absent",
    "maqnaansho": "absent",
    "excused": "excused",
    "la fasaxay": "excused",
    "sick": "sick",
    "cudurdaar": "sick",
    "cudur daar": "sick",
    "emergency": "emergency",
    "xaalad degdeg": "emergency",
    "xaalad degdeg ah": "emergency",
}


def normalize_attendance_status(value: object) -> str:
    """Return the canonical status key without changing stored history."""
    cleaned = " ".join(str(value or "").strip().lower().split())
    return _STATUS_ALIASES.get(cleaned, cleaned)


def counts_as_exam_sitting(status: object) -> bool:
    """Only present and late students are counted as having sat an exam."""
    return normalize_attendance_status(status) in SAT_STATUSES


def scheduled_subject_scope_key(academic_year_id: object, exam_id: object = None, exam_type_id: object = None) -> str:
    """Stable database uniqueness scope for an examination subject assignment."""
    if exam_id:
        return f"exam:{academic_year_id}:{exam_id}"
    return f"legacy-exam-type:{academic_year_id}:{exam_type_id or 0}"
