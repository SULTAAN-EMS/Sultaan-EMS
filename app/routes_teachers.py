from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import Workbook
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload, selectinload

from . import db
from .audit import audit
from .cloudinary_service import upload_image
from .enrollment_service import EnrollmentValidationError
from .models import AcademicClass, AcademicLevel, AcademicSection, AcademicYear, AcademicYearClass, AcademicYearLevel, AcademicYearSubject, ClubTeam, ClubTeamLeaderAssignment, ClubTeamMembership, Exam, Result, Setting, Student, StudentClub, StudentEnrollment, Subject, Teacher, TeacherActivity, TeacherResultAssignment, TeacherResultEvent, TeacherResultMark, TeacherResultMarkRevision, TeacherResultSubmission, User
from .permissions import enforce_endpoint_permission
from .security import ALLOWED_PHOTOS, allowed_file
from .teacher_services import (
    TEACHER_PERMISSIONS,
    create_or_update_teacher_user,
    default_teacher_username,
    generate_teacher_code,
    get_teacher_settings,
    log_teacher_activity,
    parse_teacher_form_dates,
    seed_teacher_settings,
    set_teacher_permissions,
    teacher_activity_summary,
    teacher_list_query,
    teacher_performance_data,
    teacher_permission_set,
)
from .routes_advanced_results import students_for_scope_query, _year_scope_ids_from_legacy
from .services import resolve_subject_max_score
from .teacher_result_history import record_teacher_result_event

teachers_bp = Blueprint("admin_teachers", __name__)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _submission_time_label(submitted_at):
    if submitted_at is None:
        return None
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=ZoneInfo("UTC"))
    local_time = submitted_at.astimezone(ZoneInfo("Africa/Mogadishu"))
    weekdays = ("Isniin", "Talaado", "Arbaca", "Khamiis", "Jimco", "Sabti", "Axad")
    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    return (
        f"{weekdays[local_time.weekday()]}, {months[local_time.month - 1]} "
        f"{local_time.day}, {local_time.year} {local_time.strftime('%I:%M %p')} EAT"
    )


RESULT_EVENT_LABELS = {
    "assignment_created": "Xilsaaris la sameeyey",
    "assignment_updated": "Xilsaaris la cusboonaysiiyey",
    "assignment_paused": "Xilsaaris la hakiyey",
    "assignment_activated": "Xilsaaris dib loo hawlgeliyey",
    "assignment_deleted": "Xilsaaris kayd ahaan loo tirtiray",
    "assignment_restored": "Xilsaaris dib loo soo celiyey",
    "draft_saved": "Qabyo la kaydiyey",
    "submitted": "Natiijo loo gudbiyey maamulka",
    "resubmitted": "Natiijo dib loo gudbiyey",
    "approved": "La ansixiyey oo la daabacay",
    "partially_approved": "Qayb ahaan la ansixiyey",
    "returned_for_correction": "Sixitaan loo celiyey",
    "student_mark_returned_for_correction": "Hal arday sixitaan loo celiyey",
    "baseline": "Xaaladdii hore ee la diiwaangeliyey",
}
RESULT_STATUS_LABELS = {
    "draft": "Qabyo",
    "submitted": "Sugaysa dib-u-eegis",
    "approved": "La ansixiyey",
    "reopened": "Sixitaan loo furay",
    "active": "Firfircoon",
    "paused": "La hakiyey",
    "deleted": "La tirtiray (kayd ah)",
}
RESULT_EVENT_ICONS = {
    "assignment_created": "fa-circle-plus",
    "assignment_updated": "fa-sliders",
    "assignment_paused": "fa-pause",
    "assignment_activated": "fa-play",
    "assignment_deleted": "fa-trash-can",
    "assignment_restored": "fa-trash-arrow-up",
    "draft_saved": "fa-floppy-disk",
    "submitted": "fa-paper-plane",
    "resubmitted": "fa-rotate",
    "approved": "fa-circle-check",
    "partially_approved": "fa-circle-half-stroke",
    "returned_for_correction": "fa-rotate-left",
    "student_mark_returned_for_correction": "fa-user-pen",
    "baseline": "fa-clock-rotate-left",
}


def _teacher_result_history_query():
    filters = {
        "q": request.args.get("q", "").strip()[:120],
        "teacher": request.args.get("teacher", "").strip()[:150],
        "year": request.args.get("year", "").strip()[:40],
        "exam": request.args.get("exam", "").strip()[:150],
        "event_type": request.args.get("event_type", "").strip(),
        "from_date": request.args.get("from_date", "").strip(),
        "to_date": request.args.get("to_date", "").strip(),
    }
    if filters["event_type"] not in RESULT_EVENT_LABELS:
        filters["event_type"] = ""

    query = TeacherResultEvent.query
    if filters["teacher"]:
        query = query.filter(TeacherResultEvent.teacher_name == filters["teacher"])
    if filters["year"]:
        query = query.filter(TeacherResultEvent.academic_year_name == filters["year"])
    if filters["exam"]:
        query = query.filter(TeacherResultEvent.exam_name == filters["exam"])
    if filters["event_type"]:
        query = query.filter(TeacherResultEvent.event_type == filters["event_type"])
    if filters["q"]:
        pattern = f"%{filters['q']}%"
        query = query.filter(or_(
            TeacherResultEvent.teacher_name.ilike(pattern),
            TeacherResultEvent.teacher_code.ilike(pattern),
            TeacherResultEvent.actor_name.ilike(pattern),
            TeacherResultEvent.exam_name.ilike(pattern),
            TeacherResultEvent.class_name.ilike(pattern),
            TeacherResultEvent.subject_name.ilike(pattern),
            TeacherResultEvent.note.ilike(pattern),
        ))

    date_error = None
    timezone = ZoneInfo("Africa/Mogadishu")
    for key, is_end in (("from_date", False), ("to_date", True)):
        raw_value = filters[key]
        if not raw_value:
            continue
        try:
            parsed = date.fromisoformat(raw_value)
        except ValueError:
            date_error = "Taariikhda shaandhaynta sax ma aha."
            continue
        boundary_date = parsed + timedelta(days=1) if is_end else parsed
        boundary = datetime.combine(boundary_date, time.min, tzinfo=timezone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        query = query.filter(
            TeacherResultEvent.created_at < boundary if is_end else TeacherResultEvent.created_at >= boundary
        )
    return query, filters, date_error


def _teacher_profile_scopes(teacher):
    """Build teacher profile choices from the current year's configured catalog."""
    years = AcademicYear.query.order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc()).all()
    year = next((item for item in years if item.is_current), years[0] if years else None)
    year_levels = []
    if year:
        year_levels = (
            AcademicYearLevel.query
            .filter_by(academic_year_id=year.id, is_active=True)
            .order_by(AcademicYearLevel.sort_order, AcademicYearLevel.name)
            .all()
        )
        year_levels = [
            item for item in year_levels
            if item.legacy_level and item.legacy_level.is_active
        ]

    groups = []
    current_level_ids = set()
    current_class_ids = set()
    current_section_ids = set()
    current_subject_ids = set()
    level_groups = {}

    for year_level in year_levels:
        level = year_level.legacy_level
        level_id = level.id
        current_level_ids.add(level_id)
        group = level_groups.get(level_id)
        if group is None:
            group = {
                "level": level,
                "label": year_level.name,
                "is_legacy": False,
                "classes": [],
                "subjects": [],
            }
            level_groups[level_id] = group
            groups.append(group)

        year_classes = (
            AcademicYearClass.query
            .filter_by(academic_year_level_id=year_level.id, is_active=True)
            .order_by(AcademicYearClass.sort_order, AcademicYearClass.name)
            .all()
        )
        for year_class in year_classes:
            school_class = year_class.legacy_class
            assignable = bool(
                school_class and school_class.is_active
                and school_class.academic_level_id == level_id
            )
            if assignable:
                current_class_ids.add(school_class.id)
            linked_sections = [
                section for section in teacher.sections
                if assignable and section.academic_class_id == school_class.id
            ]
            sections = (
                AcademicSection.query
                .filter(AcademicSection.academic_class_id == school_class.id)
                .filter(db.or_(AcademicSection.is_active.is_(True), AcademicSection.id.in_([s.id for s in linked_sections])))
                .order_by(AcademicSection.sort_order, AcademicSection.name)
                .all()
            ) if assignable else []
            current_section_ids.update(section.id for section in sections if section.is_active)
            group["classes"].append({
                "record": school_class,
                "label": year_class.name,
                "sections": sections,
                "assignable": assignable,
                "is_legacy": False,
                "missing_bridge": school_class is None,
                "inactive_bridge": bool(school_class and not school_class.is_active),
                "wrong_level_bridge": bool(school_class and school_class.academic_level_id != level_id),
            })

        year_subjects = (
            AcademicYearSubject.query
            .filter_by(
                academic_year_id=year.id,
                academic_year_level_id=year_level.id,
                is_active=True,
                subject_kind="exam",
            )
            .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name)
            .all()
        )
        for year_subject in year_subjects:
            subject = year_subject.legacy_subject
            assignable = bool(
                subject and subject.is_active
                and (subject.academic_level_id is None or subject.academic_level_id == level_id)
            )
            if assignable:
                current_subject_ids.add(subject.id)
            group["subjects"].append({
                "record": subject,
                "label": year_subject.name,
                "assignable": assignable,
                "is_legacy": False,
                "missing_bridge": subject is None,
                "inactive_bridge": bool(subject and not subject.is_active),
                "wrong_level_bridge": bool(
                    subject and subject.academic_level_id not in (None, level_id)
                ),
            })

    linked_level_ids = {item.id for item in teacher.academic_levels}
    linked_class_ids = {item.id for item in teacher.classes}
    linked_section_ids = {item.id for item in teacher.sections}
    linked_subject_ids = {item.id for item in teacher.subjects}
    linked_level_ids.update(item.academic_level_id for item in teacher.classes)
    linked_level_ids.update(item.academic_level_id for item in teacher.subjects if item.academic_level_id)

    # Keep explicitly assigned historic scopes visible while excluding unrelated legacy catalog rows.
    for group in groups:
        level_id = group["level"].id
        known_class_ids = {
            item["record"].id for item in group["classes"] if item["record"]
        }
        for school_class in teacher.classes:
            if school_class.academic_level_id == level_id and school_class.id not in known_class_ids:
                group["classes"].append({
                    "record": school_class,
                    "label": school_class.name,
                    "sections": [section for section in teacher.sections if section.academic_class_id == school_class.id],
                    "assignable": True,
                    "is_legacy": True,
                })
        known_subject_ids = {
            item["record"].id for item in group["subjects"] if item["record"]
        }
        for subject in teacher.subjects:
            if subject.academic_level_id == level_id and subject.id not in known_subject_ids:
                group["subjects"].append({
                    "record": subject,
                    "label": subject.name,
                    "assignable": True,
                    "is_legacy": True,
                })

    historic_level_ids = linked_level_ids - current_level_ids
    for level_id in sorted(historic_level_ids):
        level = db.session.get(AcademicLevel, level_id)
        if not level:
            continue
        historic_classes = [item for item in teacher.classes if item.academic_level_id == level_id]
        historic_subjects = [item for item in teacher.subjects if item.academic_level_id == level_id]
        if not historic_classes and not historic_subjects and level not in teacher.academic_levels:
            continue
        groups.append({
            "level": level,
            "label": level.name,
            "is_legacy": True,
            "classes": [{
                "record": school_class,
                "label": school_class.name,
                "sections": [section for section in teacher.sections if section.academic_class_id == school_class.id],
                "assignable": True,
                "is_legacy": True,
            } for school_class in historic_classes],
            "subjects": [{
                "record": subject,
                "label": subject.name,
                "assignable": True,
                "is_legacy": True,
            } for subject in historic_subjects],
        })

    current_subject_ids.update(
        item["record"].id
        for group in groups if not group["is_legacy"]
        for item in group["subjects"]
        if item["record"] and item["assignable"] and not item["is_legacy"]
    )
    shared_subjects = [
        subject for subject in teacher.subjects
        if subject.academic_level_id is None and subject.id not in current_subject_ids
    ]
    return {
        "year": year,
        "groups": groups,
        "shared_subjects": shared_subjects,
        "linked_level_ids": linked_level_ids,
        "linked_class_ids": linked_class_ids,
        "linked_section_ids": linked_section_ids,
        "linked_subject_ids": linked_subject_ids,
        "current_level_ids": current_level_ids,
        "current_class_ids": current_class_ids,
        "current_section_ids": current_section_ids,
        "current_subject_ids": current_subject_ids,
    }


