"""Create a Books-only subject catalog with level and class applicability."""

import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import BookSubjectCatalog


VERSION = "phase_18_book_subject_catalog_v1"
INITIAL_SUBJECTS = {
    "Xanaano": [
        "Math", "Physics", "Chemistry", "Biology", "Juqraafi", "Taariikh",
        "Technology", "Luuqadda Carabiga", "Af Soomaali", "English",
        "English Films", "Tarbiyadda Islaamka", "Business",
    ],
    "Hoose": ["Xisaab", "Af Soomaali", "Tarbiyada Islaamka", "Luuqadda Carabiga", "Saynis", "Cilmi Bulsho", "English"],
    "Dhexe": ["Xisaab", "Af Soomaali", "Tarbiyada Islaamka", "Luuqadda Carabiga", "Saynis", "Cilmi Bulsho", "English", "English Films", "Teknooloji"],
    "Sare": [
        "Math", "Physics", "Chemistry", "Biology", "Juqraafi", "Taariikh",
        "Technology", "Luuqadda Carabiga", "Af Soomaali", "English",
        "English Films", "Tarbiyadda Islaamka", "Business",
    ],
}


def upgrade():
    app = create_app()
    with app.app_context():
        BookSubjectCatalog.__table__.create(bind=db.engine, checkfirst=True)
        added = 0
        for level, subjects in INITIAL_SUBJECTS.items():
            for name in subjects:
                if not BookSubjectCatalog.query.filter_by(level=level, name=name).first():
                    db.session.add(BookSubjectCatalog(level=level, name=name, class_names=[]))
                    added += 1
        db.session.commit()

        with db.engine.begin() as connection:
            if not inspect(connection).has_table("schema_migrations"):
                connection.execute(text(
                    "CREATE TABLE schema_migrations "
                    "(version VARCHAR(120) PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
                ))
            dialect = connection.dialect.name
            if dialect == "postgresql":
                connection.execute(text(
                    "INSERT INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                ), {"version": VERSION})
            elif dialect == "sqlite":
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
            else:
                connection.execute(text(
                    "INSERT IGNORE INTO schema_migrations (version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": VERSION})
        return {"status": "applied", "seeded_subjects": added}


if __name__ == "__main__":
    print(upgrade())
