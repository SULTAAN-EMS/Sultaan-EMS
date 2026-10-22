from datetime import date, datetime, time
from io import BytesIO
from tempfile import NamedTemporaryFile
from types import SimpleNamespace

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from uuid import uuid4

from . import db
from .audit import audit
from .models import (
    AcademicClass, AcademicLevel, AcademicYear, AcademicYearClass, AcademicYearLevel, AcademicYearSubject, AttendanceRecord,
    Exam, ExamHall, ExamHallEnrollment, ExamHallSubject, ExamSession,
    ExamSessionSubject, ExamType, SchoolClass, Student, Subject
)
from .permissions import enforce_endpoint_permission
from .services import get_settings
from .pdf_rendering import html_report_to_pdf
from .attendance_summary_pdf import build_attendance_summary_pdf
from .attendance_rules import NON_SAT_STATUSES, SAT_STATUSES, normalize_attendance_status, scheduled_subject_scope_key
from .academic_hierarchy import year_classes, year_levels
from .enrollment_service import (
    EnrollmentValidationError,
    enrollment_placement_for_student,
    get_enrollment_for_student_year,
    resolve_student_placements,
    student_enrollment_legacy_scope_query,
    student_enrollment_scope_query,
)

attendance_bp = Blueprint("admin_attendance", __name__)

ATTENDANCE_STATUSES = [
    {"key": "present", "label": "Joogid", "cls": "o-present", "icon": "fa-circle-check"},
    {"key": "absent", "label": "Maqan", "cls": "o-absent", "icon": "fa-circle-xmark"},
    {"key": "excused", "label": "La fasaxay", "cls": "o-excused", "icon": "fa-file-circle-check"},
    {"key": "sick", "label": "Cudur daar", "cls": "o-sick", "icon": "fa-notes-medical"},
    {"key": "emergency", "label": "Xaalad degdeg", "cls": "o-emergency", "icon": "fa-triangle-exclamation"},
    {"key": "late", "label": "Soo daahid", "cls": "o-late", "icon": "fa-clock"},
]

ATTENDANCE_CLASS_COLORS = [
    "#24406B", "#B8863E", "#2F6B4F", "#A6402F", "#6C4E9E",
    "#2C6C87", "#96355E", "#0F766E", "#7C3AED", "#BE123C",
]


def attendance_class_color(class_name):
    """Match the deterministic class badge color used by the attendance roster."""
    encoded = f"class:{class_name or ''}".encode("utf-16-le", errors="surrogatepass")
    hashed = 0
    for offset in range(0, len(encoded), 2):
        code_unit = encoded[offset] | (encoded[offset + 1] << 8)
        hashed = (hashed * 31 + code_unit) & 0xFFFFFFFF
    return ATTENDANCE_CLASS_COLORS[hashed % len(ATTENDANCE_CLASS_COLORS)]

# Keep API validation and report calculations tied to the same canonical keys.
ATTENDANCE_STATUS_KEYS = SAT_STATUSES | NON_SAT_STATUSES

@attendance_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@attendance_bp.after_request
def prevent_cached_attendance_api_responses(response):
    """A newly saved timetable must be visible to the same browser immediately."""
    if request.path.startswith("/admin/attendance/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def get_exam_types(academic_year_id):
    """Return exams configured in Results Hub Setup for one academic year."""
    if not academic_year_id:
        return []
    exams = (
        Exam.query
        .filter_by(academic_year_id=academic_year_id, is_active=True)
        .order_by(Exam.sort_order, Exam.id.desc())
        .all()
    )
    if exams:
        return exams
    # Keep older attendance records readable while all new records use Exam.
    return (
        ExamType.query
        .filter_by(academic_year_id=academic_year_id, is_active=True)
        .order_by(ExamType.sort_order, ExamType.id)
        .all()
    )


def stored_photo_url(path):
    """Return a browser-safe existing student image URL."""
    if not path:
        return ""
    value = str(path)
    if value.startswith(("http://", "https://", "data:", "/static/")):
        return value
    if value.startswith("uploads/"):
        return url_for("static", filename=value)
    return url_for("static", filename=f"uploads/{value}")


def attendance_student_scope_query(academic_year_id, *, exam=None, level_id=None, class_id=None, section_id=None):
    """Return attendance students from the selected-year enrollment scope."""
    return student_enrollment_legacy_scope_query(
        academic_year_id,
        legacy_level_id=level_id or getattr(exam, "academic_level_id", None),
        legacy_class_id=class_id or getattr(exam, "academic_class_id", None),
        academic_section_id=section_id or getattr(exam, "academic_section_id", None),
    )


def hall_context(academic_year_id, exam_id, exam_type_id, exam_hall_id):
    """Validate the Year -> Results Hub Exam -> Hall attendance scope."""
    if not academic_year_id or not (exam_id or exam_type_id) or not exam_hall_id:
        raise ValueError("Academic year, exam type, and hall are all required")
    hall = db.session.get(ExamHall, exam_hall_id)
    if not hall or not hall.is_active:
        raise ValueError("The selected exam context is no longer available")
    if exam_id:
        exam = db.session.get(Exam, exam_id)
        if not exam or exam.academic_year_id != academic_year_id:
            raise ValueError("The selected hall does not belong to this academic year and exam type")
        if hall.exam_id == exam_id:
            return hall, exam, None
        legacy_exam_type = ExamType.query.filter_by(
            academic_year_id=exam.academic_year_id,
            name=exam.name,
        ).first()
        if legacy_exam_type and hall.exam_type_id == legacy_exam_type.id:
            return hall, exam, legacy_exam_type
        if hall.exam_type_id:
            h_type = db.session.get(ExamType, hall.exam_type_id)
            if h_type and h_type.academic_year_id == academic_year_id:
                return hall, exam, h_type
        # A class link by itself cannot prove that a hall belongs to the
        # selected examination. Reject an unscoped legacy hall instead of
        # allowing attendance data to cross exam boundaries.
        raise ValueError("The selected hall does not belong to this academic year and exam type")

    legacy_exam_type = db.session.get(ExamType, exam_type_id)
    if not legacy_exam_type or legacy_exam_type.academic_year_id != academic_year_id:
        raise ValueError("The selected hall does not belong to this academic year and exam type")
    if hall.exam_type_id == exam_type_id:
        return hall, None, legacy_exam_type
    if hall.exam and hall.exam.academic_year_id == academic_year_id:
        return hall, hall.exam, legacy_exam_type
    return hall, None, legacy_exam_type


def hall_exam_context(hall):
    """Return the canonical Exam or the legacy ExamType attached to a hall."""
    return hall.exam or hall.exam_type


def same_exam_hall_filters(hall):
    """Return SQL filters matching active halls in the same exam context."""
    if hall.exam_id:
        filters = [ExamHall.exam_id == hall.exam_id]
        legacy_exam_type = ExamType.query.filter_by(
            academic_year_id=hall.exam.academic_year_id if hall.exam else None,
            name=hall.exam.name if hall.exam else None,
        ).first()
        if legacy_exam_type:
            filters.append(and_(ExamHall.exam_id.is_(None), ExamHall.exam_type_id == legacy_exam_type.id))
        return filters
    if hall.exam_type_id:
        return [ExamHall.exam_type_id == hall.exam_type_id]
    return [ExamHall.id == hall.id]


def assignment_in_same_exam(student_id, hall, exclude_hall_id=None):
    """Find a student's active hall assignment in the same exam context."""
    query = (
        ExamHallEnrollment.query
        .join(ExamHall, ExamHallEnrollment.exam_hall_id == ExamHall.id)
        .filter(
            ExamHallEnrollment.student_id == student_id,
            ExamHall.is_active.is_(True),
            or_(*same_exam_hall_filters(hall)),
        )
    )
    if exclude_hall_id:
        query = query.filter(ExamHallEnrollment.exam_hall_id != exclude_hall_id)
    return query.first()


def hall_subjects(hall):
    """Use explicit hall subjects; legacy halls safely fall back to their level."""
    linked = (
        Subject.query
        .join(ExamHallSubject, ExamHallSubject.subject_id == Subject.id)
        .filter(ExamHallSubject.exam_hall_id == hall.id, Subject.is_active.is_(True))
        .order_by(Subject.sort_order, Subject.name)
        .all()
    )
    if linked:
        return linked
    if hall.academic_class_id and hall.academic_class:
        return (
            Subject.query
            .filter(
                Subject.is_active.is_(True),
                or_(
                    Subject.academic_level_id == hall.academic_class.academic_level_id,
                    Subject.academic_level_id.is_(None),
                ),
            )
            .order_by(Subject.sort_order, Subject.name)
            .all()
        )
    return []


def add_default_hall_subjects(hall):
    """Persist the real level curriculum when a new hall exam is registered."""
    for subject in hall_subjects(hall):
        db.session.add(ExamHallSubject(exam_hall_id=hall.id, subject_id=subject.id))


def attendance_record_for(
    student_id,
    exam_hall_id,
    subject_id,
    year_id=None,
    exam_id=None,
    exam_type_id=None,
    exam_session_id=None,
):
    """Return the one attendance row for a student within a hall subject, falling back to exam/year/type context."""
    if not student_id or not subject_id:
        return None

    # A scheduled sitting is a complete attendance scope.  It must never
    # reuse a record from a different sitting or legacy unscoped row.
    if exam_session_id:
        return (
            AttendanceRecord.query
            .filter_by(
                student_id=student_id,
                exam_hall_id=exam_hall_id,
                subject_id=subject_id,
                exam_session_id=exam_session_id,
            )
            .order_by(AttendanceRecord.id.desc())
            .first()
        )

    # First search by exact hall + subject for the legacy one-subject view.
    if exam_hall_id:
        rec = (
            AttendanceRecord.query
            .filter_by(
                student_id=student_id,
                exam_hall_id=exam_hall_id,
                subject_id=subject_id,
            )
            .order_by(AttendanceRecord.id.desc())
            .first()
        )
        if rec:
            return rec

    # Fall back to student + subject + exam
    query = AttendanceRecord.query.filter_by(student_id=student_id, subject_id=subject_id)
    if exam_id:
        rec = query.filter_by(exam_id=exam_id).order_by(AttendanceRecord.id.desc()).first()
        if rec:
            return rec

    # Fall back to student + subject + legacy_exam_type
    if exam_type_id:
        rec = query.filter_by(exam_type_id=exam_type_id).order_by(AttendanceRecord.id.desc()).first()
        if rec:
            return rec

    # A hallless legacy record can be adopted only inside the same context.
    # Never reuse an older examination just because its student and subject match.
    if year_id and not (exam_id or exam_type_id):
        rec = query.filter_by(academic_year_id=year_id).order_by(AttendanceRecord.id.desc()).first()
        if rec:
            return rec

    return None


def legacy_school_class_id(student, class_name=None, class_cache=None):
    """Provide the legacy class link required by older attendance schemas."""
    if student.class_id and not class_name:
        return student.class_id

    class_name = class_name or (
        student.academic_class.name if student.academic_class else None
    ) or student.level or "Unassigned"
    if class_cache is not None and class_name in class_cache:
        student.class_id = class_cache[class_name]
        return class_cache[class_name]
    legacy_class = SchoolClass.query.filter_by(name=class_name).first()
    if not legacy_class:
        legacy_class = SchoolClass(name=class_name)
        db.session.add(legacy_class)
        db.session.flush()
    student.class_id = legacy_class.id
    if class_cache is not None:
        class_cache[class_name] = legacy_class.id
    return legacy_class.id


def apply_attendance_values(
    record,
    *,
    student,
    year_id,
    exam,
    legacy_exam_type,
    status_val,
    exam_session=None,
    placement=None,
    legacy_class_cache=None,
):
    """Apply the canonical Results Hub scope and selected attendance status."""
    record.academic_year_id = year_id
    if placement is None:
        placement = enrollment_placement_for_student(student, year_id) or {}
    class_name = placement.get("class_name")
    # Some deployed databases predate the nullable class_id model change.
    # Populate both current and legacy class fields so a status click is valid
    # on every supported schema.
    record.class_id = legacy_school_class_id(
        student,
        class_name=class_name,
        class_cache=legacy_class_cache,
    )
    record.academic_level_id = placement.get("academic_level_id") or student.academic_level_id
    record.academic_class_id = placement.get("academic_class_id") or student.academic_class_id
    record.academic_section_id = placement.get("academic_section_id") or student.academic_section_id
    if exam:
        record.exam_id = exam.id
    if legacy_exam_type:
        record.exam_type_id = legacy_exam_type.id
    if exam_session:
        record.exam_session_id = exam_session.id
    record.attendance_date = date.today()
    record.status = status_val
    record.recorded_at = datetime.utcnow()
    user_id = getattr(current_user, "id", None) if getattr(current_user, "is_authenticated", False) else None
    record.marked_by_id = user_id


def effective_student_level_id(student, academic_year_id=None):
    """Resolve a student's configured academic level without guessing one."""
    if academic_year_id:
        placement = enrollment_placement_for_student(student, academic_year_id)
        if placement:
            return placement.get("academic_level_id")
    if student.academic_level_id:
        return student.academic_level_id
    if student.academic_class:
        return student.academic_class.academic_level_id
    return None


def student_class_name(student, academic_year_id=None):
    if academic_year_id:
        placement = enrollment_placement_for_student(student, academic_year_id)
        if placement and placement.get("class_name"):
            return placement["class_name"]
    return (
        student.academic_class.name if student.academic_class else None
    ) or (student.school_class.name if student.school_class else None) or student.level or ""


def session_matches_context(session, year_id, exam, legacy_exam_type):
    if not session or session.academic_year_id != year_id:
        return False
    if exam:
        if session.exam_id == exam.id:
            return True
        return bool(legacy_exam_type and session.exam_type_id == legacy_exam_type.id)
    return bool(legacy_exam_type and session.exam_type_id == legacy_exam_type.id)


def attendance_session_context(year_id, exam_id, exam_type_id, exam_hall_id, exam_session_id):
    hall, exam, legacy_exam_type = hall_context(year_id, exam_id, exam_type_id, exam_hall_id)
    session = db.session.get(ExamSession, exam_session_id)
    if not session or not session_matches_context(session, year_id, exam, legacy_exam_type):
        raise ValueError("Fadhiga jadwalka la doortay kuma jiro sanadkan iyo imtixaankan.")
    return hall, exam, legacy_exam_type, session


def serialize_session(session, allowed_pairs=None):
    assignments = [
        assignment
        for assignment in session.subject_assignments
        if assignment.subject
        and (
            (assignment.academic_level_id, assignment.subject_id) in allowed_pairs
            if allowed_pairs is not None
            else assignment.subject.academic_level_id == assignment.academic_level_id
        )
    ]
    rendered_date = session.session_date.strftime("%A, %d %b %Y") if session.session_date else ""
    rendered_time = session.session_time.strftime("%I:%M %p") if session.session_time else ""
    return {
        "id": session.id,
        "date": session.session_date.isoformat() if session.session_date else "",
        "date_label": rendered_date,
        "sitting_label": session.sitting_label,
        "time": session.session_time.strftime("%H:%M") if session.session_time else "",
        "time_label": rendered_time,
        "label": " - ".join(part for part in [rendered_date, session.sitting_label, rendered_time] if part),
        "subject_count": len(assignments),
        # Sent with timetable data so the browser can omit subjects already
        # allocated to another sitting before the user ever clicks Save.
        "assignments": [
            {
                "level_id": assignment.academic_level_id,
                "subject_id": assignment.subject_id,
            }
            for assignment in assignments
        ],
    }


def subjects_by_session_level(session, allowed_pairs=None):
    """Read only the level-specific subject assignments configured in timetable setup."""
    rows = (
        ExamSessionSubject.query
        .join(Subject, ExamSessionSubject.subject_id == Subject.id)
        .filter(
            ExamSessionSubject.exam_session_id == session.id,
            Subject.is_active.is_(True),
        )
        .order_by(ExamSessionSubject.academic_level_id, ExamSessionSubject.id)
        .all()
    )
    grouped = {}
    subject_ids = {row.subject_id for row in rows}
    scoped_names = {}
    if subject_ids:
        names = (
            db.session.query(
                AcademicYearLevel.legacy_level_id,
                AcademicYearSubject.legacy_subject_id,
                AcademicYearSubject.name,
            )
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearSubject.academic_year_level_id)
            .filter(
                AcademicYearSubject.academic_year_id == session.academic_year_id,
                AcademicYearSubject.subject_kind == "exam",
                AcademicYearSubject.is_active.is_(True),
                AcademicYearSubject.legacy_subject_id.in_(subject_ids),
            )
            .all()
        )
        scoped_names = {
            (level_id, subject_id): name
            for level_id, subject_id, name in names
        }
    for row in rows:
        pair = (row.academic_level_id, row.subject_id)
        if allowed_pairs is not None:
            if pair not in allowed_pairs:
                continue
        elif row.subject.academic_level_id != row.academic_level_id:
            continue
        grouped.setdefault(row.academic_level_id, []).append(SimpleNamespace(
            id=row.subject.id,
            name=scoped_names.get(pair, row.subject.name),
        ))
    return grouped


