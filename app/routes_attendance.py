from datetime import date, datetime, time
from tempfile import NamedTemporaryFile

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
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
from .attendance_rules import NON_SAT_STATUSES, SAT_STATUSES, normalize_attendance_status, scheduled_subject_scope_key
from .academic_hierarchy import year_classes, year_levels
from .enrollment_service import (
    EnrollmentValidationError,
    enrollment_placement_for_student,
    get_enrollment_for_student_year,
    student_enrollment_legacy_scope_query,
    student_enrollment_scope_query,
)

attendance_bp = Blueprint("admin_attendance", __name__)

ATTENDANCE_STATUSES = [
    {"key": "present", "label": "Joogto", "cls": "o-present", "icon": "fa-circle-check"},
    {"key": "absent", "label": "Maqan", "cls": "o-absent", "icon": "fa-circle-xmark"},
    {"key": "excused", "label": "La fasaxay", "cls": "o-excused", "icon": "fa-file-circle-check"},
    {"key": "sick", "label": "Cudur daar", "cls": "o-sick", "icon": "fa-notes-medical"},
    {"key": "emergency", "label": "Xaalad degdeg", "cls": "o-emergency", "icon": "fa-triangle-exclamation"},
    {"key": "late", "label": "Soo daahid", "cls": "o-late", "icon": "fa-clock"},
]

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
        if hall.academic_class_id:
            return hall, exam, None
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


def legacy_school_class_id(student, class_name=None):
    """Provide the legacy class link required by older attendance schemas."""
    if student.class_id and not class_name:
        return student.class_id

    class_name = class_name or (
        student.academic_class.name if student.academic_class else None
    ) or student.level or "Unassigned"
    legacy_class = SchoolClass.query.filter_by(name=class_name).first()
    if not legacy_class:
        legacy_class = SchoolClass(name=class_name)
        db.session.add(legacy_class)
        db.session.flush()
    student.class_id = legacy_class.id
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
):
    """Apply the canonical Results Hub scope and selected attendance status."""
    record.academic_year_id = year_id
    placement = enrollment_placement_for_student(student, year_id) or {}
    class_name = placement.get("class_name")
    # Some deployed databases predate the nullable class_id model change.
    # Populate both current and legacy class fields so a status click is valid
    # on every supported schema.
    record.class_id = legacy_school_class_id(student, class_name=class_name)
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
        if assignment.subject and assignment.subject.academic_level_id == assignment.academic_level_id
        and (allowed_pairs is None or (assignment.academic_level_id, assignment.subject_id) in allowed_pairs)
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
            Subject.academic_level_id == ExamSessionSubject.academic_level_id,
        )
        .order_by(ExamSessionSubject.academic_level_id, Subject.sort_order, Subject.name)
        .all()
    )
    grouped = {}
    for row in rows:
        if allowed_pairs is not None and (row.academic_level_id, row.subject_id) not in allowed_pairs:
            continue
        grouped.setdefault(row.academic_level_id, []).append(row.subject)
    return grouped


