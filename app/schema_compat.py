from sqlalchemy import inspect, text

from . import db


def ensure_schema_compatibility():
    """Add missing production columns safely without dropping or rewriting data."""
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name

    add_column_if_missing("users", "permissions", column_sql(dialect, "permissions", "TEXT"))
    add_column_if_missing("users", "photo_path", column_sql(dialect, "photo_path", "VARCHAR(255)"))
    add_column_if_missing("students", "phone", column_sql(dialect, "phone", "VARCHAR(40)"))
    add_column_if_missing("students", "level", column_sql(dialect, "level", "VARCHAR(80)"))
    add_column_if_missing("students", "section", column_sql(dialect, "section", "VARCHAR(80)"))
    add_column_if_missing("results", "grade_override", column_sql(dialect, "grade_override", "VARCHAR(10)"))
    add_column_if_missing("results", "comment", column_sql(dialect, "comment", "VARCHAR(255)"))
    add_column_if_missing("incident_reports", "signature_data", column_sql(dialect, "signature_data", "TEXT"))
    add_column_if_missing("incident_reports", "other_description", column_sql(dialect, "other_description", "VARCHAR(500)"))
    add_column_if_missing("exam_invigilators", "visible_password", column_sql(dialect, "visible_password", "VARCHAR(255)"))
    add_column_if_missing("exam_invigilators", "signature_data", column_sql(dialect, "signature_data", "TEXT"))
    widen_varchar_if_needed("results", "grade_override", 20)
    widen_varchar_if_needed("grade_scales", "grade", 20, nullable=False)
    add_column_if_missing("grade_scales", "grade_point", column_sql(dialect, "grade_point", "DECIMAL(4,2) NOT NULL DEFAULT 0"))
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
    
    # Update teacher_classes foreign key to reference academic_classes instead of school_classes
    # This requires manual migration for existing data

    # Catch-all: reconcile every remaining model column against the live schema.
    # This covers any column not hand-listed above (e.g. exams.short_code) so a
    # legacy production DB self-heals instead of raising "Unknown column ...".
    sync_all_model_columns()


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
    existing = {row["name"] for row in inspector.get_columns(table)}
    if column in existing:
        return
    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
    db.session.commit()


def add_index_if_missing(table, index_name, columns):
    inspector = inspect(db.engine)
    indexes = inspector.get_indexes(table)
    names = {idx.get("name") for idx in indexes}
    covered = {tuple(idx.get("column_names") or []) for idx in indexes}
    # Skip if an index with this name exists or one already covers the same columns.
    if index_name in names or tuple(columns) in covered:
        return
    cols = ", ".join(columns)
    # An index is a performance optimization, not required for correctness.
    # Never let it brick startup (e.g. storage-engine quirks on legacy DBs).
    try:
        db.session.execute(text(f"CREATE INDEX {index_name} ON {table} ({cols})"))
        db.session.commit()
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