def session_attendance_payload(hall, session, allowed_pairs=None):
    """Build the timetable-driven attendance roster grouped by student level/class."""
    subjects_by_level = subjects_by_session_level(session, allowed_pairs)
    enrollment_rows = (
        ExamHallEnrollment.query
        .filter_by(exam_hall_id=hall.id)
        .order_by(ExamHallEnrollment.created_at, ExamHallEnrollment.id)
        .all()
    )
    student_ids = [row.student_id for row in enrollment_rows]
    student_order = {student_id: index for index, student_id in enumerate(student_ids)}
    students = (
        student_enrollment_scope_query(session.academic_year_id)
        .filter(Student.id.in_(student_ids))
        .options(
            joinedload(Student.academic_class),
            joinedload(Student.school_class),
            joinedload(Student.academic_section),
        )
        .all()
        if student_ids else []
    )
    placements = resolve_student_placements(
        (student, session.academic_year_id) for student in students
    )
    level_ids = {
        placement.get("academic_level_id")
        for placement in placements.values()
        if placement and placement.get("academic_level_id")
    }
    levels_by_id = {
        level.id: level
        for level in AcademicLevel.query.filter(AcademicLevel.id.in_(level_ids)).all()
    } if level_ids else {}
    records = (
        AttendanceRecord.query
        .filter_by(exam_hall_id=hall.id, exam_session_id=session.id)
        .all()
    )
    record_map = {
        (record.student_id, record.subject_id): {
            "status": normalize_attendance_status(record.status),
            "note": record.note or "",
        }
        for record in records
    }
    tallies = {status["key"]: 0 for status in ATTENDANCE_STATUSES}
    groups = {}
    group_order = {}

    for student in students:
        placement = placements.get((student.id, session.academic_year_id)) or {}
        level_id = placement.get("academic_level_id") or effective_student_level_id(student)
        level = levels_by_id.get(level_id)
        class_name = placement.get("class_name") or student_class_name(student)
        class_id = placement.get("academic_class_id") or student.academic_class_id or student.class_id or 0
        key = (level_id or 0, class_id, class_name)
        group = groups.setdefault(key, {
            "level_id": level_id,
            "level_name": level.name if level else "Heer lama dejin",
            "class_name": class_name or "Fasal lama dejin",
            "subjects": [
                {"id": subject.id, "name": subject.name}
                for subject in subjects_by_level.get(level_id, [])
            ],
            "students": [],
        })
        assigned_order = student_order.get(student.id, len(student_order))
        group_order[key] = min(group_order.get(key, assigned_order), assigned_order)
        slots = []
        for subject in subjects_by_level.get(level_id, []):
            saved_record = record_map.get((student.id, subject.id), {})
            current_status = saved_record.get("status")
            if current_status not in tallies:
                current_status = None
            if current_status:
                tallies[current_status] += 1
            slots.append({
                "subject_id": subject.id,
                "subject_name": subject.name,
                "status": current_status,
                "note": saved_record.get("note", ""),
            })
        group["students"].append({
            "id": student.id,
            "student_code": student.student_code,
            "full_name": student.full_name,
            "class_name": class_name,
            "academic_class_id": placement.get("academic_class_id") or student.academic_class_id,
            "photo_url": stored_photo_url(student.photo_path),
            "slots": slots,
        })

    payload_groups = []
    for key, group in groups.items():
        group["students"].sort(key=lambda student: student_order.get(student["id"], len(student_order)))
        group["student_count"] = len(group["students"])
        group["subject_count"] = len(group["subjects"])
        payload_groups.append((group_order[key], group))
    payload_groups.sort(key=lambda item: item[0])
    payload_groups = [group for _, group in payload_groups]
    return payload_groups, tallies


def exam_scope_context(year_id, exam_id, exam_type_id):
    """Resolve the Results Hub examination scope without requiring a hall."""
    if not year_id or not (exam_id or exam_type_id):
        raise ValueError("Dooro sanad-dugsiyeedka iyo nooca imtixaanka.")
    if exam_id:
        exam = db.session.get(Exam, exam_id)
        if not exam or exam.academic_year_id != year_id:
            raise ValueError("Nooca imtixaanka la doortay kuma jiro sanadkan.")
        legacy = ExamType.query.filter_by(
            academic_year_id=year_id,
            name=exam.name,
        ).first()
        return exam, legacy
    legacy = db.session.get(ExamType, exam_type_id)
    if not legacy or legacy.academic_year_id != year_id:
        raise ValueError("Nooca imtixaanka la doortay kuma jiro sanadkan.")
    return None, legacy


def timetable_level_scope(year_id, exam=None):
    """Return only active year-aware levels and their mapped subjects.

    Timetable assignments still use legacy IDs for compatibility with the
    attendance tables, so the year-aware records are joined to their active
    legacy counterparts here.  This keeps old global levels out of a new
    year's timetable without changing the legacy schema.
    """
    query = (
        AcademicYearLevel.query
        .join(AcademicLevel, AcademicYearLevel.legacy_level_id == AcademicLevel.id)
        .filter(
            AcademicYearLevel.academic_year_id == year_id,
            AcademicYearLevel.is_active.is_(True),
            AcademicYearLevel.legacy_level_id.isnot(None),
            AcademicLevel.is_active.is_(True),
        )
    )
    if exam and exam.academic_level_id:
        query = query.filter(AcademicYearLevel.legacy_level_id == exam.academic_level_id)

    year_levels = query.order_by(
        AcademicYearLevel.sort_order,
        AcademicYearLevel.name,
        AcademicYearLevel.id,
    ).all()

    # A legacy-only year has no year-level bridge at all. Keep that historical
    # setup usable by deriving only the levels used by that year's students or
    # the exam's explicit level; never mix this fallback into a year that has
    # active year-aware levels.
    if not year_levels:
        legacy_level_ids = {
            student.academic_level_id
            for student in Student.query.filter_by(
                academic_year_id=year_id,
                is_active=True,
            ).all()
            if student.academic_level_id
        }
        if exam and exam.academic_level_id:
            legacy_level_ids.add(exam.academic_level_id)
        legacy_levels = (
            AcademicLevel.query
            .filter(AcademicLevel.id.in_(legacy_level_ids))
            .filter(AcademicLevel.is_active.is_(True))
            .order_by(AcademicLevel.sort_order, AcademicLevel.name, AcademicLevel.id)
            .all()
            if legacy_level_ids else []
        )
        return [
            {
                "id": level.id,
                "name": level.name,
                "subjects": [
                    {"id": subject.id, "name": subject.name}
                    for subject in Subject.query.filter_by(
                        academic_level_id=level.id,
                        is_active=True,
                    ).order_by(Subject.sort_order, Subject.name, Subject.id).all()
                ],
            }
            for level in legacy_levels
        ], {
            (level.id, subject.id)
            for level in legacy_levels
            for subject in Subject.query.filter_by(
                academic_level_id=level.id,
                is_active=True,
            ).all()
        }

    level_data = []
    allowed_pairs = set()
    year_level_ids = [year_level.id for year_level in year_levels]
    legacy_level_ids = [year_level.legacy_level_id for year_level in year_levels]
    year_subject_rows = (
        AcademicYearSubject.query
        .filter(
            AcademicYearSubject.academic_year_id == year_id,
            AcademicYearSubject.academic_year_level_id.in_(year_level_ids),
            AcademicYearSubject.subject_kind == "exam",
            AcademicYearSubject.is_active.is_(True),
        )
        .order_by(
            AcademicYearSubject.academic_year_level_id,
            AcademicYearSubject.sort_order,
            AcademicYearSubject.name,
            AcademicYearSubject.id,
        )
        .all()
    )
    year_subjects_by_level = {}
    for row in year_subject_rows:
        year_subjects_by_level.setdefault(row.academic_year_level_id, []).append(row)

    linked_subject_ids = {row.legacy_subject_id for row in year_subject_rows if row.legacy_subject_id}
    linked_subjects = (
        {
            subject.id: subject
            for subject in Subject.query.filter(Subject.id.in_(linked_subject_ids)).all()
        }
        if linked_subject_ids else {}
    )
    fallback_level_ids = [
        year_level.legacy_level_id
        for year_level in year_levels
        if not year_subjects_by_level.get(year_level.id)
    ]
    legacy_subjects_by_level = {}
    legacy_subjects = (
        Subject.query
        .filter(
            Subject.academic_level_id.in_(fallback_level_ids),
            Subject.is_active.is_(True),
        )
        .order_by(Subject.academic_level_id, Subject.sort_order, Subject.name, Subject.id)
        .all()
        if fallback_level_ids else []
    )
    for subject in legacy_subjects:
        legacy_subjects_by_level.setdefault(subject.academic_level_id, []).append(subject)

    for year_level in year_levels:
        subject_data = []
        scoped_subject_rows = year_subjects_by_level.get(year_level.id, [])
        if scoped_subject_rows:
            for year_subject in scoped_subject_rows:
                subject = linked_subjects.get(year_subject.legacy_subject_id)
                if not subject or not subject.is_active:
                    continue
                # The explicit year-level mapping is authoritative. Legacy
                # Subject identities can be shared across levels for compatibility.
                allowed_pairs.add((year_level.legacy_level_id, subject.id))
                subject_data.append({"id": subject.id, "name": year_subject.name})
        else:
            # Older attendance setups may already have a year-aware level but
            # no AcademicYearSubject bridge yet. Keep those schedules usable
            # while still restricting the level list to this year's records.
            for subject in legacy_subjects_by_level.get(year_level.legacy_level_id, []):
                allowed_pairs.add((year_level.legacy_level_id, subject.id))
                subject_data.append({"id": subject.id, "name": subject.name})
        level_data.append({
            "id": year_level.legacy_level_id,
            "name": year_level.name,
            "subjects": subject_data,
        })
    return level_data, allowed_pairs


def sessions_in_scope(year_id, exam, legacy_exam_type):
    query = ExamSession.query.filter_by(academic_year_id=year_id)
    if exam:
        filters = [ExamSession.exam_id == exam.id]
        if legacy_exam_type:
            # Canonical exam IDs take precedence; only legacy-only sessions
            # should be pulled in through the matching legacy type.
            filters.append(and_(
                ExamSession.exam_id.is_(None),
                ExamSession.exam_type_id == legacy_exam_type.id,
            ))
        query = query.filter(or_(*filters))
    elif legacy_exam_type:
        query = query.filter(
            ExamSession.exam_id.is_(None),
            ExamSession.exam_type_id == legacy_exam_type.id,
        )
    return (
        query.options(
            selectinload(ExamSession.subject_assignments).joinedload(ExamSessionSubject.subject)
        )
        .order_by(ExamSession.session_date, ExamSession.session_time, ExamSession.id)
        .all()
    )


def attendance_halls_in_scope(year_id, exam=None, legacy_exam_type=None):
    if exam:
        hall_scope = [ExamHall.exam_id == exam.id]
        if legacy_exam_type:
            hall_scope.append(and_(ExamHall.exam_id.is_(None), ExamHall.exam_type_id == legacy_exam_type.id))
        query = ExamHall.query.filter(ExamHall.is_active.is_(True), or_(*hall_scope))
    elif legacy_exam_type:
        query = ExamHall.query.filter(
            ExamHall.is_active.is_(True),
            ExamHall.exam_type_id == legacy_exam_type.id,
        )
    else:
        return []
    return query.options(
        joinedload(ExamHall.exam),
        joinedload(ExamHall.exam_type),
        joinedload(ExamHall.academic_class),
    ).order_by(ExamHall.sort_order, ExamHall.name).all()