@teachers_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@teachers_bp.route("/")
def dashboard():
    return redirect(url_for("admin_teachers.result_entry_setup"))


@teachers_bp.route("/result-entry", methods=["GET", "POST"])
def result_entry_setup():
    """Assign exam/class/subject scopes, set deadlines, and review submissions."""
    if request.method == "POST":
        action = request.form.get("action")
        if action in {"create", "update"}:
            edit_assignment = None
            if action == "update":
                edit_assignment = db.session.get(
                    TeacherResultAssignment, request.form.get("assignment_id", type=int)
                )
                if not edit_assignment:
                    abort(404)
                if edit_assignment.deleted_at:
                    flash("Xilsaaristan waa la tirtiray. Marka hore dib uga soo celi diiwaanka tirtiridda.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup", status="deleted"))

            teacher = db.session.get(Teacher, request.form.get("teacher_id", type=int))
            row_fields = (
                "academic_year_id", "exam_id", "academic_year_level_id",
                "academic_year_class_id", "academic_year_subject_id",
            )
            row_values = {field: request.form.getlist(field) for field in row_fields}
            row_count = max((len(values) for values in row_values.values()), default=0)
            deadline_values = request.form.getlist("deadline_at") or [""] * row_count
            max_values = request.form.getlist("max_submissions") or ["1"] * row_count
            if not teacher or not teacher.is_active or teacher.employment_status != "Active":
                flash("Dooro macallin firfircoon oo sax ah.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            if (
                not 1 <= row_count <= 30
                or (action == "update" and row_count != 1)
                or any(len(values) != row_count for values in row_values.values())
            ):
                flash("Ku dar ugu yaraan hal scope oo dhammeystiran; ugu badnaan 30 ayaa hal mar la kaydin karaa.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            if len(deadline_values) != row_count or len(max_values) != row_count:
                flash("Hubi in deadline-ka iyo tirada gudbintu scope kasta la socdaan.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))

            prepared = []
            seen_scopes = set()
            for index in range(row_count):
                year = db.session.get(AcademicYear, _safe_int(row_values["academic_year_id"][index]))
                exam = db.session.get(Exam, _safe_int(row_values["exam_id"][index]))
                year_level = db.session.get(AcademicYearLevel, _safe_int(row_values["academic_year_level_id"][index]))
                year_class = db.session.get(AcademicYearClass, _safe_int(row_values["academic_year_class_id"][index]))
                year_subject = db.session.get(AcademicYearSubject, _safe_int(row_values["academic_year_subject_id"][index]))
                school_class = year_class.legacy_class if year_class else None
                subject = year_subject.legacy_subject if year_subject else None
                deadline_raw = (deadline_values[index] or "").strip()
                try:
                    deadline = (
                        datetime.fromisoformat(deadline_raw)
                        .replace(tzinfo=ZoneInfo("Africa/Mogadishu"))
                        .astimezone(ZoneInfo("UTC"))
                        .replace(tzinfo=None)
                        if deadline_raw else None
                    )
                    max_submissions = max(1, min(10, int(max_values[index] or "1")))
                except (TypeError, ValueError):
                    flash("Fadlan hubi deadline-ka iyo tirada gudbinta scope kasta.", "danger")
                    return redirect(url_for("admin_teachers.result_entry_setup"))

                year_scope_matches = bool(
                    year and year_level and year_level.is_active
                    and year_level.academic_year_id == year.id
                    and year_level.legacy_level and year_level.legacy_level.is_active
                    and year_class and year_class.is_active
                    and year_class.academic_year_level_id == year_level.id
                    and school_class and school_class.is_active
                    and school_class.academic_level_id == year_level.legacy_level_id
                )
                subject_is_offered = bool(
                    year_scope_matches and year_subject and year_subject.is_active
                    and year_subject.subject_kind == "exam"
                    and year_subject.academic_year_id == year.id
                    and year_subject.academic_year_level_id == year_level.id
                    and subject and subject.is_active
                    and (subject.academic_level_id is None or subject.academic_level_id == year_level.legacy_level_id)
                )
                exam_matches = bool(
                    exam and exam.is_active and year_scope_matches
                    and exam.academic_year_id == year.id
                    and (exam.academic_level_id is None or exam.academic_level_id == year_level.legacy_level_id)
                    and (exam.academic_class_id is None or exam.academic_class_id == school_class.id)
                )
                if not all((year_scope_matches, subject_is_offered, exam_matches)):
                    flash("Scope kasta waa inuu si sax ah isugu waafaqaa sannadka, heerka, fasalka, maaddada iyo imtixaanka.", "danger")
                    return redirect(url_for("admin_teachers.result_entry_setup"))

                old_scope = (
                    edit_assignment.teacher_id,
                    edit_assignment.academic_year_id,
                    edit_assignment.exam_id,
                    edit_assignment.class_id,
                    edit_assignment.subject_id,
                ) if edit_assignment else None
                new_scope = (teacher.id, year.id, exam.id, school_class.id, subject.id)
                scope_changed = old_scope != new_scope

                scope_key = (year.id, exam.id, school_class.id, subject.id)
                if scope_key in seen_scopes:
                    flash("Scope isku mid ah laba jeer ayaa lagu daray. Ka saar nuqulka ka hor kaydinta.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup"))
                seen_scopes.add(scope_key)
                prepared.append((year, exam, school_class, subject, deadline, max_submissions))

            if action == "update":
                year, exam, school_class, subject, deadline, max_submissions = prepared[0]
                submission = edit_assignment.submissions[0] if edit_assignment.submissions else None

                duplicate = TeacherResultAssignment.query.filter_by(
                    teacher_id=teacher.id,
                    academic_year_id=year.id,
                    exam_id=exam.id,
                    class_id=school_class.id,
                    subject_id=subject.id,
                ).filter(TeacherResultAssignment.id != edit_assignment.id).first()
                if duplicate:
                    if duplicate.deleted_at:
                        flash("Scope-kan hore ayaa ugu jiray diiwaanka tirtiridda. Ka soo celi xilsaaristaas ka hor intaadan sii wadin.", "warning")
                        return redirect(url_for("admin_teachers.result_entry_setup", status="deleted"))
                    flash("Macallinkan scope-kan hore ayaa loogu xilsaaray. Dooro scope kale ama tafatir xilsaaristii jirtay.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup"))

                assigned_to_other_teacher = TeacherResultAssignment.query.filter_by(
                    academic_year_id=year.id,
                    exam_id=exam.id,
                    class_id=school_class.id,
                    subject_id=subject.id,
                ).filter(
                    TeacherResultAssignment.id != edit_assignment.id,
                    TeacherResultAssignment.teacher_id != teacher.id,
                    TeacherResultAssignment.deleted_at.is_(None),
                ).first()
                if scope_changed and assigned_to_other_teacher:
                    flash("Maaddadan fasalkan iyo imtixaankan macallin kale ayaa hore loogu xilsaaray.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup"))

                if scope_changed and submission:
                    flash("Xaashida natiijadu way jirtaa; macallinka, sannadka, imtixaanka, fasalka iyo maaddada lama beddeli karo si dhibcaha iyo history-gu u sii ahaadaan kuwo sax ku xidhan. Deadline-ka iyo tirada gudbinta waad beddeli kartaa.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup"))

                previous_state = "active" if edit_assignment.is_active else "paused"
                edit_assignment.teacher = teacher
                edit_assignment.academic_year = year
                edit_assignment.exam = exam
                edit_assignment.school_class = school_class
                edit_assignment.subject = subject
                edit_assignment.deadline_at = deadline
                edit_assignment.max_submissions = max(
                    max_submissions, submission.submission_count if submission else 1
                )
                record_teacher_result_event(
                    edit_assignment,
                    "assignment_updated",
                    submission=submission,
                    status_from=previous_state,
                    status_to=previous_state,
                    note="Admin-ku wuxuu tafatiray scope-ka iyo xeerarka xilsaarista.",
                )
                audit(
                    "Teacher Result Assignment Updated",
                    f"Updated {teacher.full_name}: {exam.name} / {school_class.name} / {subject.name}",
                )
                db.session.commit()
                flash("Xilsaarista waa la tafatiray, xogta xaashida natiijadana waa la ilaaliyey.", "success")
                return redirect(url_for("admin_teachers.result_entry_setup"))

            created_count = 0
            updated_count = 0
            audit_scopes = []
            for year, exam, school_class, subject, deadline, max_submissions in prepared:
                duplicate = TeacherResultAssignment.query.filter_by(
                    teacher_id=teacher.id, academic_year_id=year.id, exam_id=exam.id,
                    class_id=school_class.id, subject_id=subject.id,
                ).first()
                if duplicate and duplicate.deleted_at:
                    flash("Xilsaaristan hore ayaa loo tirtiray. Ka soo celi liiska 'La tirtiray' ka hor intaadan dib u qoondayn.", "warning")
                    return redirect(url_for("admin_teachers.result_entry_setup", status="deleted"))
                if duplicate:
                    previous_state = "active" if duplicate.is_active else "paused"
                    duplicate.deadline_at = deadline
                    duplicate.max_submissions = max(max_submissions, duplicate.submissions[0].submission_count if duplicate.submissions else 1)
                    duplicate.is_active = True
                    updated_count += 1
                    record_teacher_result_event(
                        duplicate, "assignment_updated", status_from=previous_state,
                        status_to="active", note="Scope-ka iyo xeerarkiisa waa la cusboonaysiiyey.",
                    )
                else:
                    assigned_scope = TeacherResultAssignment.query.filter_by(
                        academic_year_id=year.id,
                        exam_id=exam.id,
                        class_id=school_class.id,
                        subject_id=subject.id,
                    ).filter(
                        TeacherResultAssignment.deleted_at.is_(None),
                    ).first()
                    if assigned_scope:
                        flash("Maaddadan fasalkan iyo imtixaankan macallin kale ayaa hore loogu xilsaaray.", "warning")
                        return redirect(url_for("admin_teachers.result_entry_setup"))
                    assignment = TeacherResultAssignment(
                        teacher=teacher, academic_year=year, exam=exam,
                        school_class=school_class, subject=subject,
                        deadline_at=deadline, max_submissions=max_submissions,
                    )
                    db.session.add(assignment)
                    created_count += 1
                    record_teacher_result_event(
                        assignment, "assignment_created", status_to="active",
                        note="Xilsaarista gelinta natiijada waa la sameeyey.",
                    )
                audit_scopes.append(f"{exam.name} / {school_class.name} / {subject.name}")
            audit("Teacher Result Assignment", f"Configured {teacher.full_name}: {', '.join(audit_scopes)}")
            db.session.commit()
            flash(f"{created_count} xilsaaris cusub iyo {updated_count} xilsaaris oo la cusboonaysiiyey ayaa la kaydiyey.", "success")
            return redirect(url_for("admin_teachers.result_entry_setup"))

        assignment = db.session.get(TeacherResultAssignment, request.form.get("assignment_id", type=int))
        submission = TeacherResultSubmission.query.filter_by(assignment_id=assignment.id).first() if assignment else None
        if not assignment:
            abort(404)
        if action == "delete":
            if assignment.deleted_at:
                flash("Xilsaaristan hore ayaa loo tirtiray.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup", status="deleted"))
            previous_state = "active" if assignment.is_active else "paused"
            assignment.deleted_previous_is_active = assignment.is_active
            assignment.deleted_at = datetime.utcnow()
            assignment.is_active = False
            note = (request.form.get("deletion_note") or "").strip()[:500]
            record_teacher_result_event(
                assignment, "assignment_deleted", submission=submission,
                status_from=previous_state, status_to="deleted",
                note=note or "Xilsaarista waa laga saaray shaqada firfircoon; xaashida iyo dhibcaha waa la hayey.",
            )
            audit("Teacher Result Assignment Archived", f"Archived {assignment.teacher.full_name}: {assignment.exam.name} / {assignment.school_class.name} / {assignment.subject.name}")
            db.session.commit()
            flash("Xilsaarista waa laga saaray shaqada firfircoon; xaashida iyo dhibcihii hore waa la ilaaliyey. Tallaabada waxaa lagu daray history-ga.", "success")
            return redirect(url_for("admin_teachers.result_entry_setup"))
        if action == "restore":
            if not assignment.deleted_at:
                flash("Xilsaaristan kuma jirto diiwaanka tirtiridda.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            assignment.is_active = bool(assignment.deleted_previous_is_active)
            restored_state = "active" if assignment.is_active else "paused"
            assignment.deleted_at = None
            assignment.deleted_previous_is_active = None
            record_teacher_result_event(
                assignment, "assignment_restored", submission=submission,
                status_from="deleted", status_to=restored_state,
                note="Xilsaarista iyo xogteedii waa dib loo soo celiyey.",
            )
            audit("Teacher Result Assignment Restored", f"Restored {assignment.teacher.full_name}: {assignment.exam.name} / {assignment.school_class.name} / {assignment.subject.name}")
            db.session.commit()
            flash("Xilsaarista iyo xaashideedii waa la soo celiyey.", "success")
            return redirect(url_for("admin_teachers.result_entry_setup"))
        if assignment.deleted_at:
            flash("Xilsaaristan waa la tirtiray. Marka hore dib uga soo celi diiwaanka tirtiridda.", "warning")
            return redirect(url_for("admin_teachers.result_entry_setup", status="deleted"))
        if action == "toggle":
            previous_state = "active" if assignment.is_active else "paused"
            assignment.is_active = not assignment.is_active
            next_state = "active" if assignment.is_active else "paused"
            record_teacher_result_event(
                assignment,
                "assignment_activated" if assignment.is_active else "assignment_paused",
                submission=submission,
                status_from=previous_state,
                status_to=next_state,
                note="Xilsaarista gelinta natiijada waa la hawlgeliyey." if assignment.is_active else "Xilsaarista gelinta natiijada waa la hakiyey.",
            )
        elif action == "return_mark":
            if not submission or submission.status not in {"draft", "approved"}:
                flash("Arday keliya dib ayaa loogu celin karaa marka xaashidu qayb ahaan ama si buuxda u ansaxday.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            reviewer_note = (request.form.get("reviewer_note") or "").strip()
            if not reviewer_note:
                flash("Qor sababta ardaygan keliya loogu celinayo macallinka.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            mark = TeacherResultMark.query.filter_by(
                id=request.form.get("mark_id", type=int),
                submission_id=submission.id,
            ).first()
            if not mark or mark.status != "approved":
                flash("Dhibcaha ardaygan ma aha kuwo la ansixiyey ama xilsaaristan kuma jiraan.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            official_result = Result.query.filter_by(
                student_id=mark.student_id,
                exam_id=assignment.exam_id,
                subject_id=assignment.subject_id,
                is_published=True,
            ).first()
            if not official_result:
                flash("Natiijo rasmi ah oo la daabacay looma helin ardaygan; waxba lama beddelin.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            if mark.student.is_result_locked:
                flash("Natiijada ardaygan way xiran tahay. Marka hore ka fur ilaalinta natiijada.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))

            mark.status = "draft"
            submission.status = "reopened"
            submission.reviewed_at = datetime.utcnow()
            submission.reviewed_by_id = current_user.id
            assignment.max_submissions = max(
                assignment.max_submissions or 0,
                (submission.submission_count or 0) + 1,
            )
            student_label = f"{mark.student.student_code} · {mark.student.full_name}"
            event_note = f"{student_label} — {reviewer_note}"[:500]
            submission.reviewer_note = event_note
            record_teacher_result_event(
                assignment,
                "student_mark_returned_for_correction",
                submission=submission,
                status_from="approved",
                status_to="draft",
                marks_count=1,
                note=event_note,
            )
            audit(
                "Teacher Result Mark Returned",
                f"Returned approved mark for {student_label} to {assignment.teacher.full_name}: {reviewer_note}",
            )
            flash(f"Dhibcaha {student_label} oo keliya ayaa macallinka loogu celiyey sixitaan. Natiijada rasmiga ahi isma beddelin.", "success")
        elif action == "reopen":
            if not submission or submission.status not in {"submitted", "approved"}:
                flash("Kaliya submission la gudbiyey ama la ansixiyey ayaa dib loo furi karaa.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            reviewer_note = (request.form.get("reviewer_note") or "").strip()
            if not reviewer_note:
                flash("Qor sababta dib loogu furayo xaashida natiijada.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            reopen_status = "submitted" if submission.status == "submitted" else "approved"
            previous_status = submission.status
            reopened_mark_count = sum(mark.status == reopen_status for mark in submission.marks)
            submission.status = "reopened"
            for mark in submission.marks:
                if mark.status == reopen_status:
                    mark.status = "draft"
            assignment.max_submissions = max(assignment.max_submissions, submission.submission_count + 1)
            submission.reviewer_note = reviewer_note[:500]
            record_teacher_result_event(
                assignment, "returned_for_correction", submission=submission,
                status_from=previous_status, status_to="reopened",
                marks_count=reopened_mark_count, note=reviewer_note,
            )
        elif action == "approve":
            if not submission or submission.status != "submitted":
                flash("Submission-ku waa inuu ku jiraa xaaladda dib-u-eegis.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_marks = [
                mark for mark in submission.marks
                if mark.status == "submitted" and mark.score is not None
            ]
            if not pending_marks:
                flash("Ma jiraan dhibco cusub oo sugaya ansixin.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            marks = {mark.student_id: mark for mark in pending_marks}
            year_level, year_class = _year_scope_ids_from_legacy(
                assignment.academic_year_id,
                assignment.school_class.academic_level_id,
                assignment.class_id,
            )
            year_subject = AcademicYearSubject.query.filter_by(
                academic_year_id=assignment.academic_year_id,
                academic_year_level_id=year_level.id if year_level else None,
                legacy_subject_id=assignment.subject_id,
                subject_kind="exam",
                is_active=True,
            ).first() if year_level else None
            assignment_scope_valid = bool(
                year_level and year_class
                and assignment.exam
                and assignment.exam.academic_year_id == assignment.academic_year_id
                and year_class.legacy_class_id == assignment.class_id
                and year_class.academic_year_level_id == year_level.id
                and year_subject
                and assignment.subject
                and assignment.subject.is_active
                and (
                    assignment.subject.academic_level_id is None
                    or assignment.subject.academic_level_id == assignment.school_class.academic_level_id
                )
            )
            students = students_for_scope_query(
                assignment.academic_year_id,
                level_id=assignment.school_class.academic_level_id,
                class_id=assignment.class_id,
                exam=assignment.exam,
            ).all()
            roster_ids = {student.id for student in students}
            if not assignment_scope_valid or any(student_id not in roster_ids for student_id in marks):
                flash("Xogta ardayda ama fasalka/sanadku sax ma aha; lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_students = [student for student in students if student.id in marks]
            if any(student.is_result_locked for student in pending_students):
                flash("Fasalka waxaa ku jira arday natiijadoodu xiran tahay. Furitaanka natiijooyinkaas ayaa loo baahan yahay ka hor ansixinta.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            maximum = Decimal(str(resolve_subject_max_score(
                assignment.subject,
                exam=assignment.exam,
                academic_year_level_id=year_level.id,
                academic_level_id=assignment.school_class.academic_level_id,
            )))
            if (
                not maximum.is_finite()
                or maximum <= 0
                or any(
                    not Decimal(str(mark.score)).is_finite()
                    or mark.score < 0
                    or mark.score > maximum
                    for mark in pending_marks
                )
            ):
                flash("Dhibco ka baxsan xadka maaddada ayaa jira; submission-ka lama ansixin.", "danger")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            pending_ids = [student.id for student in pending_students]
            existing_result_count = Result.query.filter(
                Result.exam_id == assignment.exam_id,
                Result.subject_id == assignment.subject_id,
                Result.student_id.in_(pending_ids),
            ).count() if pending_ids else 0
            if existing_result_count and request.form.get("confirm_replace") != "yes":
                flash("Dhibco rasmi ah ayaa hore u jira. Isbarbar dhig kadib calaamadee sanduuqa si si cad loogu oggolaado beddeliddooda.", "warning")
                return redirect(url_for("admin_teachers.result_entry_setup"))
            for student in pending_students:
                result = Result.query.filter_by(
                    student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id
                ).first()
                if result is None:
                    result = Result(student_id=student.id, exam_id=assignment.exam_id, subject_id=assignment.subject_id, score=marks[student.id].score)
                    db.session.add(result)
                else:
                    result.score = marks[student.id].score
                result.is_published = True
                marks[student.id].status = "approved"
            already_published_ids = {
                student_id for (student_id,) in db.session.query(Result.student_id).filter(
                    Result.exam_id == assignment.exam_id,
                    Result.subject_id == assignment.subject_id,
                    Result.is_published.is_(True),
                    Result.student_id.in_(roster_ids),
                ).all()
            } if roster_ids else set()
            complete_roster = roster_ids.issubset(already_published_ids | set(pending_ids))
            submission.status = "approved" if complete_roster else "draft"
            submission.reviewed_at = datetime.utcnow()
            submission.reviewed_by_id = current_user.id
            submission.reviewer_note = (request.form.get("reviewer_note") or "Approved and published.").strip()[:500]
            record_teacher_result_event(
                assignment,
                "approved" if complete_roster else "partially_approved",
                submission=submission,
                status_from="submitted",
                status_to=submission.status,
                marks_count=len(pending_marks),
                note=submission.reviewer_note,
            )
            audit("Teacher Result Approval", f"Approved submission {submission.id} ({len(pending_marks)} submitted marks; {existing_result_count} existing scores replaced): {assignment.exam.name} / {assignment.school_class.name} / {assignment.subject.name}")
            if complete_roster:
                flash("Dhammaan dhibcaha xaashidan waa la ansixiyey oo la daabacay.", "success")
            else:
                flash("Qaybta dhibcaha la gudbiyey waa la ansixiyey; inta harsan macallinku wuu sii gelin karaa.", "success")
        else:
            abort(400)
        db.session.commit()
        if action not in {"approve", "return_mark"}:
            flash("Isbeddelka xilsaarista/submission-ka waa la kaydiyey.", "success")
        return redirect(url_for("admin_teachers.result_entry_setup"))

    summary_rows = (
        db.session.query(
            TeacherResultAssignment.deleted_at.isnot(None),
            TeacherResultAssignment.is_active,
            TeacherResultSubmission.status,
            func.count(TeacherResultAssignment.id),
        )
        .outerjoin(
            TeacherResultSubmission,
            TeacherResultSubmission.assignment_id == TeacherResultAssignment.id,
        )
        .group_by(
            TeacherResultAssignment.deleted_at.isnot(None),
            TeacherResultAssignment.is_active,
            TeacherResultSubmission.status,
        )
        .all()
    )
    summary = {"total": 0, "review": 0, "approved": 0, "draft": 0, "paused": 0, "deleted": 0}
    for is_deleted, is_active, submission_status, count in summary_rows:
        if is_deleted:
            summary["deleted"] += count
            continue
        summary["total"] += count
        if not is_active:
            summary["paused"] += count
        if submission_status == "submitted":
            summary["review"] += count
        elif submission_status == "approved":
            summary["approved"] += count
        elif submission_status in {"draft", "reopened"}:
            summary["draft"] += count

    status_filter = request.args.get("status", "all")
    teacher_filter = request.args.get("teacher_id", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    assignment_query = TeacherResultAssignment.query.options(
        joinedload(TeacherResultAssignment.teacher),
        joinedload(TeacherResultAssignment.academic_year),
        joinedload(TeacherResultAssignment.exam),
        joinedload(TeacherResultAssignment.school_class).joinedload(AcademicClass.academic_level),
        joinedload(TeacherResultAssignment.subject),
        selectinload(TeacherResultAssignment.submissions).joinedload(TeacherResultSubmission.reviewed_by),
        selectinload(TeacherResultAssignment.submissions)
        .selectinload(TeacherResultSubmission.marks)
        .joinedload(TeacherResultMark.student),
    ).order_by(TeacherResultAssignment.updated_at.desc(), TeacherResultAssignment.id.desc())
    if status_filter == "deleted":
        assignment_query = assignment_query.filter(TeacherResultAssignment.deleted_at.isnot(None))
    else:
        assignment_query = assignment_query.filter(TeacherResultAssignment.deleted_at.is_(None))
    if teacher_filter:
        assignment_query = assignment_query.filter(TeacherResultAssignment.teacher_id == teacher_filter)
    if status_filter == "review":
        assignment_query = assignment_query.join(TeacherResultAssignment.submissions).filter(
            TeacherResultSubmission.status == "submitted"
        )
    elif status_filter == "approved":
        assignment_query = assignment_query.join(TeacherResultAssignment.submissions).filter(
            TeacherResultSubmission.status == "approved"
        )
    elif status_filter == "draft":
        assignment_query = assignment_query.join(TeacherResultAssignment.submissions).filter(
            TeacherResultSubmission.status.in_(("draft", "reopened"))
        )
    elif status_filter == "paused":
        assignment_query = assignment_query.filter(TeacherResultAssignment.is_active.is_(False))
    pagination = assignment_query.paginate(page=page, per_page=20, error_out=False)
    assignments = pagination.items

    reservation_rows = db.session.query(
        TeacherResultAssignment.id,
        TeacherResultAssignment.academic_year_id,
        TeacherResultAssignment.exam_id,
        TeacherResultAssignment.class_id,
        TeacherResultAssignment.subject_id,
    ).all()
    assignment_scope_reservations = [
        {
            "assignment_id": row.id,
            "academic_year_id": row.academic_year_id,
            "exam_id": row.exam_id,
            "class_id": row.class_id,
            "subject_id": row.subject_id,
        }
        for row in reservation_rows
    ]

    review_rows = {}
    review_summaries = {}
    history_rows = {}
    assignments_with_existing_results = set()
    assignment_progress = {}
    submission_time_labels = {}
    history_marks_by_assignment = {}
    result_lookup_keys = set()
    review_rosters = {}
    review_roster_cache = {}
    review_roster_errors = set()
    for assignment in assignments:
        submission = next(iter(assignment.submissions), None)
        if not submission:
            continue
        if submission.submitted_at is not None or submission.status == "submitted":
            submission_time_labels[assignment.id] = _submission_time_label(submission.submitted_at)
        assignment_progress[assignment.id] = {
            "approved": sum(mark.status == "approved" for mark in submission.marks),
            "pending": sum(mark.status == "submitted" for mark in submission.marks),
            "draft": sum(mark.status == "draft" and mark.score is not None for mark in submission.marks),
        }
        history_marks = [mark for mark in submission.marks if mark.score is not None or mark.last_action]
        history_marks_by_assignment[assignment.id] = history_marks
        result_lookup_keys.update(
            (assignment.exam_id, assignment.subject_id, mark.student_id)
            for mark in history_marks
        )
        if submission.status == "submitted":
            roster_key = (
                assignment.academic_year_id,
                assignment.school_class.academic_level_id,
                assignment.class_id,
                assignment.exam_id,
            )
            if roster_key not in review_roster_cache:
                try:
                    review_roster_cache[roster_key] = students_for_scope_query(
                        assignment.academic_year_id,
                        level_id=assignment.school_class.academic_level_id,
                        class_id=assignment.class_id,
                        exam=assignment.exam,
                    ).order_by(Student.full_name, Student.id).all()
                except EnrollmentValidationError:
                    review_roster_cache[roster_key] = None
            students = review_roster_cache[roster_key]
            if students is None:
                review_roster_errors.add(assignment.id)
                students = []
            review_rosters[assignment.id] = students
            result_lookup_keys.update(
                (assignment.exam_id, assignment.subject_id, student.id)
                for student in students
            )

    official_results_by_key = {}
    result_keys = sorted(result_lookup_keys)
    for offset in range(0, len(result_keys), 150):
        key_batch = result_keys[offset:offset + 150]
        key_filters = [
            and_(
                Result.exam_id == exam_id,
                Result.subject_id == subject_id,
                Result.student_id == student_id,
            )
            for exam_id, subject_id, student_id in key_batch
        ]
        for result in Result.query.filter(or_(*key_filters)).all():
            official_results_by_key[(result.exam_id, result.subject_id, result.student_id)] = result

    for assignment in assignments:
        submission = next(iter(assignment.submissions), None)
        if not submission:
            continue
        history_marks = history_marks_by_assignment.get(assignment.id, [])
        history_rows[assignment.id] = []
        for mark in history_marks:
            official_result = official_results_by_key.get(
                (assignment.exam_id, assignment.subject_id, mark.student_id)
            )
            history_rows[assignment.id].append({
                "mark": mark,
                "official_score": official_result.score if official_result else None,
                "can_return": bool(
                    mark.status == "approved"
                    and official_result
                    and official_result.is_published
                    and not mark.student.is_result_locked
                ),
            })
        if submission.status != "submitted":
            continue
        submitted_marks = [mark for mark in submission.marks if mark.status == "submitted" and mark.score is not None]
        if any(
            (assignment.exam_id, assignment.subject_id, mark.student_id) in official_results_by_key
            for mark in submitted_marks
        ):
            assignments_with_existing_results.add(assignment.id)
        marks_by_student = {mark.student_id: mark for mark in submission.marks}
        review_items = []
        for student in review_rosters.get(assignment.id, []):
            mark = marks_by_student.get(student.id)
            official_result = official_results_by_key.get(
                (assignment.exam_id, assignment.subject_id, student.id)
            )
            if mark and mark.score is not None and mark.status in {"submitted", "approved"}:
                review_state = mark.status
            elif mark and mark.score is not None and mark.status == "draft":
                review_state = "draft"
            else:
                review_state = "missing"
            review_items.append({
                "student": student,
                "mark": mark,
                "official_score": official_result.score if official_result else None,
                "review_state": review_state,
                "revisions": [],
                "edit_count": 0,
            })
        review_rows[assignment.id] = review_items
        review_summaries[assignment.id] = {
            "total": len(review_items),
            "submitted": sum(item["review_state"] == "submitted" for item in review_items),
            "approved": sum(item["review_state"] == "approved" for item in review_items),
            "draft": sum(item["review_state"] == "draft" for item in review_items),
            "missing": sum(item["review_state"] == "missing" for item in review_items),
            "roster_error": assignment.id in review_roster_errors,
        }
    history_mark_ids = [
        item["mark"].id
        for rows in history_rows.values()
        for item in rows
    ]
    revisions_by_mark = {}
    if history_mark_ids:
        revisions = TeacherResultMarkRevision.query.filter(
            TeacherResultMarkRevision.mark_id.in_(history_mark_ids)
        ).order_by(TeacherResultMarkRevision.created_at, TeacherResultMarkRevision.id).all()
        for revision in revisions:
            occurred_at = revision.created_at
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=ZoneInfo("UTC"))
            revisions_by_mark.setdefault(revision.mark_id, []).append({
                "record": revision,
                "time_label": occurred_at.astimezone(ZoneInfo("Africa/Mogadishu")).strftime("%d/%m/%Y · %I:%M %p"),
            })
    for rows in history_rows.values():
        for item in rows:
            item["revisions"] = revisions_by_mark.get(item["mark"].id, [])
            item["edit_count"] = sum(
                revision["record"].is_change for revision in item["revisions"]
            )
    for rows in review_rows.values():
        for item in rows:
            mark = item["mark"]
            if mark:
                item["revisions"] = revisions_by_mark.get(mark.id, [])
                item["edit_count"] = sum(
                    revision["record"].is_change for revision in item["revisions"]
                )
    deadline_labels = {
        item.id: item.deadline_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Africa/Mogadishu"))
        for item in assignments if item.deadline_at
    }
    teachers = Teacher.query.filter_by(is_active=True, employment_status="Active").order_by(Teacher.full_name).all()
    unlinked_staff_users = (
        User.query
        .filter(User.role == "staff", ~User.teacher_profile.has())
        .order_by(User.full_name, User.username)
        .all()
    )
    years = AcademicYear.query.order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc()).all()
    year_levels = (
        AcademicYearLevel.query
        .join(AcademicYear, AcademicYear.id == AcademicYearLevel.academic_year_id)
        .options(joinedload(AcademicYearLevel.legacy_level))
        .filter(AcademicYearLevel.is_active.is_(True), AcademicYearLevel.legacy_level_id.isnot(None))
        .order_by(AcademicYear.is_current.desc(), AcademicYear.name.desc(), AcademicYearLevel.sort_order, AcademicYearLevel.name)
        .all()
    )
    year_levels = [item for item in year_levels if item.legacy_level and item.legacy_level.is_active]
    year_level_ids = {item.id for item in year_levels}
    year_classes = (
        AcademicYearClass.query
        .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
        .options(
            joinedload(AcademicYearClass.legacy_class),
            joinedload(AcademicYearClass.academic_year_level),
        )
        .filter(AcademicYearClass.is_active.is_(True), AcademicYearClass.legacy_class_id.isnot(None))
        .order_by(AcademicYearClass.sort_order, AcademicYearClass.name)
        .all()
    )
    year_classes = [
        item for item in year_classes
        if item.legacy_class and item.legacy_class.is_active
        and item.academic_year_level and item.academic_year_level.is_active
        and item.academic_year_level_id in year_level_ids
        and item.academic_year_level.legacy_level_id == item.legacy_class.academic_level_id
    ]
    year_subjects = (
        AcademicYearSubject.query
        .options(
            joinedload(AcademicYearSubject.legacy_subject),
            joinedload(AcademicYearSubject.academic_year_level),
        )
        .filter_by(is_active=True, subject_kind="exam")
        .filter(AcademicYearSubject.legacy_subject_id.isnot(None))
        .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name)
        .all()
    )
    year_subjects = [
        item for item in year_subjects
        if item.legacy_subject and item.legacy_subject.is_active
        and item.academic_year_level and item.academic_year_level.is_active
        and item.academic_year_level_id in year_level_ids
        and item.academic_year_level.academic_year_id == item.academic_year_id
        and item.academic_year_level.legacy_level_id
        and (item.legacy_subject.academic_level_id is None
             or item.legacy_subject.academic_level_id == item.academic_year_level.legacy_level_id)
    ]
    exams = (
        Exam.query
        .options(joinedload(Exam.academic_year))
        .filter_by(is_active=True)
        .order_by(Exam.academic_year_id.desc(), Exam.sort_order, Exam.name)
        .all()
    )
    selected_year_id = request.args.get("setup_year_id", type=int)
    if selected_year_id not in {item.id for item in years}:
        selected_year_id = next((item.id for item in years if item.is_current), years[0].id if years else None)
    available_levels = [item for item in year_levels if item.academic_year_id == selected_year_id]
    selected_year_level_id = request.args.get("setup_level_id", type=int)
    if selected_year_level_id not in {item.id for item in available_levels}:
        selected_year_level_id = available_levels[0].id if len(available_levels) == 1 else None
    available_year_ids = {item.id for item in years}
    active_exam_ids = {item.id for item in exams}
    active_class_ids = {item.id for item in year_classes}
    active_subject_ids = {item.id for item in year_subjects}
    assignment_year_ids = {assignment.academic_year_id for assignment in assignments}
    assignment_level_ids = {assignment.school_class.academic_level_id for assignment in assignments}
    assignment_class_ids = {assignment.class_id for assignment in assignments}
    assignment_subject_ids = {assignment.subject_id for assignment in assignments}
    scope_year_levels = []
    scope_year_classes = []
    scope_year_subjects = []
    if assignment_year_ids and assignment_level_ids:
        scope_year_levels = (
            AcademicYearLevel.query
            .filter(
                AcademicYearLevel.academic_year_id.in_(assignment_year_ids),
                AcademicYearLevel.legacy_level_id.in_(assignment_level_ids),
            )
            .order_by(AcademicYearLevel.id)
            .all()
        )
    if assignment_year_ids and assignment_class_ids:
        scope_year_classes = (
            AcademicYearClass.query
            .join(AcademicYearLevel, AcademicYearLevel.id == AcademicYearClass.academic_year_level_id)
            .options(joinedload(AcademicYearClass.academic_year_level))
            .filter(
                AcademicYearLevel.academic_year_id.in_(assignment_year_ids),
                AcademicYearClass.legacy_class_id.in_(assignment_class_ids),
            )
            .order_by(AcademicYearClass.id)
            .all()
        )
    scope_level_ids = {item.id for item in scope_year_levels}
    if assignment_year_ids and scope_level_ids and assignment_subject_ids:
        scope_year_subjects = (
            AcademicYearSubject.query
            .filter(
                AcademicYearSubject.academic_year_id.in_(assignment_year_ids),
                AcademicYearSubject.academic_year_level_id.in_(scope_level_ids),
                AcademicYearSubject.legacy_subject_id.in_(assignment_subject_ids),
                AcademicYearSubject.subject_kind == "exam",
                AcademicYearSubject.is_active.is_(True),
            )
            .order_by(AcademicYearSubject.id)
            .all()
        )
    year_level_by_scope = {}
    for item in scope_year_levels:
        year_level_by_scope.setdefault((item.academic_year_id, item.legacy_level_id), item)
    year_class_by_scope = {}
    year_class_by_year_scope = {}
    for item in scope_year_classes:
        year_class_by_scope.setdefault((item.academic_year_level_id, item.legacy_class_id), item)
        year_class_by_year_scope.setdefault(
            (item.academic_year_level.academic_year_id, item.legacy_class_id), item
        )
    year_subject_by_scope = {}
    for item in scope_year_subjects:
        year_subject_by_scope.setdefault(
            (item.academic_year_id, item.academic_year_level_id, item.legacy_subject_id), item
        )
    assignment_scope_details = {}
    for assignment in assignments:
        year_level = year_level_by_scope.get(
            (assignment.academic_year_id, assignment.school_class.academic_level_id)
        )
        year_class = (
            year_class_by_scope.get((year_level.id, assignment.class_id))
            if year_level else year_class_by_year_scope.get((assignment.academic_year_id, assignment.class_id))
        )
        if year_level is None and year_class:
            year_level = year_class.academic_year_level
        year_subject = year_subject_by_scope.get(
            (assignment.academic_year_id, year_level.id, assignment.subject_id)
        ) if year_level else None
        assignment_scope_details[assignment.id] = {
            "level_name": year_level.name if year_level else (assignment.school_class.academic_level.name if assignment.school_class.academic_level else ""),
            "class_name": year_class.name if year_class else assignment.school_class.name,
            "subject_name": year_subject.name if year_subject else assignment.subject.name,
            "academic_year_level_id": year_level.id if year_level else None,
            "academic_year_class_id": year_class.id if year_class else None,
            "academic_year_subject_id": year_subject.id if year_subject else None,
            "editable": bool(
                assignment.teacher.is_active
                and assignment.teacher.employment_status == "Active"
                and assignment.academic_year_id in available_year_ids
                and assignment.exam_id in active_exam_ids
                and year_level and year_level.id in year_level_ids
                and year_class and year_class.id in active_class_ids
                and year_subject and year_subject.id in active_subject_ids
            ),
        }
    return render_template(
        "admin/teachers/result_entry_setup.html",
        assignments=assignments, teachers=teachers, unlinked_staff_users=unlinked_staff_users,
        years=years, exams=exams,
        year_levels=year_levels, year_classes=year_classes, year_subjects=year_subjects,
        selected_year_id=selected_year_id, selected_year_level_id=selected_year_level_id,
        assignment_scope_reservations=assignment_scope_reservations,
        assignment_scope_details=assignment_scope_details,
        pagination=pagination,
        deadline_labels=deadline_labels,
        review_rows=review_rows,
        review_summaries=review_summaries,
        submission_time_labels=submission_time_labels,
        history_rows=history_rows,
        assignment_progress=assignment_progress,
        assignments_with_existing_results=assignments_with_existing_results,
        summary=summary, status_filter=status_filter, teacher_filter=teacher_filter,
    )


@teachers_bp.route("/result-entry/<int:assignment_id>/print")
def print_result_entry_assignment(assignment_id):
    """Render a printable, class-specific marks roster for an assignment."""
    assignment = TeacherResultAssignment.query.filter_by(id=assignment_id).first_or_404()
    if assignment.deleted_at:
        abort(404)
    if (
        not assignment.teacher
        or not assignment.academic_year
        or not assignment.exam
        or not assignment.school_class
        or not assignment.subject
        or assignment.exam.academic_year_id != assignment.academic_year_id
    ):
        abort(409, "The assignment no longer matches a valid academic scope.")

    year_level, year_class = _year_scope_ids_from_legacy(
        assignment.academic_year_id,
        assignment.school_class.academic_level_id,
        assignment.class_id,
    )
    if (
        not year_level
        or not year_class
        or year_class.academic_year_level_id != year_level.id
        or year_class.legacy_class_id != assignment.class_id
    ):
        abort(409, "The assignment's academic-year class scope is no longer valid.")

    grouped_assignments = (
        TeacherResultAssignment.query
        .filter_by(
            teacher_id=assignment.teacher_id,
            academic_year_id=assignment.academic_year_id,
            exam_id=assignment.exam_id,
            class_id=assignment.class_id,
        )
        .filter(TeacherResultAssignment.deleted_at.is_(None))
        .all()
    )
    subject_ids = {item.subject_id for item in grouped_assignments}
    year_subjects = (
        AcademicYearSubject.query
        .filter_by(
            academic_year_id=assignment.academic_year_id,
            academic_year_level_id=year_level.id,
            subject_kind="exam",
        )
        .filter(AcademicYearSubject.legacy_subject_id.in_(subject_ids))
        .order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name, AcademicYearSubject.id)
        .all()
    )
    year_subject_by_legacy_id = {}
    for item in year_subjects:
        year_subject_by_legacy_id.setdefault(item.legacy_subject_id, item)

    subject_rows = []
    for item in grouped_assignments:
        subject = item.subject
        year_subject = year_subject_by_legacy_id.get(item.subject_id)
        maximum = resolve_subject_max_score(
            subject,
            exam=assignment.exam,
            academic_year_level_id=year_level.id,
            academic_level_id=assignment.school_class.academic_level_id,
        )
        subject_rows.append({
            "assignment": item,
            "name": year_subject.name if year_subject else subject.name,
            "sort_order": year_subject.sort_order if year_subject else 10**9,
            "max_score": format(maximum.normalize(), "f") if maximum else "0",
        })
    subject_rows.sort(key=lambda row: (row["sort_order"], row["name"].casefold(), row["assignment"].id))

    students = (
        students_for_scope_query(
            assignment.academic_year_id,
            level_id=assignment.school_class.academic_level_id,
            class_id=assignment.class_id,
            exam=assignment.exam,
        )
        .order_by(Student.full_name)
        .all()
    )
    male_count = sum(
        str(student.gender or "").strip().casefold() in {"male", "m", "lab"}
        for student in students
    )
    female_count = sum(
        str(student.gender or "").strip().casefold() in {"female", "f", "dhedig"}
        for student in students
    )
    student_pages = [students[index:index + 15] for index in range(0, len(students), 15)] or [[]]
    subject_groups = [subject_rows[index:index + 3] for index in range(0, len(subject_rows), 3)]
    pages = []
    total_pages = len(student_pages) * len(subject_groups)
    for subjects in subject_groups:
        for student_index, student_page in enumerate(student_pages):
            first_student_number = student_index * 15 + 1
            pages.append({
                "subjects": subjects,
                "students": student_page,
                "page_number": len(pages) + 1,
                "total_pages": total_pages,
                "student_range": (
                    first_student_number,
                    min((student_index + 1) * 15, len(students)),
                ),
            })

    printed_at = datetime.now(ZoneInfo("Africa/Mogadishu"))
    somali_weekdays = ("Isniin", "Talaado", "Arbaco", "Khamiis", "Jumca", "Sabti", "Axad")
    english_months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    printed_date = (
        f"{somali_weekdays[printed_at.weekday()]}, "
        f"{english_months[printed_at.month - 1]} {printed_at.day}, {printed_at.year}"
    )

    return render_template(
        "admin/teachers/result_entry_print.html",
        assignment=assignment,
        year_level=year_level,
        year_class=year_class,
        pages=pages,
        student_count=len(students),
        male_count=male_count,
        female_count=female_count,
        printed_date=printed_date,
        printed_time=printed_at.strftime("%I:%M %p"),
    )


@teachers_bp.route("/result-entry/history")
def result_entry_history():
    query, filters, date_error = _teacher_result_history_query()
    total = query.count()
    page_size = 40
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, request.args.get("page", 1, type=int) or 1), page_count)
    events = query.order_by(TeacherResultEvent.created_at.desc(), TeacherResultEvent.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    event_counts = dict(query.with_entities(
        TeacherResultEvent.event_type, func.count(TeacherResultEvent.id)
    ).group_by(TeacherResultEvent.event_type).all())
    mogadishu = ZoneInfo("Africa/Mogadishu")
    event_times = {
        item.id: item.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(mogadishu)
        for item in events
    }
    filter_options = {
        "teachers": [row[0] for row in db.session.query(TeacherResultEvent.teacher_name).distinct().order_by(TeacherResultEvent.teacher_name).all() if row[0]],
        "years": [row[0] for row in db.session.query(TeacherResultEvent.academic_year_name).distinct().order_by(TeacherResultEvent.academic_year_name.desc()).all() if row[0]],
        "exams": [row[0] for row in db.session.query(TeacherResultEvent.exam_name).distinct().order_by(TeacherResultEvent.exam_name).all() if row[0]],
    }
    summary = {
        "total": total,
        "submitted": event_counts.get("submitted", 0) + event_counts.get("resubmitted", 0),
        "approved": event_counts.get("approved", 0) + event_counts.get("partially_approved", 0),
        "returned": event_counts.get("returned_for_correction", 0),
    }
    return render_template(
        "admin/teachers/result_entry_history.html",
        events=events,
        event_times=event_times,
        event_labels=RESULT_EVENT_LABELS,
        event_icons=RESULT_EVENT_ICONS,
        status_labels=RESULT_STATUS_LABELS,
        filters=filters,
        filter_options=filter_options,
        summary=summary,
        page=page,
        page_count=page_count,
        date_error=date_error,
    )


@teachers_bp.route("/result-entry/history/export.xlsx")
def export_result_entry_history():
    query, filters, _ = _teacher_result_history_query()
    events = query.order_by(TeacherResultEvent.created_at.desc(), TeacherResultEvent.id.desc()).limit(20000).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Taariikhda Natiijooyinka"
    sheet.append([
        "Waqtiga (EAT)", "Dhacdada", "Macallinka", "Koodhka", "Sannad-dugsiyeedka",
        "Imtixaanka", "Heerka", "Fasalka", "Maaddada", "Xaaladdii hore",
        "Xaaladda cusub", "Tirada dhibcaha", "Maamulaha", "Faahfaahin",
    ])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:N{len(events) + 1}"
    mogadishu = ZoneInfo("Africa/Mogadishu")
    for event in events:
        local_time = event.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(mogadishu)
        sheet.append([
            local_time.strftime("%Y-%m-%d %I:%M %p"),
            RESULT_EVENT_LABELS.get(event.event_type, event.event_type),
            event.teacher_name, event.teacher_code or "", event.academic_year_name,
            event.exam_name, event.level_name or "", event.class_name, event.subject_name,
            RESULT_STATUS_LABELS.get(event.status_from, event.status_from or ""),
            RESULT_STATUS_LABELS.get(event.status_to, event.status_to or ""),
            event.marks_count, event.actor_name or "", event.note or "",
        ])
    for column, width in {"A": 22, "B": 30, "C": 28, "D": 14, "E": 18, "F": 25, "G": 18, "H": 18, "I": 22, "J": 20, "K": 20, "L": 16, "M": 24, "N": 52}.items():
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="teacher-result-entry-history.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@teachers_bp.route("/list")
def teacher_list():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15
    pagination = teacher_list_query(filters).options(
        selectinload(Teacher.user),
        selectinload(Teacher.academic_levels),
        selectinload(Teacher.classes).joinedload(AcademicClass.academic_level),
        selectinload(Teacher.subjects),
    ).paginate(page=page, per_page=per_page, error_out=False)
    departments = [
        row[0]
        for row in db.session.query(Teacher.department).filter(Teacher.department.isnot(None), Teacher.department != "").distinct().order_by(Teacher.department).all()
    ]
    teacher_scope_names = {}
    for item in pagination.items:
        levels_by_id = {level.id: level.name for level in item.academic_levels}
        levels_by_id.update({school_class.academic_level.id: school_class.academic_level.name for school_class in item.classes if school_class.academic_level})
        teacher_scope_names[item.id] = {
            "levels": sorted(levels_by_id.values(), key=str.casefold),
            "classes": [f"{school_class.name}" for school_class in sorted(item.classes, key=lambda value: (value.academic_level.sort_order, value.sort_order, value.name.casefold()))],
        }
    return render_template(
        "admin/teachers/list.html",
        teachers=pagination.items,
        pagination=pagination,
        filters=filters,
        teacher_scope_names=teacher_scope_names,
        departments=departments,
        subjects=Subject.query.filter_by(is_active=True).order_by(Subject.sort_order, Subject.name).all(),
        classes=AcademicClass.query.filter_by(is_active=True).order_by(AcademicClass.academic_level_id, AcademicClass.sort_order, AcademicClass.name).all(),
        academic_levels=AcademicLevel.query.filter_by(is_active=True).order_by(AcademicLevel.sort_order, AcademicLevel.name).all(),
        account_summary={
            "total": Teacher.query.count(),
            "linked": Teacher.query.filter(Teacher.user_id.isnot(None)).count(),
            "enabled": Teacher.query.filter(
                Teacher.is_active.is_(True),
                Teacher.employment_status == "Active",
                Teacher.user.has(User.is_active.is_(True)),
            ).count(),
            "missing": Teacher.query.filter(Teacher.user_id.is_(None)).count(),
        },
    )


@teachers_bp.route("/new", methods=["GET", "POST"])
@teachers_bp.route("/<int:teacher_id>/edit", methods=["GET", "POST"])
def teacher_form(teacher_id=None):
    teacher = (db.session.get(Teacher, teacher_id) or abort(404)) if teacher_id else Teacher()
    settings = get_teacher_settings()
    scope_data = _teacher_profile_scopes(teacher)
    selected_portal_user_id = request.args.get("user_id", type=int)
    if request.method == "POST":
        selected_portal_user_id = request.form.get("existing_user_id", type=int)
    linkable_staff_users = (
        User.query
        .filter(User.role == "staff", ~User.teacher_profile.has())
        .order_by(User.full_name, User.username)
        .all()
    ) if not teacher.user_id else []
    selected_portal_user = next(
        (item for item in linkable_staff_users if item.id == selected_portal_user_id), None
    )
    if selected_portal_user and not teacher.id:
        teacher.full_name = teacher.full_name or selected_portal_user.full_name

    if request.method == "POST":
        try:
            if selected_portal_user_id:
                if teacher.user_id:
                    raise ValueError("Macallinkan akoon ayuu hore ugu xiran yahay; akoon kale laguma beddeli karo form-kan.")
                selected_portal_user = db.session.get(User, selected_portal_user_id)
                linked_profile = Teacher.query.filter_by(user_id=selected_portal_user_id).first()
                if (
                    not selected_portal_user
                    or selected_portal_user.role != "staff"
                    or (linked_profile and linked_profile.id != teacher.id)
                ):
                    raise ValueError("Akoonka la doortay ma aha staff aan macallin kale ku xirnayn.")
                teacher.user_id = selected_portal_user.id

            teacher.teacher_id = request.form["teacher_id"].strip()
            if not teacher.teacher_id:
                raise ValueError("Teacher ID is required and must be entered manually.")
            existing_id = Teacher.query.filter(Teacher.teacher_id == teacher.teacher_id, Teacher.id != teacher.id).first()
            if existing_id:
                raise ValueError("Teacher ID already exists.")

            manual_code = request.form.get("teacher_code", "").strip()
            if manual_code:
                if manual_code != (teacher.teacher_code or ""):
                    teacher.teacher_code = generate_teacher_code(manual_code=manual_code)
            elif not teacher.teacher_code:
                teacher.teacher_code = generate_teacher_code()

            teacher.full_name = request.form["full_name"].strip()
            teacher.gender = request.form.get("gender") or None
            teacher.phone = request.form.get("phone", "").strip()
            teacher.email = request.form.get("email", "").strip()
            if "address" in request.form:
                teacher.address = request.form.get("address", "").strip()
            if "emergency_contact" in request.form:
                teacher.emergency_contact = request.form.get("emergency_contact", "").strip()
            if "employment_status" in request.form:
                teacher.employment_date = parse_teacher_form_dates(request.form.get("employment_date"))
                teacher.employment_type = request.form.get("employment_type") or "Full Time"
                teacher.qualification = request.form.get("qualification", "").strip()
                teacher.years_experience = int(request.form.get("years_experience") or 0)
                teacher.department = request.form.get("department", "").strip()
                teacher.employment_status = request.form.get("employment_status") or settings.get("teacher_default_status") or "Active"
                teacher.is_active = teacher.employment_status == "Active"

            photo = request.files.get("photo")
            if photo and photo.filename:
                if not allowed_file(photo.filename, ALLOWED_PHOTOS):
                    raise ValueError("Photo must be JPG, PNG, or WEBP.")
                teacher.photo_path = upload_image(photo, "school/teachers")

            def posted_ids(field):
                values = request.form.getlist(field)
                if any(not value.isdigit() or int(value) < 1 for value in values):
                    raise ValueError("Xulashada heerka, fasalka, qaybta ama maaddadu sax ma aha.")
                return list(dict.fromkeys(int(value) for value in values))

            def selected_records(model, ids, existing_ids):
                records = model.query.filter(model.id.in_(ids)).all() if ids else []
                if len(records) != len(ids) or any(
                    not record.is_active and record.id not in existing_ids for record in records
                ):
                    raise ValueError("Waxaa la doortay xog aan jirin ama aan hadda firfircoonayn.")
                return records

            existing_level_ids = scope_data["linked_level_ids"]
            existing_class_ids = scope_data["linked_class_ids"]
            existing_section_ids = scope_data["linked_section_ids"]
            existing_subject_ids = scope_data["linked_subject_ids"]
            selected_level_ids = posted_ids("academic_level_ids")
            selected_class_ids = posted_ids("academic_class_ids")
            selected_section_ids = posted_ids("academic_section_ids")
            selected_subject_ids = posted_ids("subject_ids")

            allowed_ids = (
                (selected_level_ids, scope_data["current_level_ids"] | existing_level_ids),
                (selected_class_ids, scope_data["current_class_ids"] | existing_class_ids),
                (selected_section_ids, scope_data["current_section_ids"] | existing_section_ids),
                (selected_subject_ids, scope_data["current_subject_ids"] | existing_subject_ids),
            )
            if any(set(ids) - allowed for ids, allowed in allowed_ids):
                raise ValueError("Heer, fasal, qayb ama maaddo laguma diiwaangelin qaab-dhismeedka sanad-dugsiyeedka hadda.")

            selected_levels = selected_records(AcademicLevel, selected_level_ids, existing_level_ids)
            selected_classes = selected_records(AcademicClass, selected_class_ids, existing_class_ids)
            selected_sections = selected_records(AcademicSection, selected_section_ids, existing_section_ids)
            selected_subjects = selected_records(Subject, selected_subject_ids, existing_subject_ids)
            selected_class_id_set = {item.id for item in selected_classes}
            if any(section.academic_class_id not in selected_class_id_set for section in selected_sections):
                raise ValueError("Qaybta ardaydu waxay u baahan tahay in fasalkeeda la doorto.")

            level_ids = {item.id for item in selected_levels}
            level_ids.update(item.academic_level_id for item in selected_classes)
            level_ids.update(item.academic_level_id for item in selected_subjects if item.academic_level_id)
            teacher.academic_levels = AcademicLevel.query.filter(AcademicLevel.id.in_(level_ids)).all() if level_ids else []
            teacher.classes = selected_classes
            teacher.sections = selected_sections
            teacher.subjects = selected_subjects

            db.session.add(teacher)
            db.session.flush()

            username = request.form.get("username", "").strip() or default_teacher_username(teacher)
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            create_account = bool(request.form.get("create_account"))
            if selected_portal_user_id or create_account or teacher.user_id:
                if password and password != confirm:
                    raise ValueError("Passwords do not match.")
                had_portal_account = bool(teacher.user_id)
                create_or_update_teacher_user(
                    teacher,
                    username=username,
                    password=password,
                    account_active=request.form.get("account_active") == "on",
                    force_change=bool(request.form.get("force_password_change")),
                )
                if password:
                    actor = current_user.username if current_user.is_authenticated else "maamulka"
                    action = "Password Reset by Admin" if had_portal_account else "Password Set by Admin"
                    log_teacher_activity(
                        teacher,
                        action,
                        f"Maamulaha @{actor} ayaa dejiyey furaha gelitaanka. Qiimaha furaha lama kaydin.",
                    )

            log_teacher_activity(teacher, "Profile Updated", f"Saved teacher {teacher.teacher_id} / {teacher.teacher_code}")
            audit("Teacher Updates", f"Saved teacher {teacher.teacher_id} ({teacher.teacher_code})")
            db.session.commit()
            flash("Teacher saved successfully.", "success")
            return redirect(url_for("admin_teachers.teacher_list"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template(
        "admin/teachers/form.html",
        teacher=teacher,
        current_academic_year=scope_data["year"],
        scope_groups=scope_data["groups"],
        shared_subjects=scope_data["shared_subjects"],
        linked_level_ids=scope_data["linked_level_ids"],
        settings=settings,
        default_username=default_teacher_username(teacher) if teacher.teacher_id else "",
        linkable_staff_users=linkable_staff_users,
        selected_portal_user=selected_portal_user,
        selected_portal_user_id=selected_portal_user_id,
    )


@teachers_bp.route("/<int:teacher_id>")
def teacher_view(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    performance = teacher_performance_data(teacher)[0]
    activity = teacher_activity_summary(teacher)
    password_events = TeacherActivity.query.filter_by(teacher_id=teacher.id).filter(
        TeacherActivity.action.in_((
            "Password Set by Admin",
            "Password Reset by Admin",
            "Password Changed by Teacher",
            "Password Change",
            "Password Reset Requested",
            "Password Reset Request",
        ))
    ).order_by(TeacherActivity.created_at.desc(), TeacherActivity.id.desc()).limit(20).all()
    return render_template(
        "admin/teachers/view.html",
        teacher=teacher,
        performance=performance,
        activity=activity,
        password_events=password_events,
        permissions=teacher_permission_set(teacher),
    )


@teachers_bp.route("/<int:teacher_id>/delete", methods=["POST"])
def delete_teacher(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    code = teacher.teacher_code
    teacher_id_value = teacher.teacher_id
    if teacher.user_id:
        user = teacher.user
        teacher.user_id = None
        db.session.flush()
        db.session.delete(user)
    db.session.delete(teacher)
    audit("Teacher Updates", f"Deleted teacher {teacher_id_value} ({code})")
    db.session.commit()
    flash("Teacher deleted.", "success")
    return redirect(url_for("admin_teachers.teacher_list"))


@teachers_bp.route("/<int:teacher_id>/permissions", methods=["GET", "POST"])
def teacher_permissions(teacher_id):
    teacher = db.session.get(Teacher, teacher_id) or abort(404)
    if request.method == "POST":
        posted = request.form.getlist("permissions")
        set_teacher_permissions(teacher, posted)
        log_teacher_activity(teacher, "Permissions Updated", ", ".join(posted))
        audit("Teacher Permissions", f"Updated permissions for {teacher.teacher_id}")
        db.session.commit()
        flash("Teacher permissions saved.", "success")
        return redirect(url_for("admin_teachers.teacher_permissions", teacher_id=teacher.id))
    return render_template(
        "admin/teachers/permissions.html",
        teacher=teacher,
        permissions=TEACHER_PERMISSIONS,
        active_permissions=teacher_permission_set(teacher),
    )


@teachers_bp.route("/performance")
def performance():
    selected_id = request.args.get("teacher_id", type=int)
    teacher = db.session.get(Teacher, selected_id) if selected_id else None
    payloads = teacher_performance_data(teacher)
    return render_template(
        "admin/teachers/performance.html",
        payloads=payloads,
        teachers=Teacher.query.order_by(Teacher.full_name).all(),
        selected_id=selected_id,
    )


@teachers_bp.route("/activity")
def activity():
    teacher_id = request.args.get("teacher_id", type=int)
    teachers = Teacher.query.order_by(Teacher.full_name).all()
    summaries = []
    if teacher_id:
        teacher = db.session.get(Teacher, teacher_id) or abort(404)
        summaries = [teacher_activity_summary(teacher)]
    else:
        summaries = [teacher_activity_summary(teacher) for teacher in teachers[:30]]
    recent = TeacherActivity.query.order_by(TeacherActivity.created_at.desc()).limit(100).all()
    return render_template(
        "admin/teachers/activity.html",
        summaries=summaries,
        teachers=teachers,
        teacher_id=teacher_id,
        recent=recent,
    )


@teachers_bp.route("/settings", methods=["GET", "POST"])
def teacher_settings():
    if request.method == "POST":
        keys = [
            "teacher_code_auto_generate",
            "teacher_code_format",
            "teacher_default_username_format",
            "teacher_theme",
            "teacher_profile_photo_size",
            "teacher_max_upload_size",
            "teacher_default_status",
            "teacher_card_style",
        ]
        for key in keys:
            if key == "teacher_code_auto_generate":
                value = "on" if request.form.get(key) == "on" else "off"
            else:
                value = request.form.get(key, "").strip()
            setting = db.session.get(Setting, key) or Setting(key=key)
            setting.value = value
            db.session.add(setting)
        audit("Teacher Settings", "Updated teacher department settings")
        db.session.commit()
        flash("Teacher settings saved.", "success")
        return redirect(url_for("admin_teachers.teacher_settings"))
    return render_template("admin/teachers/settings.html", settings=get_teacher_settings())


@teachers_bp.route("/export")
def export_teachers():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
    wb = Workbook()
    ws = wb.active
    ws.title = "Teachers"
    ws.append(
        [
            "Teacher ID",
            "Teacher Code",
            "Full Name",
            "Phone",
            "Email",
            "Department",
            "School Level",
            "Subjects",
            "Classes",
            "Employment Status",
            "Account Status",
        ]
    )
    for teacher in teacher_list_query(filters).all():
        ws.append(
            [
                teacher.teacher_id,
                teacher.teacher_code,
                teacher.full_name,
                teacher.phone,
                teacher.email,
                teacher.department,
                ", ".join(sorted({level.name for level in teacher.academic_levels} | {item.academic_level.name for item in teacher.classes if item.academic_level})),
                ", ".join(subject.name for subject in teacher.subjects),
                ", ".join(school_class.name for school_class in teacher.classes),
                teacher.employment_status,
                "Gelitaan shaqaynaya" if teacher.user and teacher.user.is_active and teacher.is_active and teacher.employment_status == "Active" else ("Gelitaan xiran" if teacher.user else "Akoon ma jiro"),
            ]
        )
    audit("Teacher Export", "Exported teacher list")
    db.session.commit()
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name="teachers.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@teachers_bp.route("/print")
def print_teachers():
    filters = {
        "q": request.args.get("q", "").strip(),
        "department": request.args.get("department", "").strip(),
        "academic_level_id": request.args.get("academic_level_id", "").strip(),
        "employment_status": request.args.get("employment_status", "").strip(),
        "subject_id": request.args.get("subject_id", "").strip(),
        "class_id": request.args.get("class_id", "").strip(),
        "account_status": request.args.get("account_status", "").strip(),
    }
    teachers = teacher_list_query(filters).all()
    return render_template("admin/teachers/print.html", teachers=teachers, generated_at=datetime.now())


@teachers_bp.route("/club-teams", methods=["GET", "POST"])
def club_teams():
    """Administer mixed-class club cohorts, rosters, and dated leader rotations."""
    if request.method == "POST":
        operation = request.form.get("operation", "")
        try:
            if operation == "create_club":
                name = request.form.get("name", "").strip()
                if not name:
                    raise ValueError("Magaca naadigu waa waajib.")
                if StudentClub.query.filter(db.func.lower(StudentClub.name) == name.lower()).first():
                    raise ValueError("Naadi magacan leh hore ayuu u jiraa.")
                db.session.add(StudentClub(name=name, description=request.form.get("description", "").strip() or None))
                audit("Club Teams", f"Created club {name}")
                flash("Naadiga waa la abuuray.", "success")
            elif operation == "create_team":
                year = db.session.get(AcademicYear, request.form.get("academic_year_id", type=int))
                name = request.form.get("name", "").strip()
                if not year or not name:
                    raise ValueError("Dooro sannad-dugsiyeed, kuna qor magaca kooxda.")
                if ClubTeam.query.filter_by(academic_year_id=year.id, name=name).first():
                    raise ValueError("Koox magacan leh sannad-dugsiyeedkan way jirtaa.")
                db.session.add(ClubTeam(academic_year=year, name=name))
                audit("Club Teams", f"Created team {name} for {year.name}")
                flash("Kooxda waa la abuuray.", "success")
            elif operation == "add_member":
                team = db.session.get(ClubTeam, request.form.get("team_id", type=int))
                enrollment = db.session.get(StudentEnrollment, request.form.get("enrollment_id", type=int))
                start_date = date.fromisoformat(request.form.get("start_date", ""))
                if not team or not enrollment or enrollment.academic_year_id != team.academic_year_id:
                    raise ValueError("Ardayga iyo kooxdu waa inay ka wada tirsanaadaan isla sannad-dugsiyeedka.")
                if start_date < date.today():
                    raise ValueError("Xubinnimada cusub dib looguma qori karo taariikh hore.")
                if enrollment.status != "active":
                    raise ValueError("Kaliya ardayda firfircoon ayaa lagu dari karaa kooxda.")
                overlapping = ClubTeamMembership.query.filter(
                    ClubTeamMembership.enrollment_id == enrollment.id,
                    db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > start_date),
                ).order_by(ClubTeamMembership.start_date.desc()).first()
                if overlapping:
                    if overlapping.team_id == team.id:
                        raise ValueError("Ardaygani kooxdaas ayuu hore ugu jiraa.")
                    if start_date < overlapping.start_date:
                        raise ValueError("Wareejintu kama hor mari karto xubinnimada firfircoon ee hadda jirta.")
                    overlapping.end_date = start_date
                future_membership = ClubTeamMembership.query.filter(
                    ClubTeamMembership.enrollment_id == enrollment.id,
                    ClubTeamMembership.start_date > start_date,
                    db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > start_date),
                ).first()
                if future_membership:
                    raise ValueError("Ardaygan wuxuu leeyahay wareejin mustaqbal ah; marka hore dib u eeg taariikhdaas.")
                db.session.add(ClubTeamMembership(
                    team=team, enrollment=enrollment, start_date=start_date,
                    assigned_by_user_id=getattr(current_user, "id", None),
                ))
                audit("Club Teams", f"Assigned enrollment {enrollment.id} to {team.name} effective {start_date.isoformat()}")
                flash("Xubinta kooxda waa la kaydiyey.", "success")
            elif operation == "assign_leader":
                team = db.session.get(ClubTeam, request.form.get("team_id", type=int))
                club = db.session.get(StudentClub, request.form.get("club_id", type=int))
                teacher = db.session.get(Teacher, request.form.get("teacher_id", type=int))
                start_date = date.fromisoformat(request.form.get("start_date", ""))
                if not team or not club or not club.is_active or not teacher or not teacher.is_active or teacher.employment_status != "Active":
                    raise ValueError("Koox, naadi firfircoon, iyo hoggaamiye firfircoon dooro.")
                if not teacher.user_id or not teacher.user or not teacher.user.is_active:
                    raise ValueError("Hoggaamiyahan weli akoon portal ma leh. Marka hore ka samee/ka hawlgeli akoonka bogga macallinka.")
                if start_date < date.today():
                    raise ValueError("Qoondeyn cusub dib looguma qori karo taariikh hore.")
                current = ClubTeamLeaderAssignment.query.filter(
                    ClubTeamLeaderAssignment.team_id == team.id,
                    ClubTeamLeaderAssignment.start_date <= start_date,
                    db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > start_date),
                ).order_by(ClubTeamLeaderAssignment.start_date.desc()).first()
                if current and current.start_date > start_date:
                    raise ValueError("Wareejintu kama hor mari karto wareegga hadda la qorsheeyey.")
                if current and current.start_date == start_date and current.teacher_id == teacher.id and current.club_id == club.id:
                    raise ValueError("Kooxdan hore ayaa loogu qoondeeyey hoggaamiyahan iyo naadiga taariikhdaas.")
                future_assignment = ClubTeamLeaderAssignment.query.filter(
                    ClubTeamLeaderAssignment.team_id == team.id,
                    ClubTeamLeaderAssignment.start_date > start_date,
                    db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > start_date),
                ).first()
                if future_assignment:
                    raise ValueError("Kooxdan waxay leedahay wareejin mustaqbal ah; marka hore dib u eeg taariikhdaas.")
                if current:
                    current.end_date = start_date
                db.session.add(ClubTeamLeaderAssignment(
                    team=team, club=club, teacher=teacher, start_date=start_date,
                    assigned_by_user_id=getattr(current_user, "id", None),
                    transfer_note=request.form.get("transfer_note", "").strip() or None,
                ))
                audit("Club Teams", f"Assigned {team.name} to {teacher.full_name} / {club.name} from {start_date.isoformat()}")
                flash("Hoggaamiyaha/naadiga waa la qoondeeyey; wareegyadii hore way kaydsan yihiin.", "success")
            else:
                raise ValueError("Ficil aan la aqoonsan.")
            db.session.commit()
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("admin_teachers.club_teams", team_id=request.form.get("team_id", "")))

    years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    selected_year_id = request.args.get("year_id", type=int) or (years[0].id if years else None)
    teams_query = ClubTeam.query.filter_by(academic_year_id=selected_year_id) if selected_year_id else ClubTeam.query.filter(db.false())
    teams = teams_query.order_by(ClubTeam.name).all()
    selected_team_id = request.args.get("team_id", type=int)
    selected_team = next((item for item in teams if item.id == selected_team_id), None) or (teams[0] if teams else None)
    today = date.today()
    active_memberships = {}
    active_assignment = None
    if selected_team:
        active_memberships = {
            item.enrollment_id: item for item in ClubTeamMembership.query.filter(
                ClubTeamMembership.team_id == selected_team.id,
                ClubTeamMembership.start_date <= today,
                db.or_(ClubTeamMembership.end_date.is_(None), ClubTeamMembership.end_date > today),
            ).all()
        }
        active_assignment = ClubTeamLeaderAssignment.query.filter(
            ClubTeamLeaderAssignment.team_id == selected_team.id,
            ClubTeamLeaderAssignment.start_date <= today,
            db.or_(ClubTeamLeaderAssignment.end_date.is_(None), ClubTeamLeaderAssignment.end_date > today),
        ).order_by(ClubTeamLeaderAssignment.start_date.desc()).first()
    enrollments = StudentEnrollment.query.filter_by(
        academic_year_id=selected_team.academic_year_id, status="active",
    ).order_by(StudentEnrollment.academic_year_level_id, StudentEnrollment.academic_year_class_id, StudentEnrollment.id).all() if selected_team else []
    clubs = StudentClub.query.filter_by(is_active=True).order_by(StudentClub.name).all()
    teachers = Teacher.query.filter_by(is_active=True, employment_status="Active").order_by(Teacher.full_name).all()
    history = ClubTeamLeaderAssignment.query.filter_by(team_id=selected_team.id).order_by(ClubTeamLeaderAssignment.start_date.desc()).all() if selected_team else []
    membership_history = ClubTeamMembership.query.filter_by(team_id=selected_team.id).order_by(ClubTeamMembership.start_date.desc(), ClubTeamMembership.id.desc()).all() if selected_team else []
    return render_template(
        "admin/teachers/club_teams.html", years=years, selected_year_id=selected_year_id,
        teams=teams, selected_team=selected_team, active_memberships=active_memberships,
        active_assignment=active_assignment, enrollments=enrollments, clubs=clubs,
        teachers=teachers, history=history, membership_history=membership_history, today=today,
    )
