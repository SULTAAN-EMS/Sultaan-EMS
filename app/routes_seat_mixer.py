"""Seat Mixer — Exam Hall Seating Arrangement (v2).

A dedicated, isolated blueprint for the Seat Arrangement Mixer feature.
Uses ExamHall + ExamHallVersion + SeatMixerAssignment models.

Core concept: "Exam Halls" — each hall has its own independent versions,
each version has its own hall configuration, classes/students mix, and
seat assignments.
"""

from datetime import datetime, timezone
import json
import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from . import db
from .audit import audit
from .models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    ExamHall,
    ExamHallVersion,
    IdCardIssue,
    SeatAssignment,
    SeatMixerAssignment,
    SeatMixerSaveSnapshot,
    Setting,
    Student,
)
from .enrollment_service import EnrollmentValidationError, enrollment_placement_for_student, student_enrollment_legacy_scope_query
from .permissions import enforce_endpoint_permission
from .verification import id_card_qr_payload

seat_mixer_bp = Blueprint("seat_mixer", __name__)

# Deterministic class color palette (stable across regenerations)
CLASS_PALETTE = [
    "#60A5FA", "#F472B6", "#34D399", "#FBBF24", "#A78BFA", "#22D3EE",
    "#FB923C", "#818CF8", "#F87171", "#4ADE80", "#FB7185", "#38BDF8",
]


def get_class_color(class_id):
    """Deterministic color assignment based on class ID."""
    return CLASS_PALETTE[class_id % len(CLASS_PALETTE)]


SEAT_MIXER_CLASS_COLORS_PREFIX = "seat_mixer_class_colors_v1:"
SEAT_MIXER_CURRENT_SNAPSHOT_PREFIX = "seat_mixer_current_snapshot_v1:"
SEAT_MIXER_HISTORY_LIMIT = 10
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def seat_mixer_class_color_key(version_id):
    return f"{SEAT_MIXER_CLASS_COLORS_PREFIX}{int(version_id)}"


def seat_mixer_current_snapshot_key(version_id):
    return f"{SEAT_MIXER_CURRENT_SNAPSHOT_PREFIX}{int(version_id)}"


def current_snapshot_id(version_id):
    setting = db.session.get(Setting, seat_mixer_current_snapshot_key(version_id))
    try:
        return int(setting.value) if setting and setting.value else None
    except (TypeError, ValueError):
        return None


def set_current_snapshot_id(version_id, snapshot_id):
    setting = db.session.get(Setting, seat_mixer_current_snapshot_key(version_id))
    setting = setting or Setting(key=seat_mixer_current_snapshot_key(version_id))
    setting.value = str(int(snapshot_id))
    db.session.add(setting)


def clear_current_snapshot_id(version_id):
    setting = db.session.get(Setting, seat_mixer_current_snapshot_key(version_id))
    if setting:
        db.session.delete(setting)


def version_class_colors(version_id):
    """Return validated per-class color overrides for one saved layout version."""
    setting = db.session.get(Setting, seat_mixer_class_color_key(version_id))
    try:
        raw_colors = json.loads(setting.value) if setting and setting.value else {}
    except (TypeError, ValueError):
        raw_colors = {}
    if not isinstance(raw_colors, dict):
        return {}
    return {
        str(class_id): color.upper()
        for class_id, color in raw_colors.items()
        if str(class_id).isdigit() and isinstance(color, str) and HEX_COLOR_RE.fullmatch(color)
    }


def save_version_class_colors(version_id, colors):
    """Persist only validated color overrides; uncustomized classes use the palette."""
    setting = db.session.get(Setting, seat_mixer_class_color_key(version_id))
    if colors:
        setting = setting or Setting(key=seat_mixer_class_color_key(version_id))
        setting.value = json.dumps(colors, sort_keys=True)
        db.session.add(setting)
    elif setting:
        db.session.delete(setting)


def is_expired(hall):
    """Check if a hall's end time has passed."""
    if not hall.end_time:
        return False
    return hall.end_time < datetime.now()


def get_current_academic_year():
    """Get the current academic year."""
    return AcademicYear.query.filter_by(is_current=True).first()


def students_for_current_classes(current_year, class_ids):
    """Resolve Seat Mixer candidates through the selected year's enrollments."""
    student_ids = set()
    for class_id in class_ids:
        try:
            student_ids.update(
                student.id
                for student in student_enrollment_legacy_scope_query(
                    current_year.id,
                    legacy_class_id=class_id,
                ).filter(Student.is_active.is_(True)).all()
            )
        except EnrollmentValidationError:
            continue
    return (
        Student.query.options(joinedload(Student.academic_class), joinedload(Student.academic_level))
        .filter(Student.id.in_(student_ids), Student.is_active.is_(True))
        .order_by(Student.full_name)
        .all()
        if student_ids else []
    )


def get_school_name():
    """Get school name from settings."""
    from .models import Setting
    s = db.session.get(Setting, "dashboard_title")
    return s.value if s else "School"


def stored_photo_url(path):
    """Return a browser-safe student photo URL without duplicating media storage."""
    if not path:
        return None
    value = str(path)
    if value.startswith(("http://", "https://", "data:", "/static/")):
        return value
    if value.startswith("uploads/"):
        return url_for("static", filename=value)
    return url_for("static", filename=f"uploads/{value}")