def attendance_hall_payload(hall, exam_id=None):
    return {
        "id": hall.id,
        "name": hall.name,
        "academic_year_id": hall.academic_year_id or (
            hall.exam.academic_year_id if hall.exam else hall.exam_type.academic_year_id if hall.exam_type else None
        ),
        "academic_class_id": hall.academic_class_id,
        "academic_level_id": hall.academic_class.academic_level_id if hall.academic_class else None,
        "exam_id": hall.exam_id or exam_id,
        "exam_type_id": hall.exam_type_id,
    }


def sessions_for_attendance_hall(year_id, exam, legacy_exam_type, hall):
    sessions = sessions_in_scope(year_id, exam, legacy_exam_type)
    student_ids = [
        student_id
        for (student_id,) in db.session.query(ExamHallEnrollment.student_id)
        .filter_by(exam_hall_id=hall.id)
        .all()
    ]
    if not student_ids or not sessions:
        return []

    students = Student.query.options(
        joinedload(Student.academic_class),
        joinedload(Student.school_class),
        joinedload(Student.academic_section),
    ).filter(Student.id.in_(student_ids)).all()
    placements = resolve_student_placements(
        (student, year_id) for student in students
    )
    hall_level_ids = set()
    for student in students:
        placement = placements.get((student.id, year_id)) or {}
        level_id = placement.get("academic_level_id")
        if level_id is None:
            level_id = student.academic_level_id or (
                student.academic_class.academic_level_id if student.academic_class else None
            )
        if level_id is not None:
            hall_level_ids.add(level_id)

    return [
        session for session in sessions
        if {assignment.academic_level_id for assignment in session.subject_assignments} & hall_level_ids
    ]


def conflicting_subject_assignments(session, selected):
    """Find subjects already scheduled in another sitting of this exam scope.

    A level-subject pair may be examined only once for the same academic
    year/examination scope. Validate before replacing the current assignments.
    """
    if not selected:
        return []
    exam = db.session.get(Exam, session.exam_id) if session.exam_id else None
    legacy = db.session.get(ExamType, session.exam_type_id) if session.exam_type_id else None
    scoped_session_ids = [
        candidate.id
        for candidate in sessions_in_scope(session.academic_year_id, exam, legacy)
        if candidate.id != session.id
    ]
    if not scoped_session_ids:
        return []
    selected_pairs = set(selected)
    matches = (
        ExamSessionSubject.query
        .join(ExamSession, ExamSessionSubject.exam_session_id == ExamSession.id)
        .join(Subject, ExamSessionSubject.subject_id == Subject.id)
        .filter(ExamSessionSubject.exam_session_id.in_(scoped_session_ids))
        .all()
    )
    conflicts = {
        row.id: row for row in matches
        if (row.academic_level_id, row.subject_id) in selected_pairs
    }

    # The database's UNIQUE constraint uses this key, not the session-list
    # query above. Include any rows that the list may not expose so we can
    # report the real conflict before attempting the write.
    scope_key = scheduled_subject_scope_key(
        session.academic_year_id,
        session.exam_id,
        session.exam_type_id,
    )
    scope_matches = (
        ExamSessionSubject.query
        .join(Subject, ExamSessionSubject.subject_id == Subject.id)
        .filter(
            ExamSessionSubject.exam_scope_key == scope_key,
            ExamSessionSubject.exam_session_id != session.id,
            ExamSessionSubject.academic_level_id.in_({level_id for level_id, _ in selected}),
            ExamSessionSubject.subject_id.in_({subject_id for _, subject_id in selected}),
        )
        .all()
    )
    conflicts.update({
        row.id: row for row in scope_matches
        if (row.academic_level_id, row.subject_id) in selected_pairs
    })
    return list(conflicts.values())


def remove_orphaned_subject_assignments(session, selected):
    """Remove orphaned or mis-scoped reservations before enforcing uniqueness."""
    if not selected:
        return 0
    scope_key = scheduled_subject_scope_key(
        session.academic_year_id,
        session.exam_id,
        session.exam_type_id,
    )
    level_ids = {level_id for level_id, _ in selected}
    subject_ids = {subject_id for _, subject_id in selected}
    existing_session_ids = db.session.query(ExamSession.id)
    removed = (
        ExamSessionSubject.query
        .filter(
            ExamSessionSubject.exam_scope_key == scope_key,
            ExamSessionSubject.academic_level_id.in_(level_ids),
            ExamSessionSubject.subject_id.in_(subject_ids),
            ~ExamSessionSubject.exam_session_id.in_(existing_session_ids),
        )
        .delete(synchronize_session=False)
    )
    selected_pairs = set(selected)
    assignments = (
        ExamSessionSubject.query
        .filter(
            ExamSessionSubject.exam_scope_key == scope_key,
            ExamSessionSubject.exam_session_id != session.id,
            ExamSessionSubject.academic_level_id.in_(level_ids),
            ExamSessionSubject.subject_id.in_(subject_ids),
        )
        .options(joinedload(ExamSessionSubject.exam_session))
        .all()
    )
    for assignment in assignments:
        pair = (assignment.academic_level_id, assignment.subject_id)
        if pair not in selected_pairs:
            continue
        owner = assignment.exam_session
        if owner is None:
            db.session.delete(assignment)
            removed += 1
            continue
        owner_scope_key = scheduled_subject_scope_key(
            owner.academic_year_id,
            owner.exam_id,
            owner.exam_type_id,
        )
        if owner_scope_key == scope_key:
            continue
        duplicate = ExamSessionSubject.query.filter(
            ExamSessionSubject.exam_scope_key == owner_scope_key,
            ExamSessionSubject.academic_level_id == assignment.academic_level_id,
            ExamSessionSubject.subject_id == assignment.subject_id,
            ExamSessionSubject.id != assignment.id,
        ).first()
        assignment.exam_scope_key = (
            f"{owner_scope_key}:historic-{assignment.id}"
            if duplicate else owner_scope_key
        )
    return removed


# ==========================================
# PAGE ROUTES
# ==========================================

