"""Dedicated Phase 2B Behavior administration routes."""

import json
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from secrets import token_urlsafe
from types import SimpleNamespace

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from . import db
from .academic_hierarchy import year_levels, year_subjects
from .audit import audit
from .behavior_service import (
    BEHAVIOR_RESPONSE_TYPES,
    BehaviorValidationError,
    allocation_total,
    attendance_points_projection,
    behavior_summary,
    calculate_session_score,
    capture_attendance_session_policy,
    canonical_response_type,
    configuration_level_ids,
    configuration_for_scope,
    decimal_value,
    edit_event,
    ensure_configuration_editable,
    ensure_session_editable,
    find_event_by_idempotency_key,
    normalize_idempotency_key,
    record_event,
    restore_event,
    scoring_ledger_projection,
    session_allocation_projection,
    storage_response_type,
    validate_configuration_levels,
    validate_behavior_configuration,
    validate_enrollment_scope,
    validate_behavior_scope,
    validate_session_scope,
    void_event,
    void_attendance_record,
    restore_attendance_record,
    delete_attendance_record,
)
from .behavior_grading import (
    BehaviorGradeValidationError,
    behavior_grade_for_score,
    behavior_grade_readiness,
    behavior_grade_scales,
    validate_behavior_grade_overlap,
    validate_behavior_grade_values,
)
from .behavior_reporting import get_behavior_report_data
from .behavior_attendance import (
    attendance_days,
    attendance_status_label,
    attendance_statuses,
    enrollments_for_class,
    ensure_attendance_defaults,
    generate_daily_roster,
    mark_attendance,
    update_attendance_active_days as apply_attendance_active_days,
)
from .models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearLevelAttendanceDay,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorActionChoice,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorConfigurationLevel,
    BehaviorEvent,
    BehaviorAttendanceRecord,
    BehaviorAttendanceDeletion,
    BehaviorAttendanceStatus,
    BehaviorGradeScale,
    BehaviorSession,
    BehaviorSubCategory,
    Exam,
    ExamType,
    Student,
    StudentEnrollment,
)
from .permissions import enforce_endpoint_permission
from .services import get_settings


behavior_bp = Blueprint("behavior", __name__)


@behavior_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


def _int(value, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value):
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except (TypeError, ValueError):
        raise ValueError("Arrival time must use HH:MM format")


def _late_by_minutes(arrival_time, school_start_time):
    if not arrival_time or not school_start_time:
        return None
    start = school_start_time
    if isinstance(start, datetime):
        start = start.time()
    elif not hasattr(start, "hour"):
        start = _parse_time(start)
    return max(0, (arrival_time.hour * 60 + arrival_time.minute) - (start.hour * 60 + start.minute))


def _wants_json_response():
    """Identify the lightweight autosave requests used by Behavior settings."""
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _behavior_exam_options(year_id, configuration=None):
    """Return every active, year-scoped Exam Type from both registries.

    The Results Hub ``Exam`` table is the current source used for new records,
    while ``ExamType`` is retained for older Behavior sessions.  The previous
    implementation hid unreferenced legacy rows whenever *any* canonical Exam
    existed in the year, which made valid Exam Types disappear from Setup and
    Sessions.  Prefer canonical rows when the two compatibility registries
    contain the same name, but keep a legacy row visible when it is already
    referenced by a Behavior session.
    """
    if not year_id:
        return []
    canonical = (
        Exam.query
        .filter_by(academic_year_id=year_id, is_active=True)
        .order_by(Exam.sort_order, Exam.name, Exam.id)
        .all()
    )
    options = [
        {
            "id": item.id,
            "name": item.name,
            "sort_order": item.sort_order,
            "source": "exam",
            "value": f"exam:{item.id}",
            "is_final_evaluation": bool(item.is_final_evaluation),
        }
        for item in canonical
    ]
    canonical_names = {item.name.strip().casefold() for item in canonical}
    referenced_ids = {
        item.exam_type_id
        for item in (configuration.sessions if configuration else [])
        if item.exam_type_id is not None
    }
    legacy_rows = (
        ExamType.query
        .filter_by(academic_year_id=year_id, is_active=True)
        .order_by(ExamType.sort_order, ExamType.name, ExamType.id)
        .all()
    )
    options.extend(
        {
            "id": item.id,
            "name": item.name,
            "sort_order": item.sort_order,
            "source": "legacy",
            "value": f"legacy:{item.id}",
            "is_final_evaluation": False,
        }
        for item in legacy_rows
        if item.id in referenced_ids or item.name.strip().casefold() not in canonical_names
    )
    options.sort(key=lambda item: (item["sort_order"], item["name"].casefold(), item["source"], item["id"]))
    return options


def _session_exam_value(session):
    if session.exam_id is not None:
        return f"exam:{session.exam_id}"
    if session.exam_type_id is not None:
        return f"legacy:{session.exam_type_id}"
    return ""


def _resolve_behavior_exam(value, legacy_id=None):
    """Resolve a submitted exam reference without mixing registry IDs."""
    reference = (value or "").strip()
    if reference:
        try:
            source, raw_id = reference.split(":", 1)
            item_id = int(raw_id)
        except (TypeError, ValueError):
            source, item_id = "", None
        if source == "exam" and item_id:
            return source, db.session.get(Exam, item_id)
        if source == "legacy" and item_id:
            return source, db.session.get(ExamType, item_id)
    item_id = _int(legacy_id)
    return ("legacy", db.session.get(ExamType, item_id)) if item_id else ("", None)


def _selected_year(year_id=None):
    if year_id not in (None, ""):
        requested_id = _int(year_id)
        return db.session.get(AcademicYear, requested_id) if requested_id else None
    return AcademicYear.query.order_by(
        AcademicYear.is_current.desc(),
        AcademicYear.name.desc(),
        AcademicYear.id.desc(),
    ).first()


def _config_choices(selected_year_id=None, selected_config_id=None):
    years = AcademicYear.query.order_by(AcademicYear.name.desc(), AcademicYear.id.desc()).all()
    has_year_selection = selected_year_id not in (None, "")
    selected_config = (
        _behavior_configuration(_int(selected_config_id))
        if selected_config_id not in (None, "") else None
    )
    selected_year = _selected_year(selected_year_id)
    if selected_config and not has_year_selection:
        selected_year = selected_config.academic_year
    selected_year_id = selected_year.id if selected_year else None
    configurations = (
        BehaviorConfiguration.query
        .filter_by(academic_year_id=selected_year_id)
        .options(
            selectinload(BehaviorConfiguration.academic_year_levels).joinedload(
                BehaviorConfigurationLevel.academic_year_level
            )
        )
        .order_by(BehaviorConfiguration.id.desc())
        .all()
        if selected_year_id else []
    )
    if selected_config and selected_config.academic_year_id != selected_year_id:
        selected_config = None
    return years, selected_year, configurations, selected_config


def _selected_config(selected_config_id=None, year_id=None):
    has_config_selection = selected_config_id not in (None, "")
    requested_year_id = _int(year_id)
    selected_year = _selected_year(year_id)
    if has_config_selection:
        config = _behavior_configuration(_int(selected_config_id))
        if not config:
            return None
        if requested_year_id is not None and config.academic_year_id != requested_year_id:
            return None
        return validate_behavior_configuration(config)
    if not selected_year:
        return None
    return BehaviorConfiguration.query.filter_by(
        academic_year_id=selected_year.id
    ).order_by(BehaviorConfiguration.id).first()


def _behavior_configuration(config_id):
    """Load a Behavior configuration and its page-facing graph efficiently."""
    if not config_id:
        return None
    return db.session.get(
        BehaviorConfiguration,
        config_id,
        options=[
            joinedload(BehaviorConfiguration.academic_year),
            joinedload(BehaviorConfiguration.academic_year_level),
            selectinload(BehaviorConfiguration.academic_year_levels).joinedload(
                BehaviorConfigurationLevel.academic_year_level
            ),
            joinedload(BehaviorConfiguration.behavior_subject),
            selectinload(BehaviorConfiguration.sessions).joinedload(BehaviorSession.exam),
            selectinload(BehaviorConfiguration.sessions).joinedload(BehaviorSession.exam_type),
            selectinload(BehaviorConfiguration.categories)
            .selectinload(BehaviorCategory.actions)
            .selectinload(BehaviorAction.choices),
            selectinload(BehaviorConfiguration.categories)
            .selectinload(BehaviorCategory.subcategories)
            .selectinload(BehaviorSubCategory.actions),
            selectinload(BehaviorConfiguration.grade_scales),
            selectinload(BehaviorConfiguration.attendance_statuses),
            selectinload(BehaviorConfiguration.attendance_days),
        ],
    )


def _scope_payload(year_id, level_id=None):
    year = _selected_year(year_id)
    if not year:
        return {"success": True, "levels": [], "subjects": [], "exams": []}
    levels = year_levels(year.id)
    selected_level_id = _int(level_id)
    selected_level = _valid_level(year.id, selected_level_id) if selected_level_id else None
    subjects = (
        year_subjects(year.id, selected_level.id, subject_kind="behavior")
        if selected_level else []
    )
    exams = _behavior_exam_options(year.id)
    return {
        "success": True,
        "levels": [{"id": item.id, "name": item.name} for item in levels],
        "subjects": [
            {
                "id": item.id,
                "name": item.name,
                "academic_year_level_id": item.academic_year_level_id,
            }
            for item in subjects
        ],
        "exams": exams,
    }


def _valid_level(year_id, level_id):
    level = db.session.get(AcademicYearLevel, _int(level_id)) if level_id else None
    return level if level and level.academic_year_id == year_id else None


def _valid_class(level_id, class_id):
    item = db.session.get(AcademicYearClass, _int(class_id)) if class_id else None
    return item if item and item.academic_year_level_id == level_id and item.is_active else None


def _behavior_context(
    year_id=None,
    level_id=None,
    config_id=None,
    class_id=None,
    session_id=None,
    auto_select_config=True,
):
    """Build one year-aware context shared by all Behavior admin screens."""
    years = AcademicYear.query.order_by(AcademicYear.name.desc(), AcademicYear.id.desc()).all()
    has_year_selection = year_id not in (None, "")
    has_level_selection = level_id not in (None, "")
    has_config_selection = config_id not in (None, "")
    has_class_selection = class_id not in (None, "")
    has_session_selection = session_id not in (None, "")
    requested_year_id = _int(year_id)
    requested_level_id = _int(level_id)
    requested_config = (
        _behavior_configuration(_int(config_id))
        if has_config_selection else None
    )
    invalid_scope = has_config_selection and requested_config is None
    selected_year = _selected_year(year_id) if has_year_selection else None
    if has_year_selection and selected_year is None:
        invalid_scope = True
    if selected_year is None and not has_year_selection:
        selected_year = requested_config.academic_year if requested_config else _selected_year()
    if requested_config and selected_year and requested_config.academic_year_id != selected_year.id:
        invalid_scope = True
    levels = year_levels(selected_year.id) if selected_year else []
    if requested_config and not invalid_scope:
        configured_ids = configuration_level_ids(requested_config)
        selected_level = _valid_level(selected_year.id, requested_level_id) if has_level_selection else next(
            (
                item.academic_year_level
                for item in requested_config.academic_year_levels
                if item.academic_year_level_id in configured_ids
            ),
            requested_config.academic_year_level,
        )
        if has_level_selection and requested_level_id not in configuration_level_ids(requested_config):
            invalid_scope = True
    elif has_level_selection:
        selected_level = _valid_level(selected_year.id, requested_level_id) if selected_year else None
        if selected_level is None:
            invalid_scope = True
    else:
        selected_level = None
    if selected_level is None and not has_level_selection and not has_config_selection and levels:
        selected_level = levels[0]
    configurations = []
    if selected_year:
        query = BehaviorConfiguration.query.filter_by(academic_year_id=selected_year.id)
        configurations = query.options(
            selectinload(BehaviorConfiguration.academic_year_levels)
        ).order_by(BehaviorConfiguration.id.desc()).all()
        if selected_level:
            configurations = [
                item for item in configurations
                if selected_level.id in configuration_level_ids(item)
            ]
    selected_config = requested_config if not invalid_scope else None
    if selected_config and (
        not selected_year
        or selected_config.academic_year_id != selected_year.id
        or (selected_level and selected_level.id not in configuration_level_ids(selected_config))
    ):
        invalid_scope = True
        selected_config = None
    if selected_config is None and auto_select_config and configurations and not has_config_selection and not invalid_scope:
        selected_config = _behavior_configuration(configurations[0].id)
        # Keep an explicitly requested level.  The selected configuration may
        # serve several levels, so replacing a valid Secondary/third-level
        # selection with the legacy anchor level silently changed the page
        # scope back to Primary.
        if not has_level_selection:
            selected_level = next(
                (
                    item.academic_year_level
                    for item in selected_config.academic_year_levels
                    if item.academic_year_level_id in configuration_level_ids(selected_config)
                ),
                selected_config.academic_year_level,
            )
    classes = (
        AcademicYearClass.query
        .filter_by(academic_year_level_id=selected_level.id, is_active=True)
        .order_by(AcademicYearClass.sort_order, AcademicYearClass.name, AcademicYearClass.id)
        .all()
        if selected_level else []
    )
    selected_class = _valid_class(selected_level.id, _int(class_id)) if selected_level and has_class_selection else None
    if has_class_selection and selected_class is None:
        invalid_scope = True
    sessions = (
        selected_config.sessions
        if selected_config else []
    )
    selected_session = (
        next((item for item in sessions if item.id == _int(session_id)), None)
        if has_session_selection else None
    )
    if has_session_selection and selected_session is None:
        invalid_scope = True
    if selected_session is None and not has_session_selection and sessions:
        selected_session = sessions[0]
    return {
        "years": years,
        "selected_year": selected_year,
        "levels": levels,
        "selected_level": selected_level,
        "classes": classes,
        "selected_class": selected_class,
        "configurations": configurations,
        "config": selected_config,
        "sessions": sessions,
        "selected_session": selected_session,
        "scope_invalid": invalid_scope,
    }


def _behavior_enrollments(config, class_id=None, academic_year_level_id=None):
    if not config:
        return []
    level_ids = configuration_level_ids(config)
    requested_level_id = _int(academic_year_level_id)
    if requested_level_id in level_ids:
        level_ids = {requested_level_id}
    query = (
        StudentEnrollment.query
        .options(
            joinedload(StudentEnrollment.student),
            joinedload(StudentEnrollment.academic_year_level),
            joinedload(StudentEnrollment.academic_year_class),
            joinedload(StudentEnrollment.academic_section),
        )
        .join(Student, Student.id == StudentEnrollment.student_id)
        .join(AcademicYearClass, AcademicYearClass.id == StudentEnrollment.academic_year_class_id)
        .filter(
            StudentEnrollment.academic_year_id == config.academic_year_id,
            StudentEnrollment.academic_year_level_id.in_(level_ids),
            AcademicYearClass.academic_year_level_id.in_(level_ids),
            AcademicYearClass.is_active.is_(True),
            StudentEnrollment.status.notin_(("withdrawn", "archived")),
        )
    )
    if class_id:
        query = query.filter(StudentEnrollment.academic_year_class_id == _int(class_id))
    return query.order_by(Student.full_name, Student.student_code, StudentEnrollment.id).all()