def parse_optional_datetime(value, field_name):
    """Parse HTML datetime-local values with a useful API error."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name} datetime") from exc


SEAT_MIXER_APPEARANCE_KEY = "seat_mixer_appearance_v1"
SEAT_MIXER_APPEARANCE_DEFAULTS = {
    "seatSize": 92, "seatGap": 8, "mapZoom": 100, "photoSize": 42,
    "photoShape": "circle", "studentBorderWidth": 1, "fontFamily": "Inter",
    "fontSize": 12, "fontWeight": 700, "cornerRadius": 16,
    "cardPadding": 8, "shadow": "medium",
}

# Seat Mixer visual colors are intentionally defined only in static CSS.  These
# legacy keys are removed from persisted settings so an old administrator choice
# can never override the approved design palette after a refresh.
SEAT_MIXER_COLOR_SETTING_KEYS = {
    "textColor", "primaryColor", "secondaryColor", "backgroundColor",
    "cardColor", "headerColor", "buttonColor", "emptySeatColor",
    "occupiedSeatColor", "classColors",
}


def seat_mixer_appearance():
    """Return persisted non-color Seat Mixer preferences with safe defaults."""
    setting = db.session.get(Setting, SEAT_MIXER_APPEARANCE_KEY)
    try:
        saved = json.loads(setting.value) if setting and setting.value else {}
    except (TypeError, ValueError):
        saved = {}
    removed_color_settings = False
    for key in SEAT_MIXER_COLOR_SETTING_KEYS:
        if key in saved:
            saved.pop(key)
            removed_color_settings = True

    # Remove the marker used by the retired palette migration as well.
    palette_marker = db.session.get(Setting, "seat_mixer_appearance_palette_v2")
    if removed_color_settings or palette_marker:
        if setting:
            setting.value = json.dumps(saved)
            db.session.add(setting)
        if palette_marker:
            db.session.delete(palette_marker)
        db.session.commit()

    appearance = dict(SEAT_MIXER_APPEARANCE_DEFAULTS)
    appearance.update({key: value for key, value in saved.items() if key in appearance})
    return appearance


def hall_frequency_counts(student_ids, hall_id):
    """Count prior real examinations for each student in one physical hall.

    SeatMixerAssignment rows are saved layout revisions, not examinations; using
    them here would inflate the count. SeatAssignment is the exam-scoped history.
    """
    if not student_ids or not hall_id:
        return {}
    return dict(
        db.session.query(
            SeatAssignment.student_id,
            func.count(func.distinct(SeatAssignment.exam_id)),
        )
        .filter(
            SeatAssignment.student_id.in_(student_ids),
            SeatAssignment.exam_hall_id == hall_id,
        )
        .group_by(SeatAssignment.student_id)
        .all()
    )


def serialize_student(student, hall_frequency=0, elsewhere=None, academic_year_id=None):
    """Seat Mixer student projection sourced directly from central Student records."""
    full_name = student.full_name or ""
    placement = enrollment_placement_for_student(student, academic_year_id) if academic_year_id else None
    class_id = (placement or {}).get("academic_class_id") or student.academic_class_id
    class_name = (placement or {}).get("class_name") or (student.academic_class.name if student.academic_class else "")
    level_name = (placement or {}).get("level_name") or (student.academic_level.name if student.academic_level else "")
    return {
        "id": student.id,
        "full_name": full_name,
        "first_name": full_name.split()[0] if full_name else "Student",
        "class_id": class_id,
        "class_name": class_name,
        "level": level_name,
        "gender": student.gender or "Not recorded",
        "class_color": get_class_color(class_id or student.id),
        "photo_path": stored_photo_url(student.photo_path),
        # Compatibility alias for existing clients. This is now the true
        # examination frequency in this physical hall, not a global layout count.
        "hall_assignment_count": hall_frequency,
        "hall_frequency": hall_frequency,
        "elsewhere": elsewhere,
    }


def normalized_layout_config(config):
    """Normalize the browser's camelCase layout contract once at the boundary."""
    config = config if isinstance(config, dict) else {}
    try:
        rows = int(config.get("rows", 3))
        tables_per_row = int(config.get("tablesPerRow", config.get("tables_per_row", 5)))
        seats_per_table = int(config.get("seatsPerTable", config.get("seats_per_table", 2)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid hall layout configuration") from exc
    if not (1 <= rows <= 50 and 1 <= tables_per_row <= 100 and 1 <= seats_per_table <= 12):
        raise ValueError("Hall layout configuration is outside supported limits")
    return {
        "rows": rows,
        "tablesPerRow": tables_per_row,
        "seatsPerTable": seats_per_table,
    }


def normalized_assignments(assignments, config, validate_students=True):
    """Validate an incoming snapshot and return its compact persistent form."""
    if not isinstance(assignments, list):
        raise ValueError("Assignments must be a list")
    rows = config["rows"]
    tables_per_row = config["tablesPerRow"]
    seats_per_table = config["seatsPerTable"]
    student_ids = set()
    seat_positions = set()
    normalized = []
    for assignment in assignments:
        try:
            student_id = int(assignment["student_id"])
            row = int(assignment["row"])
            table = int(assignment["table"])
            seat = int(assignment["seat"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("One or more seat assignments are incomplete") from exc
        if not (0 <= row < rows and 0 <= table < tables_per_row and 0 <= seat < seats_per_table):
            raise ValueError("One or more seat positions are outside the hall layout")
        if student_id in student_ids or (row, table, seat) in seat_positions:
            raise ValueError("A student or seat was included more than once")
        student_ids.add(student_id)
        seat_positions.add((row, table, seat))
        normalized.append({"student_id": student_id, "row": row, "table": table, "seat": seat})

    if validate_students and student_ids:
        active_count = Student.query.filter(
            Student.id.in_(student_ids),
            Student.is_active.is_(True),
        ).count()
        if active_count != len(student_ids):
            raise ValueError("One or more selected students are no longer active")
    return normalized


def normalized_selected_students(raw_selection):
    """Keep selected-but-unplaced students inside a history snapshot as well."""
    if not isinstance(raw_selection, dict):
        return {}
    selected = {}
    for raw_class_id, raw_student_ids in raw_selection.items():
        try:
            class_id = int(raw_class_id)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_student_ids, list):
            continue
        student_ids = []
        for raw_student_id in raw_student_ids:
            try:
                student_id = int(raw_student_id)
            except (TypeError, ValueError):
                continue
            if student_id not in student_ids:
                student_ids.append(student_id)
        if student_ids:
            selected[str(class_id)] = student_ids
    return selected


def seat_mixer_metrics(assignments, academic_year_id=None):
    """Calculate the same class-separation metrics used by the live map."""
    student_ids = [item["student_id"] for item in assignments]
    class_ids = {}
    if student_ids:
        for student in Student.query.filter(Student.id.in_(student_ids)).all():
            placement = enrollment_placement_for_student(student, academic_year_id) if academic_year_id else None
            class_ids[student.id] = (placement or {}).get("academic_class_id") or student.academic_class_id
    grouped = {}
    for item in assignments:
        class_id = class_ids.get(item["student_id"])
        if class_id is not None:
            grouped.setdefault(class_id, []).append(item)

    hard = adjacent = near = same_row = pairs = 0
    for placements in grouped.values():
        for index, left in enumerate(placements):
            for right in placements[index + 1:]:
                distance = abs(left["row"] - right["row"]) + abs(left["table"] - right["table"])
                if left["row"] == right["row"] and left["table"] == right["table"]:
                    distance += abs(left["seat"] - right["seat"]) * 0.15
                pairs += 1
                if distance < 0.5:
                    hard += 1
                elif distance <= 1.05:
                    adjacent += 1
                elif distance <= 2.05:
                    near += 1
                elif distance <= 3.05 and left["row"] == right["row"]:
                    same_row += 1

    return {
        "integrity_score": max(0, round(100 - hard * 25 - adjacent * 3 - near * 0.25 - same_row * 0.1)),
        "near_adjacency_count": adjacent,
        "placed_count": len(assignments),
    }


def replace_active_assignments(version_id, assignments, config):
    """Keep the active print/export layout aligned with the selected snapshot."""
    SeatMixerAssignment.query.filter_by(version_id=version_id).delete(synchronize_session=False)
    for assignment in assignments:
        db.session.add(SeatMixerAssignment(
            version_id=version_id,
            student_id=assignment["student_id"],
            row_number=assignment["row"],
            table_number=assignment["table"],
            seat_number=assignment["seat"],
            rows_config=config["rows"],
            tables_per_row_config=config["tablesPerRow"],
            seats_per_table_config=config["seatsPerTable"],
        ))


def snapshot_payload(snapshot):
    try:
        payload = json.loads(snapshot.snapshot_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        config = normalized_layout_config(payload.get("config", {}))
        assignments = normalized_assignments(payload.get("assignments", []), config, validate_students=False)
    except ValueError:
        return None
    return {
        "config": config,
        "assignments": assignments,
        "selected_students": normalized_selected_students(payload.get("selected_students", {})),
        "last_meta": str(payload.get("last_meta") or "Saved layout"),
    }


def serialize_saved_assignments(hall, assignments):
    """Hydrate compact snapshot rows from the canonical Student records."""
    hall_exam = hall.exam
    hall_exam_type = hall.exam_type
    academic_year_id = (
        hall_exam.academic_year_id if hall_exam else
        hall_exam_type.academic_year_id if hall_exam_type else None
    )
    student_ids = [item["student_id"] for item in assignments]
    students = (
        Student.query
        .filter(Student.id.in_(student_ids))
        .options(joinedload(Student.academic_class), joinedload(Student.academic_level))
        .all()
    ) if student_ids else []
    student_map = {student.id: student for student in students}
    assignment_counts = hall_frequency_counts(student_ids, hall.id)
    saved_data = []
    for assignment in assignments:
        student = student_map.get(assignment["student_id"])
        if not student:
            continue
        saved_data.append({
            "student_id": student.id,
            **serialize_student(
                student,
                assignment_counts.get(student.id, 0),
                academic_year_id=academic_year_id,
            ),
            "row": assignment["row"],
            "table": assignment["table"],
            "seat": assignment["seat"],
        })
    return saved_data


def history_metadata(version_id):
    snapshots = (
        SeatMixerSaveSnapshot.query
        .filter_by(version_id=version_id)
        .order_by(SeatMixerSaveSnapshot.created_at.desc(), SeatMixerSaveSnapshot.id.desc())
        .limit(SEAT_MIXER_HISTORY_LIMIT)
        .all()
    )
    current_id = current_snapshot_id(version_id)
    if current_id is None and snapshots:
        current_id = snapshots[0].id
    return [{
        "id": snapshot.id,
        # Stored timestamps are UTC-naive. Mark them as UTC so the browser can
        # render the correct local date and time for the administrator.
        "created_at": snapshot.created_at.replace(tzinfo=timezone.utc).isoformat() if snapshot.created_at else None,
        "integrity_score": snapshot.integrity_score,
        "near_adjacency_count": snapshot.near_adjacency_count,
        "placed_count": snapshot.placed_count,
        "is_current": snapshot.id == current_id,
    } for snapshot in snapshots], current_id


@seat_mixer_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@seat_mixer_bp.route("/")
def index():
    """Main page — single-page app with all 3 screens.

    Screen 1: Exam Halls list + create form
    Screen 2: Versions list per hall
    Screen 3: Builder (per Hall + Version)
    """
    # Load all exam halls
    halls = (
        ExamHall.query
        .filter_by(is_active=True)
        .order_by(ExamHall.sort_order, ExamHall.name)
        .all()
    )

    # Load levels and classes for the builder
    levels = (
        AcademicLevel.query
        .filter_by(is_active=True)
        .order_by(AcademicLevel.sort_order)
        .all()
    )
    classes = (
        AcademicClass.query
        .filter_by(is_active=True)
        .order_by(AcademicClass.sort_order)
        .all()
    )

    # Serialize hall data
    hall_data = []
    for h in halls:
        hall_data.append({
            "id": h.id,
            "name": h.name,
            "code": h.code,
            "start_time": h.start_time.strftime("%Y-%m-%dT%H:%M") if h.start_time else None,
            "end_time": h.end_time.strftime("%Y-%m-%dT%H:%M") if h.end_time else None,
            "version_count": len(h.versions),
            "is_expired": is_expired(h),
        })

    # Serialize level/class data
    level_data = []
    for lvl in levels:
        level_classes = [c for c in classes if c.academic_level_id == lvl.id]
        level_data.append({
            "id": lvl.id,
            "name": lvl.name,
            "sort_order": lvl.sort_order,
            "classes": [
                {"id": c.id, "name": c.name, "sort_order": c.sort_order}
                for c in level_classes
            ],
        })

    return render_template(
        "admin/seat_mixer_index.html",
        halls=hall_data,
        levels=level_data,
        class_palette=CLASS_PALETTE,
        school_name=get_school_name(),
    )


@seat_mixer_bp.route("/api/create-hall", methods=["POST"])
def api_create_hall():
    """Create a new exam hall with its first version."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    start = data.get("start")
    end = data.get("end")

    if not name:
        return jsonify({"error": "Hall name is required"}), 400

    # Parse datetimes
    try:
        start_dt = parse_optional_datetime(start, "start")
        end_dt = parse_optional_datetime(end, "end")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if start_dt and end_dt and end_dt <= start_dt:
        return jsonify({"error": "End time must be after start time"}), 400

    # Generate unique code
    existing_count = ExamHall.query.count()
    code = f"HALL-{existing_count + 1}"
    while ExamHall.query.filter_by(code=code).first():
        existing_count += 1
        code = f"HALL-{existing_count + 1}"

    hall = ExamHall(
        name=name,
        code=code,
        start_time=start_dt,
        end_time=end_dt,
        sort_order=existing_count,
    )
    db.session.add(hall)
    db.session.flush()

    # Create first version
    version = ExamHallVersion(
        exam_hall_id=hall.id,
        version_number=1,
        label="Version 1",
    )
    db.session.add(version)
    db.session.commit()

    audit("Seat Mixer", f"Created exam hall '{name}' (code: {code})")
    return jsonify({
        "success": True,
        "hall": {
            "id": hall.id,
            "name": hall.name,
            "code": hall.code,
            "start_time": hall.start_time.strftime("%Y-%m-%dT%H:%M") if hall.start_time else None,
            "end_time": hall.end_time.strftime("%Y-%m-%dT%H:%M") if hall.end_time else None,
            "version_count": 1,
            "is_expired": is_expired(hall),
        },
        "version": {
            "id": version.id,
            "label": version.label,
            "version_number": version.version_number,
        },
    })


@seat_mixer_bp.route("/api/hall/<int:hall_id>", methods=["PATCH", "DELETE"])
def api_manage_hall(hall_id):
    """Rename, schedule, or remove an Exam Hall with a single persisted source."""
    hall = db.session.get(ExamHall, hall_id) or abort(404)

    if request.method == "DELETE":
        # The UI always asks for confirmation. Requiring this flag also protects
        # the API from accidental destructive calls.
        if not (request.get_json(silent=True) or {}).get("confirm"):
            return jsonify({"error": "Deletion must be explicitly confirmed"}), 400
        name = hall.name
        for version in hall.versions:
            for key in (seat_mixer_class_color_key(version.id), seat_mixer_current_snapshot_key(version.id)):
                setting = db.session.get(Setting, key)
                if setting:
                    db.session.delete(setting)
        db.session.delete(hall)
        db.session.commit()
        audit("Seat Mixer", f"Deleted exam hall '{name}' and its saved versions")
        return jsonify({"success": True})

    data = request.get_json(silent=True) or {}
    name = (data.get("name") if "name" in data else hall.name) or ""
    name = name.strip()
    if not name:
        return jsonify({"error": "Hall name is required"}), 400
    try:
        start_dt = parse_optional_datetime(data.get("start") if "start" in data else hall.start_time, "start")
        end_dt = parse_optional_datetime(data.get("end") if "end" in data else hall.end_time, "end")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if start_dt and end_dt and end_dt <= start_dt:
        return jsonify({"error": "End time must be after start time"}), 400

    hall.name = name
    hall.start_time = start_dt
    hall.end_time = end_dt
    db.session.commit()
    audit("Seat Mixer", f"Updated exam hall '{hall.name}'")
    return jsonify({
        "success": True,
        "hall": {
            "id": hall.id,
            "name": hall.name,
            "code": hall.code,
            "start_time": hall.start_time.strftime("%Y-%m-%dT%H:%M") if hall.start_time else None,
            "end_time": hall.end_time.strftime("%Y-%m-%dT%H:%M") if hall.end_time else None,
            "version_count": len(hall.versions),
            "is_expired": is_expired(hall),
        },
    })


@seat_mixer_bp.route("/api/hall/<int:hall_id>/add-version", methods=["POST"])
def api_add_version(hall_id):
    """Add a new version to a hall."""
    hall = db.session.get(ExamHall, hall_id) or abort(404)

    version_number = len(hall.versions) + 1
    version = ExamHallVersion(
        exam_hall_id=hall.id,
        version_number=version_number,
        label=f"Version {version_number}",
    )
    db.session.add(version)
    db.session.commit()

    audit("Seat Mixer", f"Added version {version_number} to hall '{hall.name}'")
    return jsonify({
        "success": True,
        "version": {
            "id": version.id,
            "label": version.label,
            "version_number": version.version_number,
        },
    })


@seat_mixer_bp.route("/api/version/<int:version_id>", methods=["PATCH", "DELETE"])
def api_manage_version(version_id):
    """Rename or delete a single saved layout version."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    hall_name = version.hall.name
    if request.method == "DELETE":
        if not (request.get_json(silent=True) or {}).get("confirm"):
            return jsonify({"error": "Deletion must be explicitly confirmed"}), 400
        label = version.label
        for key in (seat_mixer_class_color_key(version.id), seat_mixer_current_snapshot_key(version.id)):
            setting = db.session.get(Setting, key)
            if setting:
                db.session.delete(setting)
        db.session.delete(version)
        db.session.commit()
        audit("Seat Mixer", f"Deleted layout version '{label}' from hall '{hall_name}'")
        return jsonify({"success": True})

    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    if not label:
        return jsonify({"error": "Version name is required"}), 400
    version.label = label
    db.session.commit()
    audit("Seat Mixer", f"Renamed layout version to '{label}' in hall '{hall_name}'")
    return jsonify({"success": True, "version": {"id": version.id, "label": version.label}})


@seat_mixer_bp.route("/api/version/<int:version_id>/duplicate", methods=["POST"])
def api_duplicate_version(version_id):
    """Clone a saved layout into a separately manageable history version."""
    source = db.session.get(ExamHallVersion, version_id) or abort(404)
    next_number = (
        db.session.query(func.max(ExamHallVersion.version_number))
        .filter_by(exam_hall_id=source.exam_hall_id)
        .scalar()
        or 0
    ) + 1
    duplicate = ExamHallVersion(
        exam_hall_id=source.exam_hall_id,
        version_number=next_number,
        label=f"{source.label} copy",
    )
    db.session.add(duplicate)
    db.session.flush()
    for assignment in source.assignments:
        db.session.add(SeatMixerAssignment(
            version_id=duplicate.id,
            student_id=assignment.student_id,
            row_number=assignment.row_number,
            table_number=assignment.table_number,
            seat_number=assignment.seat_number,
            rows_config=assignment.rows_config,
            tables_per_row_config=assignment.tables_per_row_config,
            seats_per_table_config=assignment.seats_per_table_config,
        ))
    save_version_class_colors(duplicate.id, version_class_colors(source.id))
    db.session.commit()
    audit("Seat Mixer", f"Duplicated layout '{source.label}' in hall '{source.hall.name}'")
    return jsonify({"success": True, "version": {
        "id": duplicate.id,
        "label": duplicate.label,
        "version_number": duplicate.version_number,
    }})


@seat_mixer_bp.route("/api/appearance", methods=["GET", "PATCH"])
def api_appearance():
    """Persist dedicated Seat Mixer appearance preferences in existing settings."""
    if request.method == "GET":
        return jsonify({"appearance": seat_mixer_appearance()})
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Appearance settings must be an object"}), 400
    appearance = seat_mixer_appearance()
    for key in SEAT_MIXER_APPEARANCE_DEFAULTS:
        if key in payload:
            appearance[key] = payload[key]
    setting = db.session.get(Setting, SEAT_MIXER_APPEARANCE_KEY) or Setting(key=SEAT_MIXER_APPEARANCE_KEY)
    setting.value = json.dumps(appearance)
    db.session.add(setting)
    db.session.commit()
    audit("Seat Mixer", "Updated Seat Mixer appearance settings")
    return jsonify({"success": True, "appearance": appearance})


@seat_mixer_bp.route("/api/hall/<int:hall_id>/versions")
def api_versions(hall_id):
    """List all versions for a hall with their saved status."""
    hall = db.session.get(ExamHall, hall_id) or abort(404)

    ordered_versions = sorted(
        hall.versions,
        key=lambda item: (item.updated_at or item.created_at or datetime.min, item.id),
        reverse=True,
    )
    latest_id = ordered_versions[0].id if ordered_versions else None
    versions = []
    for v in hall.versions:
        has_saved = db.session.query(
            SeatMixerAssignment.query.filter_by(version_id=v.id).exists()
        ).scalar() or db.session.query(
            SeatMixerSaveSnapshot.query.filter_by(version_id=v.id).exists()
        ).scalar()

        versions.append({
            "id": v.id,
            "label": v.label,
            "version_number": v.version_number,
            "has_saved": has_saved,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
            "assignment_count": SeatMixerAssignment.query.filter_by(version_id=v.id).count(),
            "is_latest": v.id == latest_id,
        })

    return jsonify({
        "hall": {
            "id": hall.id,
            "name": hall.name,
            "start_time": hall.start_time.strftime("%Y-%m-%dT%H:%M") if hall.start_time else None,
            "end_time": hall.end_time.strftime("%Y-%m-%dT%H:%M") if hall.end_time else None,
            "is_expired": is_expired(hall),
        },
        "versions": versions,
    })


@seat_mixer_bp.route("/api/students")
def api_students():
    """Fetch real students for selected classes — scoped by current academic year.

    Also returns where each student is already assigned (if anywhere).
    """
    class_ids = request.args.getlist("class_ids", type=int)
    hall_id = request.args.get("hall_id", type=int)

    if not class_ids:
        return jsonify({"students": []})

    current_year = get_current_academic_year()
    if not current_year:
        return jsonify({"error": "No current academic year found"}), 400

    # Single query with eager loading — no N+1
    students = students_for_current_classes(current_year, class_ids)

    # The live roster remains the source of truth. Hall frequency is queried
    # separately against exam-scoped history for the currently selected hall.
    student_ids = [s.id for s in students]
    if not student_ids:
        return jsonify({"students": []})

    counts = hall_frequency_counts(student_ids, hall_id)
    existing = (
        db.session.query(
            SeatMixerAssignment.student_id,
            ExamHall.name.label("hall_name"),
            ExamHall.id.label("hall_id"),
            ExamHallVersion.label.label("version_label"),
            ExamHallVersion.id.label("version_id"),
        )
        .join(ExamHallVersion, SeatMixerAssignment.version_id == ExamHallVersion.id)
        .join(ExamHall, ExamHallVersion.exam_hall_id == ExamHall.id)
        .filter(SeatMixerAssignment.student_id.in_(student_ids))
        .all()
    )
    assignment_map = {}
    for row in existing:
        assignment_map[row.student_id] = {
            "hall_name": row.hall_name,
            "hall_id": row.hall_id,
            "version_label": row.version_label,
            "version_id": row.version_id,
        }

    student_data = [
        serialize_student(s, counts.get(s.id, 0), assignment_map.get(s.id), current_year.id)
        for s in students
    ]

    return jsonify({"students": student_data})


@seat_mixer_bp.route("/api/version/<int:version_id>/data")
def api_version_data(version_id):
    """Load the active saved revision (or a requested history preview)."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    hall = version.hall
    requested_snapshot_id = request.args.get("snapshot_id", type=int)
    current_id = current_snapshot_id(version_id)
    snapshot = None
    if requested_snapshot_id:
        snapshot = SeatMixerSaveSnapshot.query.filter_by(
            id=requested_snapshot_id,
            version_id=version_id,
        ).first() or abort(404)
    elif current_id:
        snapshot = SeatMixerSaveSnapshot.query.filter_by(
            id=current_id,
            version_id=version_id,
        ).first()
    if snapshot is None:
        snapshot = (
            SeatMixerSaveSnapshot.query
            .filter_by(version_id=version_id)
            .order_by(SeatMixerSaveSnapshot.created_at.desc(), SeatMixerSaveSnapshot.id.desc())
            .first()
        )

    selected_students = {}
    last_meta = "Loaded (saved)"
    if snapshot:
        saved_layout = snapshot_payload(snapshot)
        if saved_layout:
            config = saved_layout["config"]
            assignment_rows = saved_layout["assignments"]
            selected_students = saved_layout["selected_students"]
            last_meta = saved_layout["last_meta"]
        else:
            config = normalized_layout_config({})
            assignment_rows = []
    else:
        # Backward-compatible path for layouts saved before history snapshots.
        assignments = SeatMixerAssignment.query.filter_by(version_id=version_id).all()
        config = normalized_layout_config({
            "rows": assignments[0].rows_config if assignments else 3,
            "tablesPerRow": assignments[0].tables_per_row_config if assignments else 5,
            "seatsPerTable": assignments[0].seats_per_table_config if assignments else 2,
        })
        assignment_rows = [{
            "student_id": assignment.student_id,
            "row": assignment.row_number,
            "table": assignment.table_number,
            "seat": assignment.seat_number,
        } for assignment in assignments]

    config["classColors"] = version_class_colors(version_id)

    return jsonify({
        "version_id": version_id,
        "hall_id": hall.id,
        "hall_name": hall.name,
        "version_label": version.label,
        "is_expired": is_expired(hall),
        "config": config,
        "saved_data": serialize_saved_assignments(hall, assignment_rows),
        "selected_students": selected_students,
        "snapshot_id": snapshot.id if snapshot else None,
        "is_preview": bool(requested_snapshot_id and snapshot and snapshot.id != current_id),
        "last_meta": last_meta,
    })


@seat_mixer_bp.route("/api/version/<int:version_id>/class-colors", methods=["PATCH"])
def api_version_class_colors(version_id):
    """Persist one manual color override without changing the seating layout."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    if is_expired(version.hall):
        return jsonify({"error": "Cannot change an expired hall layout"}), 400

    payload = request.get_json(silent=True) or {}
    class_id = payload.get("class_id")
    color = payload.get("color")
    try:
        class_id = int(class_id)
    except (TypeError, ValueError):
        return jsonify({"error": "A valid class is required"}), 400
    if not db.session.get(AcademicClass, class_id):
        return jsonify({"error": "The selected class no longer exists"}), 404
    if not isinstance(color, str) or not HEX_COLOR_RE.fullmatch(color):
        return jsonify({"error": "Color must use #RRGGBB format"}), 400
    color = color.upper()

    selected_ids = payload.get("selected_class_ids", [])
    if not isinstance(selected_ids, list):
        selected_ids = []
    selected_ids = {str(item) for item in selected_ids if str(item).isdigit()}
    selected_ids.add(str(class_id))
    colors = version_class_colors(version_id)
    for other_id in selected_ids:
        if other_id == str(class_id):
            continue
        other_color = colors.get(other_id, get_class_color(int(other_id)))
        if other_color.upper() == color:
            return jsonify({"error": "That color is already assigned to another selected class"}), 409

    colors[str(class_id)] = color
    try:
        save_version_class_colors(version_id, colors)
        db.session.commit()
        audit("Seat Mixer", f"Updated class color for layout '{version.label}'")
        return jsonify({"success": True, "class_colors": colors})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@seat_mixer_bp.route("/api/save", methods=["POST"])
def api_save():
    """Create a new immutable save revision and make it the active layout."""
    data = request.get_json(silent=True) or {}
    version_id = data.get("version_id")
    assignments = data.get("assignments", [])
    config = data.get("config", {})

    if not version_id:
        return jsonify({"error": "Missing version_id"}), 400

    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    hall = version.hall

    # Don't allow saving to expired halls
    if is_expired(hall):
        return jsonify({"error": "Cannot save to an expired hall"}), 400

    try:
        normalized_config = normalized_layout_config(config)
        normalized_rows = normalized_assignments(assignments, normalized_config)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    selected_students = normalized_selected_students(data.get("selected_students", {}))
    last_meta = str(data.get("last_meta") or "Saved layout").strip()[:160] or "Saved layout"

    try:
        replace_active_assignments(version_id, normalized_rows, normalized_config)
        hall_exam = hall.exam
        hall_exam_type = hall.exam_type
        academic_year_id = (
            hall_exam.academic_year_id if hall_exam else
            hall_exam_type.academic_year_id if hall_exam_type else None
        )
        metrics = seat_mixer_metrics(normalized_rows, academic_year_id=academic_year_id)
        snapshot = SeatMixerSaveSnapshot(
            version_id=version_id,
            snapshot_json=json.dumps({
                "config": normalized_config,
                "assignments": normalized_rows,
                "selected_students": selected_students,
                "last_meta": last_meta,
            }, separators=(",", ":"), sort_keys=True),
            integrity_score=metrics["integrity_score"],
            near_adjacency_count=metrics["near_adjacency_count"],
            placed_count=metrics["placed_count"],
        )
        db.session.add(snapshot)
        db.session.flush()
        set_current_snapshot_id(version_id, snapshot.id)

        # Rolling history: preserve the newest ten revisions only.
        expired_snapshots = (
            SeatMixerSaveSnapshot.query
            .filter_by(version_id=version_id)
            .order_by(SeatMixerSaveSnapshot.created_at.desc(), SeatMixerSaveSnapshot.id.desc())
            .offset(SEAT_MIXER_HISTORY_LIMIT)
            .all()
        )
        for expired_snapshot in expired_snapshots:
            db.session.delete(expired_snapshot)
        db.session.commit()
        audit(
            "Seat Mixer",
            f"Saved layout revision for '{hall.name}' / '{version.label}' ({len(normalized_rows)} seats)",
        )
        history, _ = history_metadata(version_id)
        return jsonify({
            "success": True,
            "count": len(normalized_rows),
            "snapshot_id": snapshot.id,
            "metrics": metrics,
            "history": history,
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@seat_mixer_bp.route("/api/version/<int:version_id>/history")
def api_version_history(version_id):
    """List the rolling save history for one layout version."""
    db.session.get(ExamHallVersion, version_id) or abort(404)
    history, current_id = history_metadata(version_id)
    return jsonify({"history": history, "current_snapshot_id": current_id})


@seat_mixer_bp.route("/api/version/<int:version_id>/history/<int:snapshot_id>")
def api_history_snapshot(version_id, snapshot_id):
    """Return one historical layout in the same shape as the active builder data."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    snapshot = SeatMixerSaveSnapshot.query.filter_by(
        id=snapshot_id,
        version_id=version_id,
    ).first() or abort(404)
    layout = snapshot_payload(snapshot)
    if not layout:
        return jsonify({"error": "This saved layout is no longer valid"}), 422
    config = dict(layout["config"])
    config["classColors"] = version_class_colors(version_id)
    return jsonify({
        "version_id": version_id,
        "hall_id": version.exam_hall_id,
        "hall_name": version.hall.name,
        "version_label": version.label,
        "is_expired": is_expired(version.hall),
        "config": config,
        "saved_data": serialize_saved_assignments(version.hall, layout["assignments"]),
        "selected_students": layout["selected_students"],
        "snapshot_id": snapshot.id,
        "is_preview": snapshot.id != current_snapshot_id(version_id),
        "last_meta": layout["last_meta"],
    })


@seat_mixer_bp.route("/api/version/<int:version_id>/history/<int:snapshot_id>/restore", methods=["POST"])
def api_restore_history_snapshot(version_id, snapshot_id):
    """Make one saved revision the active layout without discarding history."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    if is_expired(version.hall):
        return jsonify({"error": "Cannot restore an expired hall layout"}), 400
    snapshot = SeatMixerSaveSnapshot.query.filter_by(
        id=snapshot_id,
        version_id=version_id,
    ).first() or abort(404)
    layout = snapshot_payload(snapshot)
    if not layout:
        return jsonify({"error": "This saved layout is no longer valid"}), 422
    try:
        restorable_rows = normalized_assignments(layout["assignments"], layout["config"])
        replace_active_assignments(version_id, restorable_rows, layout["config"])
        set_current_snapshot_id(version_id, snapshot.id)
        db.session.commit()
        audit("Seat Mixer", f"Restored save revision {snapshot.id} for '{version.hall.name}' / '{version.label}'")
        history, _ = history_metadata(version_id)
        return jsonify({"success": True, "snapshot_id": snapshot.id, "history": history})
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@seat_mixer_bp.route("/api/version/<int:version_id>/history/<int:snapshot_id>", methods=["DELETE"])
def api_delete_history_snapshot(version_id, snapshot_id):
    """Permanently remove one unneeded layout revision without touching others."""
    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    if not (request.get_json(silent=True) or {}).get("confirm"):
        return jsonify({"error": "Deletion must be explicitly confirmed"}), 400
    snapshot = SeatMixerSaveSnapshot.query.filter_by(
        id=snapshot_id,
        version_id=version_id,
    ).first() or abort(404)

    try:
        was_current = snapshot.id == current_snapshot_id(version_id)
        if was_current:
            replacement = (
                SeatMixerSaveSnapshot.query
                .filter(
                    SeatMixerSaveSnapshot.version_id == version_id,
                    SeatMixerSaveSnapshot.id != snapshot.id,
                )
                .order_by(SeatMixerSaveSnapshot.created_at.desc(), SeatMixerSaveSnapshot.id.desc())
                .first()
            )
            if replacement:
                layout = snapshot_payload(replacement)
                if not layout:
                    return jsonify({"error": "The replacement saved layout is no longer valid"}), 422
                replacement_rows = normalized_assignments(layout["assignments"], layout["config"])
                replace_active_assignments(version_id, replacement_rows, layout["config"])
                set_current_snapshot_id(version_id, replacement.id)
            else:
                SeatMixerAssignment.query.filter_by(version_id=version_id).delete(synchronize_session=False)
                clear_current_snapshot_id(version_id)

        db.session.delete(snapshot)
        db.session.commit()
        audit("Seat Mixer", f"Deleted save revision {snapshot_id} for '{version.hall.name}' / '{version.label}'")
        history, active_snapshot_id = history_metadata(version_id)
        return jsonify({
            "success": True,
            "history": history,
            "current_snapshot_id": active_snapshot_id,
        })
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500


@seat_mixer_bp.route("/api/class-students")
def api_class_students():
    """Get students from same class for replacement modal — single query."""
    class_id = request.args.get("class_id", type=int)
    version_id = request.args.get("version_id", type=int)
    hall_id = request.args.get("hall_id", type=int)
    current_student_id = request.args.get("current_student_id", type=int)

    if not class_id:
        return jsonify({"error": "Missing class_id"}), 400

    current_year = get_current_academic_year()
    if not current_year:
        return jsonify({"error": "No current academic year found"}), 400

    # Single query for students in this class
    students = students_for_current_classes(current_year, [class_id])

    # Single query for existing seat positions in this version
    seat_positions = {}
    if version_id:
        existing = (
            SeatMixerAssignment.query
            .filter_by(version_id=version_id)
            .all()
        )
        for sa in existing:
            seat_positions[sa.student_id] = {
                "row": sa.row_number,
                "table": sa.table_number,
                "seat": sa.seat_number,
            }

    student_ids = [s.id for s in students]
    assignment_counts = hall_frequency_counts(student_ids, hall_id)

    student_data = []
    for s in students:
        pos = seat_positions.get(s.id)
        if pos:
            position = {
                "row": pos["row"],
                "table": pos["table"],
                "seat": pos["seat"],
                "label": f"R{pos['row']+1} T{pos['table']+1} S{pos['seat']+1}",
            }
        else:
            position = {"row": None, "table": None, "seat": None, "label": "Not Assigned"}

        student_data.append({
            **serialize_student(s, assignment_counts.get(s.id, 0), academic_year_id=current_year.id),
            "position": position,
            "is_current": s.id == current_student_id,
        })

    return jsonify({"students": student_data})


@seat_mixer_bp.route("/print")
def print_arrangement():
    """Dedicated plain print/export view for one version."""
    version_id = request.args.get("version_id", type=int)
    orientation = request.args.get("orientation", "portrait").lower()
    if orientation not in {"portrait", "landscape"}:
        orientation = "landscape"

    if not version_id:
        flash("Missing version ID.", "danger")
        return redirect(url_for("seat_mixer.index"))

    version = db.session.get(ExamHallVersion, version_id) or abort(404)
    hall = version.hall

    # Load saved assignments with eager-loaded student data
    assignments = (
        SeatMixerAssignment.query
        .filter_by(version_id=version_id)
        .options(joinedload(SeatMixerAssignment.student))
        .order_by(
            SeatMixerAssignment.row_number,
            SeatMixerAssignment.table_number,
            SeatMixerAssignment.seat_number,
        )
        .all()
    )

    if not assignments:
        flash("No saved arrangement found for this version.", "warning")
        return redirect(url_for("seat_mixer.index"))

    rows = assignments[0].rows_config or 3
    tables_per_row = assignments[0].tables_per_row_config or 5
    seats_per_table = assignments[0].seats_per_table_config or 2
    seat_map = {(item.row_number, item.table_number, item.seat_number): item for item in assignments}
    print_rows = []
    for row_number in range(rows):
        tables = []
        for table_number in range(tables_per_row):
            tables.append({
                "row": row_number,
                "table": table_number,
                "seats": [seat_map.get((row_number, table_number, seat_number)) for seat_number in range(seats_per_table)],
            })
        print_rows.append(tables)
    hall_exam = hall.exam
    hall_exam_type = hall.exam_type
    academic_year_id = (
        hall_exam.academic_year_id if hall_exam else
        hall_exam_type.academic_year_id if hall_exam_type else None
    )
    print_placements = {}
    print_classes = {}
    class_counts = {}
    for assignment in assignments:
        student = assignment.student
        if student:
            placement = enrollment_placement_for_student(student, academic_year_id) or {}
            print_placements[student.id] = placement
            class_id = placement.get("academic_class_id") or student.academic_class_id
            class_name = placement.get("class_name") or (student.academic_class.name if student.academic_class else "")
            if class_id and class_name:
                print_classes.setdefault(class_id, class_name)
                class_counts[class_id] = class_counts.get(class_id, 0) + 1

    class_colors = version_class_colors(version_id)
    # Reuse the ID-card verification token/QR mechanism. Existing active
    # issues are preferred; missing issues are created through the same helper
    # used by ID Cards so every printed student receives a valid destination.
    from .routes_id_cards import get_or_create_issue
    student_qr = {}
    issues_created = False
    for assignment in assignments:
        student = assignment.student
        if not student or student.id in student_qr:
            continue
        issue = IdCardIssue.query.filter_by(
            student_id=student.id,
            academic_year_id=academic_year_id or student.academic_year_id,
            status="Active",
        ).first()
        if not issue:
            issue = get_or_create_issue(student, academic_year_id=academic_year_id)
            issues_created = True
        student_qr[str(student.id)] = id_card_qr_payload(issue)
    if issues_created:
        db.session.commit()

    academic_year_name = (
        hall_exam.academic_year.name if hall_exam and hall_exam.academic_year
        else hall_exam_type.academic_year.name if hall_exam_type and hall_exam_type.academic_year
        else ""
    )
    exam_name = hall_exam.name if hall_exam else hall_exam_type.name if hall_exam_type else ""
    logo_setting = db.session.get(Setting, "logo_path")
    return render_template(
        "admin/seat_mixer_print.html",
        hall=hall,
        version=version,
        assignments=assignments,
        print_rows=print_rows,
        print_classes=sorted(print_classes.items(), key=lambda item: item[1]),
        class_counts=class_counts,
        seats_per_table=seats_per_table,
        class_color=lambda class_id: class_colors.get(str(class_id), get_class_color(class_id)),
        photo_url=stored_photo_url,
        qr_payload=lambda student_id: student_qr.get(str(student_id)),
        placement_for_student=lambda student_id: print_placements.get(student_id, {}),
        school_name=get_school_name(),
        school_logo=stored_photo_url(logo_setting.value) if logo_setting and logo_setting.value else None,
        academic_year_name=academic_year_name,
        exam_name=exam_name,
        hall_start_time=hall.start_time,
        orientation=orientation,
    )