@attendance_bp.route("/")
def dashboard():
    """Redesigned Attendance Marking Page."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first() or (years[0] if years else None)
    
    exam_types = get_exam_types(current_year.id if current_year else None)
    
    return render_template(
        "admin/attendance.html",
        years=years,
        current_year=current_year,
        exam_types=exam_types,
        halls=[],
        statuses=ATTENDANCE_STATUSES,
        attendance_class_colors=ATTENDANCE_CLASS_COLORS,
        settings=get_settings(),
    )


@attendance_bp.route("/history")
def history():
    """Browse attendance records with filters scoped to saved history."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first()
    all_years = request.args.get("all_years") == "1"
    year_was_provided = "academic_year_id" in request.args
    year_id = request.args.get("academic_year_id", type=int)
    if not year_was_provided and not all_years:
        year_id = current_year.id if current_year else (years[0].id if years else None)
    if year_id not in {year.id for year in years}:
        year_id = None

    exam_options = []
    exam_query = Exam.query
    legacy_exam_query = ExamType.query
    if year_id:
        exam_query = exam_query.filter_by(academic_year_id=year_id)
        legacy_exam_query = legacy_exam_query.filter_by(academic_year_id=year_id)
    for exam in exam_query.options(joinedload(Exam.academic_year)).order_by(
        Exam.academic_year_id.desc(), Exam.sort_order, Exam.name
    ).all():
        year_label = exam.academic_year.name if exam.academic_year else ""
        label = f"{exam.name} · {year_label}" if not year_id else exam.name
        exam_options.append({"value": f"exam:{exam.id}", "label": f"{label} · Nidaam cusub"})
    for legacy_exam in legacy_exam_query.options(joinedload(ExamType.academic_year)).order_by(
        ExamType.academic_year_id.desc(), ExamType.sort_order, ExamType.name
    ).all():
        year_label = legacy_exam.academic_year.name if legacy_exam.academic_year else ""
        label = f"{legacy_exam.name} · {year_label}" if not year_id else legacy_exam.name
        exam_options.append({"value": f"legacy:{legacy_exam.id}", "label": f"{label} · Diiwaan hore"})
    exam_options.sort(key=lambda option: option["label"].casefold())

    selected_exam_scope = (request.args.get("exam_scope") or "").strip()
    selected_exam = None
    selected_legacy_exam = None
    if selected_exam_scope.startswith("exam:"):
        selected_exam = db.session.get(Exam, parse_int(selected_exam_scope.split(":", 1)[1]))
        if selected_exam and year_id and selected_exam.academic_year_id != year_id:
            selected_exam = None
    elif selected_exam_scope.startswith("legacy:"):
        selected_legacy_exam = db.session.get(ExamType, parse_int(selected_exam_scope.split(":", 1)[1]))
        if selected_legacy_exam and year_id and selected_legacy_exam.academic_year_id != year_id:
            selected_legacy_exam = None
    if not selected_exam and not selected_legacy_exam:
        selected_exam_scope = ""

    base_query = AttendanceRecord.query
    if year_id:
        base_query = base_query.filter(AttendanceRecord.academic_year_id == year_id)
    if selected_exam:
        matching_legacy = ExamType.query.filter_by(
            academic_year_id=selected_exam.academic_year_id,
            name=selected_exam.name,
        ).first()
        exam_scope_filters = [AttendanceRecord.exam_id == selected_exam.id]
        if matching_legacy:
            exam_scope_filters.append(and_(
                AttendanceRecord.exam_id.is_(None),
                AttendanceRecord.exam_type_id == matching_legacy.id,
            ))
        base_query = base_query.filter(or_(*exam_scope_filters))
    elif selected_legacy_exam:
        base_query = base_query.filter(AttendanceRecord.exam_type_id == selected_legacy_exam.id)

    hall_ids = [hall_id for (hall_id,) in base_query.with_entities(AttendanceRecord.exam_hall_id).filter(
        AttendanceRecord.exam_hall_id.isnot(None)
    ).distinct().all()]
    hall_options = ExamHall.query.filter(ExamHall.id.in_(hall_ids)).order_by(ExamHall.name).all() if hall_ids else []

    selected_hall_id = request.args.get("hall_id", type=int)
    session_options_query = base_query.with_entities(AttendanceRecord.exam_session_id).filter(
        AttendanceRecord.exam_session_id.isnot(None)
    )
    if selected_hall_id:
        session_options_query = session_options_query.filter(AttendanceRecord.exam_hall_id == selected_hall_id)
    session_ids = [session_id for (session_id,) in session_options_query.distinct().all()]
    session_options = ExamSession.query.filter(ExamSession.id.in_(session_ids)).order_by(
        ExamSession.session_date.desc(), ExamSession.session_time.desc(), ExamSession.id.desc()
    ).all() if session_ids else []

    selected_session_id = request.args.get("session_id", type=int)
    selected_status = request.args.get("status", "").strip().lower()
    status_keys = {item["key"] for item in ATTENDANCE_STATUSES}
    if selected_status not in status_keys:
        selected_status = ""
    search = (request.args.get("q") or "").strip()[:100]
    filter_errors = []

    def parse_filter_date(name, label):
        value = (request.args.get(name) or "").strip()
        if not value:
            return None, ""
        try:
            return date.fromisoformat(value), value
        except ValueError:
            filter_errors.append(f"Taariikhda {label} sax ma aha.")
            return None, ""

    start_date, start_date_value = parse_filter_date("date_from", "bilowga")
    end_date, end_date_value = parse_filter_date("date_to", "dhammaadka")
    if start_date and end_date and start_date > end_date:
        filter_errors.append("Taariikhda bilowgu kama dambayn karto taariikhda dhammaadka.")
        start_date = end_date = None
        start_date_value = end_date_value = ""

    query = base_query
    if selected_hall_id:
        query = query.filter(AttendanceRecord.exam_hall_id == selected_hall_id)
    if selected_session_id:
        query = query.filter(AttendanceRecord.exam_session_id == selected_session_id)
    if selected_status:
        query = query.filter(AttendanceRecord.status == selected_status)
    if start_date:
        query = query.filter(AttendanceRecord.attendance_date >= start_date)
    if end_date:
        query = query.filter(AttendanceRecord.attendance_date <= end_date)
    if search:
        query = query.join(Student).filter(or_(
            Student.full_name.ilike(f"%{search}%"),
            Student.student_code.ilike(f"%{search}%"),
        ))

    per_page = 50
    total_records = query.count()
    page = max(1, request.args.get("page", type=int) or 1)
    page_count = max(1, (total_records + per_page - 1) // per_page)
    page = min(page, page_count)
    records = query.options(
        joinedload(AttendanceRecord.student),
        joinedload(AttendanceRecord.exam_hall),
        joinedload(AttendanceRecord.subject),
        joinedload(AttendanceRecord.academic_year),
        joinedload(AttendanceRecord.exam_session),
        joinedload(AttendanceRecord.exam),
        joinedload(AttendanceRecord.exam_type),
        joinedload(AttendanceRecord.marked_by),
    ).order_by(
        AttendanceRecord.attendance_date.desc(),
        AttendanceRecord.recorded_at.desc(),
        AttendanceRecord.id.desc(),
    ).offset((page - 1) * per_page).limit(per_page).all()

    return render_template(
        "admin/attendance_history.html",
        active_area="history",
        years=years,
        exam_options=exam_options,
        hall_options=hall_options,
        session_options=session_options,
        statuses=ATTENDANCE_STATUSES,
        records=records,
        total_records=total_records,
        page=page,
        page_count=page_count,
        selected={
            "year_id": str(year_id or ""),
            "exam_scope": selected_exam_scope,
            "hall_id": str(selected_hall_id or ""),
            "session_id": str(selected_session_id or ""),
            "status": selected_status,
            "date_from": start_date_value,
            "date_to": end_date_value,
            "q": search,
            "all_years": all_years or (year_was_provided and not year_id),
        },
        filter_errors=filter_errors,
    )


@attendance_bp.route("/hall-roster")
def hall_roster():
    """Hall Exam Roster Page."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    return render_template(
        "admin/hall_roster.html",
        years=years,
    )


@attendance_bp.route("/summary-report")
def summary_report():
    return render_template(
        "admin/attendance_summary_report.html",
        **_attendance_summary_page_context(request.args),
    )


@attendance_bp.route("/summary-report.pdf")
def summary_report_pdf():
    context = _attendance_summary_page_context(request.args, force_generate=True)
    if context["filter_errors"] or not context["report"]:
        abort(400, description="Buuxi filtarrada warbixinta ka hor dhoofinta PDF-ka.")
    pdf = build_attendance_summary_pdf(context)
    return send_file(
        BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="Warbixinta-Guud-Xaadirinta.pdf",
    )


def _attendance_summary_page_context(args, force_generate=False):
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first()
    year_id = args.get("academic_year_id", type=int)
    if year_id not in {year.id for year in years}:
        year_id = current_year.id if current_year else (years[0].id if years else None)

    exam_options = []
    selected_exam_scope = (args.get("exam_scope") or "").strip()
    exam = None
    legacy_exam_type = None
    if year_id:
        for item in get_exam_types(year_id):
            source = "legacy" if isinstance(item, ExamType) else "exam"
            exam_options.append({
                "value": f"{source}:{item.id}",
                "label": item.name,
                "name": item.name,
                "id": item.id,
                "source": source,
            })
        if selected_exam_scope.startswith("exam:"):
            exam = db.session.get(Exam, parse_int(selected_exam_scope.split(":", 1)[1]))
            if not exam or exam.academic_year_id != year_id or not exam.is_active:
                exam = None
                selected_exam_scope = ""
            else:
                legacy_exam_type = ExamType.query.filter_by(
                    academic_year_id=year_id,
                    name=exam.name,
                ).first()
        elif selected_exam_scope.startswith("legacy:"):
            legacy_exam_type = db.session.get(
                ExamType, parse_int(selected_exam_scope.split(":", 1)[1])
            )
            if (
                not legacy_exam_type
                or legacy_exam_type.academic_year_id != year_id
                or not legacy_exam_type.is_active
            ):
                legacy_exam_type = None
                selected_exam_scope = ""
        else:
            selected_exam_scope = ""

    halls = attendance_halls_in_scope(year_id, exam, legacy_exam_type) if (exam or legacy_exam_type) else []
    valid_hall_ids = {hall.id for hall in halls}
    requested_hall_ids = {
        hall_id for value in args.getlist("hall_ids")
        if (hall_id := parse_int(value)) in valid_hall_ids
    }
    generate = force_generate or args.get("generate") == "1"
    selected_all_halls = args.get("all_halls") == "1" or (
        not generate and "all_halls" not in args
    )
    selected_hall_ids = (
        valid_hall_ids if selected_all_halls else requested_hall_ids
    )

    filter_errors = []
    start_date_value = (args.get("date_from") or "").strip()
    end_date_value = (args.get("date_to") or "").strip()
    start_date = end_date = None
    for field, value, label in (
        ("date_from", start_date_value, "bilowga"),
        ("date_to", end_date_value, "dhammaadka"),
    ):
        if not value:
            if generate:
                filter_errors.append(f"Geli taariikhda {label}.")
            continue
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            filter_errors.append(f"Taariikhda {label} sax ma aha.")
            continue
        if field == "date_from":
            start_date = parsed
        else:
            end_date = parsed

    if start_date and end_date and start_date > end_date:
        filter_errors.append("Taariikhda bilowgu kama dambayn karto taariikhda dhammaadka.")
    if generate and not (exam or legacy_exam_type):
        filter_errors.append("Dooro nooca imtixaanka.")
    if generate and not selected_hall_ids:
        filter_errors.append("Dooro ugu yaraan hal hall ama calaamadee dhammaan halls-ka.")
    if generate and not halls:
        filter_errors.append("Imtixaankan kuma jiro hall firfircoon.")

    report = None
    if generate and not filter_errors:
        report = _attendance_summary_report_data(
            year_id=year_id,
            exam=exam,
            legacy_exam_type=legacy_exam_type,
            halls=[hall for hall in halls if hall.id in selected_hall_ids],
            start_date=start_date,
            end_date=end_date,
        )

    return {
        "active_area": "report",
        "years": years,
        "selected_year_id": year_id,
        "exam_options": exam_options,
        "selected_exam_scope": selected_exam_scope,
        "halls": halls,
        "selected_hall_ids": selected_hall_ids,
        "selected_all_halls": selected_all_halls,
        "date_from": start_date_value,
        "date_to": end_date_value,
        "filter_errors": filter_errors,
        "report": report,
        "settings": get_settings(),
        "today": date.today(),
    }


def _attendance_summary_report_data(*, year_id, exam, legacy_exam_type, halls, start_date, end_date):
    sessions = [
        session for session in sessions_in_scope(year_id, exam, legacy_exam_type)
        if start_date <= session.session_date <= end_date
    ]
    _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    hall_session_ids = {
        hall.id: {
            session.id for session in sessions_for_attendance_hall(
                year_id, exam, legacy_exam_type, hall
            )
        }
        for hall in halls
    }
    status_keys = [item["key"] for item in ATTENDANCE_STATUSES]
    totals = {
        "students": 0,
        "slots": 0,
        "marked": 0,
        "sessions": 0,
        "absent_students": 0,
        **{key: 0 for key in status_keys},
    }
    rows = []
    absent_students = []
    hall_totals = {
        hall.id: {"hall": hall.name, "sessions": 0, "unmarked": 0, **{
            key: 0 for key in ("students", "slots", "marked", "absent_students", *status_keys)
        }}
        for hall in halls
    }
    day_totals = {}

    for session in sessions:
        for hall in halls:
            if session.id not in hall_session_ids[hall.id]:
                continue
            groups, tallies = session_attendance_payload(hall, session, allowed_pairs)
            students = [student for group in groups for student in group["students"]]
            slot_count = sum(len(student["slots"]) for student in students)
            marked_count = sum(
                1 for student in students for slot in student["slots"]
                if slot.get("status")
            )
            absent_count = 0
            for group in groups:
                for student in group["students"]:
                    absent_subjects = [
                        slot["subject_name"] for slot in student["slots"]
                        if slot.get("status") == "absent"
                    ]
                    if not absent_subjects:
                        continue
                    absent_count += 1
                    absent_students.append({
                        "date": session.session_date,
                        "session": session.sitting_label,
                        "time": session.session_time,
                        "hall": hall.name,
                        "student_code": student.get("student_code") or "—",
                        "full_name": student.get("full_name") or "Arday aan la aqoon",
                        "level_name": group.get("level_name") or "—",
                        "class_name": student.get("class_name") or group.get("class_name") or "—",
                        "subjects": absent_subjects,
                    })

            row = {
                "session_id": session.id,
                "date": session.session_date,
                "session": session.sitting_label,
                "time": session.session_time,
                "hall": hall.name,
                "students": len(students),
                "slots": slot_count,
                "marked": marked_count,
                "unmarked": slot_count - marked_count,
                "absent_students": absent_count,
                **{key: tallies.get(key, 0) for key in status_keys},
            }
            rows.append(row)
            aggregate_keys = ("students", "slots", "marked", "absent_students", *status_keys)
            for key in aggregate_keys:
                totals[key] += row[key]
            totals["sessions"] += 1
            for aggregate in (
                hall_totals.setdefault(hall.id, {"hall": hall.name, "sessions": 0, **{key: 0 for key in aggregate_keys}}),
                day_totals.setdefault(session.session_date, {"date": session.session_date, "sessions": 0, **{key: 0 for key in aggregate_keys}}),
            ):
                aggregate["sessions"] += 1
                for key in aggregate_keys:
                    aggregate[key] += row[key]

    absent_students.sort(key=lambda row: (
        row["date"], row["hall"].casefold(), row["class_name"].casefold(),
        row["full_name"].casefold(),
    ))
    totals["unmarked"] = totals["slots"] - totals["marked"]
    for aggregate in [*hall_totals.values(), *day_totals.values()]:
        aggregate["unmarked"] = aggregate["slots"] - aggregate["marked"]
    return {
        "rows": rows,
        "totals": totals,
        "hall_totals": list(hall_totals.values()),
        "day_totals": [day_totals[key] for key in sorted(day_totals)],
        "absent_students": absent_students,
        "session_count": len({row["session_id"] for row in rows}),
        "hall_count": len(halls),
        "start_date": start_date,
        "end_date": end_date,
        "year": db.session.get(AcademicYear, year_id),
        "exam_name": exam.name if exam else legacy_exam_type.name,
        "hall_names": sorted({hall.name for hall in halls}, key=str.casefold),
    }


@attendance_bp.route("/timetable")
def timetable():
    """Timetable setup is the single source for attendance session subjects."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first() or (years[0] if years else None)
    exam_types = [
        {
            "id": exam.id,
            "name": exam.name,
            "source": "legacy" if isinstance(exam, ExamType) else "exam",
        }
        for exam in get_exam_types(current_year.id if current_year else None)
    ]
    return render_template(
        "admin/exam_timetable.html",
        years=years,
        current_year=current_year,
        exam_types=exam_types,
        settings=get_settings(),
    )


# ==========================================
# JSON API ENDPOINTS
# ==========================================

@attendance_bp.route("/api/exam-types")
def api_exam_types():
    year_id = request.args.get("academic_year_id", type=int)
    types = get_exam_types(year_id)
    return jsonify({
        "success": True,
        "exam_types": [
            {
                "id": exam.id,
                "name": exam.name,
                "source": "legacy" if isinstance(exam, ExamType) else "exam",
            }
            for exam in types
        ]
    })


@attendance_bp.route("/api/sessions")
def api_sessions():
    year_id = request.args.get("academic_year_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    exam_type_id = request.args.get("exam_type_id", type=int)
    hall_id = request.args.get("exam_hall_id", type=int)
    try:
        exam, legacy_exam_type = exam_scope_context(year_id, exam_id, exam_type_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc), "sessions": []}), 400

    if hall_id:
        hall = db.session.get(ExamHall, hall_id)
        if not hall or not hall.is_active:
            return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo.", "sessions": []}), 404
        try:
            # Validate the full year -> exam -> hall relationship before using
            # the hall roster to filter timetable sessions.
            hall_context(year_id, exam_id, exam_type_id, hall_id)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc), "sessions": []}), 400
        sessions = sessions_for_attendance_hall(year_id, exam, legacy_exam_type, hall)
    else:
        sessions = sessions_in_scope(year_id, exam, legacy_exam_type)

    _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    return jsonify({"success": True, "sessions": [serialize_session(session, allowed_pairs) for session in sessions]})


@attendance_bp.route("/api/timetable-data")
def api_timetable_data():
    year_id = request.args.get("academic_year_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    exam_type_id = request.args.get("exam_type_id", type=int)
    try:
        exam, legacy_exam_type = exam_scope_context(year_id, exam_id, exam_type_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc), "sessions": [], "levels": []}), 400

    level_data, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    unassigned_subject_count = AcademicYearSubject.query.filter(
        AcademicYearSubject.academic_year_id == year_id,
        AcademicYearSubject.subject_kind == "exam",
        AcademicYearSubject.is_active.is_(True),
        AcademicYearSubject.legacy_subject_id.is_(None),
    ).count()
    return jsonify({
        "success": True,
        "sessions": [
            serialize_session(session, allowed_pairs)
            for session in sessions_in_scope(year_id, exam, legacy_exam_type)
        ],
        "levels": level_data,
        "unassigned_subject_count": unassigned_subject_count,
    })


@attendance_bp.route("/api/sessions/<int:session_id>")
def api_session_detail(session_id):
    session = db.session.get(ExamSession, session_id)
    if not session:
        return jsonify({"success": False, "error": "Fadhiga jadwalka lama heli karo."}), 404
    exam, _ = exam_scope_context(session.academic_year_id, session.exam_id, session.exam_type_id)
    _, allowed_pairs = timetable_level_scope(session.academic_year_id, exam=exam)
    return jsonify({
        "success": True,
        "session": serialize_session(session, allowed_pairs),
        "assignments": [
            {"level_id": assignment.academic_level_id, "subject_id": assignment.subject_id}
            for assignment in session.subject_assignments
            if assignment.subject
            and (assignment.academic_level_id, assignment.subject_id) in allowed_pairs
        ],
    })


@attendance_bp.route("/api/sessions", methods=["POST"])
def api_create_session():
    data = request.get_json(silent=True) or request.form
    year_id = parse_int(data.get("academic_year_id"))
    exam_id = parse_int(data.get("exam_id"))
    exam_type_id = parse_int(data.get("exam_type_id"))
    date_value = (data.get("date") or "").strip()
    sitting_label = (data.get("sitting_label") or "").strip()
    time_value = (data.get("time") or "").strip()

    try:
        exam, legacy_exam_type = exam_scope_context(year_id, exam_id, exam_type_id)
        session_date = datetime.strptime(date_value, "%Y-%m-%d").date()
        session_time = datetime.strptime(time_value, "%H:%M").time() if time_value else None
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc) or "Taariikhda ama waqtiga lama aqoonsan."}), 400

    if not sitting_label:
        return jsonify({"success": False, "error": "Magaca fadhiga waa waajib."}), 400

    duplicate = ExamSession.query.filter_by(
        academic_year_id=year_id,
        exam_id=exam.id if exam else None,
        exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
        session_date=session_date,
        sitting_label=sitting_label,
        session_time=session_time,
    ).first()
    if duplicate:
        return jsonify({"success": False, "error": "Fadhigan jadwalka hore ayaa loo sameeyey."}), 409

    session = ExamSession(
        academic_year_id=year_id,
        exam_id=exam.id if exam else None,
        exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
        session_date=session_date,
        sitting_label=sitting_label,
        session_time=session_time,
    )
    db.session.add(session)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "error": "Fadhigan jadwalka hore ayaa loo sameeyey."}), 409
    audit("Exam Timetable", f"Created session '{session.sitting_label}' for {session.session_date.isoformat()}")
    return jsonify({"success": True, "session": serialize_session(session)})