def _student_board_rows(config, selected_session, class_id=None, academic_year_level_id=None):
    if not config or not selected_session:
        return []
    enrollments = _behavior_enrollments(config, class_id, academic_year_level_id)
    enrollment_ids = [item.id for item in enrollments]
    events_by_enrollment = defaultdict(list)
    attendance_by_enrollment = defaultdict(list)
    if enrollment_ids:
        event_rows = BehaviorEvent.query.filter(
            BehaviorEvent.behavior_configuration_id == config.id,
            BehaviorEvent.behavior_session_id == selected_session.id,
            BehaviorEvent.student_enrollment_id.in_(enrollment_ids),
            BehaviorEvent.status == "active",
        ).all()
        attendance_rows = BehaviorAttendanceRecord.query.filter(
            BehaviorAttendanceRecord.behavior_configuration_id == config.id,
            BehaviorAttendanceRecord.behavior_session_id == selected_session.id,
            BehaviorAttendanceRecord.student_enrollment_id.in_(enrollment_ids),
        ).order_by(
            BehaviorAttendanceRecord.attendance_date.asc(),
            BehaviorAttendanceRecord.id.asc(),
        ).all()
        for row in event_rows:
            events_by_enrollment[row.student_enrollment_id].append(row)
        for row in attendance_rows:
            attendance_by_enrollment[row.student_enrollment_id].append(row)
    rows = []
    for enrollment in enrollments:
        score = calculate_session_score(
            config,
            selected_session,
            enrollment,
            behavior_events=events_by_enrollment.get(enrollment.id, []),
            attendance_records=attendance_by_enrollment.get(enrollment.id, []),
        )
        events = score.get("_active_events", [])
        rows.append({
            "enrollment": enrollment,
            "score": score,
            "grade": (
                behavior_grade_for_score(selected_session, score["final_score"])
                if score.get("final_score") is not None else None
            ),
            "events": events,
            "positive_events": sum(1 for item in events if item.polarity == "positive"),
            "negative_events": sum(1 for item in events if item.polarity == "negative"),
        })
    return rows


def _event_page_data(args):
    context = _behavior_context(
        args.get("year_id"),
        args.get("level_id"),
        args.get("config_id"),
        args.get("class_id"),
        args.get("session_id"),
        auto_select_config=False,
    )
    query = BehaviorEvent.query.options(
        joinedload(BehaviorEvent.student),
        joinedload(BehaviorEvent.student_enrollment).joinedload(
            StudentEnrollment.academic_year_class
        ),
        joinedload(BehaviorEvent.configuration).joinedload(
            BehaviorConfiguration.academic_year
        ),
        joinedload(BehaviorEvent.configuration).joinedload(
            BehaviorConfiguration.academic_year_level
        ),
    )
    if context["scope_invalid"]:
        query = query.filter(BehaviorEvent.id == -1)
    if context["selected_year"]:
        query = query.filter(BehaviorEvent.configuration.has(
            BehaviorConfiguration.academic_year_id == context["selected_year"].id
        ))
    if context["selected_level"]:
        query = query.filter(BehaviorEvent.configuration.has(
            BehaviorConfiguration.academic_year_levels.any(
                BehaviorConfigurationLevel.academic_year_level_id == context["selected_level"].id
            )
            | (
                ~BehaviorConfiguration.academic_year_levels.any()
                & (BehaviorConfiguration.academic_year_level_id == context["selected_level"].id)
            )
        ))
    if context["config"]:
        query = query.filter_by(behavior_configuration_id=context["config"].id)
    if context["selected_class"]:
        query = query.filter(BehaviorEvent.student_enrollment.has(
            StudentEnrollment.academic_year_class_id == context["selected_class"].id
        ))
    if context["selected_session"]:
        query = query.filter_by(behavior_session_id=context["selected_session"].id)
    if args.get("student_enrollment_id"):
        query = query.filter_by(student_enrollment_id=_int(args.get("student_enrollment_id")))
    if args.get("direction") in {"positive", "negative"}:
        query = query.filter_by(polarity=args.get("direction"))
    if args.get("category_id"):
        query = query.filter_by(behavior_category_id=_int(args.get("category_id")))
    if args.get("action_id"):
        query = query.filter_by(behavior_action_id=_int(args.get("action_id")))
    if args.get("status") in BehaviorEvent.STATUS_VALUES:
        query = query.filter_by(status=args.get("status"))
    try:
        if args.get("date_from"):
            query = query.filter(BehaviorEvent.occurred_at >= datetime.combine(date.fromisoformat(args.get("date_from")), datetime.min.time()))
        if args.get("date_to"):
            query = query.filter(BehaviorEvent.occurred_at < datetime.combine(date.fromisoformat(args.get("date_to")), datetime.max.time()))
    except ValueError:
        pass
    rows = query.order_by(BehaviorEvent.occurred_at.desc(), BehaviorEvent.id.desc()).limit(500).all()
    context["students"] = (
        _behavior_enrollments(
            context["config"],
            context["selected_class"].id if context["selected_class"] else None,
            context["selected_level"].id if context["selected_level"] else None,
        )
        if not context["scope_invalid"] else []
    )
    context["categories"] = context["config"].categories if context["config"] else []
    context["actions"] = [item for category in context["categories"] for item in category.actions]
    context["rows"] = rows
    return context


@behavior_bp.route("/")
def dashboard():
    context = _behavior_context(
        request.args.get("year_id"),
        request.args.get("level_id"),
        request.args.get("config_id"),
        request.args.get("class_id"),
        request.args.get("session_id"),
    )
    board_rows = (
        _student_board_rows(
            context["config"],
            context["selected_session"],
            context["selected_class"].id if context["selected_class"] else None,
            context["selected_level"].id if context["selected_level"] else None,
        )
        if not context["scope_invalid"] else []
    )
    active_events = [event for row in board_rows for event in row["events"]]
    evaluated = [row for row in board_rows if row["events"]]
    positive = [row for row in board_rows if row["positive_events"]]
    negative = [row for row in board_rows if row["negative_events"]]
    # Allocated Attendance without a record intentionally produces an
    # incomplete score (None). It must not be added to Decimal totals.
    final_scores = [
        row["score"]["final_score"]
        for row in board_rows
        if row["score"].get("final_score") is not None
    ]
    average = (sum(final_scores, Decimal("0.000")) / len(final_scores)).quantize(Decimal("0.001")) if final_scores else Decimal("0.000")
    summary = {
        "students": len(board_rows),
        "evaluated": len(evaluated),
        "clean": len(board_rows) - len(evaluated),
        "positive": len(positive),
        "negative": len(negative),
        "active_events": len(active_events),
        "session_average": average,
    }
    try:
        session_allocation = (
        session_allocation_projection(context["selected_session"])
            if context["selected_session"] else None
        )
        session_allocation_error = None
    except BehaviorValidationError as exc:
        session_allocation = None
        session_allocation_error = str(exc)
    return render_template(
        "admin/behavior/dashboard.html",
        **context,
        board_rows=board_rows,
        summary=summary,
        session_allocation=session_allocation,
        session_allocation_error=session_allocation_error,
    )


@behavior_bp.route("/api/scope")
def scope_api():
    return jsonify(_scope_payload(request.args.get("year_id"), request.args.get("level_id")))


