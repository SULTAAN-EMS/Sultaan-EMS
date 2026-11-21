"""Shared examination-attendance rules for result entry and approval."""

from __future__ import annotations

from sqlalchemy import or_

from .attendance_rules import NON_SAT_STATUSES, SAT_STATUSES, normalize_attendance_status
from .models import AttendanceRecord, ExamSession


EXAM_ATTENDANCE_STATUS_LABELS = {
    "present": "Joogid",
    "late": "Soo daahid",
    "absent": "Maqan",
    "excused": "La fasaxay",
    "sick": "Cudur-daar",
    "emergency": "Xaalad deg-deg ah",
}

EXAM_ATTENDANCE_STATUS_ICONS = {
    "present": "fa-circle-check",
    "late": "fa-clock",
    "absent": "fa-circle-xmark",
    "excused": "fa-file-circle-check",
    "sick": "fa-notes-medical",
    "emergency": "fa-triangle-exclamation",
}


def exam_attendance_for_students(assignment, student_ids):
    """Return the latest relevant exam-hall attendance record per student.

    The session join keeps this compatible with records created by the newer
    scheduled-sitting flow as well as older rows that stored ``exam_id``
    directly on the attendance record.
    """
    ids = {int(student_id) for student_id in student_ids if student_id is not None}
    if not ids:
        return {}

    records = (
        AttendanceRecord.query
        .outerjoin(ExamSession, AttendanceRecord.exam_session_id == ExamSession.id)
        .filter(
            AttendanceRecord.academic_year_id == assignment.academic_year_id,
            AttendanceRecord.subject_id == assignment.subject_id,
            AttendanceRecord.student_id.in_(ids),
            or_(
                AttendanceRecord.exam_id == assignment.exam_id,
                ExamSession.exam_id == assignment.exam_id,
            ),
        )
        .order_by(
            AttendanceRecord.attendance_date.desc(),
            AttendanceRecord.recorded_at.desc(),
            AttendanceRecord.id.desc(),
        )
        .all()
    )

    result = {}
    for record in records:
        if record.student_id in result:
            continue
        key = normalize_attendance_status(record.status)
        result[record.student_id] = {
            "key": key,
            "label": EXAM_ATTENDANCE_STATUS_LABELS.get(key, str(record.status or "").strip()),
            "icon": EXAM_ATTENDANCE_STATUS_ICONS.get(key, "fa-clipboard-check"),
            "blocks_score": key in NON_SAT_STATUSES,
            "can_score": key in SAT_STATUSES,
            "record_id": record.id,
            "attendance_date": record.attendance_date.isoformat() if record.attendance_date else None,
        }
    return result


def attendance_score_block_message(attendance):
    if not attendance or not attendance.get("blocks_score"):
        return None
    return f"Dhibco lama gelin karo: ardaygu waa {attendance['label']}."