@attendance_bp.route("/api/sessions/<int:session_id>/subjects", methods=["PUT", "POST"])
def api_update_session_subjects(session_id):
    data = request.get_json(silent=True) or request.form
    session = db.session.get(ExamSession, session_id)
    if not session:
        return jsonify({"success": False, "error": "Fadhiga jadwalka lama heli karo."}), 404

    assignments = data.get("assignments") or []
    if not isinstance(assignments, list):
        return jsonify({"success": False, "error": "Liiska maadooyinka lama aqoonsan."}), 400

    exam, _ = exam_scope_context(session.academic_year_id, session.exam_id, session.exam_type_id)
    _, allowed_pairs = timetable_level_scope(session.academic_year_id, exam=exam)
    selected = []
    seen_pairs = set()
    for assignment in assignments:
        level_id = parse_int(assignment.get("level_id")) if isinstance(assignment, dict) else None
        subject_id = parse_int(assignment.get("subject_id")) if isinstance(assignment, dict) else None
        subject = db.session.get(Subject, subject_id) if subject_id else None
        level = db.session.get(AcademicLevel, level_id) if level_id else None
        if not level or not level.is_active or not subject or not subject.is_active:
            return jsonify({"success": False, "error": "Waxaa jira level ama maado aan sax ahayn."}), 400
        if (level.id, subject.id) not in allowed_pairs:
            return jsonify({"success": False, "error": "Level-ka ama maadada laguma dejin sanadkan iyo nooca imtixaankan."}), 400
        pair = (level.id, subject.id)
        if pair in seen_pairs:
            return jsonify({"success": False, "error": "Maado isku mid ah laba jeer looma dejin karo fadhigan."}), 400
        seen_pairs.add(pair)
        selected.append((level.id, subject.id))

    remove_orphaned_subject_assignments(session, selected)
    conflicts = conflicting_subject_assignments(session, selected)
    if conflicts:
        details = set()
        for row in conflicts:
            if not row.subject:
                continue
            owner = row.exam_session
            location = ""
            if owner:
                location = owner.sitting_label or "Fadhi aan magac lahayn"
                if owner.session_date:
                    location = f"{location}, {owner.session_date.strftime('%d %b %Y')}"
            details.add(f"{row.subject.name} ({location})" if location else row.subject.name)
        return jsonify({
            "success": False,
            "error": "Maadadan hore ayaa loogu dejiyey fadhi kale oo imtixaankan ah: "
            f"{', '.join(sorted(details)) or 'Fadhi kale'}."
        }), 409

    def persist_assignments():
        ExamSessionSubject.query.filter_by(exam_session_id=session.id).delete(synchronize_session="fetch")
        db.session.add_all([
            ExamSessionSubject(
                exam_session_id=session.id,
                academic_level_id=level_id,
                subject_id=subject_id,
                exam_scope_key=scheduled_subject_scope_key(
                    session.academic_year_id,
                    session.exam_id,
                    session.exam_type_id,
                ),
            )
            for level_id, subject_id in selected
        ])
        db.session.commit()

    try:
        persist_assignments()
    except IntegrityError:
        db.session.rollback()
        try:
            # Older production schemas may still have a per-session subject
            # constraint, which blocks a shared subject across two levels.
            from .schema_compat import ensure_exam_session_subject_scope_constraints

            ensure_exam_session_subject_scope_constraints()
            db.session.expire_all()
            session = db.session.get(ExamSession, session_id)
            persist_assignments()
        except IntegrityError:
            db.session.rollback()
            # Keep the database scope constraint as the final guard against a
            # real duplicate assignment or a concurrent write.
            return jsonify({
                "success": False,
                "error": "Maadadan hore ayaa loogu dejiyey fadhi kale oo imtixaankan ah."
            }), 409
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Timetable subject constraint repair failed for session %s", session_id
            )
            return jsonify({
                "success": False,
                "error": "Jadwalka maadooyinka lama kaydin karin. Fadlan dib u tijaabi."
            }), 500
    audit("Exam Timetable", f"Updated subjects for session '{session.sitting_label}'")
    db.session.refresh(session)
    db.session.expire(session, ["subject_assignments"])
    return jsonify({"success": True, "session": serialize_session(session, allowed_pairs)})


@attendance_bp.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    session = db.session.get(ExamSession, session_id)
    if not session:
        return jsonify({"success": False, "error": "Fadhiga jadwalka lama heli karo."}), 404
    label = session.sitting_label
    try:
        # Preserve attendance history while avoiding ORM-loading every row
        # linked to this sitting during the parent delete.
        AttendanceRecord.query.filter_by(exam_session_id=session.id).update(
            {AttendanceRecord.exam_session_id: None},
            synchronize_session=False,
        )
        ExamSessionSubject.query.filter_by(exam_session_id=session.id).delete(
            synchronize_session=False
        )
        db.session.delete(session)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unable to delete exam timetable session: %s", exc)
        return jsonify({"success": False, "error": "Fadhiga jadwalka lama tirtiri karo."}), 500
    audit("Exam Timetable", f"Deleted session '{label}'")
    return jsonify({"success": True, "message": "Fadhiga jadwalka si sax ah ayaa loo tirtiray."})


@attendance_bp.route("/api/halls")
def api_halls():
    year_id = request.args.get("academic_year_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    legacy_exam_type_id = request.args.get("exam_type_id", type=int)

    if not year_id or not (exam_id or legacy_exam_type_id):
        return jsonify({"success": True, "halls": []})
    exam = db.session.get(Exam, exam_id) if exam_id else None
    if exam_id and (not exam or exam.academic_year_id != year_id):
        return jsonify({"success": True, "halls": []})
    legacy_exam_type = (
        ExamType.query.filter_by(academic_year_id=exam.academic_year_id, name=exam.name).first()
        if exam else db.session.get(ExamType, legacy_exam_type_id)
    )
    if not exam and (
        not legacy_exam_type or legacy_exam_type.academic_year_id != year_id
    ):
        return jsonify({"success": True, "halls": []})
    halls = attendance_halls_in_scope(year_id, exam, legacy_exam_type)
    return jsonify({
        "success": True,
        "halls": [attendance_hall_payload(hall, exam.id if exam else None) for hall in halls]
    })


@attendance_bp.route("/api/attendance-context")
def api_attendance_context():
    year_id = request.args.get("academic_year_id", type=int)
    requested_exam_id = request.args.get("exam_id", type=int)
    requested_exam_type_id = request.args.get("exam_type_id", type=int)
    requested_hall_id = request.args.get("exam_hall_id", type=int)
    requested_session_id = request.args.get("exam_session_id", type=int)
    if not year_id or not db.session.get(AcademicYear, year_id):
        return jsonify({"success": False, "error": "Sanad-dugsiyeedka lama heli karo."}), 400

    exam_types = get_exam_types(year_id)
    exam_options = [
        {
            "id": item.id,
            "name": item.name,
            "source": "legacy" if isinstance(item, ExamType) else "exam",
        }
        for item in exam_types
    ]
    selected_exam = next((
        item for item in exam_types
        if (isinstance(item, ExamType) and item.id == requested_exam_type_id)
        or (isinstance(item, Exam) and item.id == requested_exam_id)
    ), None) or (exam_types[0] if exam_types else None)
    exam = selected_exam if selected_exam and not isinstance(selected_exam, ExamType) else None
    legacy_exam_type = selected_exam if isinstance(selected_exam, ExamType) else (
        ExamType.query.filter_by(academic_year_id=year_id, name=exam.name).first() if exam else None
    )
    selected_exam_value = next((
        option for option in exam_options
        if selected_exam and option["id"] == selected_exam.id
        and option["source"] == ("legacy" if isinstance(selected_exam, ExamType) else "exam")
    ), None)

    halls = attendance_halls_in_scope(year_id, exam, legacy_exam_type)
    selected_hall = next((hall for hall in halls if hall.id == requested_hall_id), None)
    if selected_hall is None and halls:
        selected_hall = halls[0]

    sessions = []
    selected_session = None
    groups = []
    tallies = {status["key"]: 0 for status in ATTENDANCE_STATUSES}
    if selected_hall:
        try:
            hall_context(
                year_id,
                exam.id if exam else None,
                legacy_exam_type.id if legacy_exam_type else None,
                selected_hall.id,
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        scoped_sessions = sessions_for_attendance_hall(
            year_id, exam, legacy_exam_type, selected_hall
        )
        _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
        sessions = [serialize_session(session, allowed_pairs) for session in scoped_sessions]
        selected_session = next((
            session for session in scoped_sessions if session.id == requested_session_id
        ), None) or (scoped_sessions[0] if scoped_sessions else None)
        if selected_session:
            groups, tallies = session_attendance_payload(
                selected_hall, selected_session, allowed_pairs
            )

    return jsonify({
        "success": True,
        "exam_types": exam_options,
        "halls": [attendance_hall_payload(hall, exam.id if exam else None) for hall in halls],
        "sessions": sessions,
        "selected_exam": selected_exam_value,
        "selected_hall_id": selected_hall.id if selected_hall else None,
        "selected_session_id": selected_session.id if selected_session else None,
        "groups": groups,
        "tallies": tallies,
    })


@attendance_bp.route("/api/halls/create", methods=["POST"])
def api_create_hall():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    exam_id = parse_int(data.get("exam_id"))
    legacy_exam_type_id = parse_int(data.get("exam_type_id"))
    academic_year_id = parse_int(data.get("academic_year_id"))
    academic_class_id = data.get("academic_class_id")

    if not name:
        return jsonify({"success": False, "error": "Magaca Hall-ka waa inuu buuxsamaa."}), 400

    exam = db.session.get(Exam, exam_id) if exam_id else None
    legacy_exam_type = db.session.get(ExamType, legacy_exam_type_id) if legacy_exam_type_id else None
    context = exam or legacy_exam_type
    if not context or context.academic_year_id != academic_year_id:
        return jsonify({"success": False, "error": "Dooro sanad-dugsiyeed iyo nooca imtixaanka saxda ah."}), 400
    academic_class = db.session.get(AcademicClass, parse_int(academic_class_id)) if academic_class_id else None
    if not academic_class or not academic_class.is_active:
        return jsonify({"success": False, "error": "Dooro fasalka ay ardeydu ka imanayaan."}), 400

    # Auto-generate a unique code
    code_base = name.upper().replace(" ", "_")[:20]
    unique_code = f"HALL_{code_base}_{uuid4().hex[:8].upper()}"

    hall = ExamHall(
        name=name,
        code=unique_code,
        exam_id=exam.id if exam else None,
        exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
        academic_year_id=academic_year_id,
        academic_class_id=academic_class.id,
        is_active=True,
    )
    db.session.add(hall)
    db.session.flush()
    add_default_hall_subjects(hall)
    db.session.commit()
    audit("Hall Creation", f"Created new Exam Hall '{hall.name}'")

    return jsonify({
        "success": True,
        "hall": {
            "id": hall.id,
            "name": hall.name,
            "academic_year_id": hall.academic_year_id,
            "academic_class_id": hall.academic_class_id,
            "exam_id": hall.exam_id,
            "exam_type_id": hall.exam_type_id,
        }
    })


@attendance_bp.route("/api/halls/delete", methods=["POST"])
def api_delete_hall():
    data = request.get_json(silent=True) or request.form
    exam_hall_id = parse_int(data.get("exam_hall_id"))

    if not exam_hall_id:
        return jsonify({"success": False, "error": "Dooro hall-ka la tirtirayo."}), 400

    hall = db.session.get(ExamHall, exam_hall_id)
    if not hall or not hall.is_active:
        return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo."}), 404

    hall.is_active = False
    db.session.commit()
    audit("Hall Delete", f"Deactivated Exam Hall '{hall.name}'")

    return jsonify({
        "success": True,
        "hall": {"id": hall.id, "name": hall.name}
    })


@attendance_bp.route("/api/levels-and-classes")
def api_levels_and_classes():
    academic_year_id = request.args.get("academic_year_id", type=int)
    if not academic_year_id or not db.session.get(AcademicYear, academic_year_id):
        return jsonify({"success": True, "levels": []})

    # The roster selectors use legacy IDs for compatibility with the hall
    # endpoints, but the available options must come only from this year's
    # year-aware hierarchy mappings.
    res = []
    for year_level in year_levels(academic_year_id):
        legacy_level = year_level.legacy_level
        if not legacy_level or not legacy_level.is_active or not year_level.legacy_level_id:
            continue
        classes = []
        for year_class in year_classes(year_level.id):
            legacy_class = year_class.legacy_class
            if legacy_class and legacy_class.is_active and year_class.legacy_class_id:
                classes.append({"id": year_class.legacy_class_id, "name": year_class.name})
        res.append({
            "id": year_level.legacy_level_id,
            "name": year_level.name,
            "classes": classes,
        })
    return jsonify({"success": True, "levels": res})


@attendance_bp.route("/api/hall-roster-data")
def api_hall_roster_data():
    exam_hall_id = request.args.get("exam_hall_id", type=int)
    level_id = request.args.get("academic_level_id", type=int)
    class_id = request.args.get("academic_class_id", type=int)
    q = (request.args.get("q") or "").strip().lower()
    offset = max(request.args.get("offset", default=0, type=int) or 0, 0)
    requested_limit = request.args.get("limit", default=100, type=int)
    limit = min(max(requested_limit if requested_limit is not None else 100, 1), 100)
    include_assigned = request.args.get("include_assigned", "1") != "0"

    if not exam_hall_id:
        return jsonify({"success": True, "assigned_students": [], "available_students": []})

    hall = db.session.get(ExamHall, exam_hall_id)
    exam_context = hall_exam_context(hall) if hall else None
    if not hall or not hall.is_active or not exam_context:
        return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo."}), 404
    if class_id:
        selected_class = db.session.get(AcademicClass, class_id)
        selected_level_id = level_id or getattr(exam_context, "academic_level_id", None)
        class_scope_query = (
            AcademicYearClass.query
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
            .filter(
                AcademicYearLevel.academic_year_id == exam_context.academic_year_id,
                AcademicYearLevel.is_active.is_(True),
                AcademicYearClass.legacy_class_id == class_id,
                AcademicYearClass.is_active.is_(True),
            )
        )
        if selected_level_id:
            class_scope_query = class_scope_query.filter(
                AcademicYearLevel.legacy_level_id == selected_level_id
            )
        if not selected_class or not selected_class.is_active or not class_scope_query.first():
            return jsonify({"success": False, "error": "Fasalka la doortay kuma jiro heerkan."}), 400

    # Assigned students for this hall
    # Enrollment order is the source of truth for class-group order.  This keeps
    # the roster in the order classes were added to the hall, instead of letting
    # an unordered student query silently alphabetize or interleave groups.
    assigned_enrollment_query = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id)
    if include_assigned:
        enrollments = assigned_enrollment_query.order_by(
            ExamHallEnrollment.created_at, ExamHallEnrollment.id
        ).all()
        assigned_student_ids = [enrollment.student_id for enrollment in enrollments]
    else:
        enrollments = []
        assigned_student_ids = None
    try:
        pool_scope_query = attendance_student_scope_query(
            exam_context.academic_year_id,
            exam=exam_context,
            level_id=level_id,
            class_id=class_id,
        )
        assigned_scope_query = (
            attendance_student_scope_query(exam_context.academic_year_id, exam=exam_context)
            if include_assigned and assigned_student_ids else None
        )
    except EnrollmentValidationError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    pool_scope_query = pool_scope_query.options(
        joinedload(Student.academic_class),
        joinedload(Student.school_class),
        joinedload(Student.academic_section),
    )
    if assigned_scope_query is not None:
        assigned_scope_query = assigned_scope_query.options(
            joinedload(Student.academic_class),
            joinedload(Student.school_class),
            joinedload(Student.academic_section),
        )

    assigned_students_raw = {}
    if include_assigned and assigned_student_ids:
        assigned_students_raw = {
            student.id: student
            for student in assigned_scope_query.filter(Student.id.in_(assigned_student_ids)).all()
        }
    
    # Available pool: system students matching level/class filters NOT YET assigned
    pool_query = pool_scope_query.filter(Student.is_active.is_(True))

    pool_query = pool_query.filter(
        ~Student.id.in_(db.session.query(ExamHallEnrollment.student_id).filter_by(exam_hall_id=exam_hall_id))
    )

    if q:
        pool_query = pool_query.filter(or_(Student.student_code.ilike(f"%{q}%"), Student.full_name.ilike(f"%{q}%")))

    available_page = pool_query.order_by(Student.full_name, Student.id).offset(offset).limit(limit + 1).all()
    has_more = len(available_page) > limit
    available_students_raw = available_page[:limit]

    # Placement resolution is batched: resolving each row independently adds
    # several hierarchy queries per student and makes large classes very slow.
    placements = resolve_student_placements(
        (student, exam_context.academic_year_id)
        for student in [*assigned_students_raw.values(), *available_students_raw]
    )

    assigned_list = []
    for enrollment in enrollments:
        s = assigned_students_raw.get(enrollment.student_id)
        if not s:
            continue
        placement = placements.get((s.id, exam_context.academic_year_id)) or {}
        cls_name = placement.get("class_name") or (
            s.academic_class.name if s.academic_class else (s.school_class.name if s.school_class else (s.level or ""))
        )
        assigned_list.append({
            "id": s.id,
            "student_code": s.student_code,
            "full_name": s.full_name,
            "class_name": cls_name,
            "academic_class_id": placement.get("academic_class_id") or s.academic_class_id,
            "assigned_hall_id": hall.id,
            "assigned_hall_name": hall.name,
            "is_current_hall": True,
            "photo_url": stored_photo_url(s.photo_path),
        })

    available_ids = [student.id for student in available_students_raw]
    assignment_map = {}
    if available_ids:
        same_context_enrollments = (
            ExamHallEnrollment.query
            .join(ExamHall, ExamHallEnrollment.exam_hall_id == ExamHall.id)
            .options(joinedload(ExamHallEnrollment.exam_hall))
            .filter(
                ExamHallEnrollment.student_id.in_(available_ids),
                ExamHall.is_active.is_(True),
                or_(*same_exam_hall_filters(hall)),
                ExamHallEnrollment.exam_hall_id != exam_hall_id,
            )
            .all()
        )
        assignment_map = {
            enrollment.student_id: enrollment.exam_hall
            for enrollment in same_context_enrollments
            if enrollment.exam_hall
        }

    available_list = []
    for s in available_students_raw:
        placement = placements.get((s.id, exam_context.academic_year_id)) or {}
        cls_name = placement.get("class_name") or (s.academic_class.name if s.academic_class else (s.school_class.name if s.school_class else (s.level or "")))
        assigned_hall = assignment_map.get(s.id)
        available_list.append({
            "id": s.id,
            "student_code": s.student_code,
            "full_name": s.full_name,
            "class_name": cls_name,
            "academic_class_id": placement.get("academic_class_id") or s.academic_class_id,
            "assigned_hall_id": assigned_hall.id if assigned_hall else None,
            "assigned_hall_name": assigned_hall.name if assigned_hall else "",
            "is_current_hall": False,
            "photo_url": stored_photo_url(s.photo_path),
        })

    return jsonify({
        "success": True,
        "assigned_students": assigned_list,
        "available_students": available_list,
        "has_more": has_more,
        "offset": offset,
        "limit": limit,
    })


def parse_int(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


@attendance_bp.route("/api/hall-roster/assign", methods=["POST"])
def api_assign_student():
    data = request.get_json(silent=True) or request.form
    exam_hall_id = parse_int(data.get("exam_hall_id"))
    student_id = parse_int(data.get("student_id"))

    if not exam_hall_id or not student_id:
        return jsonify({"success": False, "error": "Invalid parameters"}), 400

    hall = db.session.get(ExamHall, exam_hall_id)
    student = db.session.get(Student, student_id)
    exam_context = hall_exam_context(hall) if hall else None
    if not hall or not exam_context or not student or not student.is_active:
        return jsonify({"success": False, "error": "Hall-ka ama ardayga lama heli karo."}), 404
    try:
        in_scope = student_enrollment_scope_query(exam_context.academic_year_id).filter(Student.id == student.id).first()
    except EnrollmentValidationError:
        in_scope = None
    if not in_scope:
        return jsonify({"success": False, "error": "Ardaygani kuma jiro sanad-dugsiyeedka hall-kan."}), 400

    existing = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id, student_id=student_id).first()
    if not existing:
        other_assignment = assignment_in_same_exam(student_id, hall, exclude_hall_id=exam_hall_id)
        if other_assignment and other_assignment.exam_hall:
            return jsonify({
                "success": False,
                "code": "STUDENT_ALREADY_ASSIGNED",
                "error": f"Ardeygan hore ayuu ugu qoran yahay {other_assignment.exam_hall.name}.",
                "assigned_hall": {
                    "id": other_assignment.exam_hall.id,
                    "name": other_assignment.exam_hall.name,
                }
            }), 409
        enrollment = ExamHallEnrollment(exam_hall_id=exam_hall_id, student_id=student_id)
        db.session.add(enrollment)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"success": False, "error": "Ardaygan horay ayuu hall-kan ugu qornaa."}), 409
        audit("Hall Roster", f"Assigned {student.student_code} to hall '{hall.name}'")

    return jsonify({"success": True})


