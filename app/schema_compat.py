from sqlalchemy import inspect, text

from . import db


def ensure_schema_compatibility():
    """Add missing production columns safely without dropping or rewriting data."""
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name

    add_column_if_missing("users", "permissions", column_sql(dialect, "permissions", "TEXT"))
    add_column_if_missing("users", "photo_path", column_sql(dialect, "photo_path", "VARCHAR(255)"))
    add_column_if_missing("students", "phone", column_sql(dialect, "phone", "VARCHAR(40)"))
    add_column_if_missing("students", "gender", column_sql(dialect, "gender", "VARCHAR(10)"))
    add_column_if_missing("students", "level", column_sql(dialect, "level", "VARCHAR(80)"))
    add_column_if_missing("students", "section", column_sql(dialect, "section", "VARCHAR(80)"))
    add_column_if_missing("results", "grade_override", column_sql(dialect, "grade_override", "VARCHAR(10)"))
    add_column_if_missing("results", "comment", column_sql(dialect, "comment", "VARCHAR(255)"))
    add_column_if_missing("incident_reports", "signature_data", column_sql(dialect, "signature_data", "TEXT"))
    add_column_if_missing("incident_reports", "other_description", column_sql(dialect, "other_description", "VARCHAR(500)"))
    add_column_if_missing("student_feedback", "delivered_at", column_sql(dialect, "delivered_at", "DATETIME"))
    add_column_if_missing("student_feedback", "read_at", column_sql(dialect, "read_at", "DATETIME"))
    add_column_if_missing("student_complaints", "delivered_at", column_sql(dialect, "delivered_at", "DATETIME"))
    add_column_if_missing("student_complaints", "read_at", column_sql(dialect, "read_at", "DATETIME"))
    # Phase 3C evaluation snapshots: keep the selected exam and an explicit
    # evaluation status on legacy Phase 3B databases.
    add_column_if_missing("promotion_evaluations", "exam_id", column_sql(dialect, "exam_id", "INTEGER"))
    add_column_if_missing(
        "promotion_evaluations",
        "evaluation_status",
        column_sql(dialect, "evaluation_status", "VARCHAR(20) NOT NULL DEFAULT 'EVALUATED'"),
    )
    add_index_if_missing("promotion_evaluations", "idx_promotion_evaluations_exam_id", ["exam_id"])
    add_index_if_missing("promotion_evaluations", "idx_promotion_evaluations_status", ["evaluation_status"])
    add_foreign_key_if_missing(
        "promotion_evaluations",
        "fk_promotion_evaluations_exam_id",
        ["exam_id"],
        "exams",
        ["id"],
        ondelete="SET NULL",
    )
    migrate_promotion_evaluation_schema()
    add_column_if_missing("exam_invigilators", "visible_password", column_sql(dialect, "visible_password", "VARCHAR(255)"))
    add_column_if_missing("exam_invigilators", "signature_data", column_sql(dialect, "signature_data", "TEXT"))
    widen_varchar_if_needed("results", "grade_override", 20)
    widen_varchar_if_needed("grade_scales", "grade", 20, nullable=False)
    add_column_if_missing("grade_scales", "grade_point", column_sql(dialect, "grade_point", "DECIMAL(6,3) NOT NULL DEFAULT 0"))
    widen_decimal_if_needed("results", "score", 8, 3)
    widen_decimal_if_needed("subjects", "max_score", 8, 3)
    widen_decimal_if_needed("grade_scales", "min_score", 8, 3)
    widen_decimal_if_needed("grade_scales", "max_score", 8, 3)
    widen_decimal_if_needed("grade_scales", "grade_point", 6, 3)
    add_column_if_missing("grade_scales", "is_pass", column_sql(dialect, "is_pass", "BOOLEAN NOT NULL DEFAULT TRUE"))
    add_column_if_missing("grade_scales", "badge_color", column_sql(dialect, "badge_color", "VARCHAR(20) NOT NULL DEFAULT '#10b981'"))
    add_column_if_missing("grade_scales", "text_color", column_sql(dialect, "text_color", "VARCHAR(20) NOT NULL DEFAULT '#ffffff'"))
    add_column_if_missing("grade_scales", "background_color", column_sql(dialect, "background_color", "VARCHAR(20) NOT NULL DEFAULT '#ecfdf5'"))
    add_column_if_missing("grade_scales", "border_color", column_sql(dialect, "border_color", "VARCHAR(20) NOT NULL DEFAULT '#10b981'"))
    add_column_if_missing("grade_scales", "sort_order", column_sql(dialect, "sort_order", "INTEGER NOT NULL DEFAULT 0"))
    add_column_if_missing("grade_scales", "is_active", column_sql(dialect, "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"))

    # Regression fix: rows inserted before is_active column was added may have
    # is_active=NULL. Set them to TRUE so grade lookups always find results.
    try:
        db.session.execute(text("UPDATE grade_scales SET is_active = 1 WHERE is_active IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Per-exam grade scales (exam_id IS NULL is the global fallback)
    add_column_if_missing("grade_scales", "exam_id", column_sql(dialect, "exam_id", "INTEGER"))
    add_index_if_missing("grade_scales", "idx_grade_scales_exam_id", ["exam_id"])
    add_index_if_missing("grade_scales", "idx_grade_scales_min_score", ["min_score"])
    add_index_if_missing("grade_scales", "idx_grade_scales_max_score", ["max_score"])
    add_index_if_missing("grade_scales", "idx_grade_scales_exam_range", ["exam_id", "min_score", "max_score"])
    add_foreign_key_if_missing(
        "grade_scales",
        "fk_grade_scales_exam_id",
        ["exam_id"],
        "exams",
        ["id"],
        ondelete="SET NULL",
    )
    
    # New academic structure columns
    add_column_if_missing("students", "academic_level_id", column_sql(dialect, "academic_level_id", "INTEGER"))
    add_column_if_missing("students", "academic_class_id", column_sql(dialect, "academic_class_id", "INTEGER"))
    add_column_if_missing("students", "academic_section_id", column_sql(dialect, "academic_section_id", "INTEGER"))
    add_column_if_missing("subjects", "academic_level_id", column_sql(dialect, "academic_level_id", "INTEGER"))
    add_column_if_missing("exams", "academic_level_id", column_sql(dialect, "academic_level_id", "INTEGER"))
    add_column_if_missing("exams", "academic_class_id", column_sql(dialect, "academic_class_id", "INTEGER"))
    add_column_if_missing("exams", "academic_section_id", column_sql(dialect, "academic_section_id", "INTEGER"))
    add_column_if_missing("attendance_records", "academic_level_id", column_sql(dialect, "academic_level_id", "INTEGER"))
    add_column_if_missing("attendance_records", "academic_class_id", column_sql(dialect, "academic_class_id", "INTEGER"))
    add_column_if_missing("attendance_records", "academic_section_id", column_sql(dialect, "academic_section_id", "INTEGER"))

    # Hall-roster / attendance phase columns
    add_column_if_missing("exam_halls", "exam_id", column_sql(dialect, "exam_id", "INTEGER"))
    add_column_if_missing("exam_halls", "exam_type_id", column_sql(dialect, "exam_type_id", "INTEGER"))
    add_column_if_missing("exam_halls", "academic_class_id", column_sql(dialect, "academic_class_id", "INTEGER"))
    add_column_if_missing("exam_halls", "academic_year_id", column_sql(dialect, "academic_year_id", "INTEGER"))
    add_column_if_missing("attendance_records", "exam_hall_id", column_sql(dialect, "exam_hall_id", "INTEGER"))
    add_column_if_missing("attendance_records", "subject_id", column_sql(dialect, "subject_id", "INTEGER"))
    add_column_if_missing("attendance_records", "exam_session_id", column_sql(dialect, "exam_session_id", "INTEGER"))
    add_column_if_missing("attendance_records", "exam_type_id", column_sql(dialect, "exam_type_id", "INTEGER"))
    add_column_if_missing("attendance_records", "status", column_sql(dialect, "status", "VARCHAR(50)"))
    add_column_if_missing("attendance_records", "recorded_at", column_sql(dialect, "recorded_at", "DATETIME"))
    add_index_if_missing("attendance_records", "idx_attendance_records_exam_session", ["exam_session_id"])
    add_index_if_missing(
        "attendance_records",
        "idx_attendance_records_session_hall_subject",
        ["exam_session_id", "exam_hall_id", "subject_id"],
    )
    add_foreign_key_if_missing(
        "attendance_records",
        "fk_attendance_records_exam_session_id",
        ["exam_session_id"],
        "exam_sessions",
        ["id"],
        ondelete="CASCADE",
    )
    migrate_attendance_session_unique_constraint()
    migrate_exam_schedule_subject_scope_constraint()

    # Update teacher_classes foreign key to reference academic_classes instead of school_classes
    # This requires manual migration for existing data

    # Catch-all: reconcile every remaining model column against the live schema.
    # This covers any column not hand-listed above (e.g. exams.short_code) so a
    # legacy production DB self-heals instead of raising "Unknown column ...".
    sync_all_model_columns()
    seed_legacy_student_genders()
    remove_obsolete_subject_short_name_settings()


def seed_legacy_student_genders():
    """Seed the existing demonstration students once without altering recorded values."""
    from .models import Setting, Student

    try:
        marker_key = "student_gender_backfill_v1"
        if db.session.get(Setting, marker_key):
            return

        missing_gender_students = (
            Student.query.filter(Student.gender.is_(None))
            .order_by(Student.id)
            .all()
        )
        for index, student in enumerate(missing_gender_students):
            # Existing records are sample data. A deterministic split makes the
            # local report preview meaningful without overwriting later choices.
            student.gender = "Male" if index % 2 == 0 else "Female"

        db.session.add(Setting(key=marker_key, value="completed"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def sync_all_model_columns():
    """Add any mapped model column that is missing from an already-existing table.

    ``db.create_all()`` only creates missing *tables*; it never alters existing
    ones. This walks every mapped table/column and issues idempotent, best-effort
    ``ALTER TABLE ... ADD COLUMN`` statements so new model columns appear on old
    databases automatically. Each statement is isolated so one failure can never
    brick startup.
    """
    inspector = inspect(db.engine)
    dialect = db.engine.dialect
    for table in db.metadata.sorted_tables:
        try:
            if not inspector.has_table(table.name):
                continue  # brand-new table; db.create_all() already handled it
            existing = {row["name"] for row in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                add_model_column(table, column, dialect)
        except Exception as e:
            # Handle connection errors during inspection (e.g., Railway MySQL timeout)
            # Log and continue to next table rather than failing startup
            print(f"Warning: Could not inspect table {table.name}: {e}")
            db.session.rollback()
            # Refresh inspector to handle potential connection loss
            inspector = inspect(db.engine)
            continue


def add_model_column(table, column, dialect):
    prep = dialect.identifier_preparer
    tbl = prep.format_table(table)
    col = prep.quote(column.name)
    try:
        type_sql = column.type.compile(dialect=dialect)
    except Exception:
        return  # unsupported/unknown type: skip rather than crash startup
    default_sql = _model_column_default_sql(column)

    # Prefer honoring NOT NULL + default; degrade to a nullable column so the
    # ALTER can't fail on a table that already holds rows.
    candidates = []
    if not column.nullable and default_sql is not None:
        candidates.append(f"{col} {type_sql} NOT NULL DEFAULT {default_sql}")
    if default_sql is not None:
        candidates.append(f"{col} {type_sql} NULL DEFAULT {default_sql}")
    candidates.append(f"{col} {type_sql} NULL")

    for body in candidates:
        try:
            db.session.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {body}"))
            db.session.commit()
            return
        except Exception:
            db.session.rollback()


def _model_column_default_sql(column):
    """Best-effort SQL literal for a column's default, or None if not expressible."""
    server_default = column.server_default
    if server_default is not None:
        arg = getattr(server_default, "arg", None)
        text_val = getattr(arg, "text", None)
        if text_val:
            return text_val
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
    return None


def add_column_if_missing(table, column, ddl):
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return
    existing = {row["name"] for row in inspector.get_columns(table)}
    if column in existing:
        return
    try:
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def add_index_if_missing(table, index_name, columns, unique=False):
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return
    indexes = inspector.get_indexes(table)
    unique_constraints = inspector.get_unique_constraints(table)
    names = {idx.get("name") for idx in indexes} | {item.get("name") for item in unique_constraints}
    covered = {tuple(idx.get("column_names") or []) for idx in indexes}
    covered |= {tuple(item.get("column_names") or []) for item in unique_constraints}
    # Skip if an index with this name exists or one already covers the same columns.
    if index_name in names or tuple(columns) in covered:
        return
    cols = ", ".join(columns)
    # An index is a performance optimization, not required for correctness.
    # Never let it brick startup (e.g. storage-engine quirks on legacy DBs).
    try:
        unique_sql = "UNIQUE " if unique else ""
        db.session.execute(text(f"CREATE {unique_sql}INDEX {index_name} ON {table} ({cols})"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def remove_obsolete_subject_short_name_settings():
    """Remove retired, non-relational display preferences once.

    Subject names now have one authoritative value: ``subjects.name``.  These
    old Settings entries only controlled display aliases, so removing them does
    not alter subjects, results, schedules, or any historical record.
    """
    from .models import Setting

    try:
        keys = ["display_subject_names"]
        Setting.query.filter(
            (Setting.key.in_(keys)) | (Setting.key.like("subject_short_name_%"))
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()


def migrate_promotion_evaluation_schema():
    """Make Phase 3C nullable outcomes safe on existing databases.

    Phase 3B required PASS/FAIL values.  Phase 3C must also retain an
    explicit INCOMPLETE/INVALID status without inventing a final outcome.
    PostgreSQL can alter this in place; SQLite receives a data-preserving
    table rebuild because it cannot drop old NOT NULL/check constraints.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("promotion_evaluations"):
        return
    columns = {item["name"]: item for item in inspector.get_columns("promotion_evaluations")}
    needs_nullable_outcomes = any(
        not columns.get(name, {}).get("nullable", True)
        for name in ("overall_percentage", "base_outcome", "final_outcome")
    )
    if not needs_nullable_outcomes:
        return
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        try:
            db.session.execute(text(
                "ALTER TABLE promotion_evaluations "
                "ALTER COLUMN overall_percentage DROP NOT NULL, "
                "ALTER COLUMN base_outcome DROP NOT NULL, "
                "ALTER COLUMN final_outcome DROP NOT NULL"
            ))
            constraints = db.session.execute(text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'promotion_evaluations'::regclass"
            ))
            names = {row[0] for row in constraints}
            for name in ("ck_promotion_evaluation_base_outcome", "ck_promotion_evaluation_final_outcome"):
                if name in names:
                    db.session.execute(text(f"ALTER TABLE promotion_evaluations DROP CONSTRAINT {name}"))
            db.session.execute(text(
                "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                "ck_promotion_evaluation_base_outcome CHECK "
                "(base_outcome IS NULL OR base_outcome IN ('PASS', 'FAIL'))"
            ))
            db.session.execute(text(
                "ALTER TABLE promotion_evaluations ADD CONSTRAINT "
                "ck_promotion_evaluation_final_outcome CHECK "
                "(final_outcome IS NULL OR final_outcome IN ('PASS', 'FAIL'))"
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
        return
    if dialect != "sqlite":
        return
    existing = set(columns)
    try:
        with db.engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            connection.execute(text(
                "CREATE TABLE promotion_evaluations_phase3c ("
                "id INTEGER PRIMARY KEY, "
                "student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT, "
                "student_enrollment_id INTEGER NOT NULL REFERENCES student_enrollments(id) ON DELETE RESTRICT, "
                "academic_year_id INTEGER NOT NULL REFERENCES academic_years(id) ON DELETE RESTRICT, "
                "academic_year_level_id INTEGER NOT NULL REFERENCES academic_year_levels(id) ON DELETE RESTRICT, "
                "exam_id INTEGER REFERENCES exams(id) ON DELETE SET NULL, "
                "promotion_rule_id INTEGER REFERENCES promotion_rules(id) ON DELETE SET NULL, "
                "promotion_rule_snapshot_json TEXT NOT NULL DEFAULT '{}', "
                "evaluation_context_json TEXT NOT NULL DEFAULT '{}', "
                "overall_percentage NUMERIC(8, 3), "
                "base_outcome VARCHAR(4), "
                "final_outcome VARCHAR(4), "
                "evaluation_status VARCHAR(20) NOT NULL DEFAULT 'EVALUATED', "
                "critical_subject_results_json TEXT NOT NULL DEFAULT '[]', "
                "override_reason VARCHAR(80), "
                "evaluated_at DATETIME NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "CONSTRAINT ck_promotion_evaluation_base_outcome CHECK "
                "(base_outcome IS NULL OR base_outcome IN ('PASS', 'FAIL')), "
                "CONSTRAINT ck_promotion_evaluation_final_outcome CHECK "
                "(final_outcome IS NULL OR final_outcome IN ('PASS', 'FAIL')), "
                "CONSTRAINT ck_promotion_evaluation_status CHECK "
                "(evaluation_status IN ('EVALUATED', 'INCOMPLETE', 'INVALID', 'NOT_EVALUATED'))"
                ")"
            ))
            status_expr = "evaluation_status" if "evaluation_status" in existing else "'EVALUATED'"
            exam_expr = "exam_id" if "exam_id" in existing else "NULL"
            connection.execute(text(
                "INSERT INTO promotion_evaluations_phase3c "
                "(id, student_id, student_enrollment_id, academic_year_id, "
                "academic_year_level_id, exam_id, promotion_rule_id, "
                "promotion_rule_snapshot_json, evaluation_context_json, "
                "overall_percentage, base_outcome, final_outcome, evaluation_status, "
                "critical_subject_results_json, override_reason, evaluated_at, created_at, updated_at) "
                "SELECT id, student_id, student_enrollment_id, academic_year_id, "
                f"academic_year_level_id, {exam_expr}, promotion_rule_id, "
                "promotion_rule_snapshot_json, evaluation_context_json, overall_percentage, "
                "base_outcome, final_outcome, " + status_expr + ", critical_subject_results_json, "
                "override_reason, evaluated_at, created_at, updated_at "
                "FROM promotion_evaluations"
            ))
            connection.execute(text("DROP TABLE promotion_evaluations"))
            connection.execute(text("ALTER TABLE promotion_evaluations_phase3c RENAME TO promotion_evaluations"))
            for name, columns_sql in {
                "ix_promotion_evaluations_student_id": "student_id",
                "ix_promotion_evaluations_student_enrollment_id": "student_enrollment_id",
                "ix_promotion_evaluations_academic_year_id": "academic_year_id",
                "ix_promotion_evaluations_academic_year_level_id": "academic_year_level_id",
                "ix_promotion_evaluations_exam_id": "exam_id",
                "ix_promotion_evaluations_evaluation_status": "evaluation_status",
                "ix_promotion_evaluations_evaluated_at": "evaluated_at",
            }.items():
                connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON promotion_evaluations ({columns_sql})"))
            connection.execute(text("PRAGMA foreign_keys=ON"))
    except Exception:
        db.session.rollback()


def migrate_attendance_session_unique_constraint():
    """Scope new attendance rows by scheduled sitting on MySQL deployments.

    Older installs used a unique key without ``exam_session_id``.  That key
    prevents the same student from sitting the same subject in two different
    scheduled sittings, which is no longer correct.  SQLite development DBs
    receive the current model definition on creation; existing SQLite files
    are intentionally left untouched because SQLite cannot safely alter a
    table-level unique constraint in place.
    """
    if db.engine.dialect.name != "mysql":
        return

    inspector = inspect(db.engine)
    if not inspector.has_table("attendance_records"):
        return

    try:
        unique_names = {
            item.get("name")
            for item in inspector.get_unique_constraints("attendance_records")
            if item.get("name")
        }
        index_names = {
            item.get("name")
            for item in inspector.get_indexes("attendance_records")
            if item.get("name") and item.get("unique")
        }
        legacy_name = "uq_student_hall_subject_attendance"
        if legacy_name in unique_names or legacy_name in index_names:
            db.session.execute(text(f"DROP INDEX {legacy_name} ON attendance_records"))
            db.session.commit()

        inspector = inspect(db.engine)
        names = {
            item.get("name")
            for item in inspector.get_unique_constraints("attendance_records")
            if item.get("name")
        }
        names.update(
            item.get("name")
            for item in inspector.get_indexes("attendance_records")
            if item.get("name") and item.get("unique")
        )
        if "uq_student_hall_subject_session_attendance" not in names:
            db.session.execute(text(
                "CREATE UNIQUE INDEX uq_student_hall_subject_session_attendance "
                "ON attendance_records (student_id, exam_hall_id, subject_id, exam_session_id)"
            ))
            db.session.commit()
    except Exception:
        db.session.rollback()


def migrate_exam_schedule_subject_scope_constraint():
    """Backfill and enforce one subject per exam scope and level.

    Existing duplicate schedules are retained for audit/history. The earliest
    assignment keeps the canonical key; later historical duplicates receive a
    suffix, allowing the new unique index to protect every future write.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("exam_session_subjects"):
        return
    add_column_if_missing(
        "exam_session_subjects",
        "exam_scope_key",
        column_sql(db.engine.dialect.name, "exam_scope_key", "VARCHAR(100)"),
    )
    from .attendance_rules import scheduled_subject_scope_key
    from .models import ExamSession, ExamSessionSubject

    try:
        assignments = (
            ExamSessionSubject.query
            .join(ExamSession, ExamSessionSubject.exam_session_id == ExamSession.id)
            .order_by(ExamSessionSubject.id)
            .all()
        )
        seen = set()
        changed = False
        for assignment in assignments:
            session = assignment.exam_session
            base_key = scheduled_subject_scope_key(
                session.academic_year_id,
                session.exam_id,
                session.exam_type_id,
            )
            pair = (base_key, assignment.academic_level_id, assignment.subject_id)
            desired_key = base_key if pair not in seen else f"{base_key}:historic-{assignment.id}"
            seen.add(pair)
            if assignment.exam_scope_key != desired_key:
                assignment.exam_scope_key = desired_key
                changed = True
        if changed:
            db.session.commit()
        add_index_if_missing(
            "exam_session_subjects",
            "uq_exam_scope_level_subject",
            ["exam_scope_key", "academic_level_id", "subject_id"],
            unique=True,
        )
    except Exception:
        db.session.rollback()


def add_foreign_key_if_missing(table, constraint_name, columns, ref_table, ref_columns, ondelete=None):
    # SQLite cannot add foreign keys via ALTER TABLE; only enforce on engines that support it.
    if db.engine.dialect.name == "sqlite":
        return
    inspector = inspect(db.engine)
    fks = inspector.get_foreign_keys(table)
    names = {fk.get("name") for fk in fks}
    covered = {tuple(fk.get("constrained_columns") or []) for fk in fks}
    # Skip if this constraint exists or another FK already covers the same columns.
    if constraint_name in names or tuple(columns) in covered:
        return
    cols = ", ".join(columns)
    ref_cols = ", ".join(ref_columns)
    ddl = (
        f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
        f"FOREIGN KEY ({cols}) REFERENCES {ref_table} ({ref_cols})"
    )
    if ondelete:
        ddl += f" ON DELETE {ondelete}"
    # The referential constraint is a safety net, not required for the column
    # to be queryable. Never let it brick startup (e.g. MyISAM/engine or
    # orphaned-data quirks on legacy production DBs).
    try:
        db.session.execute(text(ddl))
        db.session.commit()
    except Exception:
        db.session.rollback()


def column_sql(dialect, name, type_sql):
    if dialect == "sqlite":
        return f"{name} {type_sql}"
    return f"{name} {type_sql}"


def widen_varchar_if_needed(table, column, length, nullable=True):
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return
    existing = {row["name"]: row for row in inspector.get_columns(table)}
    row = existing.get(column)
    if not row:
        return
    current_length = getattr(row["type"], "length", None)
    if current_length and current_length >= length:
        return
    dialect = db.engine.dialect.name
    if dialect == "mysql":
        null_sql = "NULL" if nullable else "NOT NULL"
        db.session.execute(text(f"ALTER TABLE {table} MODIFY COLUMN {column} VARCHAR({length}) {null_sql}"))
        db.session.commit()


def widen_decimal_if_needed(table, column, precision, scale):
    """Widen numeric columns in-place without changing existing values.

    MySQL supports this metadata-only change for these decimal columns. SQLite
    keeps its existing affinity; new databases receive the model definition.
    """
    if db.engine.dialect.name != "mysql":
        return
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return
    row = {item["name"]: item for item in inspector.get_columns(table)}.get(column)
    if not row:
        return
    current = row.get("type")
    current_precision = getattr(current, "precision", None)
    current_scale = getattr(current, "scale", None)
    if current_precision and current_scale is not None and current_precision >= precision and current_scale >= scale:
        return
    nullable_sql = "NULL" if row.get("nullable", True) else "NOT NULL"
    try:
        db.session.execute(text(f"ALTER TABLE {table} MODIFY COLUMN {column} DECIMAL({precision},{scale}) {nullable_sql}"))
        db.session.commit()
    except Exception:
        db.session.rollback()
