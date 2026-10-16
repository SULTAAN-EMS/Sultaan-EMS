"""Create administrator-managed club teams, dated memberships and rotations."""

import argparse
import os
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app, db
from app.models import ClubTeam, ClubTeamLeaderAssignment, ClubTeamMembership, StudentClub


VERSION = "phase_12_club_leader_teams_v1"


def upgrade():
    app = create_app()
    with app.app_context():
        required = {"academic_years", "student_enrollments", "teachers", "users"}
        missing = required - set(inspect(db.engine).get_table_names())
        if missing:
            raise RuntimeError(f"Required tables are missing before migration: {', '.join(sorted(missing))}")

        for model in (StudentClub, ClubTeam, ClubTeamMembership, ClubTeamLeaderAssignment):
            model.__table__.create(bind=db.engine, checkfirst=True)

        default_clubs = (
            "Naadiga Dacwada iyo Thaqaafada Islaamka",
            "Naadiga Ganacsiga",
            "Naadiga Ciyaaraha iyo Jirdhiska",
            "Naadiga Fanka iyo Farshaxanka",
            "Naadiga Luqadda Ingiriisiga",
            "Naadiga Teknoolojiyadda iyo Saxaafadda",
            "Naadiga Doodaha iyo Hadal-jeedinta",
            "Naadiga Beeraha",
            "Naadiga Sayniska iyo Cilmi-baadhista",
        )
        existing_clubs = {
            name.lower() for (name,) in db.session.query(StudentClub.name).all()
        }
        for name in default_clubs:
            if name.lower() not in existing_clubs:
                db.session.add(StudentClub(name=name))
        db.session.commit()

        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
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

        return {"status": "applied", "tables": 4}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(upgrade())