@attendance_bp.route("/api/hall-roster/transfer", methods=["POST"])
def api_transfer_student():
    data = request.get_json(silent=True) or request.form
    target_hall_id = parse_int(data.get("to_exam_hall_id") or data.get("exam_hall_id"))
    student_id = parse_int(data.get("student_id"))

    if not target_hall_id or not student_id:
        return jsonify({"success": False, "error": "Invalid parameters"}), 400

    target_hall = db.session.get(ExamHall, target_hall_id)
    student = Student.query.filter_by(id=student_id).with_for_update().first()
    exam_context = hall_exam_context(target_hall) if target_hall else None
    if not target_hall or not target_hall.is_active or not exam_context or not student or not student.is_active:
        return jsonify({"success": False, "error": "Hall-ka ama ardayga lama heli karo."}), 404
    try:
        in_scope = student_enrollment_scope_query(exam_context.academic_year_id).filter(Student.id == student.id).first()
    except EnrollmentValidationError:
        in_scope = None
    if not in_scope:
        return jsonify({"success": False, "error": "Ardaygani kuma jiro sanad-dugsiyeedka hall-kan."}), 400

    existing_target = ExamHallEnrollment.query.filter_by(
        exam_hall_id=target_hall_id,
        student_id=student_id,
    ).first()

    previous_assignments = (
        ExamHallEnrollment.query
        .join(ExamHall, ExamHallEnrollment.exam_hall_id == ExamHall.id)
        .filter(
            ExamHallEnrollment.student_id == student_id,
            or_(*same_exam_hall_filters(target_hall)),
            ExamHall.id != target_hall_id,
        )
        .all()
    )
    if existing_target and not previous_assignments:
        return jsonify({"success": True, "message": "Ardaygu hore ayuu hall-kan ugu qornaa."})

    previous_hall_names = [en.exam_hall.name for en in previous_assignments if en.exam_hall]
    previous_hall_ids = [en.exam_hall_id for en in previous_assignments]
    year_id = exam_context.academic_year_id

    # Restrict migration to this exam's sessions and legacy rows. A hall can
    # contain stale records from another exam, which must remain untouched.
    legacy_exam_type = (
        exam_context
        if isinstance(exam_context, ExamType)
        else ExamType.query.filter_by(
            academic_year_id=year_id,
            name=exam_context.name,
        ).first()
    )
    session_query = ExamSession.query.filter_by(academic_year_id=year_id)
    if isinstance(exam_context, Exam):
        session_filters = [ExamSession.exam_id == exam_context.id]
        if legacy_exam_type:
            session_filters.append(ExamSession.exam_type_id == legacy_exam_type.id)
        session_query = session_query.filter(or_(*session_filters))
    elif legacy_exam_type:
        session_query = session_query.filter_by(exam_type_id=legacy_exam_type.id)
    scoped_session_ids = [session.id for session in session_query.all()]

    valid_session_scope = [AttendanceRecord.exam_session_id.is_(None)]
    if scoped_session_ids:
        valid_session_scope.append(AttendanceRecord.exam_session_id.in_(scoped_session_ids))

    attendance_scope = []
    if isinstance(exam_context, Exam):
        attendance_scope.append(and_(
            AttendanceRecord.exam_id == exam_context.id,
            or_(
                AttendanceRecord.exam_type_id.is_(None),
                AttendanceRecord.exam_type_id == (legacy_exam_type.id if legacy_exam_type else None),
            ),
            or_(*valid_session_scope),
        ))
    if legacy_exam_type:
        attendance_scope.append(and_(
            AttendanceRecord.exam_id.is_(None),
            AttendanceRecord.exam_type_id == legacy_exam_type.id,
            or_(*valid_session_scope),
        ))
    if scoped_session_ids:
        attendance_scope.append(and_(
            AttendanceRecord.exam_id.is_(None),
            AttendanceRecord.exam_type_id.is_(None),
            AttendanceRecord.exam_session_id.in_(scoped_session_ids),
        ))
    attendance_scope.append(and_(
        AttendanceRecord.exam_id.is_(None),
        AttendanceRecord.exam_type_id.is_(None),
        AttendanceRecord.exam_session_id.is_(None),
    ))

    source_records = []
    target_records = []
    if previous_hall_ids or not existing_target:
        scoped_records = AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.academic_year_id == year_id,
            or_(*attendance_scope),
        )
        source_hall_scope = [AttendanceRecord.exam_hall_id.is_(None)]
        if previous_hall_ids:
            source_hall_scope.append(AttendanceRecord.exam_hall_id.in_(previous_hall_ids))
        source_records = scoped_records.filter(
            or_(*source_hall_scope)
        ).order_by(AttendanceRecord.id).all()
        target_records = scoped_records.filter_by(exam_hall_id=target_hall_id).all()

    target_by_slot = {}
    for record in target_records:
        target_by_slot.setdefault((record.subject_id, record.exam_session_id), record)

    merges = []
    for record in source_records:
        target_record = target_by_slot.get((record.subject_id, record.exam_session_id))
        if not target_record:
            continue
        if normalize_attendance_status(record.status) != normalize_attendance_status(target_record.status):
            return jsonify({
                "success": False,
                "code": "ATTENDANCE_CONFLICT",
                "error": (
                    "Xaadirinta ardaygan ee labada hall way kala duwan tahay. "
                    "Wareejinta lama samayn si aan xogta midna u lumin. "
                    "Fadlan marka hore sax xaadirinta is-khilaafsan."
                ),
                "subject": record.subject.name if record.subject else None,
            }), 409
        merges.append((record, target_record))

    try:
        for source_record, target_record in merges:
            notes = [note.strip() for note in (target_record.note, source_record.note) if note and note.strip()]
            target_record.note = " | ".join(dict.fromkeys(notes))[:255] or None
            if source_record.recorded_at and (
                not target_record.recorded_at or source_record.recorded_at > target_record.recorded_at
            ):
                target_record.recorded_at = source_record.recorded_at
                target_record.attendance_date = source_record.attendance_date
            target_record.marked_by_id = target_record.marked_by_id or source_record.marked_by_id
            target_record.class_id = target_record.class_id or source_record.class_id
            target_record.academic_level_id = target_record.academic_level_id or source_record.academic_level_id
            target_record.academic_class_id = target_record.academic_class_id or source_record.academic_class_id
            target_record.academic_section_id = target_record.academic_section_id or source_record.academic_section_id
            target_record.exam_id = target_record.exam_id or source_record.exam_id
            target_record.exam_type_id = target_record.exam_type_id or source_record.exam_type_id
            target_record.exam_session_id = target_record.exam_session_id or source_record.exam_session_id
            db.session.delete(source_record)

        merged_ids = {source.id for source, _ in merges}
        for record in source_records:
            if record.id not in merged_ids:
                record.exam_hall_id = target_hall_id
        for enrollment in previous_assignments:
            db.session.delete(enrollment)
        if not existing_target:
            db.session.add(ExamHallEnrollment(exam_hall_id=target_hall_id, student_id=student_id))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "error": "Transfer-ka lama dhammeystiri karin. Fadlan mar kale isku day."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed migrating attendance while transferring student %s", student_id)
        return jsonify({"success": False, "error": "Wareejinta iyo xaadirinta lama wada kaydin. Fadlan mar kale isku day."}), 500

    audit(
        "Hall Roster",
        f"Transferred {student.student_code} from {', '.join(previous_hall_names) or 'no hall'} "
        f"to '{target_hall.name}'; moved {len(source_records) - len(merges)} attendance rows, "
        f"merged {len(merges)} duplicate rows",
    )
    return jsonify({
        "success": True,
        "student_id": student_id,
        "from_halls": previous_hall_names,
        "to_hall": {"id": target_hall.id, "name": target_hall.name},
        "attendance_records_moved": len(source_records) - len(merges),
        "attendance_records_merged": len(merges),
        "message": f"Ardayga si sax ah ayaa loogu wareejiyey {target_hall.name}.",
    })