@behavior_bp.route("/subjects", methods=["POST"])
def create_subject():
    """Create a Behavior-only subject inside the selected year-level scope."""
    year_id = _int(request.form.get("academic_year_id"))
    level_id = _int(request.form.get("academic_year_level_id"))
    name = (request.form.get("name") or "").strip()
    try:
        year = db.session.get(AcademicYear, year_id) if year_id else None
        level = db.session.get(AcademicYearLevel, level_id) if level_id else None
        if not year or not level or level.academic_year_id != year.id:
            raise BehaviorValidationError(
                "Select a matching Academic Year and year-aware Academic Level first"
            )
        if not name:
            raise BehaviorValidationError("Behavior subject name is required")
        existing = AcademicYearSubject.query.filter_by(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            name=name,
        ).first()
        if existing:
            raise BehaviorValidationError(
                "A subject with this name already exists for the selected year and level"
            )
        last_subject = (
            AcademicYearSubject.query
            .filter_by(
                academic_year_id=year.id,
                academic_year_level_id=level.id,
            )
            .order_by(AcademicYearSubject.sort_order.desc(), AcademicYearSubject.id.desc())
            .first()
        )
        subject = AcademicYearSubject(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            name=name,
            subject_kind="behavior",
            max_score=0,
            sort_order=(last_subject.sort_order + 1) if last_subject else 1,
            is_active=True,
            legacy_subject_id=None,
        )
        db.session.add(subject)
        db.session.flush()
        audit(
            "Behavior Subject",
            f"Created Behavior subject {subject.name} for {year.name} / {level.name}",
        )
        db.session.commit()
        flash("Behavior subject created for the selected Academic Year and Level.", "success")
        return redirect(
            url_for(
                "behavior.configuration",
                year_id=year.id,
                level_id=level.id,
                subject_id=subject.id,
            )
        )
    except (BehaviorValidationError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError:
        db.session.rollback()
        flash(
            "A Behavior subject with this name already exists for the selected year and level.",
            "danger",
        )
    return redirect(
        url_for(
            "behavior.configuration",
            year_id=year_id,
            level_id=level_id,
        )
    )


@behavior_bp.route("/subjects/<int:subject_id>/delete", methods=["POST"])
def delete_subject(subject_id):
    subject = db.session.get(AcademicYearSubject, subject_id)
    year_id = request.form.get("academic_year_id") or (subject.academic_year_id if subject else None)
    level_id = request.form.get("academic_year_level_id") or (subject.academic_year_level_id if subject else None)
    try:
        if not subject or subject.subject_kind != "behavior":
            raise BehaviorValidationError("Behavior subject was not found")
        if BehaviorConfiguration.query.filter_by(academic_year_subject_id=subject.id).first():
            raise BehaviorValidationError(
                "Delete the Behavior configuration first; this subject is still in use."
            )
        audit("Behavior Subject", f"Deleted Behavior subject {subject.id}: {subject.name}")
        db.session.delete(subject)
        db.session.commit()
        flash("Behavior subject deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorValidationError)
            else "This Behavior subject cannot be deleted because it is still in use.",
            "danger",
        )
    return redirect(url_for("behavior.configuration", year_id=year_id, level_id=level_id))


@behavior_bp.route("/configuration", methods=["GET", "POST"])
def configuration():
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    if request.method == "POST":
        try:
            year_id = _int(request.form.get("academic_year_id"))
            existing_config = db.session.get(BehaviorConfiguration, config_id) if config_id else None
            level_ids = [
                item for item in (_int(value) for value in request.form.getlist("academic_year_level_ids"))
                if item is not None
            ]
            # Keep older integrations and bookmarked/admin forms working while
            # the current UI submits the multi-level checkbox field.
            if not level_ids:
                legacy_level_id = _int(request.form.get("academic_year_level_id"))
                if legacy_level_id is not None:
                    level_ids = [legacy_level_id]
            mode = (request.form.get("academic_level_mode") or "selected").strip().lower()
            year = db.session.get(AcademicYear, year_id) if year_id else None
            if not year:
                raise BehaviorValidationError("Academic Year does not exist")
            available_levels = year_levels(year.id)
            if mode == "all":
                level_ids = [item.id for item in available_levels]
            if mode not in {"all", "selected"}:
                raise BehaviorValidationError("Select All Levels or Selected Levels")
            if not level_ids:
                raise BehaviorValidationError("Select at least one Academic Level")
            if len(level_ids) != len(set(level_ids)):
                raise BehaviorValidationError("An Academic Level cannot be selected more than once")
            # Existing configurations retain their canonical subject/legacy
            # anchor for history compatibility; membership is the level scope.
            level_id = existing_config.academic_year_level_id if existing_config else level_ids[0]
            level_check = SimpleNamespace(
                id=config_id,
                academic_year_id=year.id,
                academic_year_level_id=level_id,
                academic_year_subject_id=_int(request.form.get("academic_year_subject_id")),
            )
            validate_configuration_levels(level_check, level_ids)
            subject_id = _int(request.form.get("academic_year_subject_id"))
            year, level, subject = validate_behavior_scope(year_id, level_id, subject_id)
            config = existing_config
            if config and (
                config.academic_year_id != year.id
                or config.academic_year_subject_id != subject.id
            ):
                raise BehaviorValidationError("An existing Behavior configuration cannot change its academic scope")
            if not config:
                config = configuration_for_scope(year.id, level.id, subject.id)
            if not config:
                config = BehaviorConfiguration(
                    academic_year_id=year.id,
                    academic_year_level_id=level.id,
                    academic_year_subject_id=subject.id,
                    created_by=current_user.id,
                )
                db.session.add(config)
            frequency = (request.form.get("frequency") or "").strip().lower()
            if frequency:
                if frequency not in BehaviorConfiguration.FREQUENCY_VALUES:
                    raise BehaviorValidationError("Behavior configuration frequency is invalid")
                config.frequency = frequency
            # ``status`` is retained only for old database compatibility. The
            # Behavior workflow no longer gates valid configurations behind a
            # Draft/Active/Archived lifecycle.
            config.status = "active"
            db.session.flush()
            current_memberships = BehaviorConfigurationLevel.query.filter_by(
                behavior_configuration_id=config.id,
            ).all()
            selected_ids = set(level_ids)
            for membership in current_memberships:
                if membership.academic_year_level_id not in selected_ids:
                    db.session.delete(membership)
            existing_ids = {item.academic_year_level_id for item in current_memberships}
            for selected_id in selected_ids - existing_ids:
                db.session.add(BehaviorConfigurationLevel(
                    behavior_configuration_id=config.id,
                    academic_year_level_id=selected_id,
                ))
            # Configuration creation is immediately operational. Completeness
            # is reported separately from saving and never controls editing.
            db.session.flush()
            audit(
                "Behavior Configuration",
                f"Saved configuration {config.id} for {year.name} / {len(selected_ids)} Academic Level(s) / {subject.name}",
            )
            db.session.commit()
            flash("Behavior configuration saved.", "success")
            return redirect(url_for("behavior.configuration", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash(
                "A Behavior configuration already exists for this Academic Year, Level, and Subject.",
                "danger",
            )

    years, selected_year, configurations, selected_config = _config_choices(
        request.args.get("year_id") or request.form.get("academic_year_id"),
        config_id,
    )
    selected_level_ids = configuration_level_ids(selected_config) if selected_config else set()
    # The subject remains tied to the configuration's original canonical
    # level; the membership set above is the authoritative multi-level scope.
    selected_level_id = (
        selected_config.academic_year_level_id
        if selected_config else _int(request.args.get("level_id"))
    )
    selected_subject_id = (
        selected_config.academic_year_subject_id
        if selected_config else _int(request.args.get("subject_id"))
    )
    levels = year_levels(selected_year.id) if selected_year else []
    if selected_level_id is None and levels:
        selected_level_id = levels[0].id
    selected_level = next(
        (item for item in levels if item.id == selected_level_id),
        None,
    )
    subjects = year_subjects(selected_year.id, selected_level_id, subject_kind="behavior") if selected_year and selected_level_id else []
    exam_types = _behavior_exam_options(selected_year.id, selected_config) if selected_year else []
    selected_exam_ref = (request.args.get("exam_ref") or "").strip()
    selected_exam = next(
        (item for item in exam_types if item["value"] == selected_exam_ref),
        None,
    )
    if selected_exam is None:
        selected_exam_ref = ""
    visible_exam_types = [selected_exam] if selected_exam else exam_types
    configured_sessions = {
        _session_exam_value(item): item
        for item in selected_config.sessions
    } if selected_config else {}
    allocation = allocation_total(selected_config) if selected_config else Decimal("0.000")
    active_session_count = len(selected_config.sessions) if selected_config else 0
    active_category_count = len(selected_config.categories) if selected_config else 0
    active_action_count = (
        sum(
            1
            for category in selected_config.categories
            for action in category.actions
        )
        if selected_config else 0
    )
    return render_template(
        "admin/behavior/configuration.html",
        years=years,
        selected_year=selected_year,
        levels=levels,
        selected_level=selected_level,
        subjects=subjects,
        configurations=configurations,
        selected_config=selected_config,
        selected_level_id=selected_level_id,
        selected_subject_id=selected_subject_id,
        allocation=allocation,
        allocation_remaining=(Decimal("100.000") - allocation).quantize(Decimal("0.001")),
        active_session_count=active_session_count,
        active_category_count=active_category_count,
        active_action_count=active_action_count,
        exam_types=exam_types,
        selected_exam=selected_exam,
        selected_exam_ref=selected_exam_ref,
        visible_exam_types=visible_exam_types,
        configured_sessions=configured_sessions,
        selected_level_ids=selected_level_ids,
        academic_level_mode=("all" if selected_year and selected_level_ids == {item.id for item in levels} else "selected"),
        setup_complete=(
            active_session_count > 0
            and allocation == Decimal("100.000")
            and active_category_count > 0
            and active_action_count > 0
        ),
    )


@behavior_bp.route("/configuration/<int:config_id>/delete", methods=["POST"])
def delete_configuration(config_id):
    config = db.session.get(BehaviorConfiguration, config_id)
    year_id = request.form.get("year_id") or (config.academic_year_id if config else None)
    try:
        if not config:
            raise BehaviorValidationError("Behavior configuration was not found")
        if BehaviorEvent.query.filter_by(behavior_configuration_id=config.id).first():
            raise BehaviorValidationError(
                "This Behavior configuration cannot be deleted because it has historical events."
            )
        audit("Behavior Configuration", f"Deleted Behavior configuration {config.id}")
        db.session.delete(config)
        db.session.commit()
        flash("Behavior configuration deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorValidationError)
            else "This Behavior configuration cannot be deleted because it is still in use.",
            "danger",
        )
    return redirect(url_for("behavior.configuration", year_id=year_id))


@behavior_bp.route("/grade-management", methods=["GET", "POST"])
def grade_management():
    """Manage natural-score grade bands owned exclusively by one Behavior session."""
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    year_id = request.args.get("year_id") or request.form.get("year_id")
    config = _selected_config(config_id, year_id)
    session_id = _int(request.args.get("session_id") or request.form.get("session_id"))
    selected_session = (
        next((item for item in config.sessions if item.id == session_id), None)
        if config and session_id
        else None
    )
    if config and selected_session is None and not session_id:
        selected_session = next(iter(config.sessions), None)

    if request.method == "POST":
        try:
            if not config:
                raise BehaviorGradeValidationError(
                    "Select a Behavior configuration before saving a grade band."
                )
            ensure_configuration_editable(config)
            session_id = _int(request.form.get("session_id"))
            selected_session = next(
                (item for item in config.sessions if item.id == session_id),
                None,
            )
            if not selected_session:
                raise BehaviorGradeValidationError(
                    "Select a Behavior session before saving its grade scale."
                )

            if request.form.get("action") == "generate_previous":
                source_session = next(
                    (
                        item for item in config.sessions
                        if item.id == _int(request.form.get("source_session_id"))
                    ),
                    None,
                )
                if not source_session or source_session.id == selected_session.id:
                    raise BehaviorGradeValidationError(
                        "Select a different previous Behavior session to copy."
                    )
                if behavior_grade_scales(selected_session):
                    raise BehaviorGradeValidationError(
                        "Clear the selected session's existing grade bands before copying another scale."
                    )
                source_maximum = decimal_value(
                    source_session.maximum_score,
                    "Source session maximum",
                    minimum="0.001",
                )
                target_maximum = decimal_value(
                    selected_session.maximum_score,
                    "Session maximum",
                    minimum="0.001",
                )
                ratio = target_maximum / source_maximum
                copied = 0
                for source_scale in behavior_grade_scales(source_session):
                    minimum = min(
                        target_maximum,
                        (Decimal(str(source_scale.min_score)) * ratio).quantize(Decimal("0.001")),
                    )
                    maximum = min(
                        target_maximum,
                        (Decimal(str(source_scale.max_score)) * ratio).quantize(Decimal("0.001")),
                    )
                    if maximum < minimum:
                        maximum = minimum
                    db.session.add(
                        BehaviorGradeScale(
                            behavior_configuration_id=config.id,
                            behavior_session_id=selected_session.id,
                            grade=source_scale.grade,
                            min_score=minimum,
                            max_score=maximum,
                            grade_point=source_scale.grade_point,
                            description=source_scale.description,
                            sort_order=source_scale.sort_order,
                            is_active=source_scale.is_active,
                            is_pass=source_scale.is_pass,
                        )
                    )
                    copied += 1
                if not copied:
                    raise BehaviorGradeValidationError(
                        "The selected previous Behavior session has no grade bands to copy."
                    )
                db.session.flush()
                audit(
                    "Behavior Grade Management",
                    f"Copied {copied} Behavior grade bands from session {source_session.id} "
                    f"to session {selected_session.id}",
                )
                db.session.commit()
                flash("Behavior grade scale copied. Review the natural score ranges before using it.", "success")
                return redirect(url_for(
                    "behavior.grade_management",
                    config_id=config.id,
                    session_id=selected_session.id,
                ))

            scale_id = _int(request.form.get("grade_id"))
            item = db.session.get(BehaviorGradeScale, scale_id) if scale_id else None
            if item and (
                item.behavior_configuration_id != config.id
                or item.behavior_session_id != selected_session.id
            ):
                raise BehaviorGradeValidationError(
                    "The selected Behavior grade band is outside this Behavior session."
                )
            values = validate_behavior_grade_values(
                request.form.get("grade"),
                request.form.get("min_score"),
                request.form.get("max_score"),
                request.form.get("grade_point"),
                request.form.get("description"),
                request.form.get("sort_order"),
                session_maximum=selected_session.maximum_score,
            )
            if request.form.get("is_active"):
                validate_behavior_grade_overlap(
                    selected_session,
                    values["min_score"],
                    values["max_score"],
                    exclude_id=item.id if item else None,
                )
            if not item:
                item = BehaviorGradeScale(
                    behavior_configuration_id=config.id,
                    behavior_session_id=selected_session.id,
                )
                db.session.add(item)
            for key, value in values.items():
                setattr(item, key, value)
            item.is_active = bool(request.form.get("is_active"))
            item.is_pass = bool(request.form.get("is_pass"))
            db.session.flush()
            audit(
                "Behavior Grade Management",
                f"Saved Behavior grade {item.grade} ({item.min_score}-{item.max_score}) "
                f"for session {selected_session.id} in configuration {config.id}",
            )
            db.session.commit()
            flash("Behavior grade band saved.", "success")
            return redirect(url_for(
                "behavior.grade_management",
                config_id=config.id,
                session_id=selected_session.id,
            ))
        except (BehaviorGradeValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash(
                "This Behavior grade letter already exists in the selected configuration.",
                "danger",
            )

    years, selected_year, configurations, selected_config = _config_choices(
        config.academic_year_id if config else year_id,
        config.id if config else config_id,
    )
    sessions = list(selected_config.sessions) if selected_config else []
    selected_session = (
        next((item for item in sessions if item.id == session_id), None)
        if session_id else (sessions[0] if sessions else None)
    )
    scales = behavior_grade_scales(selected_session) if selected_session else []
    readiness = (
        behavior_grade_readiness(selected_session)
        if selected_session
        else {
            "ready": False,
            "message": "Select a Behavior session to manage its private grade scale.",
        }
    )
    return render_template(
        "admin/behavior/grade_management.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=selected_config,
        sessions=sessions,
        selected_session=selected_session,
        scales=scales,
        readiness=readiness,
    )


@behavior_bp.route("/grade-management/<int:scale_id>/delete", methods=["POST"])
def delete_grade_scale(scale_id):
    item = db.session.get(BehaviorGradeScale, scale_id)
    config_id = request.form.get("config_id") or (item.behavior_configuration_id if item else None)
    session_id = request.form.get("session_id") or (item.behavior_session_id if item else None)
    try:
        if not item:
            raise BehaviorGradeValidationError("Behavior grade band was not found.")
        config = item.configuration
        if not config or config.id != _int(config_id) or item.behavior_session_id != _int(session_id):
            raise BehaviorGradeValidationError(
                "The Behavior grade band is outside the selected session."
            )
        ensure_configuration_editable(config)
        audit("Behavior Grade Management", f"Deleted Behavior grade {item.id} from session {item.behavior_session_id}")
        db.session.delete(item)
        db.session.commit()
        flash("Behavior grade band deleted.", "success")
    except (BehaviorGradeValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorGradeValidationError)
            else "This Behavior grade band cannot be deleted.",
            "danger",
        )
    return redirect(url_for(
        "behavior.grade_management",
        config_id=config_id,
        session_id=session_id,
    ))


@behavior_bp.route("/sessions", methods=["GET", "POST"])
def sessions():
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    config = _selected_config(config_id, request.args.get("year_id"))
    if request.method == "POST":
        try:
            if not config:
                raise BehaviorValidationError("Select a Behavior configuration first")
            exam_source, exam_type = _resolve_behavior_exam(
                request.form.get("exam_ref"),
                request.form.get("exam_type_id"),
            )
            if not exam_type or exam_source not in {"exam", "legacy"}:
                raise BehaviorValidationError("Select a valid Exam Type for this Academic Year")
            if exam_source == "exam":
                exam_type = validate_session_scope(config, exam_id=exam_type.id)
            else:
                exam_type = validate_session_scope(config, exam_type_id=exam_type.id)
            session_id = _int(request.form.get("session_id"))
            item = db.session.get(BehaviorSession, session_id) if session_id else None
            if item and item.behavior_configuration_id != config.id:
                raise BehaviorValidationError("Behavior session is outside the selected configuration")
            ensure_session_editable(config, item)
            legacy_session = bool(
                item
                and item.behavior_allocation is None
                and item.attendance_allocation is None
            )
            submitted_exam_ref = f"{exam_source}:{exam_type.id}"
            duplicate = next(
                (
                    existing
                    for existing in config.sessions
                    if existing.id != (item.id if item else None)
                    and _session_exam_value(existing) == submitted_exam_ref
                ),
                None,
            )
            if duplicate:
                raise BehaviorValidationError(
                    f"{exam_type.name} is already assigned to this Behavior configuration. "
                    "Edit the existing session below instead."
                )
            if not item:
                item = BehaviorSession(
                    behavior_configuration_id=config.id,
                )
                db.session.add(item)
            item.exam_id = exam_type.id if exam_source == "exam" else None
            item.exam_type_id = exam_type.id if exam_source == "legacy" else None
            item.session_label = (request.form.get("session_label") or exam_type.name).strip()
            if not item.session_label:
                raise BehaviorValidationError("Session label is required")
            maximum_score = decimal_value(
                request.form.get("maximum_score"),
                "Session maximum",
                minimum="0.001",
            )
            if legacy_session:
                behavior_allocation = None
                attendance_allocation = None
            else:
                behavior_raw = request.form.get("behavior_allocation")
                attendance_raw = request.form.get("attendance_allocation")
                behavior_allocation = (
                    decimal_value(behavior_raw, "Behavior allocation", minimum="0")
                    if behavior_raw not in (None, "") else (maximum_score / Decimal("2")).quantize(Decimal("0.001"))
                )
                attendance_allocation = (
                    decimal_value(attendance_raw, "Attendance allocation", minimum="0")
                    if attendance_raw not in (None, "") else (maximum_score - behavior_allocation).quantize(Decimal("0.001"))
                )
                if (behavior_allocation + attendance_allocation).quantize(Decimal("0.001")) != maximum_score:
                    raise BehaviorValidationError(
                        "Behavior and Attendance allocations must equal the session maximum"
                    )
            item.maximum_score = maximum_score
            item.behavior_allocation = behavior_allocation
            item.attendance_allocation = attendance_allocation
            item.sort_order = _int(request.form.get("sort_order"), 0)
            item.is_active = True  # legacy column; all saved sessions are operational
            db.session.flush()
            ensure_attendance_defaults(config)
            capture_attendance_session_policy(config, item)
            config.annual_allocation = allocation_total(config)
            audit(
                "Behavior Sessions",
                f"Saved Behavior session {item.id} for configuration {config.id}; allocation {config.annual_allocation}",
            )
            db.session.commit()
            flash(
                "Behavior session allocation saved. Complete the annual total of 100 before recording events.",
                "success",
            )
            return redirect(url_for("behavior.sessions", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            current_app.logger.exception(
                "Behavior session save failed with a database integrity error"
            )
            flash(
                "This session could not be saved because it conflicts with an existing session or database constraint.",
                "danger",
            )

    years, selected_year, configurations, _ = _config_choices(
        config.academic_year_id if config else request.args.get("year_id"),
        config.id if config else None,
    )
    exams = _behavior_exam_options(selected_year.id, config) if selected_year else []
    assigned_exam_refs = {
        _session_exam_value(item)
        for item in (config.sessions if config else [])
    }
    available_exams = [
        exam for exam in exams if exam["value"] not in assigned_exam_refs
    ]
    selected_exam_ref = (request.args.get("exam_ref") or "").strip()
    if selected_exam_ref not in {item["value"] for item in exams}:
        selected_exam_ref = ""
    return render_template(
        "admin/behavior/sessions.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=config,
        exams=exams,
        available_exams=available_exams,
        selected_exam_ref=selected_exam_ref,
        allocation=allocation_total(config) if config else Decimal("0.000"),
    )


@behavior_bp.route("/session-allocation", methods=["GET"])
def session_allocation():
    """Dedicated planning view backed by the canonical session mutation route."""
    context = _behavior_context(
        request.args.get("year_id"),
        request.args.get("level_id"),
        request.args.get("config_id"),
        request.args.get("class_id"),
        request.args.get("session_id"),
    )
    config = context["config"]
    sessions = context["sessions"]
    session_views = []
    for item in sessions:
        try:
            allocation = session_allocation_projection(item)
            allocation_error = None
        except BehaviorValidationError as exc:
            allocation = None
            allocation_error = str(exc)
        session_views.append({"session": item, "allocation": allocation, "error": allocation_error})

    enrollments = _behavior_enrollments(
        config,
        context["selected_class"].id if context["selected_class"] else None,
        context["selected_level"].id if context["selected_level"] else None,
    ) if config else []
    requested_enrollment_id = _int(request.args.get("enrollment_id"))
    selected_enrollment = next(
        (item for item in enrollments if item.id == requested_enrollment_id),
        None,
    )
    if selected_enrollment is None and enrollments:
        selected_enrollment = enrollments[0]

    selected_allocation = None
    selected_allocation_error = None
    selected_score = None
    selected_projection = None
    selected_session = context["selected_session"]
    if selected_session:
        try:
            selected_allocation = session_allocation_projection(selected_session)
        except BehaviorValidationError as exc:
            selected_allocation_error = str(exc)
        if selected_enrollment and not selected_allocation_error:
            try:
                selected_score = calculate_session_score(
                    config,
                    selected_session,
                    selected_enrollment,
                )
                selected_projection = scoring_ledger_projection(selected_score)
            except BehaviorValidationError as exc:
                selected_allocation_error = str(exc)

    return render_template(
        "admin/behavior/session_allocation.html",
        **context,
        session_views=session_views,
        enrollments=enrollments,
        selected_enrollment=selected_enrollment,
        selected_allocation=selected_allocation,
        selected_allocation_error=selected_allocation_error,
        selected_score=selected_score,
        selected_projection=selected_projection,
        selected_exam_ref=_session_exam_value(selected_session) if selected_session else "",
    )


@behavior_bp.route("/sessions/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    item = db.session.get(BehaviorSession, session_id)
    config_id = request.form.get("config_id") or (item.behavior_configuration_id if item else None)
    try:
        if not item:
            raise BehaviorValidationError("Behavior session was not found")
        config = item.configuration
        ensure_configuration_editable(config)
        if BehaviorEvent.query.filter_by(behavior_session_id=item.id).first():
            raise BehaviorValidationError(
                "This Behavior session cannot be deleted because it has historical events."
            )
        audit("Behavior Sessions", f"Deleted Behavior session {item.id}")
        remaining_allocation = allocation_total(config) - decimal_value(
            item.maximum_score,
            "Session maximum",
            minimum="0.001",
        )
        db.session.delete(item)
        config.annual_allocation = max(Decimal("0.000"), remaining_allocation).quantize(Decimal("0.001"))
        db.session.commit()
        flash("Behavior session deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorValidationError)
            else "This Behavior session cannot be deleted because it is still in use.",
            "danger",
        )
    return redirect(url_for("behavior.sessions", config_id=config_id))


@behavior_bp.route("/categories", methods=["GET", "POST"])
def categories():
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    config = _selected_config(config_id, request.args.get("year_id"))
    if request.method == "POST":
        try:
            if not config:
                raise BehaviorValidationError("Select a Behavior configuration first")
            ensure_configuration_editable(config)
            name = (request.form.get("name") or "").strip()
            polarity = (request.form.get("polarity") or "").strip().lower()
            if not name or polarity not in {"positive", "negative"}:
                raise BehaviorValidationError(
                    "Category name and Positive/Negative polarity are required"
                )
            item = (
                db.session.get(BehaviorCategory, _int(request.form.get("category_id")))
                if request.form.get("category_id") else None
            )
            if item and item.behavior_configuration_id != config.id:
                raise BehaviorValidationError(
                    "Behavior category is outside the selected configuration"
                )
            item = item or BehaviorCategory(behavior_configuration_id=config.id)
            item.name = name
            item.polarity = polarity
            item.description = (request.form.get("description") or "").strip() or None
            item.sort_order = _int(request.form.get("sort_order"), 0)
            item.is_active = True  # legacy column; all saved categories are operational
            db.session.add(item)
            db.session.flush()
            audit(
                "Behavior Categories",
                f"Saved {polarity} category {item.name} for configuration {config.id}",
            )
            db.session.commit()
            flash("Behavior category saved.", "success")
            return redirect(url_for("behavior.categories", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash(
                "A category with this name and polarity already exists in the selected configuration.",
                "danger",
            )
    years, selected_year, configurations, _ = _config_choices(
        config.academic_year_id if config else request.args.get("year_id"),
        config.id if config else None,
    )
    return render_template(
        "admin/behavior/categories.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=config,
    )


@behavior_bp.route("/taxonomy")
def taxonomy():
    """Single visual taxonomy workspace; existing mutation routes remain unchanged."""
    config_id = _int(request.args.get("config_id"))
    config = _selected_config(config_id, request.args.get("year_id"))
    years, selected_year, configurations, _ = _config_choices(
        config.academic_year_id if config else request.args.get("year_id"),
        config.id if config else None,
    )
    return render_template(
        "admin/behavior/taxonomy.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=config,
        active_tab=request.args.get("tab", "overview"),
    )


@behavior_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
def delete_category(category_id):
    item = db.session.get(BehaviorCategory, category_id)
    config_id = request.form.get("config_id") or (item.behavior_configuration_id if item else None)
    try:
        if not item:
            raise BehaviorValidationError("Behavior category was not found")
        config = item.configuration
        ensure_configuration_editable(config)
        if BehaviorEvent.query.filter_by(behavior_category_id=item.id).first():
            raise BehaviorValidationError(
                "This Behavior category cannot be deleted because it has historical events."
            )
        audit("Behavior Categories", f"Deleted category {item.id}: {item.name}")
        db.session.delete(item)
        db.session.commit()
        flash("Behavior category deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorValidationError)
            else "This category cannot be deleted because it is still in use.",
            "danger",
        )
    return redirect(url_for("behavior.categories", config_id=config_id))


@behavior_bp.route("/actions", methods=["GET", "POST"])
def actions():
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    config = _selected_config(config_id, request.args.get("year_id"))
    if request.method == "POST":
        try:
            if not config:
                raise BehaviorValidationError("Select a Behavior configuration first")
            ensure_configuration_editable(config)
            category = db.session.get(
                BehaviorCategory,
                _int(request.form.get("behavior_category_id")),
            )
            if not category or category.behavior_configuration_id != config.id:
                raise BehaviorValidationError(
                    "Category does not belong to the selected Behavior configuration"
                )
            subcategory = None
            subcategory_id = _int(request.form.get("behavior_subcategory_id"))
            if subcategory_id:
                subcategory = db.session.get(BehaviorSubCategory, subcategory_id)
                if not subcategory or subcategory.behavior_category_id != category.id:
                    raise BehaviorValidationError(
                        "Sub-category does not belong to the selected Behavior category"
                    )
            item = (
                db.session.get(BehaviorAction, _int(request.form.get("action_id")))
                if request.form.get("action_id") else None
            )
            if item and item.behavior_category_id != category.id:
                raise BehaviorValidationError(
                    "Behavior action is outside the selected category"
                )
            original_behavior_type = item.behavior_type if item else None
            original_action_points = (
                decimal_value(item.points, "Action points", minimum="0.001")
                if item else None
            )
            item = item or BehaviorAction(behavior_category_id=category.id)
            submitted_type = (request.form.get("behavior_type") or "short_answer").strip().lower()
            legacy_types = {"direct_action", "choice", "selection", "dropdown", "linear_scale"}
            if submitted_type in {"dropdown", "linear_scale"} and not item.id:
                raise BehaviorValidationError("Drop-down and Linear scale are no longer supported")
            if submitted_type in BEHAVIOR_RESPONSE_TYPES:
                behavior_type = storage_response_type(submitted_type)
                response_type = submitted_type
            elif submitted_type in {"direct_action", "choice", "selection"}:
                behavior_type = submitted_type
                response_type = canonical_response_type(submitted_type)
            else:
                raise BehaviorValidationError("Behavior action response type is invalid")
            item.behavior_subcategory_id = subcategory.id if subcategory else None
            item.behavior_type = behavior_type
            item.response_type = response_type
            item.response_required = request.form.get("response_required") == "1"
            item.name = (request.form.get("name") or "").strip()
            item.level_number = _int(request.form.get("level_number"), 1)
            item.points = decimal_value(
                request.form.get("points"),
                "Action points",
                minimum="0.001",
            )
            if item.id and original_action_points and item.points < original_action_points:
                historical_points = [
                    decimal_value(
                        event.response_points if event.response_points is not None else event.points_applied,
                        "Historical response points",
                        minimum="0",
                    )
                    for event in item.events
                ]
                if any(value > item.points for value in historical_points):
                    raise BehaviorValidationError(
                        "Action Maximum cannot be lowered below points already recorded in historical responses."
                    )
            item.frequency = (request.form.get("frequency") or "ad_hoc").strip().lower()
            if item.frequency not in BehaviorAction.FREQUENCY_VALUES:
                raise BehaviorValidationError("Behavior action frequency is invalid")
            item.description = (request.form.get("description") or "").strip() or None
            item.sort_order = _int(request.form.get("sort_order"), 0)
            if response_type == "rating":
                item.rating_scale = _int(request.form.get("rating_scale"))
                if item.rating_scale not in {5, 7, 8, 10}:
                    raise BehaviorValidationError("Rating scale must be 5, 7, 8, or 10")
            else:
                item.rating_scale = None
            item.is_active = True  # legacy column; all saved actions are operational
            if not item.name:
                raise BehaviorValidationError("Action name is required")
            choice_types = {"multiple_choice", "checkboxes"}
            choice_builder_submitted = request.form.get("choices_builder") == "1"
            choice_ids_raw = request.form.getlist("choice_id")
            choice_labels = request.form.getlist("choice_label")
            choice_points = request.form.getlist("choice_points")
            choice_descriptions = request.form.getlist("choice_description")
            if len(choice_labels) != len(choice_points) or len(choice_labels) != len(choice_descriptions):
                raise BehaviorValidationError("Each choice must include a label, points, and description field")
            choice_specs = []
            seen_labels = set()
            for index, (label_raw, points_raw, description_raw) in enumerate(
                zip(choice_labels, choice_points, choice_descriptions), start=1
            ):
                label = (label_raw or "").strip()
                points_text = (points_raw or "").strip()
                description = (description_raw or "").strip() or None
                if not label and not points_text and not description:
                    continue
                if not label:
                    raise BehaviorValidationError(f"Choice {index} requires a label")
                label_key = label.casefold()
                if label_key in seen_labels:
                    raise BehaviorValidationError("Choice labels must be unique within an action")
                seen_labels.add(label_key)
                choice_specs.append(
                    {
                        "id": _int(choice_ids_raw[index - 1]) if index <= len(choice_ids_raw) and choice_ids_raw[index - 1] else None,
                        "label": label,
                        "points": decimal_value(points_text, "Choice points", minimum="0"),
                        "description": description,
                    }
                )
            if choice_builder_submitted and response_type in choice_types and not choice_specs:
                raise BehaviorValidationError("Add at least one choice for this action type")
            if choice_specs and response_type not in choice_types:
                raise BehaviorValidationError("Response options are only valid for multiple choice or checkboxes")
            choice_total = sum((spec["points"] for spec in choice_specs), Decimal("0.000"))
            if any(spec["points"] > item.points for spec in choice_specs):
                raise BehaviorValidationError("Response option points cannot exceed the action maximum")
            if response_type == "checkboxes" and choice_total > item.points:
                raise BehaviorValidationError("Combined checkbox points cannot exceed the action maximum")
            db.session.add(item)
            db.session.flush()
            existing_choices = {choice.id: choice for choice in item.choices}
            submitted_existing_ids = {
                spec["id"] for spec in choice_specs if spec["id"] is not None
            }
            if any(choice_id not in existing_choices for choice_id in submitted_existing_ids):
                raise BehaviorValidationError("One or more choices do not belong to this action")
            if item.id and item.events:
                existing_snapshot = {
                    choice.id: (choice.label, choice.points, choice.description)
                    for choice in item.choices
                }
                submitted_snapshot = {
                    spec["id"]: (spec["label"], spec["points"], spec["description"])
                    for spec in choice_specs if spec["id"] is not None
                }
                if behavior_type != original_behavior_type or submitted_snapshot != existing_snapshot or any(
                    spec["id"] is None for spec in choice_specs
                ):
                    raise BehaviorValidationError(
                        "This action's type and choices cannot be changed after it has recorded events"
                    )
            else:
                for choice in item.choices:
                    if choice.id not in submitted_existing_ids:
                        db.session.delete(choice)
                for sort_order, spec in enumerate(choice_specs, start=1):
                    choice = existing_choices.get(spec["id"])
                    if choice is None:
                        choice = BehaviorActionChoice(behavior_action_id=item.id)
                        db.session.add(choice)
                    choice.label = spec["label"]
                    choice.points = spec["points"]
                    choice.description = spec["description"]
                    choice.sort_order = sort_order
                    choice.is_active = True
                db.session.flush()
            audit(
                "Behavior Actions",
                f"Saved action {item.name} level {item.level_number} for category {category.name}",
            )
            db.session.commit()
            flash("Behavior action saved.", "success")
            return redirect(url_for("behavior.actions", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash(
                "An action with this name and level already exists in the selected category.",
                "danger",
            )
    years, selected_year, configurations, _ = _config_choices(
        config.academic_year_id if config else request.args.get("year_id"),
        config.id if config else None,
    )
    category_rows = config.categories if config else []
    return render_template(
        "admin/behavior/actions.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=config,
        category_rows=category_rows,
    )


@behavior_bp.route("/actions/<int:action_id>/delete", methods=["POST"])
def delete_action(action_id):
    item = db.session.get(BehaviorAction, action_id)
    config_id = request.form.get("config_id") or (
        item.category.configuration.id if item and item.category and item.category.configuration else None
    )
    try:
        if not item:
            raise BehaviorValidationError("Behavior action was not found")
        category = item.category
        config = category.configuration if category else None
        if not config:
            raise BehaviorValidationError("Behavior action configuration was not found")
        ensure_configuration_editable(config)
        if BehaviorEvent.query.filter_by(behavior_action_id=item.id).first():
            raise BehaviorValidationError(
                "This Behavior action cannot be deleted because it has historical events."
            )
        audit("Behavior Actions", f"Deleted action {item.id}: {item.name}")
        db.session.delete(item)
        db.session.commit()
        flash("Behavior action deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(
            str(exc) if isinstance(exc, BehaviorValidationError)
            else "This action cannot be deleted because it is still in use.",
            "danger",
        )
    return redirect(url_for("behavior.actions", config_id=config_id))


@behavior_bp.route("/subcategories", methods=["GET", "POST"])
def subcategories():
    config_id = _int(request.args.get("config_id") or request.form.get("config_id"))
    config = _selected_config(config_id, request.args.get("year_id"))
    if request.method == "POST":
        try:
            if not config:
                raise BehaviorValidationError("Select a Behavior configuration first")
            ensure_configuration_editable(config)
            category = db.session.get(BehaviorCategory, _int(request.form.get("behavior_category_id")))
            if not category or category.behavior_configuration_id != config.id:
                raise BehaviorValidationError("Category does not belong to the selected Behavior configuration")
            item = db.session.get(BehaviorSubCategory, _int(request.form.get("subcategory_id"))) if request.form.get("subcategory_id") else None
            if item and item.behavior_category_id != category.id:
                raise BehaviorValidationError("Sub-category is outside the selected category")
            name = (request.form.get("name") or "").strip()
            if not name:
                raise BehaviorValidationError("Sub-category name is required")
            item = item or BehaviorSubCategory(behavior_category_id=category.id)
            item.name = name
            item.description = (request.form.get("description") or "").strip() or None
            item.sort_order = _int(request.form.get("sort_order"), 0)
            item.is_active = True
            db.session.add(item)
            db.session.commit()
            audit("Behavior Sub-categories", f"Saved sub-category {item.name} for configuration {config.id}")
            flash("Behavior sub-category saved.", "success")
            return redirect(url_for("behavior.subcategories", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("A sub-category with this name already exists in the selected category.", "danger")
    years, selected_year, configurations, _ = _config_choices(
        config.academic_year_id if config else request.args.get("year_id"),
        config.id if config else None,
    )
    return render_template(
        "admin/behavior/subcategories.html",
        years=years,
        selected_year=selected_year,
        configurations=configurations,
        config=config,
    )


@behavior_bp.route("/subcategories/<int:subcategory_id>/delete", methods=["POST"])
def delete_subcategory(subcategory_id):
    item = db.session.get(BehaviorSubCategory, subcategory_id)
    config_id = request.form.get("config_id") or (item.category.configuration.id if item and item.category else None)
    try:
        if not item:
            raise BehaviorValidationError("Behavior sub-category was not found")
        config = item.category.configuration if item.category else None
        ensure_configuration_editable(config)
        if item.actions:
            raise BehaviorValidationError("Move or remove the sub-category actions before deleting this sub-category")
        db.session.delete(item)
        db.session.commit()
        flash("Behavior sub-category deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, BehaviorValidationError) else "This sub-category is still in use.", "danger")
    return redirect(url_for("behavior.subcategories", config_id=config_id))


@behavior_bp.route("/actions/<int:action_id>/choices", methods=["POST"])
def add_action_choice(action_id):
    action = db.session.get(BehaviorAction, action_id)
    config_id = request.form.get("config_id") or (action.category.configuration.id if action and action.category else None)
    try:
        if not action or not action.category or not action.category.configuration:
            raise BehaviorValidationError("Behavior action was not found")
        config = action.category.configuration
        ensure_configuration_editable(config)
        if action.behavior_type not in {"choice", "rating", "selection"}:
            raise BehaviorValidationError("Change the action type before adding choices")
        label = (request.form.get("label") or "").strip()
        if not label:
            raise BehaviorValidationError("Choice label is required")
        choice_points = decimal_value(request.form.get("points"), "Choice points", minimum="0")
        action_points = decimal_value(action.points, "Action points", minimum="0.001")
        if choice_points > action_points:
            raise BehaviorValidationError(
                f"Choice points cannot exceed this action maximum ({action_points:g})"
            )
        item = BehaviorActionChoice(
            behavior_action_id=action.id,
            label=label,
            points=choice_points,
            description=(request.form.get("description") or "").strip() or None,
            sort_order=_int(request.form.get("sort_order"), 0),
            is_active=True,
        )
        db.session.add(item)
        db.session.commit()
        flash("Behavior action choice saved.", "success")
    except (BehaviorValidationError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError:
        db.session.rollback()
        flash("This action choice label already exists.", "danger")
    return redirect(url_for("behavior.actions", config_id=config_id))


@behavior_bp.route("/actions/choices/<int:choice_id>/delete", methods=["POST"])
def delete_action_choice(choice_id):
    item = db.session.get(BehaviorActionChoice, choice_id)
    config_id = request.form.get("config_id") or (item.action.category.configuration.id if item and item.action and item.action.category else None)
    try:
        if not item or not item.action or not item.action.category:
            raise BehaviorValidationError("Behavior action choice was not found")
        config = item.action.category.configuration
        ensure_configuration_editable(config)
        if BehaviorEvent.query.filter_by(behavior_action_choice_id=item.id).first():
            raise BehaviorValidationError("This choice cannot be deleted because it is part of historical events")
        db.session.delete(item)
        db.session.commit()
        flash("Behavior action choice deleted.", "success")
    except (BehaviorValidationError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, BehaviorValidationError) else "This choice is still in use.", "danger")
    return redirect(url_for("behavior.actions", config_id=config_id))


@behavior_bp.route("/students", methods=["GET", "POST"])
def students():
    config_id = request.args.get("config_id") or request.form.get("config_id")
    context = _behavior_context(
        request.args.get("year_id") or request.form.get("year_id"),
        request.args.get("level_id") or request.form.get("level_id"),
        config_id,
        request.args.get("class_id") or request.form.get("class_id"),
        request.args.get("session_id") or request.form.get("behavior_session_id"),
    )
    config = context["config"]
    if request.method == "POST":
        try:
            if not config:
                raise BehaviorValidationError("Select a Behavior configuration first")
            enrollment = db.session.get(
                StudentEnrollment,
                _int(request.form.get("student_enrollment_id")),
            )
            session = db.session.get(
                BehaviorSession,
                _int(request.form.get("behavior_session_id")),
            )
            category = db.session.get(
                BehaviorCategory,
                _int(request.form.get("behavior_category_id")),
            )
            action = db.session.get(
                BehaviorAction,
                _int(request.form.get("behavior_action_id")),
            )
            choice = db.session.get(
                BehaviorActionChoice,
                _int(request.form.get("behavior_action_choice_id")),
            ) if request.form.get("behavior_action_choice_id") else None
            choice_ids = []
            for raw_choice_id in request.form.getlist("behavior_action_choice_ids"):
                choice_id = _int(raw_choice_id)
                if choice_id and choice_id not in choice_ids:
                    choice_ids.append(choice_id)
            choices = [db.session.get(BehaviorActionChoice, choice_id) for choice_id in choice_ids]
            if choice and choice not in choices:
                choices.insert(0, choice)
            if not all((enrollment, session, category, action)):
                raise BehaviorValidationError(
                    "Student, session, category, and action are required"
                )
            direction = request.form.get("direction")
            idempotency_key = normalize_idempotency_key(request.form.get("idempotency_key"))
            existing = find_event_by_idempotency_key(idempotency_key)
            record_event(
                config,
                enrollment,
                session,
                category,
                action,
                notes=request.form.get("notes"),
                occurred_at=_parse_event_datetime(request.form.get("occurred_at")),
                created_by=current_user.id,
                direction=direction,
                idempotency_key=idempotency_key,
                choice=choice,
                choices=choices,
                response_text=request.form.get("response_text"),
                rating=request.form.get("response_rating"),
            )
            if not existing:
                audit(
                    "Behavior Events",
                    f"Recorded {category.polarity} event for student {enrollment.student_id} in configuration {config.id}",
                )
            else:
                flash("Duplicate submission prevented; the original Behavior event was kept.", "info")
            db.session.commit()
            if not existing:
                flash(
                    "Behavior event recorded. Historical points were captured on the event.",
                    "success",
                )
            return redirect(url_for(
                "behavior.students",
                config_id=config.id,
                enrollment_id=enrollment.id,
                session_id=session.id,
                class_id=request.form.get("class_id") or None,
            ))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    enrollments = (
        _behavior_enrollments(
            config,
            context["selected_class"].id if context["selected_class"] else None,
            context["selected_level"].id if context["selected_level"] else None,
        )
        if not context["scope_invalid"] else []
    )
    selected_enrollment_id = _int(request.args.get("enrollment_id") or request.form.get("student_enrollment_id"))
    requested_enrollment = (
        db.session.get(StudentEnrollment, selected_enrollment_id)
        if selected_enrollment_id else None
    )
    selected_enrollment = (
        requested_enrollment
        if requested_enrollment and any(item.id == requested_enrollment.id for item in enrollments)
        else (enrollments[0] if enrollments else None)
    )
    selected_session = context["selected_session"]
    score = (
        calculate_session_score(config, selected_session, selected_enrollment)
        if selected_enrollment and selected_session and config else None
    )
    event_rows = (
        BehaviorEvent.query.filter_by(
            behavior_configuration_id=config.id,
            student_enrollment_id=selected_enrollment.id,
            behavior_session_id=selected_session.id,
        ).order_by(BehaviorEvent.occurred_at.desc(), BehaviorEvent.id.desc()).all()
        if config and selected_enrollment and selected_session and not context["scope_invalid"] else []
    )
    board_rows = (
        _student_board_rows(
            config,
            selected_session,
            context["selected_class"].id if context["selected_class"] else None,
            context["selected_level"].id if context["selected_level"] else None,
        )
        if not context["scope_invalid"] else []
    )
    try:
        session_allocation = (
            session_allocation_projection(selected_session)
            if selected_session else None
        )
        session_allocation_error = None
    except BehaviorValidationError as exc:
        session_allocation = None
        session_allocation_error = str(exc)
    return render_template(
        "admin/behavior/students.html",
        **context,
        enrollments=enrollments,
        selected_enrollment=selected_enrollment,
        current_time=datetime.utcnow(),
        idempotency_key=token_urlsafe(24),
        score=score,
        event_rows=event_rows,
        board_rows=board_rows,
        session_allocation=session_allocation,
        session_allocation_error=session_allocation_error,
    )


@behavior_bp.route("/students/<int:enrollment_id>")
def student_detail(enrollment_id):
    config = _selected_config(request.args.get("config_id"), request.args.get("year_id"))
    if not config:
        flash("Select a Behavior configuration first.", "warning")
        return redirect(url_for("behavior.students"))
    try:
        enrollment = validate_enrollment_scope(config, enrollment_id)
    except BehaviorValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("behavior.students", config_id=config.id))
    has_session_selection = request.args.get("session_id") not in (None, "")
    session_id = _int(request.args.get("session_id"))
    session = (
        next((item for item in config.sessions if item.id == session_id), None)
        if has_session_selection else None
    )
    if has_session_selection and not session:
        flash("The selected Behavior session is outside this configuration.", "danger")
        return redirect(url_for("behavior.students", config_id=config.id, enrollment_id=enrollment.id))
    session = session or next(iter(config.sessions), None)
    if not session:
        flash("No Behavior session exists for this configuration.", "warning")
        return redirect(url_for("behavior.students", config_id=config.id, enrollment_id=enrollment.id))
    try:
        validate_session_scope(
            config,
            exam_type_id=session.exam_type_id,
            exam_id=session.exam_id,
        )
    except BehaviorValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("behavior.students", config_id=config.id, enrollment_id=enrollment.id))
    score = calculate_session_score(config, session, enrollment)
    grade = (
        behavior_grade_for_score(session, score["final_score"])
        if score.get("final_score") is not None else None
    )
    events = BehaviorEvent.query.filter_by(
        behavior_configuration_id=config.id,
        student_enrollment_id=enrollment.id,
        behavior_session_id=session.id,
    ).order_by(BehaviorEvent.occurred_at.desc(), BehaviorEvent.id.desc()).all()
    return render_template(
        "admin/behavior/student_detail.html",
        config=config,
        enrollment=enrollment,
        session=session,
        score=score,
        grade=grade,
        events=events,
    )


@behavior_bp.route("/students/<int:enrollment_id>/report")
def student_report(enrollment_id):
    """Render the selected student's year-aware Behavior report for PDF/print."""
    config = _selected_config(request.args.get("config_id"), request.args.get("year_id"))
    if not config:
        flash("Select a Behavior configuration first.", "warning")
        return redirect(url_for("behavior.students"))
    try:
        enrollment = validate_enrollment_scope(config, enrollment_id)
    except BehaviorValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("behavior.students", config_id=config.id))

    session_id = _int(request.args.get("session_id"))
    session = next((item for item in config.sessions if item.id == session_id), None)
    if not session:
        flash("The selected Behavior session is outside this configuration.", "danger")
        return redirect(url_for("behavior.students", config_id=config.id))
    linked_exam = session.exam or session.exam_type
    if not linked_exam:
        flash("This Behavior session is not linked to an examination.", "danger")
        return redirect(url_for("behavior.students", config_id=config.id, session_id=session.id))

    report = next(
        (
            item for item in get_behavior_report_data(enrollment.student, linked_exam)
            if item.get("configuration_id") == config.id and item.get("session_id") == session.id
        ),
        None,
    )
    if not report:
        flash("No Behavior report is available for this student and session.", "warning")
        return redirect(url_for("behavior.students", config_id=config.id, session_id=session.id))

    events = report.get("events", [])
    category_map = {}
    for event in events:
        key = event.get("category_name") or "Behavior"
        category = category_map.setdefault(
            key,
            {"name": key, "polarity": event.get("polarity") or "positive", "events": [], "total": Decimal("0")},
        )
        category["events"].append(event)
        amount = Decimal(str(event.get("points") or 0))
        category["total"] += amount if event.get("polarity") == "positive" else -amount
    for category in category_map.values():
        category["polarity"] = "positive" if category["total"] >= 0 else "negative"
    categories = list(category_map.values())
    positive_points = sum(
        (Decimal(str(item.get("points") or 0)) for item in events if item.get("polarity") == "positive"),
        Decimal("0"),
    )
    negative_points = sum(
        (Decimal(str(item.get("points") or 0)) for item in events if item.get("polarity") == "negative"),
        Decimal("0"),
    )
    event_dates = [item.get("occurred_at") for item in events if item.get("occurred_at")]
    report.update(
        {
            "categories": categories,
            "positive_points": positive_points,
            "negative_points": negative_points,
            "net_points": positive_points - negative_points,
            "period_from": min(event_dates) if event_dates else None,
            "period_to": max(event_dates) if event_dates else None,
            "generated_on": datetime.utcnow(),
        }
    )
    report_ledger = report.get("ledger") or {}
    behavior_ledger = report_ledger.get("behavior") or {}
    session_ledger = report_ledger.get("session") or {}
    strengths = [
        item.get("action_name") or item.get("category_name")
        for item in events
        if item.get("polarity") == "positive"
    ][:5]
    improvements = [
        item.get("action_name") or item.get("category_name")
        for item in events
        if item.get("polarity") == "negative"
    ][:5]
    return render_template(
        "admin/behavior/student_report.html",
        settings=get_settings(),
        student=enrollment.student,
        enrollment=enrollment,
        config=config,
        session=session,
        report=report,
        behavior_ledger=behavior_ledger,
        session_ledger=session_ledger,
        strengths=strengths,
        improvements=improvements,
        print_mode=request.args.get("print") == "1",
        portal_read_only=request.args.get("portal_read_only") == "1",
        portal_back_url=request.args.get("portal_back_url"),
        portal_download_url=request.args.get("portal_download_url"),
    )


@behavior_bp.route("/events")
def events():
    return render_template("admin/behavior/events.html", **_event_page_data(request.args))


def _parse_event_datetime(value, fallback=None):
    value = (value or "").strip()
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise BehaviorValidationError("Event date and time is invalid") from exc


@behavior_bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
def edit(event_id):
    event = db.session.get(BehaviorEvent, event_id)
    if not event:
        flash("Behavior event was not found", "danger")
        return redirect(url_for("behavior.events"))
    config = validate_behavior_configuration(event.configuration)
    if request.method == "POST":
        try:
            session = db.session.get(BehaviorSession, _int(request.form.get("behavior_session_id")))
            category = db.session.get(BehaviorCategory, _int(request.form.get("behavior_category_id")))
            action = db.session.get(BehaviorAction, _int(request.form.get("behavior_action_id")))
            choice = db.session.get(
                BehaviorActionChoice,
                _int(request.form.get("behavior_action_choice_id")),
            ) if request.form.get("behavior_action_choice_id") else None
            choice_ids = []
            for raw_choice_id in request.form.getlist("behavior_action_choice_ids"):
                choice_id = _int(raw_choice_id)
                if choice_id and choice_id not in choice_ids:
                    choice_ids.append(choice_id)
            choices = [db.session.get(BehaviorActionChoice, choice_id) for choice_id in choice_ids]
            if choice and choice not in choices:
                choices.insert(0, choice)
            if not choices and not choice and action and action.id == event.behavior_action_id and (event.response_snapshot or event.behavior_action_choice_id):
                try:
                    snapshot = json.loads(event.response_snapshot or "{}")
                    saved_ids = [int(item["id"]) for item in snapshot.get("selected_options", []) if item.get("id")]
                    if not saved_ids and event.behavior_action_choice_id:
                        saved_ids = [event.behavior_action_choice_id]
                    choices = [db.session.get(BehaviorActionChoice, choice_id) for choice_id in saved_ids]
                    choices = [item for item in choices if item is not None]
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    choices = []
            if not all((session, category, action)):
                raise BehaviorValidationError("Session, category, and action are required")
            response_text = request.form.get("response_text") if "response_text" in request.form else event.response_text
            response_rating = request.form.get("response_rating") if "response_rating" in request.form else event.response_rating
            _, old_values, new_values = edit_event(
                event,
                config,
                event.student_enrollment,
                session,
                category,
                action,
                direction=request.form.get("direction"),
                occurred_at=_parse_event_datetime(request.form.get("occurred_at"), event.occurred_at),
                notes=request.form.get("notes"),
                reason=request.form.get("reason"),
                choice=choice,
                choices=choices,
                response_text=response_text,
                rating=response_rating,
            )
            audit(
                "Behavior Events",
                f"Edited Behavior event {event.id}; old={json.dumps(old_values, default=str, sort_keys=True)}; new={json.dumps(new_values, default=str, sort_keys=True)}",
            )
            db.session.commit()
            flash("Behavior event updated and the old/new values were audited.", "success")
            return redirect(url_for("behavior.events", config_id=config.id))
        except (BehaviorValidationError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except IntegrityError:
            db.session.rollback()
            flash("The Behavior event could not be updated because of a data conflict.", "danger")
    return render_template(
        "admin/behavior/edit.html",
        event=event,
        config=config,
        sessions=list(config.sessions),
        categories=list(config.categories),
        actions=[item for category in config.categories for item in category.actions],
    )


@behavior_bp.route("/events/<int:event_id>")
def event_detail(event_id):
    event = db.session.get(BehaviorEvent, event_id)
    if not event:
        flash("Behavior event was not found.", "danger")
        return redirect(url_for("behavior.events"))
    configuration = validate_behavior_configuration(event.configuration)
    return render_template(
        "admin/behavior/event_detail.html",
        event=event,
        config=configuration,
        selected_year=configuration.academic_year,
        selected_level=configuration.academic_year_level,
    )


@behavior_bp.route("/events/<int:event_id>/void", methods=["POST"])
def void(event_id):
    event = db.session.get(BehaviorEvent, event_id)
    try:
        if not event:
            raise BehaviorValidationError("Behavior event was not found")
        void_event(event, current_user.id, request.form.get("reason"))
        audit("Behavior Events", f"Voided Behavior event {event.id}")
        db.session.commit()
        flash("Behavior event voided and retained in history.", "success")
    except BehaviorValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    config_id = request.form.get("config_id") or (event.behavior_configuration_id if event else None)
    return redirect(url_for("behavior.events", config_id=config_id))


@behavior_bp.route("/events/<int:event_id>/restore", methods=["POST"])
def restore(event_id):
    event = db.session.get(BehaviorEvent, event_id)
    try:
        if not event:
            raise BehaviorValidationError("Behavior event was not found")
        restore_event(event)
        audit("Behavior Events", f"Restored Behavior event {event.id}")
        db.session.commit()
        flash("Behavior event restored and included in the current score.", "success")
    except BehaviorValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    config_id = request.form.get("config_id") or (event.behavior_configuration_id if event else None)
    return redirect(url_for("behavior.events", config_id=config_id))


@behavior_bp.route("/history")
def history():
    return render_template("admin/behavior/history.html", **_event_page_data(request.args))


@behavior_bp.route("/audit")
def audit_history():
    from .models import AuditLog

    rows = AuditLog.query.filter(
        AuditLog.action.like("Behavior%")
    ).order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template(
        "admin/behavior/audit.html",
        rows=rows,
        config=None,
        selected_year=None,
        selected_level=None,
    )


@behavior_bp.route("/attendance", methods=["GET", "POST"])
def attendance():
    """Daily Behavior attendance, deliberately separate from exam-hall attendance."""
    context = _behavior_context(
        request.args.get("year_id") or request.form.get("year_id"),
        request.args.get("level_id") or request.form.get("level_id"),
        request.args.get("config_id") or request.form.get("config_id"),
        request.args.get("class_id") or request.form.get("class_id"),
        request.args.get("session_id") or request.form.get("session_id"),
    )
    config = context["config"]
    selected_session = context["selected_session"]
    attendance_view = request.args.get("attendance_view", "")
    raw_date = request.args.get("attendance_date") or request.form.get("attendance_date")
    try:
        attendance_date = date.fromisoformat(raw_date) if raw_date else date.today()
    except ValueError:
        attendance_date = date.today()
        flash("Attendance date was invalid; today's date was selected.", "warning")
    raw_time = request.args.get("attendance_time") or request.form.get("attendance_time")
    try:
        attendance_time = _parse_time(raw_time) if raw_time else datetime.now().time().replace(second=0, microsecond=0)
    except ValueError:
        attendance_time = datetime.now().time().replace(second=0, microsecond=0)
        flash("Attendance time was invalid; the current time was selected.", "warning")

    if config:
        try:
            before_statuses = BehaviorAttendanceStatus.query.filter_by(
                behavior_configuration_id=config.id
            ).count()
            selected_level_id = context["selected_level"].id
            before_days = AcademicYearLevelAttendanceDay.query.filter_by(
                academic_year_level_id=selected_level_id
            ).count()
            ensure_attendance_defaults(config, selected_level_id)
            after_statuses = BehaviorAttendanceStatus.query.filter_by(
                behavior_configuration_id=config.id
            ).count()
            after_days = AcademicYearLevelAttendanceDay.query.filter_by(
                academic_year_level_id=selected_level_id
            ).count()
            if after_statuses != before_statuses or after_days != before_days:
                db.session.commit()
            db.session.expire(config, ["attendance_statuses", "attendance_days"])
        except BehaviorValidationError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    if request.method == "POST":
        try:
            if not config or context["scope_invalid"]:
                raise BehaviorValidationError("Select a valid year-aware Behavior scope first")
            if not selected_session:
                raise BehaviorValidationError("Select an Exam Type session first")
            action = request.form.get("action")
            if action == "generate":
                created = generate_daily_roster(
                    config,
                    selected_session,
                    attendance_date,
                    context["selected_class"].id if context["selected_class"] else None,
                    attendance_time=attendance_time,
                    academic_year_level_id=context["selected_level"].id,
                )
                audit("Behavior Attendance", f"Generated {created} attendance rows for configuration {config.id}")
                db.session.commit()
                flash(f"{created} missing Present attendance row(s) generated.", "success")
            elif action == "save_all":
                if attendance_date.weekday() not in {
                    item.weekday
                    for item in attendance_days(config, academic_year_level_id=context["selected_level"].id)
                }:
                    raise BehaviorValidationError("The selected date is not configured as a school attendance day")
                enrollments = enrollments_for_class(
                    config,
                    context["selected_class"].id if context["selected_class"] else None,
                    context["selected_level"].id,
                )
                existing_voided_ids = {
                    item.student_enrollment_id
                    for item in BehaviorAttendanceRecord.query.filter(
                        BehaviorAttendanceRecord.behavior_configuration_id == config.id,
                        BehaviorAttendanceRecord.behavior_session_id == selected_session.id,
                        BehaviorAttendanceRecord.attendance_date == attendance_date,
                        BehaviorAttendanceRecord.status == "voided",
                        BehaviorAttendanceRecord.student_enrollment_id.in_([item.id for item in enrollments]),
                    ).all()
                }
                saved_count = 0
                for enrollment in enrollments:
                    # A VOIDED row is an immutable audit record.  Do not let a
                    # roster-wide save silently reactivate or edit it.
                    if enrollment.id in existing_voided_ids:
                        continue
                    status_id = _int(request.form.get(f"status_{enrollment.id}"))
                    arrival_time = _parse_time(request.form.get(f"arrival_time_{enrollment.id}"))
                    school_start_time = getattr(config, "school_start_time", None) or current_app.config.get("SCHOOL_START_TIME")
                    mark_attendance(
                        config,
                        selected_session,
                        enrollment,
                        status_id,
                        attendance_date,
                        note=request.form.get(f"note_{enrollment.id}"),
                        marked_by_id=current_user.id,
                        attendance_time=attendance_time,
                        arrival_time=arrival_time,
                        late_by_minutes=_late_by_minutes(arrival_time, school_start_time),
                    )
                    saved_count += 1
                audit("Behavior Attendance", f"Saved attendance for {saved_count} enrollment(s) in configuration {config.id}")
                db.session.commit()
                if existing_voided_ids:
                    flash(
                        f"Attendance saved for {saved_count} student(s). "
                        f"{len(existing_voided_ids)} voided record(s) remained unchanged.",
                        "success",
                    )
                else:
                    flash(f"Attendance saved for {saved_count} student(s).", "success")
            elif action == "save_status":
                key = (request.form.get("key") or "").strip().lower().replace(" ", "_")
                label = (request.form.get("label") or "").strip()
                polarity = (request.form.get("polarity") or "neutral").strip().lower()
                if not key or not label or polarity not in {"positive", "negative", "neutral"}:
                    raise BehaviorValidationError("Status key, label, and polarity are required")
                status = BehaviorAttendanceStatus(
                    behavior_configuration_id=config.id,
                    key=key,
                    label=label,
                    polarity=polarity,
                    points=decimal_value(request.form.get("points"), "Attendance points", minimum="0"),
                    contributes_to_behavior=True,
                    sort_order=len(config.attendance_statuses) + 1,
                    is_active=True,
                )
                db.session.add(status)
                db.session.flush()
                audit("Behavior Attendance", f"Added attendance status {status.key} to configuration {config.id}")
                db.session.commit()
                flash("Attendance status saved.", "success")
            elif action == "save_days":
                active_days = {_int(value) for value in request.form.getlist("school_days")}
                before_days, saved_days = apply_attendance_active_days(
                    config,
                    active_days,
                    context["selected_level"].id,
                )
                audit(
                    "Behavior Attendance Calendar",
                    f"Updated active days for Academic Year Level {context['selected_level'].id} "
                    f"(configuration {config.id}): {sorted(before_days)} -> {sorted(saved_days)}",
                )
                db.session.commit()
                flash("Attendance active days saved for this Academic Year Level.", "success")
            else:
                raise BehaviorValidationError("Unknown attendance action")
            return redirect(url_for(
                "behavior.attendance",
                config_id=config.id,
                level_id=context["selected_level"].id,
                class_id=context["selected_class"].id if context["selected_class"] else None,
                session_id=selected_session.id,
                attendance_date=attendance_date.isoformat(),
                attendance_time=attendance_time.strftime("%H:%M"),
            ))
        except (BehaviorValidationError, ValueError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Attendance could not be saved because it conflicts with an existing configuration.", "danger")
            else:
                flash(str(exc), "danger")

    statuses = attendance_statuses(config) if config else []
    # The settings drawer exposes only selectable statuses.  Availability is
    # automatic now; retired/inactive historical rows remain in the database
    # for audit and are not presented as editable settings.
    all_statuses = attendance_statuses(config) if config else []
    all_days = (
        attendance_days(config, active_only=False, academic_year_level_id=context["selected_level"].id)
        if config and context["selected_level"] else []
    )
    school_day = bool(config and attendance_date.weekday() in {item.weekday for item in all_days if item.is_active})
    enrollments = (
        enrollments_for_class(
            config,
            context["selected_class"].id if context["selected_class"] else None,
            context["selected_level"].id,
        )
        if config and selected_session and not context["scope_invalid"] else []
    )
    records = {}
    if config and selected_session and enrollments:
        records = {
            item.student_enrollment_id: item
            for item in BehaviorAttendanceRecord.query.filter(
                BehaviorAttendanceRecord.behavior_configuration_id == config.id,
                BehaviorAttendanceRecord.behavior_session_id == selected_session.id,
                BehaviorAttendanceRecord.attendance_date == attendance_date,
                BehaviorAttendanceRecord.student_enrollment_id.in_([item.id for item in enrollments]),
            ).all()
        }
    rows = [{"enrollment": enrollment, "record": records.get(enrollment.id)} for enrollment in enrollments]
    # The roster, contextual profile, records drawer, and class overview all
    # read this same scoped record set. No parallel attendance data is created.
    history_records = []
    profiles = {}
    record_rows = []
    overview_counts = defaultdict(int)
    history_by_enrollment = defaultdict(list)
    if config and selected_session and enrollments:
        enrollment_ids = [item.id for item in enrollments]
        history_records = BehaviorAttendanceRecord.query.filter(
            BehaviorAttendanceRecord.behavior_configuration_id == config.id,
            BehaviorAttendanceRecord.behavior_session_id == selected_session.id,
            BehaviorAttendanceRecord.student_enrollment_id.in_(enrollment_ids),
        ).order_by(BehaviorAttendanceRecord.attendance_date.desc(), BehaviorAttendanceRecord.id.desc()).all()
        for item in history_records:
            key = (item.status_key_snapshot or "").strip().lower() or "unknown"
            history_by_enrollment[item.student_enrollment_id].append(item)
            record_rows.append({
                "date": item.attendance_date.isoformat(),
                "date_display": item.attendance_date.strftime("%B %d, %Y"),
                "student": item.student.full_name,
                "mother": item.student.mother_name or "-",
                "student_code": item.student.student_code,
                "class_name": item.academic_year_class.name if item.academic_year_class else "-",
                "photo_path": item.student.photo_path or "",
                "photo_url": (
                    item.student.photo_path
                    if (item.student.photo_path or "").startswith(("http://", "https://", "data:"))
                    else url_for("static", filename=item.student.photo_path)
                    if item.student.photo_path
                    else ""
                ),
                "status": attendance_status_label(key, item.status_label_snapshot),
                "status_key": key,
                "record_id": item.id,
                "record_status": item.status or "active",
                "void_reason": item.void_reason or "",
                "voided_by": item.voider.username if item.voider else "",
                "voided_at": item.voided_at.isoformat() if item.voided_at else "",
                "edit_url": url_for(
                    "behavior.attendance",
                    config_id=config.id,
                    session_id=selected_session.id,
                    class_id=item.academic_year_class_id,
                    attendance_date=item.attendance_date.isoformat(),
                ),
                "void_url": url_for("behavior.void_attendance", record_id=item.id),
                "restore_url": url_for("behavior.restore_attendance", record_id=item.id),
                "delete_url": url_for("behavior.delete_attendance", record_id=item.id),
                "arrival_time": item.arrival_time.strftime("%I:%M %p").lstrip("0") if item.arrival_time else "",
                "attendance_time": item.attendance_time.strftime("%I:%M %p").lstrip("0") if item.attendance_time else "",
                "late_by_minutes": item.late_by_minutes,
                "points": str(item.points_applied or 0),
                "polarity": item.polarity,
                "note": item.note or "",
            })
        deleted_records = BehaviorAttendanceDeletion.query.filter(
            BehaviorAttendanceDeletion.behavior_configuration_id == config.id,
            BehaviorAttendanceDeletion.behavior_session_id == selected_session.id,
            BehaviorAttendanceDeletion.student_enrollment_id.in_(enrollment_ids),
        ).order_by(
            BehaviorAttendanceDeletion.attendance_date.desc(),
            BehaviorAttendanceDeletion.deleted_at.desc(),
        ).all()
        for item in deleted_records:
            record_rows.append({
                "date": item.attendance_date.isoformat(),
                "date_display": item.attendance_date.strftime("%B %d, %Y"),
                "student": item.student_name,
                "mother": item.mother_name or "-",
                "student_code": item.student_code,
                "class_name": item.class_name or "-",
                "photo_path": "",
                "photo_url": "",
                "status": item.status_label,
                "status_key": item.status_key,
                "record_id": item.original_record_id,
                "record_status": "deleted",
                "void_reason": item.void_reason or "",
                "deletion_reason": item.deletion_reason,
                "deleted_by": item.deleted_by_username,
                "deleted_at": item.deleted_at.isoformat() if item.deleted_at else "",
                "arrival_time": item.arrival_time.strftime("%I:%M %p").lstrip("0") if item.arrival_time else "",
                "attendance_time": item.attendance_time.strftime("%I:%M %p").lstrip("0") if item.attendance_time else "",
                "late_by_minutes": item.late_by_minutes,
                "points": str(item.points_applied or 0),
                "polarity": item.polarity,
                "note": item.note or "",
            })
        for item in records.values():
            if (item.status or "active") != "active":
                continue
            key = (item.status_key_snapshot or "").strip().lower() or "unknown"
            overview_counts[key] += 1
        for enrollment in enrollments:
            scoped_records = history_by_enrollment.get(enrollment.id, [])
            active_scoped_records = [
                item for item in scoped_records if (item.status or "active") == "active"
            ]
            counts = defaultdict(int)
            for item in active_scoped_records:
                key = (item.status_key_snapshot or "").strip().lower() or "unknown"
                counts[key] += 1
            total = len(active_scoped_records)
            attended = counts["present"] + counts["late"]
            profile_points = attendance_points_projection(scoped_records)
            canonical_score = calculate_session_score(config, selected_session, enrollment)
            profiles[enrollment.id] = {
                "name": enrollment.student.full_name,
                "mother": enrollment.student.mother_name or "-",
                "student_code": enrollment.student.student_code,
                "class_name": enrollment.academic_year_class.name,
                "year_name": config.academic_year.name,
                "level_name": config.academic_year_level.name,
                "present": counts["present"],
                "late": counts["late"],
                "absent": counts["absent"],
                "excused": counts["excused"],
                "official_leave": counts["official_leave"],
                "total": total,
                "percentage": round((attended / total) * 100, 1) if total else 0,
                "points": str(profile_points["signed_total"]),
                # The profile keeps the raw status counts for operational
                # review, but final score fields come from the central
                # Attendance scoring service.
                "attendance_score": str(canonical_score.get("attendance_score") or "") if canonical_score.get("attendance_score") is not None else "-",
                "attendance_allocation": str(canonical_score.get("attendance_allocation") or "") if canonical_score.get("attendance_allocation") is not None else "-",
                "attendance_scoring_status": canonical_score.get("attendance_status", "-"),
                "attendance_scoring_reason": canonical_score.get("attendance_reason") or "",
                "history": [
                    {
                        "date": item.attendance_date.isoformat(),
                        "date_display": item.attendance_date.strftime("%B %d, %Y"),
                        "status": attendance_status_label(
                            (item.status_key_snapshot or "").strip().lower(),
                            item.status_label_snapshot,
                        ),
                        "record_status": item.status or "active",
                        "void_reason": item.void_reason or "",
                        "arrival_time": item.arrival_time.strftime("%I:%M %p").lstrip("0") if item.arrival_time else "",
                        "late_by_minutes": item.late_by_minutes,
                        "note": item.note or "-",
                    }
                    for item in scoped_records[:12]
                ],
            }
    repeat_alerts = []
    for enrollment in enrollments:
        scoped_records = [
            item for item in history_by_enrollment.get(enrollment.id, [])
            if (item.status or "active") == "active"
        ]
        absent_count = sum(1 for item in scoped_records if (item.status_key_snapshot or "").lower() == "absent")
        late_count = sum(1 for item in scoped_records if (item.status_key_snapshot or "").lower() == "late")
        if absent_count >= 2 or late_count >= 2:
            repeat_alerts.append({
                "name": enrollment.student.full_name,
                "absent": absent_count,
                "late": late_count,
            })
    current_total = sum(overview_counts.values())
    current_attended = overview_counts["present"] + overview_counts["late"]
    current_points = attendance_points_projection(list(records.values()))
    overview = {
        "total": len(enrollments),
        "present": overview_counts["present"],
        "late": overview_counts["late"],
        "absent": overview_counts["absent"],
        "excused": overview_counts["excused"],
        "official_leave": overview_counts["official_leave"],
        "percentage": round((current_attended / current_total) * 100, 1) if current_total else 0,
        "impact_points": str(current_points["signed_total"]),
        "repeat_alerts": repeat_alerts,
    }
    return render_template(
        "admin/behavior/attendance.html",
        **context,
        attendance_date=attendance_date,
        attendance_time=attendance_time,
        statuses=statuses,
        all_statuses=all_statuses,
        all_days=all_days,
        school_day=school_day,
        rows=rows,
        profiles=profiles,
        record_rows=record_rows,
        overview=overview,
        official_status_labels={item.key: item.label for item in statuses},
        attendance_view=attendance_view,
        attendance_view_target=attendance_view or "records",
    )


@behavior_bp.route("/attendance/records/<int:record_id>/void", methods=["POST"])
def void_attendance(record_id):
    """Void one Attendance row while retaining its complete audit history."""
    record = db.session.get(BehaviorAttendanceRecord, record_id)
    try:
        if not record:
            raise BehaviorValidationError("Attendance record was not found")
        void_attendance_record(record, current_user.id, request.form.get("reason"))
        audit(
            "Behavior Attendance",
            f"Voided Attendance record {record.id}; reason={record.void_reason!r}",
        )
        db.session.commit()
        flash("Attendance record voided and retained in history.", "success")
    except BehaviorValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError:
        db.session.rollback()
        flash("The Attendance record could not be voided because of a data conflict.", "danger")
    return redirect(url_for(
        "behavior.attendance",
        config_id=record.behavior_configuration_id if record else request.form.get("config_id"),
        level_id=(record.student_enrollment.academic_year_level_id if record and record.student_enrollment else request.form.get("level_id")),
        session_id=record.behavior_session_id if record else request.form.get("session_id"),
        attendance_date=record.attendance_date.isoformat() if record else request.form.get("attendance_date"),
        attendance_view="records",
    ))


@behavior_bp.route("/attendance/records/<int:record_id>/restore", methods=["POST"])
def restore_attendance(record_id):
    """Restore a voided Attendance row only through an explicit action."""
    record = db.session.get(BehaviorAttendanceRecord, record_id)
    try:
        if not record:
            raise BehaviorValidationError("Attendance record was not found")
        restore_attendance_record(record)
        audit("Behavior Attendance", f"Restored Attendance record {record.id}")
        db.session.commit()
        flash("Attendance record restored and included in current scoring.", "success")
    except BehaviorValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError:
        db.session.rollback()
        flash("The Attendance record could not be restored because of a data conflict.", "danger")
    return redirect(url_for(
        "behavior.attendance",
        config_id=record.behavior_configuration_id if record else request.form.get("config_id"),
        level_id=(record.student_enrollment.academic_year_level_id if record and record.student_enrollment else request.form.get("level_id")),
        session_id=record.behavior_session_id if record else request.form.get("session_id"),
        attendance_date=record.attendance_date.isoformat() if record else request.form.get("attendance_date"),
        attendance_view="records",
    ))


@behavior_bp.route("/attendance/records/<int:record_id>/delete", methods=["POST"])
def delete_attendance(record_id):
    """Hard-delete an Attendance row after explicit confirmation.

    A separate deletion snapshot is retained for read-only Deleted filtering;
    it is never returned to scoring, portal, history, or reports.
    """
    record = db.session.get(BehaviorAttendanceRecord, record_id)
    config_id = record.behavior_configuration_id if record else request.form.get("config_id")
    session_id = record.behavior_session_id if record else request.form.get("session_id")
    attendance_date = record.attendance_date.isoformat() if record else request.form.get("attendance_date")
    try:
        if not record:
            raise BehaviorValidationError("Attendance record was not found")
        if request.form.get("confirmation", "").strip() != "DELETE ATTENDANCE RECORD":
            raise BehaviorValidationError("Type DELETE ATTENDANCE RECORD to confirm permanent deletion")
        deletion = delete_attendance_record(record, current_user, request.form.get("reason"))
        audit(
            "Behavior Attendance Deleted",
            f"Hard-deleted Attendance record {deletion.original_record_id}; "
            f"student={deletion.student_code}; reason={deletion.deletion_reason!r}",
        )
        db.session.commit()
        flash("Attendance record permanently deleted. A read-only deletion snapshot was retained.", "success")
    except BehaviorValidationError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except IntegrityError:
        db.session.rollback()
        flash("The Attendance record could not be permanently deleted; no partial change was committed.", "danger")
    return redirect(url_for(
        "behavior.attendance",
        config_id=config_id,
        level_id=(record.student_enrollment.academic_year_level_id if record and record.student_enrollment else request.form.get("level_id")),
        session_id=session_id,
        attendance_date=attendance_date,
        attendance_view="records",
    ))


@behavior_bp.route("/attendance/students/<int:enrollment_id>/report")
def attendance_report(enrollment_id):
    """Render one A4 Attendance page for every month in the selected session."""
    context = _behavior_context(
        request.args.get("year_id"),
        request.args.get("level_id"),
        request.args.get("config_id"),
        request.args.get("class_id"),
        request.args.get("session_id"),
    )
    config = context["config"]
    selected_session = context["selected_session"]
    if not config or not selected_session or context["scope_invalid"]:
        flash("Select a valid Attendance year, configuration, and session first.", "warning")
        return redirect(url_for("behavior.attendance"))
    try:
        enrollment = validate_enrollment_scope(config, enrollment_id)
    except BehaviorValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("behavior.attendance", config_id=config.id, session_id=selected_session.id))

    raw_date = request.args.get("attendance_date")
    try:
        report_date = date.fromisoformat(raw_date) if raw_date else date.today()
    except ValueError:
        report_date = date.today()

    all_session_records = BehaviorAttendanceRecord.query.filter(
        BehaviorAttendanceRecord.student_enrollment_id == enrollment.id,
        BehaviorAttendanceRecord.behavior_configuration_id == config.id,
        BehaviorAttendanceRecord.behavior_session_id == selected_session.id,
        BehaviorAttendanceRecord.status == "active",
    ).order_by(BehaviorAttendanceRecord.attendance_date).all()
    try:
        canonical_score = calculate_session_score(config, selected_session, enrollment)
    except BehaviorValidationError:
        # Keep historical reports printable while preserving the raw detail
        # rows for an administrator to repair the invalid configuration.
        canonical_score = {}
    linked_exam = selected_session.exam or selected_session.exam_type
    behavior_report = next(
        (
            item for item in get_behavior_report_data(enrollment.student, linked_exam)
            if item.get("configuration_id") == config.id and item.get("session_id") == selected_session.id
        ),
        None,
    ) if linked_exam else None
    behavior_grade_data = (behavior_report.get("grade") or {}) if behavior_report else {}
    behavior_grade = behavior_grade_data.get("grade") or "N/A"

    active_weekdays = {
        item.weekday
        for item in attendance_days(
            config,
            academic_year_level_id=context["selected_level"].id if context["selected_level"] else None,
        )
        if item.is_active
    }
    status_keys = {"present", "late", "absent", "excused", "official_leave"}
    status_polarities = {
        item.key: item.polarity
        for item in attendance_statuses(config, active_only=False)
        if item.key in status_keys
    }
    somali_weekdays = {0: "Isniin", 1: "Talaada", 2: "Arbaca", 3: "Khamiis", 4: "Jumca", 5: "Sabti", 6: "Axad"}
    aliases = {
        "joogid": "present",
        "daahid": "late",
        "maqnaansho": "absent",
        "cudurdaar": "excused",
        "fasaxid_rasmi_ah": "official_leave",
        "officialleave": "official_leave",
    }

    def report_status_key(value, label=None):
        first_normalized = ""
        for candidate in (value, label):
            normalized = (candidate or "").strip().lower().replace("-", "_").replace(" ", "_")
            if not first_normalized:
                first_normalized = normalized
            resolved = aliases.get(normalized, normalized)
            if resolved in status_keys:
                return resolved
        return first_normalized

    def format_points(value, signed=False):
        if value is None:
            return "-"
        value = Decimal(str(value)).quantize(Decimal("0.01"))
        text = f"{abs(value):.2f}" if signed else f"{value:.2f}"
        if signed and value > 0:
            return f"+{text}"
        if signed and value < 0:
            return f"-{text}"
        return text

    attendance_ledger = (canonical_score.get("ledger") or {}).get("attendance") or {}
    normalized_attendance = attendance_ledger.get("allocation") is not None
    attendance_status = attendance_ledger.get("status") or "LEGACY_COMPATIBILITY"
    attendance_incomplete = attendance_status == "INCOMPLETE"
    session_projection = attendance_points_projection(all_session_records)
    session_earned_value = attendance_ledger.get("earned_score")
    session_allocation_value = attendance_ledger.get("allocation")
    if not normalized_attendance:
        session_earned_value = session_projection["signed_total"]
    session_grand_display = (
        "INCOMPLETE" if attendance_incomplete else
        f"{format_points(session_earned_value)} / {format_points(session_allocation_value)}"
        if normalized_attendance else format_points(session_earned_value, signed=True)
    )

    def build_month(report_date):
        month_start = report_date.replace(day=1)
        month_end = report_date.replace(day=monthrange(report_date.year, report_date.month)[1])
        records = [item for item in all_session_records if month_start <= item.attendance_date <= month_end]
        record_by_date = {item.attendance_date: item for item in records}
        counts = {key: 0 for key in status_keys}
        for record in records:
            key = report_status_key(record.status_key_snapshot, record.status_label_snapshot)
            if key not in status_keys:
                key = "excused" if record.polarity == "neutral" else ("absent" if record.polarity == "negative" else "present")
            counts[key] += 1
        total_school_days = sum(
            1 for day_number in range(1, month_end.day + 1)
            if month_start.replace(day=day_number).weekday() in active_weekdays
        )
        attended_days = counts["present"] + counts["late"]
        attendance_percentage = round((attended_days / total_school_days) * 100, 1) if total_school_days else 0.0
        overall_label = "EXCELLENT" if attendance_percentage >= 90 else "GOOD" if attendance_percentage >= 75 else "NEEDS SUPPORT"
        donut_denominator = total_school_days or 1
        angles = {
            "donut_present_angle": counts["present"] / donut_denominator * 360,
            "donut_late_angle": counts["late"] / donut_denominator * 360,
            "donut_absent_angle": counts["absent"] / donut_denominator * 360,
            "donut_excused_angle": counts["excused"] / donut_denominator * 360,
            "donut_official_leave_angle": counts["official_leave"] / donut_denominator * 360,
        }

        def calendar_cell(day_value):
            if day_value is None:
                return {"pad": True, "day": ""}
            current = month_start.replace(day=day_value)
            record = record_by_date.get(current)
            key = report_status_key(record.status_key_snapshot, record.status_label_snapshot) if record else ""
            if key not in status_keys and record:
                key = "excused" if record.polarity == "neutral" else ("absent" if record.polarity == "negative" else "present")
            non_school_day = current.weekday() not in active_weekdays
            return {"pad": False, "day": day_value, "holiday": non_school_day, "active": not non_school_day, "key": key, "marker": "N" if non_school_day else ("x" if key == "absent" else "." if key == "late" else "-" if key == "excused" else "check" if key == "present" else "")}

        calendar_weeks, week = [], []
        order = [5, 6, 0, 1, 2, 3, 4]
        for _ in range(order.index(month_start.weekday())):
            week.append(calendar_cell(None))
        for day_number in range(1, month_end.day + 1):
            week.append(calendar_cell(day_number))
            if len(week) == 7:
                calendar_weeks.append(week)
                week = []
        if week:
            while len(week) < 7:
                week.append(calendar_cell(None))
            calendar_weeks.append(week)

        absence_rows = []
        monthly_points = attendance_points_projection(records)
        for record in records:
            key = report_status_key(record.status_key_snapshot, record.status_label_snapshot)
            if key not in {"late", "absent", "excused", "official_leave"}:
                key = "present" if record.polarity == "positive" else ("excused" if record.polarity == "neutral" else "absent")
            label = attendance_status_label(key, record.status_label_snapshot)
            saved_attendance_time = record.attendance_time or (record.created_at.time() if record.created_at else None)
            display_time = saved_attendance_time if key == "present" else record.arrival_time
            raw_points = Decimal(str(record.points_applied or 0))
            polarity = (record.polarity or status_polarities.get(key, "neutral")).strip().lower()
            if polarity not in {"positive", "negative", "neutral"}:
                polarity = status_polarities.get(key, "neutral")
            signed_points = -abs(raw_points) if polarity == "negative" else abs(raw_points)
            point_text = format_points(signed_points, signed=True) if signed_points else "0.00"
            tag_label = label
            if key == "late" and record.late_by_minutes is not None:
                tag_label = f"{label} ({record.late_by_minutes} daqiiqo)"
            absence_rows.append({"date": record.attendance_date, "day": somali_weekdays[record.attendance_date.weekday()], "key": key, "label": label, "tag_label": tag_label, "time": display_time.strftime("%I:%M %p").lstrip("0") if display_time else "-", "points": point_text, "note": record.note or "-"})

        weekly_max = max(len(active_weekdays), 1)
        trend = []
        for week_number in range(5):
            start_day = week_number * 7 + 1
            end_day = min(start_day + 6, month_end.day)
            values = {key: 0 for key in status_keys}
            for record in records:
                if start_day <= record.attendance_date.day <= end_day:
                    key = report_status_key(record.status_key_snapshot, record.status_label_snapshot)
                    if key not in status_keys:
                        key = "excused" if record.polarity == "neutral" else ("absent" if record.polarity == "negative" else "present")
                    values[key] += 1
            total = sum(values.values())
            trend.append({"total": total, "present": values["present"], "late": values["late"], "absent": values["absent"], "excused": values["excused"], "official_leave": values["official_leave"], "label": f"Usbuuca {week_number + 1}aad", "range": f"{start_day}-{end_day} {report_date.strftime('%b')}"})

        return {
            "report_date": report_date,
            "month_start": month_start,
            "month_end": month_end,
            "records": records,
            "calendar_weeks": calendar_weeks,
            "counts": SimpleNamespace(**counts),
            "total_school_days": total_school_days,
            "attended_days": attended_days,
            "attendance_percentage": attendance_percentage,
            "overall_label": overall_label,
            **angles,
            "absence_rows": absence_rows,
            "total_positive_points": "INCOMPLETE" if attendance_incomplete else format_points(monthly_points["positive_points"], signed=True),
            "total_negative_points": "INCOMPLETE" if attendance_incomplete else format_points(-monthly_points["negative_points"], signed=True),
            "grand_total_points": session_grand_display,
            "attendance_positive_max": format_points(attendance_ledger.get("positive_capacity")) if normalized_attendance else "-",
            "attendance_negative_max": format_points(attendance_ledger.get("negative_capacity")) if normalized_attendance else "-",
            "attendance_allocation": format_points(session_allocation_value) if normalized_attendance else "-",
            "attendance_remaining": "-" if attendance_incomplete else format_points(attendance_ledger.get("remaining")) if normalized_attendance else "-",
            "attendance_positive_remaining": format_points(attendance_ledger.get("positive_remaining")) if normalized_attendance and not attendance_incomplete else "-",
            "attendance_negative_remaining": format_points(attendance_ledger.get("negative_remaining")) if normalized_attendance and not attendance_incomplete else "-",
            "attendance_earned": "INCOMPLETE" if attendance_incomplete else format_points(session_earned_value, signed=True),
            "attendance_reason": canonical_score.get("attendance_reason") if canonical_score else None,
            "attendance_positive_used": "INCOMPLETE" if attendance_incomplete else format_points(attendance_ledger.get("positive_used")) if normalized_attendance else format_points(session_projection["positive_points"]),
            "attendance_negative_used": "INCOMPLETE" if attendance_incomplete else format_points(attendance_ledger.get("negative_used")) if normalized_attendance else format_points(session_projection["negative_points"]),
            "monthly_positive_points": monthly_points["positive_points"],
            "monthly_negative_points": monthly_points["negative_points"],
            "monthly_total_points": monthly_points["signed_total"],
            "attendance_status": attendance_status,
            "normalized_attendance": normalized_attendance,
            "attendance_incomplete": attendance_incomplete,
            "weekly_max": weekly_max,
            "trend": trend,
            "behavior_grade": behavior_grade,
            "behavior_percentage": behavior_report.get("percentage") if behavior_report else attendance_percentage,
            "session_grand_earned": session_earned_value,
            "session_grand_allocation": session_allocation_value,
        }

    month_keys = sorted({(item.attendance_date.year, item.attendance_date.month) for item in all_session_records})
    if not month_keys:
        month_keys = [(report_date.year, report_date.month)]
    else:
        # Keep the month selected by the caller first, then continue in
        # chronological order so the report opens where the administrator was
        # working while still including the complete session history.
        selected_month_key = (report_date.year, report_date.month)
        if selected_month_key in month_keys:
            month_keys = [selected_month_key] + [key for key in month_keys if key != selected_month_key]
    monthly_reports = [build_month(date(year, month, 1)) for year, month in month_keys]
    return render_template(
        "admin/behavior/attendance_report.html",
        settings=get_settings(),
        config=config,
        session=selected_session,
        enrollment=enrollment,
        student=enrollment.student,
        report_date=report_date,
        monthly_reports=monthly_reports,
        # Compatibility inputs for the existing chart enhancement script; the
        # server-rendered chart itself is generated separately for every page.
        weekly_max=monthly_reports[0]["weekly_max"],
        trend=monthly_reports[0]["trend"],
        # The stylesheet predates the per-month loop and still evaluates the
        # first page's donut angles at template-load time.
        donut_present_angle=monthly_reports[0]["donut_present_angle"],
        donut_late_angle=monthly_reports[0]["donut_late_angle"],
        donut_absent_angle=monthly_reports[0]["donut_absent_angle"],
        donut_excused_angle=monthly_reports[0]["donut_excused_angle"],
        donut_official_leave_angle=monthly_reports[0]["donut_official_leave_angle"],
        attendance_percentage=monthly_reports[0]["attendance_percentage"],
        counts=monthly_reports[0]["counts"],
        total_school_days=monthly_reports[0]["total_school_days"],
        print_mode=request.args.get("print") == "1",
        portal_read_only=request.args.get("portal_read_only") == "1",
        portal_back_url=request.args.get("portal_back_url"),
        portal_download_url=request.args.get("portal_download_url"),
        portal_download_filename=request.args.get("portal_download_filename"),
    )


def _attendance_redirect(config, session_id=None, attendance_date=None, level_id=None):
    return redirect(url_for(
        "behavior.attendance",
        config_id=config.id if config else None,
        session_id=session_id,
        level_id=level_id,
        attendance_date=attendance_date.isoformat() if attendance_date else None,
    ))


@behavior_bp.route("/attendance/statuses/<int:status_id>", methods=["POST"])
def update_attendance_status(status_id):
    item = db.session.get(BehaviorAttendanceStatus, status_id)
    config = db.session.get(BehaviorConfiguration, _int(request.form.get("config_id")))
    wants_json = _wants_json_response()
    try:
        if not item or not config or item.behavior_configuration_id != config.id:
            raise BehaviorValidationError("Attendance status is outside the selected configuration")
        ensure_configuration_editable(config)
        item.label = (request.form.get("label") or "").strip()
        item.polarity = (request.form.get("polarity") or "neutral").strip().lower()
        item.points = decimal_value(request.form.get("points"), "Attendance points", minimum="0")
        # Status availability is automatic.  Keep the legacy column true for
        # old clients and existing databases, but do not accept a user-facing
        # Active checkbox anymore.
        item.is_active = True
        if not item.label or item.polarity not in {"positive", "negative", "neutral"}:
            raise BehaviorValidationError("Attendance status label and polarity are required")
        db.session.commit()
        if wants_json:
            return jsonify(
                success=True,
                status={
                    "id": item.id,
                    "key": item.key,
                    "label": item.label,
                    "polarity": item.polarity,
                    "points": f"{item.points:.3f}",
                    "is_active": item.is_active,
                },
            )
        flash("Attendance status updated.", "success")
    except (BehaviorValidationError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        if wants_json:
            return jsonify(success=False, error=str(exc) if not isinstance(exc, IntegrityError) else "Attendance status could not be updated."), 400
        flash(str(exc) if not isinstance(exc, IntegrityError) else "Attendance status could not be updated.", "danger")
    try:
        selected_date = date.fromisoformat(request.form.get("attendance_date"))
    except (TypeError, ValueError):
        selected_date = date.today()
    return _attendance_redirect(
        config,
        _int(request.form.get("session_id")),
        selected_date,
        _int(request.form.get("level_id")),
    )

@behavior_bp.route("/attendance/active-days", methods=["POST"])
def update_attendance_active_days():
    """Autosave the canonical Academic Year Level Attendance calendar."""
    config = db.session.get(BehaviorConfiguration, _int(request.form.get("config_id")))
    wants_json = _wants_json_response()
    try:
        if not config:
            raise BehaviorValidationError("Behavior configuration was not found")
        ensure_configuration_editable(config)
        active_days = {_int(value) for value in request.form.getlist("school_days")}
        level_id = _int(request.form.get("level_id"))
        before_days, saved_days = apply_attendance_active_days(config, active_days, level_id)
        audit(
            "Behavior Attendance Calendar",
            f"Updated active days for Academic Year Level {level_id} "
            f"(configuration {config.id}): {sorted(before_days)} -> {sorted(saved_days)}",
        )
        db.session.commit()
        if wants_json:
            return jsonify(
                success=True,
                active_days=sorted(saved_days),
                inactive_days=sorted(set(range(7)) - saved_days),
            )
        flash("Attendance active days saved for this Academic Year Level.", "success")
    except (BehaviorValidationError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        if wants_json:
            return jsonify(success=False, error=str(exc) if not isinstance(exc, IntegrityError) else "Attendance active days could not be saved."), 400
        flash(str(exc) if not isinstance(exc, IntegrityError) else "Attendance active days could not be saved.", "danger")
    try:
        selected_date = date.fromisoformat(request.form.get("attendance_date"))
    except (TypeError, ValueError):
        selected_date = date.today()
    return _attendance_redirect(
        config,
        _int(request.form.get("session_id")),
        selected_date,
        _int(request.form.get("level_id")),
    )
