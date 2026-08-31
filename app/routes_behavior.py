"""Dedicated Phase 2B Behavior administration routes."""

import json
from datetime import date, datetime
from decimal import Decimal
from secrets import token_urlsafe

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from . import db
from .academic_hierarchy import year_levels, year_subjects
from .audit import audit
from .behavior_service import (
    BehaviorValidationError,
    allocation_total,
    behavior_summary,
    calculate_session_score,
    configuration_for_scope,
    decimal_value,
    edit_event,
    ensure_configuration_editable,
    ensure_session_editable,
    find_event_by_idempotency_key,
    normalize_idempotency_key,
    record_event,
    restore_event,
    validate_behavior_configuration,
    validate_enrollment_scope,
    validate_behavior_scope,
    validate_session_scope,
    void_event,
)
from .behavior_grading import (
    BehaviorGradeValidationError,
    behavior_grade_for_score,
    behavior_grade_readiness,
    behavior_grade_scales,
    validate_behavior_grade_overlap,
    validate_behavior_grade_values,
)
from .models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    BehaviorGradeScale,
    BehaviorSession,
    Exam,
    ExamType,
    Student,
    StudentEnrollment,
)
from .permissions import enforce_endpoint_permission


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
        db.session.get(BehaviorConfiguration, _int(selected_config_id))
        if selected_config_id not in (None, "") else None
    )
    selected_year = _selected_year(selected_year_id)
    if selected_config and not has_year_selection:
        selected_year = selected_config.academic_year
    selected_year_id = selected_year.id if selected_year else None
    configurations = (
        BehaviorConfiguration.query
        .filter_by(academic_year_id=selected_year_id)
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
        config = db.session.get(BehaviorConfiguration, _int(selected_config_id))
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
        db.session.get(BehaviorConfiguration, _int(config_id))
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
        selected_level = requested_config.academic_year_level
        if has_level_selection and selected_level.id != requested_level_id:
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
        if selected_level:
            query = query.filter_by(academic_year_level_id=selected_level.id)
        configurations = query.order_by(BehaviorConfiguration.id.desc()).all()
    selected_config = requested_config if not invalid_scope else None
    if selected_config and (
        not selected_year
        or selected_config.academic_year_id != selected_year.id
        or (selected_level and selected_config.academic_year_level_id != selected_level.id)
    ):
        invalid_scope = True
        selected_config = None
    if selected_config is None and auto_select_config and configurations and not has_config_selection and not invalid_scope:
        selected_config = configurations[0]
        selected_level = selected_config.academic_year_level
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
    sessions = list(selected_config.sessions) if selected_config else []
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


def _behavior_enrollments(config, class_id=None):
    if not config:
        return []
    query = (
        StudentEnrollment.query
        .join(Student, Student.id == StudentEnrollment.student_id)
        .join(AcademicYearClass, AcademicYearClass.id == StudentEnrollment.academic_year_class_id)
        .filter(
            StudentEnrollment.academic_year_id == config.academic_year_id,
            StudentEnrollment.academic_year_level_id == config.academic_year_level_id,
            AcademicYearClass.academic_year_level_id == config.academic_year_level_id,
            AcademicYearClass.is_active.is_(True),
            StudentEnrollment.status.notin_(("withdrawn", "archived")),
        )
    )
    if class_id:
        query = query.filter(StudentEnrollment.academic_year_class_id == _int(class_id))
    return query.order_by(Student.full_name, Student.student_code, StudentEnrollment.id).all()


def _student_board_rows(config, selected_session, class_id=None):
    if not config or not selected_session:
        return []
    rows = []
    for enrollment in _behavior_enrollments(config, class_id):
        score = calculate_session_score(config, selected_session, enrollment)
        events = BehaviorEvent.query.filter_by(
            behavior_configuration_id=config.id,
            behavior_session_id=selected_session.id,
            student_enrollment_id=enrollment.id,
            status="active",
        ).all()
        rows.append({
            "enrollment": enrollment,
            "score": score,
            "grade": behavior_grade_for_score(selected_session, score["final_score"]),
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
    query = BehaviorEvent.query
    if context["scope_invalid"]:
        query = query.filter(BehaviorEvent.id == -1)
    if context["selected_year"]:
        query = query.filter(BehaviorEvent.configuration.has(
            BehaviorConfiguration.academic_year_id == context["selected_year"].id
        ))
    if context["selected_level"]:
        query = query.filter(BehaviorEvent.configuration.has(
            BehaviorConfiguration.academic_year_level_id == context["selected_level"].id
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
        _behavior_enrollments(context["config"], context["selected_class"].id if context["selected_class"] else None)
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
        )
        if not context["scope_invalid"] else []
    )
    active_events = [event for row in board_rows for event in row["events"]]
    evaluated = [row for row in board_rows if row["events"]]
    positive = [row for row in board_rows if row["positive_events"]]
    negative = [row for row in board_rows if row["negative_events"]]
    final_scores = [row["score"]["final_score"] for row in board_rows]
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
    return render_template(
        "admin/behavior/dashboard.html",
        **context,
        board_rows=board_rows,
        summary=summary,
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
            level_id = _int(request.form.get("academic_year_level_id"))
            subject_id = _int(request.form.get("academic_year_subject_id"))
            year, level, subject = validate_behavior_scope(year_id, level_id, subject_id)
            config = db.session.get(BehaviorConfiguration, config_id) if config_id else None
            if config and (
                config.academic_year_id != year.id
                or config.academic_year_level_id != level.id
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
            # Configuration creation is immediately operational. Completeness
            # is reported separately from saving and never controls editing.
            db.session.flush()
            audit(
                "Behavior Configuration",
                f"Saved configuration {config.id} for {year.name} / {level.name} / {subject.name}",
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
    """Manage raw-score bands owned exclusively by one Behavior session."""
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
                flash("Behavior grade scale copied. Review the raw score ranges before using it.", "success")
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
            item.maximum_score = decimal_value(
                request.form.get("maximum_score"),
                "Session maximum",
                minimum="0.001",
            )
            item.sort_order = _int(request.form.get("sort_order"), 0)
            item.is_active = True  # legacy column; all saved sessions are operational
            db.session.flush()
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
            item = (
                db.session.get(BehaviorAction, _int(request.form.get("action_id")))
                if request.form.get("action_id") else None
            )
            if item and item.behavior_category_id != category.id:
                raise BehaviorValidationError(
                    "Behavior action is outside the selected category"
                )
            item = item or BehaviorAction(behavior_category_id=category.id)
            item.name = (request.form.get("name") or "").strip()
            item.level_number = _int(request.form.get("level_number"), 1)
            item.points = decimal_value(
                request.form.get("points"),
                "Action points",
                minimum="0.001",
            )
            item.frequency = (request.form.get("frequency") or "ad_hoc").strip().lower()
            if item.frequency not in BehaviorAction.FREQUENCY_VALUES:
                raise BehaviorValidationError("Behavior action frequency is invalid")
            item.description = (request.form.get("description") or "").strip() or None
            item.sort_order = _int(request.form.get("sort_order"), 0)
            item.is_active = True  # legacy column; all saved actions are operational
            if not item.name:
                raise BehaviorValidationError("Action name is required")
            db.session.add(item)
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
        )
        if not context["scope_invalid"] else []
    )
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
    grade = behavior_grade_for_score(session, score["final_score"])
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
            if not all((session, category, action)):
                raise BehaviorValidationError("Session, category, and action are required")
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
    validate_behavior_configuration(event.configuration)
    return render_template("admin/behavior/event_detail.html", event=event)


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
    return render_template("admin/behavior/audit.html", rows=rows)