@attendance_bp.route("/api/hall-roster/remove", methods=["POST"])
def api_remove_student():
    data = request.get_json(silent=True) or request.form
    exam_hall_id = parse_int(data.get("exam_hall_id"))
    student_id = parse_int(data.get("student_id"))

    if not exam_hall_id or not student_id:
        return jsonify({"success": False, "error": "Invalid parameters"}), 400

    existing = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id, student_id=student_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        audit("Hall Roster", f"Removed student {student_id} from hall {exam_hall_id}")

    return jsonify({"success": True})


@attendance_bp.route("/api/hall-roster/remove-class", methods=["POST"])
def api_remove_class_from_hall():
    """Remove only one class group from the selected hall roster."""
    data = request.get_json(silent=True) or request.form
    exam_hall_id = parse_int(data.get("exam_hall_id"))
    academic_class_id = parse_int(data.get("academic_class_id"))
    class_name = (data.get("class_name") or "").strip()
    if not exam_hall_id or (not academic_class_id and not class_name):
        return jsonify({"success": False, "error": "Fasalka la nadiifinayo lama helin."}), 400

    hall = db.session.get(ExamHall, exam_hall_id)
    if not hall or not hall.is_active:
        return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo."}), 404

    enrollments = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id).all()
    student_ids = [enrollment.student_id for enrollment in enrollments]
    students = Student.query.filter(Student.id.in_(student_ids)).all() if student_ids else []
    hall_context_obj = hall_exam_context(hall)
    year_id = hall_context_obj.academic_year_id if hall_context_obj else getattr(hall, "academic_year_id", None)
    if not year_id:
        current_year = AcademicYear.query.filter_by(is_current=True).first()
        year_id = current_year.id if current_year else None
    if not year_id:
        return jsonify({"success": False, "error": "Sanad-dugsiyeedka hall-kan lama heli karo."}), 400
    try:
        scoped_students = attendance_student_scope_query(
            year_id,
            level_id=hall.academic_class.academic_level_id if hall.academic_class else None,
            class_id=academic_class_id,
        ).filter(Student.id.in_(student_ids)).all()
    except EnrollmentValidationError:
        scoped_students = []
    matched_ids = set()
    for student in scoped_students:
        placement = enrollment_placement_for_student(student, year_id) or {}
        resolved_class_id = placement.get("academic_class_id") or student.academic_class_id
        resolved_class_name = placement.get("class_name") or student_class_name(student)
        if (academic_class_id and resolved_class_id == academic_class_id) or (class_name and resolved_class_name == class_name):
            matched_ids.add(student.id)
    removed = 0
    for enrollment in enrollments:
        if enrollment.student_id in matched_ids:
            db.session.delete(enrollment)
            removed += 1
    if removed:
        db.session.commit()
        audit("Hall Roster", f"Removed {removed} students from class group in hall {hall.id}")
    return jsonify({"success": True, "removed": removed})


