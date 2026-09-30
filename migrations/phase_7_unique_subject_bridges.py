"""Give every year-level examination subject its own legacy Result identity.

The Results table still points at ``subjects.id`` for compatibility.  A
renamed year-level subject could therefore accidentally keep the old bridge
and a newly-created subject could reuse it, making two visible columns write
to one Result row.  This migration separates those ambiguous bridges without
rewriting existing scores: the mapping whose legacy name already matches the
configured name keeps the old identity, while the other mapping receives a
new identity.
"""

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import AcademicYearLevel, AcademicYearSubject, Subject
from sqlalchemy import inspect, text


VERSION = "phase_7_unique_subject_bridges_v1"


def _normalized(value):
    return " ".join(str(value or "").strip().casefold().split())


def _record_migration(connection):
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    ))
    if connection.dialect.name == "sqlite":
        connection.execute(text(
            "INSERT OR IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})
    elif connection.dialect.name == "postgresql":
        connection.execute(text(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
        ), {"version": VERSION})
    else:
        connection.execute(text(
            "INSERT IGNORE INTO schema_migrations "
            "(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"
        ), {"version": VERSION})


def _mapped_ids_for_scope(year_id, year_level_id):
    return {
        item.legacy_subject_id
        for item in AcademicYearSubject.query.filter_by(
            academic_year_id=year_id,
            academic_year_level_id=year_level_id,
        ).all()
        if item.legacy_subject_id
    }


def _get_or_create_subject(item, year_level, mapped_ids):
    """Resolve a safe identity for one duplicate year subject."""
    level_id = year_level.legacy_level_id
    if level_id:
        exact = Subject.query.filter_by(
            name=item.name,
            academic_level_id=level_id,
        ).first()
        if exact and exact.id not in mapped_ids:
            return exact, False

    # A legacy database may enforce unique Subject.name globally. Reuse an
    # unclaimed same-name identity when it is not already used in this exact
    # year-level scope.
    same_name = Subject.query.filter_by(name=item.name).first()
    if same_name and same_name.id not in mapped_ids:
        return same_name, False

    subject = Subject(
        name=item.name,
        academic_level_id=level_id,
        max_score=item.max_score,
        sort_order=item.sort_order,
    )
    db.session.add(subject)
    db.session.flush()
    return subject, True


def upgrade():
    app = create_app()
    with app.app_context():
        existing = None
        if inspect(db.engine).has_table("schema_migrations"):
            existing = db.session.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = :version"),
                {"version": VERSION},
            ).first()
        if existing:
            return {"status": "already_applied", "repaired": 0, "created": 0}

        groups = defaultdict(list)
        items = AcademicYearSubject.query.filter(
            AcademicYearSubject.subject_kind == "exam",
            AcademicYearSubject.legacy_subject_id.isnot(None),
        ).order_by(
            AcademicYearSubject.academic_year_id,
            AcademicYearSubject.academic_year_level_id,
            AcademicYearSubject.sort_order,
            AcademicYearSubject.id,
        ).all()
        for item in items:
            groups[(item.academic_year_id, item.academic_year_level_id, item.legacy_subject_id)].append(item)

        repaired = 0
        created = 0
        for (_year_id, year_level_id, _legacy_id), duplicate_items in groups.items():
            if len(duplicate_items) < 2:
                continue
            year_level = db.session.get(AcademicYearLevel, year_level_id)
            if not year_level:
                continue

            legacy = db.session.get(Subject, duplicate_items[0].legacy_subject_id)
            keeper = next(
                (
                    item for item in duplicate_items
                    if legacy and _normalized(legacy.name) == _normalized(item.name)
                ),
                duplicate_items[0],
            )
            mapped_ids = _mapped_ids_for_scope(keeper.academic_year_id, year_level_id)
            for item in duplicate_items:
                if item.id == keeper.id:
                    continue
                replacement, was_created = _get_or_create_subject(item, year_level, mapped_ids)
                item.legacy_subject_id = replacement.id
                mapped_ids.add(replacement.id)
                repaired += 1
                created += int(was_created)

        db.session.commit()
        with db.engine.begin() as connection:
            _record_migration(connection)
        return {"status": "applied", "repaired": repaired, "created": created}


def main():
    argparse.ArgumentParser(description="Repair duplicate year-level subject bridges").parse_args()
    print(upgrade())


if __name__ == "__main__":
    main()