def session_attendance_payload(hall, session, allowed_pairs=None):
    """Build the timetable-driven attendance roster grouped by student level/class."""
    subjects_by_level = subjects_by_session_level(session, allowed_pairs)
    enrollment_rows = ExamHallEnrollment.query.filter_by(exam_hall_id=hall.id).all()
    student_ids = [row.student_id for row in enrollment_rows]
    students = (
        student_enrollment_scope_query(session.academic_year_id)
        .filter(Student.id.in_(student_ids))
        .all()
        if student_ids else []
    )
    records = (
        AttendanceRecord.query
        .filter_by(exam_hall_id=hall.id, exam_session_id=session.id)
        .all()
    )
    status_map = {
        (record.student_id, record.subject_id): normalize_attendance_status(record.status)
        for record in records
    }
    tallies = {status["key"]: 0 for status in ATTENDANCE_STATUSES}
    groups = {}

    for student in students:
        placement = enrollment_placement_for_student(student, session.academic_year_id) or {}
        level_id = placement.get("academic_level_id") or effective_student_level_id(student)
        level = db.session.get(AcademicLevel, level_id) if level_id else None
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
        slots = []
        for subject in subjects_by_level.get(level_id, []):
            current_status = status_map.get((student.id, subject.id))
            if current_status not in tallies:
                current_status = None
            if current_status:
                tallies[current_status] += 1
            slots.append({
                "subject_id": subject.id,
                "subject_name": subject.name,
                "status": current_status,
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
    for group in groups.values():
        group["students"].sort(key=lambda student: (student["full_name"] or "").lower())
        group["student_count"] = len(group["students"])
        group["subject_count"] = len(group["subjects"])
        payload_groups.append(group)
    payload_groups.sort(key=lambda item: (item["level_name"].lower(), item["class_name"].lower()))
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

    level_data = []
    allowed_pairs = set()
    for year_level in query.order_by(
        AcademicYearLevel.sort_order,
        AcademicYearLevel.name,
        AcademicYearLevel.id,
    ).all():
        subjects = (
            AcademicYearSubject.query
            .join(Subject, AcademicYearSubject.legacy_subject_id == Subject.id)
            .filter(
                AcademicYearSubject.academic_year_id == year_id,
                AcademicYearSubject.academic_year_level_id == year_level.id,
                AcademicYearSubject.is_active.is_(True),
                AcademicYearSubject.legacy_subject_id.isnot(None),
                Subject.is_active.is_(True),
                Subject.academic_level_id == year_level.legacy_level_id,
            )
            .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name, AcademicYearSubject.id)
            .all()
        )
        subject_data = []
        if subjects:
            for year_subject in subjects:
                subject = year_subject.legacy_subject
                if not subject:
                    continue
                allowed_pairs.add((year_level.legacy_level_id, subject.id))
                subject_data.append({"id": subject.id, "name": year_subject.name})
        else:
            # Older attendance setups may already have a year-aware level but
            # no AcademicYearSubject bridge yet. Keep those schedules usable
            # while still restricting the level list to this year's records.
            legacy_subjects = (
                Subject.query
                .filter_by(academic_level_id=year_level.legacy_level_id, is_active=True)
                .order_by(Subject.sort_order, Subject.name, Subject.id)
                .all()
            )
            for subject in legacy_subjects:
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
            filters.append(ExamSession.exam_type_id == legacy_exam_type.id)
        query = query.filter(or_(*filters))
    elif legacy_exam_type:
        query = query.filter(ExamSession.exam_type_id == legacy_exam_type.id)
    return query.order_by(ExamSession.session_date, ExamSession.session_time, ExamSession.id).all()


def conflicting_subject_assignments(session, selected):
    """Find subjects already scheduled in another sitting of this exam scope.

    A subject belongs to one level and may be examined only once for the same
    academic year/examination scope.  We validate before replacing the current
    session assignments, keeping the existing schedule intact on conflict.
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
    return [
        row for row in matches
        if (row.academic_level_id, row.subject_id) in selected_pairs
    ]


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
        settings=get_settings(),
    )


@attendance_bp.route("/hall-roster")
def hall_roster():
    """Hall Exam Roster Page."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first() or (years[0] if years else None)
    exam_types = get_exam_types(current_year.id if current_year else None)

    levels = AcademicLevel.query.filter_by(is_active=True).order_by(AcademicLevel.sort_order).all()
    
    return render_template(
        "admin/hall_roster.html",
        years=years,
        current_year=current_year,
        exam_types=exam_types,
        levels=levels,
        settings=get_settings(),
    )


@attendance_bp.route("/timetable")
def timetable():
    """Timetable setup is the single source for attendance session subjects."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    current_year = AcademicYear.query.filter_by(is_current=True).first() or (years[0] if years else None)
    return render_template(
        "admin/exam_timetable.html",
        years=years,
        current_year=current_year,
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

    sessions = sessions_in_scope(year_id, exam, legacy_exam_type)
    if hall_id:
        hall = db.session.get(ExamHall, hall_id)
        if not hall or not hall.is_active:
            return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo.", "sessions": []}), 404
        enrolled_ids = [row.student_id for row in ExamHallEnrollment.query.filter_by(exam_hall_id=hall.id).all()]
        students = Student.query.filter(Student.id.in_(enrolled_ids)).all() if enrolled_ids else []
        hall_level_ids = {effective_student_level_id(student) for student in students}
        hall_level_ids.discard(None)
        sessions = [
            session for session in sessions
            if {assignment.academic_level_id for assignment in session.subject_assignments} & hall_level_ids
        ]

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
            if assignment.subject and assignment.subject.academic_level_id == assignment.academic_level_id
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
    seen_subject_ids = set()
    for assignment in assignments:
        level_id = parse_int(assignment.get("level_id")) if isinstance(assignment, dict) else None
        subject_id = parse_int(assignment.get("subject_id")) if isinstance(assignment, dict) else None
        subject = db.session.get(Subject, subject_id) if subject_id else None
        level = db.session.get(AcademicLevel, level_id) if level_id else None
        if not level or not level.is_active or not subject or not subject.is_active:
            return jsonify({"success": False, "error": "Waxaa jira level ama maado aan sax ahayn."}), 400
        if subject.academic_level_id != level.id:
            return jsonify({"success": False, "error": f"{subject.name} kuma xirna heerka {level.name}."}), 400
        if (level.id, subject.id) not in allowed_pairs:
            return jsonify({"success": False, "error": "Level-ka ama maadada laguma dejin sanadkan iyo nooca imtixaankan."}), 400
        if subject.id in seen_subject_ids:
            return jsonify({"success": False, "error": "Maado isku mid ah laba jeer looma dejin karo fadhigan."}), 400
        seen_subject_ids.add(subject.id)
        selected.append((level.id, subject.id))

    conflicts = conflicting_subject_assignments(session, selected)
    if conflicts:
        names = ", ".join(sorted({row.subject.name for row in conflicts if row.subject}))
        return jsonify({
            "success": False,
            "error": f"Maadadan hore ayaa loogu dejiyey fadhi kale oo imtixaankan ah: {names}."
        }), 409

    try:
        ExamSessionSubject.query.filter_by(exam_session_id=session.id).delete(synchronize_session=False)
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
    except IntegrityError:
        db.session.rollback()
        # The database scope constraint is the final guard for concurrent
        # browsers. Give the same clear business error as the pre-check.
        return jsonify({
            "success": False,
            "error": "Maadadan hore ayaa loogu dejiyey fadhi kale oo imtixaankan ah."
        }), 409
    audit("Exam Timetable", f"Updated subjects for session '{session.sitting_label}'")
    db.session.refresh(session)
    return jsonify({"success": True, "session": serialize_session(session, allowed_pairs)})


@attendance_bp.route("/api/sessions/<int:session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    session = db.session.get(ExamSession, session_id)
    if not session:
        return jsonify({"success": False, "error": "Fadhiga jadwalka lama heli karo."}), 404
    label = session.sitting_label
    try:
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
    if exam_id:
        exam = db.session.get(Exam, exam_id)
        if not exam or exam.academic_year_id != year_id:
            return jsonify({"success": True, "halls": []})
        legacy_exam_type = ExamType.query.filter_by(
            academic_year_id=exam.academic_year_id,
            name=exam.name,
        ).first()
        hall_scope = [ExamHall.exam_id == exam_id]
        if legacy_exam_type:
            hall_scope.append(and_(ExamHall.exam_id.is_(None), ExamHall.exam_type_id == legacy_exam_type.id))
        query = ExamHall.query.filter(ExamHall.is_active.is_(True), or_(*hall_scope))
    else:
        legacy_exam_type = db.session.get(ExamType, legacy_exam_type_id)
        if not legacy_exam_type or legacy_exam_type.academic_year_id != year_id:
            return jsonify({"success": True, "halls": []})
        query = ExamHall.query.filter(
            ExamHall.is_active.is_(True),
            ExamHall.exam_type_id == legacy_exam_type_id,
        )

    halls = query.order_by(ExamHall.sort_order, ExamHall.name).all()
    return jsonify({
        "success": True,
        "halls": [{
            "id": h.id,
            "name": h.name,
            "academic_class_id": h.academic_class_id,
            "academic_level_id": h.academic_class.academic_level_id if h.academic_class else None,
            "exam_id": h.exam_id or exam_id,
            "exam_type_id": h.exam_type_id,
        } for h in halls]
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
        "hall": {"id": hall.id, "name": hall.name, "academic_class_id": hall.academic_class_id, "exam_id": hall.exam_id}
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

    if not exam_hall_id:
        return jsonify({"success": True, "assigned_students": [], "available_students": []})

    hall = db.session.get(ExamHall, exam_hall_id)
    exam_context = hall_exam_context(hall) if hall else None
    if not hall or not hall.is_active or not exam_context:
        return jsonify({"success": False, "error": "Hall-ka la doortay lama heli karo."}), 404
    if class_id:
        selected_class = db.session.get(AcademicClass, class_id)
        if not selected_class or (level_id and selected_class.academic_level_id != level_id):
            return jsonify({"success": False, "error": "Fasalka la doortay kuma jiro heerkan."}), 400

    # Assigned students for this hall
    # Enrollment order is the source of truth for class-group order.  This keeps
    # the roster in the order classes were added to the hall, instead of letting
    # an unordered student query silently alphabetize or interleave groups.
    enrollments = (
        ExamHallEnrollment.query
        .filter_by(exam_hall_id=exam_hall_id)
        .order_by(ExamHallEnrollment.created_at, ExamHallEnrollment.id)
        .all()
    )
    assigned_student_ids = [e.student_id for e in enrollments]
    same_context_enrollments = (
        ExamHallEnrollment.query
        .join(ExamHall, ExamHallEnrollment.exam_hall_id == ExamHall.id)
        .filter(
            ExamHallEnrollment.student_id.isnot(None),
            ExamHall.is_active.is_(True),
            or_(*same_exam_hall_filters(hall)),
        )
        .all()
    )
    assignment_map = {en.student_id: en.exam_hall for en in same_context_enrollments if en.exam_hall_id != exam_hall_id}

    try:
        scope_query = attendance_student_scope_query(
            exam_context.academic_year_id,
            exam=exam_context,
            level_id=level_id,
            class_id=class_id,
        )
    except EnrollmentValidationError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    assigned_students_raw = {}
    if assigned_student_ids:
        assigned_students_raw = {
            student.id: student
            for student in scope_query.filter(Student.id.in_(assigned_student_ids)).all()
        }
    
    # Filter assigned list by search query
    assigned_list = []
    for enrollment in enrollments:
        s = assigned_students_raw.get(enrollment.student_id)
        if not s:
            continue
        if q and q not in s.student_code.lower() and q not in s.full_name.lower():
            continue
        placement = enrollment_placement_for_student(s, exam_context.academic_year_id)
        cls_name = (placement or {}).get("class_name") or (
            s.academic_class.name if s.academic_class else (s.school_class.name if s.school_class else (s.level or ""))
        )
        assigned_list.append({
            "id": s.id,
            "student_code": s.student_code,
            "full_name": s.full_name,
            "class_name": cls_name,
            "academic_class_id": (placement or {}).get("academic_class_id") or s.academic_class_id,
            "assigned_hall_id": hall.id,
            "assigned_hall_name": hall.name,
            "is_current_hall": True,
            "photo_url": stored_photo_url(s.photo_path),
        })

    # Available pool: system students matching level/class filters NOT YET assigned
    pool_query = scope_query.filter(Student.is_active.is_(True))

    if assigned_student_ids:
        pool_query = pool_query.filter(~Student.id.in_(assigned_student_ids))

    if q:
        pool_query = pool_query.filter(or_(Student.student_code.ilike(f"%{q}%"), Student.full_name.ilike(f"%{q}%")))

    available_students_raw = pool_query.order_by(Student.full_name).limit(300).all()

    available_list = []
    for s in available_students_raw:
        placement = enrollment_placement_for_student(s, exam_context.academic_year_id)
        cls_name = (placement or {}).get("class_name") or (s.academic_class.name if s.academic_class else (s.school_class.name if s.school_class else (s.level or "")))
        assigned_hall = assignment_map.get(s.id)
        available_list.append({
            "id": s.id,
            "student_code": s.student_code,
            "full_name": s.full_name,
            "class_name": cls_name,
            "academic_class_id": (placement or {}).get("academic_class_id") or s.academic_class_id,
            "assigned_hall_id": assigned_hall.id if assigned_hall else None,
            "assigned_hall_name": assigned_hall.name if assigned_hall else "",
            "is_current_hall": False,
            "photo_url": stored_photo_url(s.photo_path),
        })

    return jsonify({
        "success": True,
        "assigned_students": assigned_list,
        "available_students": available_list,
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
    student = db.session.get(Student, student_id)
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
    if existing_target:
        return jsonify({"success": True, "message": "Ardaygu hore ayuu hall-kan ugu qornaa."})

    previous_assignments = (
        ExamHallEnrollment.query
        .join(ExamHall, ExamHallEnrollment.exam_hall_id == ExamHall.id)
        .filter(
            ExamHallEnrollment.student_id == student_id,
            ExamHall.is_active.is_(True),
            or_(*same_exam_hall_filters(target_hall)),
        )
        .all()
    )
    previous_hall_names = [en.exam_hall.name for en in previous_assignments if en.exam_hall]

    try:
        for enrollment in previous_assignments:
            db.session.delete(enrollment)
        db.session.add(ExamHallEnrollment(exam_hall_id=target_hall_id, student_id=student_id))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "error": "Transfer-ka lama dhammeystiri karin. Fadlan mar kale isku day."}), 409

    audit("Hall Roster", f"Transferred {student.student_code} from {', '.join(previous_hall_names) or 'no hall'} to '{target_hall.name}'")
    return jsonify({
        "success": True,
        "student_id": student_id,
        "from_halls": previous_hall_names,
        "to_hall": {"id": target_hall.id, "name": target_hall.name},
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
    enrollments = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id).all()
    enrolled_student_ids = [e.student_id for e in enrollments]

    students_raw = (
        student_enrollment_scope_query(year_id)
        .filter(Student.id.in_(enrolled_student_ids))
        .order_by(Student.full_name)
        .all()
        if enrolled_student_ids else []
    )

    # Fetch recorded attendance for this hall & subject
    records = AttendanceRecord.query.filter_by(exam_hall_id=exam_hall_id, subject_id=subject_id).order_by(AttendanceRecord.id.asc()).all() if subject_id else []
    record_map = {}
    for r in records:
        record_map[r.student_id] = normalize_attendance_status(r.status)

    students_data = []
    tallies = {st["key"]: 0 for st in ATTENDANCE_STATUSES}

    for s in students_raw:
        # An absent record is intentionally unmarked. Attendance becomes a
        # committed per-subject decision only after the operator clicks a status.
        st_val = record_map.get(s.id)
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

    return jsonify({"success": True, "student_id": student_id, "status": record.status, "record_id": record.id})


def mark_session_bulk_attendance(*, hall, exam, legacy_exam_type, session, year_id, status_val):
    """Apply one bulk action to every visible student x subject slot in a sitting."""
    enrollment_rows = ExamHallEnrollment.query.filter_by(exam_hall_id=hall.id).all()
    student_ids = [row.student_id for row in enrollment_rows]
    students = (
        student_enrollment_scope_query(year_id)
        .filter(Student.id.in_(student_ids))
        .all()
        if student_ids else []
    )
    _, allowed_pairs = timetable_level_scope(year_id, exam=exam)
    subject_map = subjects_by_session_level(session, allowed_pairs)

    if status_val == "clear":
        removed = (
            AttendanceRecord.query
            .filter_by(exam_hall_id=hall.id, exam_session_id=session.id)
            .delete(synchronize_session=False)
        )
        db.session.commit()
        audit("Bulk Attendance", f"Cleared {removed} session slots in Hall {hall.id}")
        return {"success": True, "updated_count": removed, "status": "clear"}

    status_val = normalize_attendance_status(status_val)
    if status_val not in ATTENDANCE_STATUS_KEYS:
        return {"success": False, "error": "Xaaladda xaadirinta lama aqoonsan."}, 400

    updated_count = 0
    try:
        for student in students:
            placement = enrollment_placement_for_student(student, year_id) or {}
            for subject in subject_map.get(placement.get("academic_level_id") or effective_student_level_id(student), []):
                record = attendance_record_for(
                    student.id,
                    hall.id,
                    subject.id,
                    year_id=year_id,
                    exam_id=exam.id if exam else None,
                    exam_type_id=legacy_exam_type.id if legacy_exam_type else None,
                    exam_session_id=session.id,
                )
                if not record:
                    record = AttendanceRecord(
                        student_id=student.id,
                        exam_hall_id=hall.id,
                        subject_id=subject.id,
                        exam_session_id=session.id,
                    )
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
                )
                db.session.add(record)
                updated_count += 1
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

    rows = query.order_by(AttendanceRecord.recorded_at.desc()).limit(1000).all()

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    tmp = NamedTemporaryFile(delete=False, suffix=".pdf")
    doc = SimpleDocTemplate(tmp.name, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    data = [["Date", "Student ID", "Name", "Exam Hall", "Subject", "Status"]]
    
    for row in rows:
        data.append([
            row.recorded_at.strftime("%Y-%m-%d %H:%M") if row.recorded_at else "",
            row.student.student_code if row.student else "",
            row.student.full_name if row.student else "",
            row.exam_hall.name if row.exam_hall else "",
            row.subject.name if row.subject else "",
            row.status.capitalize(),
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002060")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbff")]),
    ]))
    settings = get_settings()
    doc.build([Paragraph(f"{settings.get('school_name', 'SULTAN EMS')} Attendance Report", styles["Title"]), Spacer(1, 12), table])
    return send_file(tmp.name, as_attachment=True, download_name="attendance.pdf")


@attendance_bp.route("/print")
def print_sheet():
    exam_hall_id = request.args.get("exam_hall_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    exam_session_id = request.args.get("exam_session_id", type=int)

    enrollments = ExamHallEnrollment.query.filter_by(exam_hall_id=exam_hall_id).all() if exam_hall_id else []
    student_ids = [e.student_id for e in enrollments]
    students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.full_name).all() if student_ids else []

    hall = db.session.get(ExamHall, exam_hall_id) if exam_hall_id else None
    subject = db.session.get(Subject, subject_id) if subject_id else None
    session = db.session.get(ExamSession, exam_session_id) if exam_session_id else None

    return render_template(
        "admin/attendance_print.html",
        students=students,
        hall=hall,
        subject=subject,
        exam_session=session,
        statuses=ATTENDANCE_STATUSES,
        settings=get_settings(),
        today=date.today(),
    )