@attendance_bp.route("/api/attendance-data")
def api_attendance_data():
    year_id = request.args.get("academic_year_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    exam_type_id = request.args.get("exam_type_id", type=int)
    exam_hall_id = request.args.get("exam_hall_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    exam_session_id = request.args.get("exam_session_id", type=int)

    if not exam_hall_id:
        return jsonify({"success": True, "students": [], "subjects": [], "tallies": {}})

    try:
        if exam_session_id:
            hall, exam, legacy_exam_type, session = attendance_session_context(
                year_id,
                exam_id,
                exam_type_id,
                exam_hall_id,
                exam_session_id,
            )
            _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
            groups, tallies = session_attendance_payload(hall, session, allowed_pairs)
            return jsonify({
                "success": True,
                "session": serialize_session(session, allowed_pairs),
                "groups": groups,
                "tallies": tallies,
                "exam_id": exam.id if exam else None,
            })
        hall, exam, legacy_exam_type = hall_context(year_id, exam_id, exam_type_id, exam_hall_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    # Subjects are scoped to the selected hall exam, never to a global list.
    subjects = hall_subjects(hall)
    subject_list = [{"id": s.id, "name": s.name} for s in subjects]

    if not subject_id and subjects:
        subject_id = subjects[0].id
    if subject_id and subject_id not in {subject.id for subject in subjects}:
        subject_id = subjects[0].id if subjects else None

    # Fetch enrolled students
    enrollments = (
        ExamHallEnrollment.query
        .filter_by(exam_hall_id=exam_hall_id)
        .order_by(ExamHallEnrollment.created_at, ExamHallEnrollment.id)
        .all()
    )
    enrolled_student_ids = [e.student_id for e in enrollments]

    scoped_students = (
        student_enrollment_scope_query(year_id)
        .filter(Student.id.in_(enrolled_student_ids))
        .all()
        if enrolled_student_ids else []
    )
    scoped_students_by_id = {student.id: student for student in scoped_students}
    students_raw = [
        scoped_students_by_id[student_id]
        for student_id in enrolled_student_ids
        if student_id in scoped_students_by_id
    ]

    # Fetch recorded attendance for this hall & subject
    records = AttendanceRecord.query.filter_by(exam_hall_id=exam_hall_id, subject_id=subject_id).order_by(AttendanceRecord.id.asc()).all() if subject_id else []
    record_map = {}
    for r in records:
        record_map[r.student_id] = {
            "status": normalize_attendance_status(r.status),
            "note": r.note or "",
        }

    students_data = []
    tallies = {st["key"]: 0 for st in ATTENDANCE_STATUSES}

    for s in students_raw:
        # An absent record is intentionally unmarked. Attendance becomes a
        # committed per-subject decision only after the operator clicks a status.
        saved_record = record_map.get(s.id, {})
        st_val = saved_record.get("status")
        if st_val not in tallies:
            st_val = None
        if st_val:
            tallies[st_val] += 1
        placement = enrollment_placement_for_student(s, year_id) or {}
        cls_name = placement.get("class_name") or student_class_name(s)

        students_data.append({
            "id": s.id,
            "student_code": s.student_code,
            "full_name": s.full_name,
            "class_name": cls_name,
            "academic_class_id": placement.get("academic_class_id") or s.academic_class_id,
            "photo_url": stored_photo_url(s.photo_path),
            "status": st_val,
            "note": saved_record.get("note", ""),
        })

    return jsonify({
        "success": True,
        "students": students_data,
        "subjects": subject_list,
        "exam_id": exam.id if exam else None,
        "subject_id": subject_id,
        "tallies": tallies,
    })


@attendance_bp.route("/api/mark-status", methods=["POST"])
def api_mark_status():
    data = request.get_json(silent=True) or request.form
    student_id = parse_int(data.get("student_id"))
    exam_hall_id = parse_int(data.get("exam_hall_id"))
    subject_id = parse_int(data.get("subject_id"))
    year_id = parse_int(data.get("academic_year_id"))
    exam_id = parse_int(data.get("exam_id"))
    exam_type_id = parse_int(data.get("exam_type_id"))
    exam_session_id = parse_int(data.get("exam_session_id"))
    status_val = (data.get("status") or "present").lower().strip()
    note_was_submitted = "note" in data
    note_value = str(data.get("note") or "").strip()
    note_only = str(data.get("note_only") or "").strip().lower() in {"1", "true", "yes"}

    if len(note_value) > 255:
        return jsonify({"success": False, "error": "Faalladu kama badnaan karto 255 xaraf."}), 400

    status_val = normalize_attendance_status(status_val)
    if status_val not in ATTENDANCE_STATUS_KEYS:
        return jsonify({"success": False, "error": "Xaaladda xaadirinta lama aqoonsan."}), 400

    if not student_id or not exam_hall_id or not subject_id:
        return jsonify({"success": False, "error": "Fadlan buuxi dhammaan xogta loo baahan yahay."}), 400

    if not year_id:
        hall_obj = db.session.get(ExamHall, exam_hall_id)
        if hall_obj:
            exam_ctx = hall_exam_context(hall_obj)
            if exam_ctx:
                year_id = exam_ctx.academic_year_id
        if not year_id:
            curr_y = AcademicYear.query.filter_by(is_current=True).first()
            year_id = curr_y.id if curr_y else None

    if not year_id:
        return jsonify({"success": False, "error": "Sanad-dugsiyeedka lama heli karo."}), 400

    try:
        if exam_session_id:
            hall, exam, legacy_exam_type, exam_session = attendance_session_context(
                year_id,
                exam_id,
                exam_type_id,
                exam_hall_id,
                exam_session_id,
            )
        else:
            hall, exam, legacy_exam_type = hall_context(year_id, exam_id, exam_type_id, exam_hall_id)
            exam_session = None
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if not exam_session and subject_id not in {subject.id for subject in hall_subjects(hall)}:
        return jsonify({"success": False, "error": "Maadadani kuma jirto hall exam-kan."}), 400
    if not ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id, student_id=student_id).first():
        return jsonify({"success": False, "error": "Ardaygani kuma jiro hall exam-kan."}), 400
    student = db.session.get(Student, student_id)
    if not student:
        return jsonify({"success": False, "error": "Ardaygan lama heli karo."}), 404
    placement = enrollment_placement_for_student(student, year_id)
    if not placement:
        return jsonify({"success": False, "error": "Ardaygani kuma jiro sanad-dugsiyeedka imtixaankan."}), 400
    if exam_session:
        student_level_id = placement.get("academic_level_id") or effective_student_level_id(student)
        _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
        session_subject_ids = {
            assignment.subject_id
            for assignment in exam_session.subject_assignments
            if assignment.academic_level_id == student_level_id
            and (assignment.academic_level_id, assignment.subject_id) in allowed_pairs
        }
        if subject_id not in session_subject_ids:
            return jsonify({"success": False, "error": "Ardaygani ma fadhiisanayo maadadan fadhigan."}), 400

    existing = attendance_record_for(
        student_id,
        exam_hall_id,
        subject_id,
        year_id=year_id,
        exam_id=exam.id if exam else None,
        exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
        exam_session_id=exam_session.id if exam_session else None,
    )

    if note_only:
        if not existing:
            return jsonify({
                "success": False,
                "error": "Marka hore calaamadee xaaladda xaadirinta, dabadeed faallada kaydi.",
            }), 409
        existing.note = note_value or None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Unable to save attendance note")
            return jsonify({"success": False, "error": "Faallada lama kaydin. Fadlan mar kale isku day."}), 500
        return jsonify({
            "success": True,
            "student_id": student_id,
            "status": normalize_attendance_status(existing.status),
            "note": existing.note or "",
            "record_id": existing.id,
        })

    record = existing or AttendanceRecord(
        student_id=student_id,
        exam_hall_id=exam_hall_id,
        subject_id=subject_id,
        exam_session_id=exam_session.id if exam_session else None,
    )
    record.exam_hall_id = exam_hall_id
    record.subject_id = subject_id
    apply_attendance_values(
        record,
        student=student,
        year_id=year_id,
        exam=exam,
        legacy_exam_type=legacy_exam_type,
        status_val=status_val,
        exam_session=exam_session,
    )
    if note_was_submitted:
        record.note = note_value or None

    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # A rapid click can race with another request or an existing student record.
        record = attendance_record_for(
            student_id,
            exam_hall_id,
            subject_id,
            year_id=year_id,
            exam_id=exam.id if exam else None,
            exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
            exam_session_id=exam_session.id if exam_session else None,
        )
        if not record:
            current_app.logger.exception("Unable to create attendance status")
            return jsonify({"success": False, "error": "Xaadirinta lama kaydin. Fadlan mar kale isku day."}), 409
        record.exam_hall_id = exam_hall_id
        record.subject_id = subject_id
        record.exam_session_id = exam_session.id if exam_session else None
        apply_attendance_values(
            record,
            student=student,
            year_id=year_id,
            exam=exam,
            legacy_exam_type=legacy_exam_type,
            status_val=status_val,
            exam_session=exam_session,
        )
        if note_was_submitted:
            record.note = note_value or None
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            current_app.logger.exception("Unable to update attendance status after rollback")
            return jsonify({"success": False, "error": "Xaadirinta lama kaydin. Fadlan mar kale isku day."}), 409
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("Unexpected database error updating attendance: %s", exc)
            return jsonify({"success": False, "error": "Xaadirinta lama kaydin. Fadlan mar kale isku day."}), 500
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unexpected database error saving attendance: %s", exc)
        return jsonify({"success": False, "error": "Xaadirinta lama kaydin. Fadlan mar kale isku day."}), 500

    return jsonify({
        "success": True,
        "student_id": student_id,
        "status": record.status,
        "note": record.note or "",
        "record_id": record.id,
    })


def mark_session_bulk_attendance(*, hall, exam, legacy_exam_type, session, year_id, status_val):
    """Apply one bulk action to every visible student x subject slot in a sitting."""
    enrollment_rows = ExamHallEnrollment.query.filter_by(exam_hall_id=hall.id).all()
    student_ids = [row.student_id for row in enrollment_rows]
    students = (
        student_enrollment_scope_query(year_id)
        .filter(Student.id.in_(student_ids))
        .options(
            joinedload(Student.academic_class),
            joinedload(Student.school_class),
            joinedload(Student.academic_section),
        )
        .all()
        if student_ids else []
    )
    _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    subject_map = subjects_by_session_level(session, allowed_pairs)

    if status_val == "clear":
        try:
            removed = (
                AttendanceRecord.query
                .filter_by(exam_hall_id=hall.id, exam_session_id=session.id)
                .delete(synchronize_session=False)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Unable to clear attendance for hall %s, session %s",
                hall.id,
                session.id,
            )
            return {"success": False, "error": "Xaadirinta lama nadiifin. Xogtii hore waa la ilaaliyey; fadlan mar kale isku day."}, 500
        audit("Bulk Attendance", f"Cleared {removed} session slots in Hall {hall.id}")
        return {"success": True, "updated_count": removed, "status": "clear"}

    status_val = normalize_attendance_status(status_val)
    if status_val not in ATTENDANCE_STATUS_KEYS:
        return {"success": False, "error": "Xaaladda xaadirinta lama aqoonsan."}, 400

    updated_count = 0
    new_records = []
    legacy_class_cache = {}
    try:
        placements = resolve_student_placements(
            (student, year_id) for student in students
        )
        student_level_ids = {}
        for student in students:
            placement = placements.get((student.id, year_id)) or {}
            student_level_ids[student.id] = (
                placement.get("academic_level_id")
                or student.academic_level_id
                or (student.academic_class.academic_level_id if student.academic_class else None)
            )

        subject_ids = {
            subject.id
            for subjects in subject_map.values()
            for subject in subjects
        }
        existing_records = []
        if student_ids and subject_ids:
            existing_records = (
                AttendanceRecord.query
                .filter_by(exam_hall_id=hall.id, exam_session_id=session.id)
                .filter(
                    AttendanceRecord.student_id.in_(student_ids),
                    AttendanceRecord.subject_id.in_(subject_ids),
                )
                .order_by(AttendanceRecord.id.desc())
                .all()
            )
        records_by_slot = {}
        for record in existing_records:
            records_by_slot.setdefault((record.student_id, record.subject_id), record)

        for student in students:
            placement = placements.get((student.id, year_id)) or {}
            for subject in subject_map.get(student_level_ids[student.id], []):
                record = records_by_slot.get((student.id, subject.id))
                if not record:
                    record = AttendanceRecord(
                        student_id=student.id,
                        exam_hall_id=hall.id,
                        subject_id=subject.id,
                        exam_session_id=session.id,
                    )
                    new_records.append(record)
                    records_by_slot[(student.id, subject.id)] = record
                record.exam_hall_id = hall.id
                record.subject_id = subject.id
                apply_attendance_values(
                    record,
                    student=student,
                    year_id=year_id,
                    exam=exam,
                    legacy_exam_type=legacy_exam_type,
                    status_val=status_val,
                    exam_session=session,
                    placement=placement,
                    legacy_class_cache=legacy_class_cache,
                )
                updated_count += 1
        db.session.add_all(new_records)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"success": False, "error": "Xaadirinta qaar lama kaydin. Fadlan mar kale isku day."}, 409
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unexpected error saving session attendance: %s", exc)
        return {"success": False, "error": "Xaadirinta lama kaydin. Fadlan mar kale isku day."}, 500

    audit("Bulk Attendance", f"Marked {updated_count} session slots as {status_val} in Hall {hall.id}")
    return {"success": True, "updated_count": updated_count, "status": status_val}


@attendance_bp.route("/api/mark-bulk", methods=["POST"])
def api_mark_bulk():
    data = request.get_json(silent=True) or request.form
    exam_hall_id = parse_int(data.get("exam_hall_id"))
    subject_id = parse_int(data.get("subject_id"))
    year_id = parse_int(data.get("academic_year_id"))
    exam_id = parse_int(data.get("exam_id"))
    exam_type_id = parse_int(data.get("exam_type_id"))
    exam_session_id = parse_int(data.get("exam_session_id"))
    status_val = (data.get("status") or "present").lower().strip()

    if not exam_hall_id or (not subject_id and not exam_session_id):
        return jsonify({"success": False, "error": "Fadlan buuxi dhammaan xogta loo baahan yahay."}), 400

    if not year_id:
        hall_obj = db.session.get(ExamHall, exam_hall_id)
        if hall_obj:
            exam_ctx = hall_exam_context(hall_obj)
            if exam_ctx:
                year_id = exam_ctx.academic_year_id
        if not year_id:
            curr_y = AcademicYear.query.filter_by(is_current=True).first()
            year_id = curr_y.id if curr_y else None

    if not year_id:
        return jsonify({"success": False, "error": "Sanad-dugsiyeedka lama heli karo."}), 400

    try:
        if exam_session_id:
            hall, exam, legacy_exam_type, session = attendance_session_context(
                year_id,
                exam_id,
                exam_type_id,
                exam_hall_id,
                exam_session_id,
            )
            result = mark_session_bulk_attendance(
                hall=hall,
                exam=exam,
                legacy_exam_type=legacy_exam_type,
                session=session,
                year_id=year_id,
                status_val=status_val,
            )
            if isinstance(result, tuple):
                payload, status_code = result
                return jsonify(payload), status_code
            return jsonify(result)
        hall, exam, legacy_exam_type = hall_context(year_id, exam_id, exam_type_id, exam_hall_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if subject_id not in {subject.id for subject in hall_subjects(hall)}:
        return jsonify({"success": False, "error": "Maadadani kuma jirto hall exam-kan."}), 400

    enrollments = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id).all()
    student_ids = [e.student_id for e in enrollments]
    students_by_id = {
        student.id: student
        for student in student_enrollment_scope_query(year_id).filter(Student.id.in_(student_ids)).all()
    } if student_ids else {}

    if status_val == "clear":
        AttendanceRecord.query.filter_by(exam_hall_id=exam_hall_id, subject_id=subject_id).delete(synchronize_session=False)
        db.session.commit()
        audit("Bulk Attendance", f"Cleared attendance for {len(student_ids)} students in Hall {exam_hall_id}")
        return jsonify({"success": True, "updated_count": len(student_ids), "status": "clear"})

    status_val = normalize_attendance_status(status_val)
    if status_val not in ATTENDANCE_STATUS_KEYS:
        return jsonify({"success": False, "error": "Xaaladda xaadirinta lama aqoonsan."}), 400

    for s_id in student_ids:
        student = students_by_id.get(s_id)
        if not student:
            return jsonify({"success": False, "error": "Mid ka mid ah ardeyda hall-kan lama heli karo."}), 404
        existing = attendance_record_for(s_id, exam_hall_id, subject_id, year_id=year_id, exam_id=exam.id if exam else None, exam_type_id=legacy_exam_type.id if legacy_exam_type else None)

        record = existing or AttendanceRecord(
            student_id=s_id,
            exam_hall_id=exam_hall_id,
            subject_id=subject_id,
        )
        record.exam_hall_id = exam_hall_id
        record.subject_id = subject_id
        apply_attendance_values(
            record,
            student=student,
            year_id=year_id,
            exam=exam,
            legacy_exam_type=legacy_exam_type,
            status_val=status_val,
        )
        db.session.add(record)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Fallback: resolve a concurrent insert per student, without hiding a
        # failed save behind a false success response.
        failed_ids = []
        for s_id in student_ids:
            student = students_by_id.get(s_id)
            record = attendance_record_for(s_id, exam_hall_id, subject_id, year_id=year_id, exam_id=exam.id if exam else None, exam_type_id=legacy_exam_type.id if legacy_exam_type else None)
            if not record:
                record = AttendanceRecord(student_id=s_id, exam_hall_id=exam_hall_id, subject_id=subject_id)
            record.exam_hall_id = exam_hall_id
            record.subject_id = subject_id
            apply_attendance_values(
                record,
                student=student,
                year_id=year_id,
                exam=exam,
                legacy_exam_type=legacy_exam_type,
                status_val=status_val,
            )
            db.session.add(record)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                failed_ids.append(s_id)
        if failed_ids:
            current_app.logger.error("Bulk attendance failed for students: %s", failed_ids)
            return jsonify({"success": False, "error": "Xaadirinta qaar lama kaydin. Fadlan mar kale isku day."}), 409
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Unexpected error in mark bulk: %s", exc)
        return jsonify({"success": False, "error": f"Bulk attendance lama kaydin: {str(exc)}"}), 500

    audit("Bulk Attendance", f"Marked {len(student_ids)} students as {status_val} for Hall {exam_hall_id}")

    return jsonify({"success": True, "updated_count": len(student_ids), "status": status_val})


# ==========================================
# EXPORT & PRINT ROUTES
# ==========================================

@attendance_bp.route("/export.xlsx")
def export_excel():
    exam_hall_id = request.args.get("exam_hall_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    exam_session_id = request.args.get("exam_session_id", type=int)

    query = AttendanceRecord.query
    if exam_hall_id:
        query = query.filter_by(exam_hall_id=exam_hall_id)
    if subject_id:
        query = query.filter_by(subject_id=subject_id)
    if exam_session_id:
        query = query.filter_by(exam_session_id=exam_session_id)

    records = query.order_by(AttendanceRecord.recorded_at.desc()).limit(1000).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Date", "Student ID", "Student Name", "Exam Hall", "Subject", "Status", "Marked By"])

    for row in records:
        ws.append([
            row.recorded_at.strftime("%Y-%m-%d %H:%M") if row.recorded_at else "",
            row.student.student_code if row.student else "",
            row.student.full_name if row.student else "",
            row.exam_hall.name if row.exam_hall else "",
            row.subject.name if row.subject else "",
            row.status,
            row.marked_by.username if row.marked_by else "",
        ])

    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name="attendance.xlsx")


@attendance_bp.route("/export.pdf")
def export_pdf():
    report = _attendance_report_context()
    html = render_template("admin/attendance_print.html", **report, auto_print=False)
    pdf = html_report_to_pdf(html, request.url_root)
    return send_file(
        BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Warbixinta-Xaadirinta-{report['hall'].code}-{report['exam_session'].session_date.isoformat()}.pdf",
    )


@attendance_bp.route("/print")
def print_sheet():
    return render_template(
        "admin/attendance_print.html",
        **_attendance_report_context(),
        auto_print=True,
    )


def _attendance_report_context():
    """Build the same year/exam/hall/sitting-scoped dataset for print and PDF."""
    year_id = request.args.get("academic_year_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    exam_type_id = request.args.get("exam_type_id", type=int)
    hall_id = request.args.get("exam_hall_id", type=int)
    session_id = request.args.get("exam_session_id", type=int)
    scoped_hall = db.session.get(ExamHall, hall_id) if hall_id else None
    hall_exam = hall_exam_context(scoped_hall) if scoped_hall else None
    if hall_exam:
        year_id = year_id or hall_exam.academic_year_id
        if not exam_id and not exam_type_id:
            if isinstance(hall_exam, Exam):
                exam_id = hall_exam.id
                legacy_exam_type = ExamType.query.filter_by(
                    academic_year_id=year_id,
                    name=hall_exam.name,
                ).first()
                exam_type_id = legacy_exam_type.id if legacy_exam_type else None
            else:
                exam_type_id = hall_exam.id
    if not all((year_id, hall_id, session_id)) or not (exam_id or exam_type_id):
        abort(400, description="Dooro sanad-dugsiyeedka, imtixaanka, hall-ka iyo fadhiga ka hor samaynta warbixinta.")

    try:
        hall, exam, legacy_exam_type, exam_session = attendance_session_context(
            year_id, exam_id, exam_type_id, hall_id, session_id
        )
    except ValueError as exc:
        abort(400, description=str(exc))

    _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    groups, tallies = session_attendance_payload(hall, exam_session, allowed_pairs)
    roster_ids = [
        student_row["id"]
        for group in groups
        for student_row in group["students"]
    ]
    students = (
        Student.query
        .filter(Student.id.in_(roster_ids))
        .options(
            joinedload(Student.academic_class),
            joinedload(Student.school_class),
            joinedload(Student.academic_section),
        )
        .all()
        if roster_ids else []
    )
    students_by_id = {student.id: student for student in students}
    placements = resolve_student_placements(
        (student, year_id) for student in students
    )
    status_labels = {item["key"]: item["label"] for item in ATTENDANCE_STATUSES}
    status_classes = {item["key"]: item["key"] for item in ATTENDANCE_STATUSES}
    report_students = []
    subject_record_count = 0
    marked_record_count = 0

    for group in groups:
        for student_row in group["students"]:
            student = students_by_id.get(student_row["id"])
            if not student:
                continue
            placement = placements.get((student.id, year_id)) or {}
            slots = []
            for slot in student_row["slots"]:
                status = slot.get("status")
                subject_record_count += 1
                if status:
                    marked_record_count += 1
                slots.append({
                    "subject_name": slot["subject_name"],
                    "status": status,
                    "status_label": status_labels.get(status, "Lama calaamadin"),
                    "status_class": status_classes.get(status, "unmarked"),
                    "note": slot.get("note") or "",
                })
            report_students.append({
                "student": student,
                "photo_url": stored_photo_url(student.photo_path),
                "class_name": placement.get("class_name") or student_row.get("class_name") or "Lama diiwaangelin",
                "class_color": attendance_class_color(
                    placement.get("class_name") or student_row.get("class_name") or "Lama diiwaangelin"
                ),
                "level_name": placement.get("level_name") or group.get("level_name") or "Lama diiwaangelin",
                "section_name": placement.get("section_name") or student.section or "",
                "slots": slots,
            })

    return {
        "students": report_students,
        "hall": hall,
        "exam": exam,
        "exam_id": exam.id if exam else None,
        "exam_type_id": legacy_exam_type.id if legacy_exam_type else None,
        "exam_name": exam.name if exam else legacy_exam_type.name,
        "exam_session": exam_session,
        "academic_year": exam_session.academic_year,
        "settings": get_settings(),
        "today": date.today(),
        "statuses": [
            {**status, "count": tallies.get(status["key"], 0)}
            for status in ATTENDANCE_STATUSES
        ],
        "student_count": len(report_students),
        "subject_record_count": subject_record_count,
        "marked_record_count": marked_record_count,
        "unmarked_record_count": subject_record_count - marked_record_count,
    }
