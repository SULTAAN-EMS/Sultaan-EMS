"""Targeted regression tests for the two confirmed Phase 1 root causes."""

import sqlite3
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import create_engine, text

from migrations.phase_4a_exam_aware_promotion_rules import (
    _ensure_sqlite_promotion_rule_constraint,
)


class TestPhase2PromotionRootCauses(unittest.TestCase):
    def test_sqlite_rule_rebuild_preserves_ids_and_relationships(self):
        with NamedTemporaryFile(suffix=".db", delete=False) as handle:
            database_path = Path(handle.name)
        try:
            raw = sqlite3.connect(database_path)
            raw.executescript(
                """
                PRAGMA foreign_keys=OFF;
                CREATE TABLE academic_years (id INTEGER PRIMARY KEY);
                CREATE TABLE academic_year_levels (id INTEGER PRIMARY KEY);
                CREATE TABLE exams (id INTEGER PRIMARY KEY);
                CREATE TABLE academic_year_subjects (id INTEGER PRIMARY KEY);
                CREATE TABLE promotion_rules (
                    id INTEGER PRIMARY KEY,
                    academic_year_id INTEGER NOT NULL,
                    academic_year_level_id INTEGER NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    overall_pass_threshold NUMERIC(6, 3) NOT NULL,
                    critical_subject_pass_threshold NUMERIC(6, 3) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    exam_id INTEGER NULL,
                    CONSTRAINT uq_promotion_rule_year_level
                        UNIQUE (academic_year_id, academic_year_level_id)
                );
                CREATE TABLE promotion_rule_critical_subjects (
                    id INTEGER PRIMARY KEY,
                    promotion_rule_id INTEGER NOT NULL,
                    academic_year_subject_id INTEGER NOT NULL
                );
                INSERT INTO academic_years VALUES (1);
                INSERT INTO academic_year_levels VALUES (1);
                INSERT INTO exams VALUES (1), (2), (3);
                INSERT INTO academic_year_subjects VALUES (10), (11), (12);
                INSERT INTO promotion_rules VALUES
                    (7, 1, 1, 1, 50, 50, '2026-01-01', '2026-01-01', 1);
                INSERT INTO promotion_rule_critical_subjects VALUES (9, 7, 10);
                """
            )
            raw.commit()
            raw.close()

            engine = create_engine(f"sqlite:///{database_path}")
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                self.assertTrue(_ensure_sqlite_promotion_rule_constraint(connection))
                connection.commit()
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()

                schema = connection.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='promotion_rules'"
                    )
                ).scalar()
                self.assertIn(
                    "UNIQUE (academic_year_id, academic_year_level_id, exam_id)",
                    schema,
                )
                self.assertEqual(
                    connection.execute(
                        text(
                            "SELECT id, promotion_rule_id, academic_year_subject_id "
                            "FROM promotion_rule_critical_subjects"
                        )
                    ).all(),
                    [(9, 7, 10)],
                )
                connection.execute(
                    text(
                        "INSERT INTO promotion_rules "
                        "(id, academic_year_id, academic_year_level_id, exam_id, "
                        "is_active, overall_pass_threshold, critical_subject_pass_threshold, "
                        "created_at, updated_at) VALUES "
                        "(8, 1, 1, 2, 1, 50, 50, '2026-01-01', '2026-01-01')"
                    )
                )
                with self.assertRaises(Exception):
                    connection.execute(
                        text(
                            "INSERT INTO promotion_rules "
                            "(id, academic_year_id, academic_year_level_id, exam_id, "
                            "is_active, overall_pass_threshold, critical_subject_pass_threshold, "
                            "created_at, updated_at) VALUES "
                            "(9, 1, 1, 2, 1, 50, 50, '2026-01-01', '2026-01-01')"
                        )
                    )
                connection.rollback()
                self.assertFalse(_ensure_sqlite_promotion_rule_constraint(connection))
            engine.dispose()
        finally:
            database_path.unlink(missing_ok=True)

    def test_evaluation_template_preserves_action_before_loading_state(self):
        template = Path("app/templates/admin/promotion_rules_evaluate.html").read_text(
            encoding="utf-8"
        )
        action_write = template.index("actionField.value")
        disabled_write = template.index("submitter.disabled = true")
        self.assertLess(action_write, disabled_write)
        self.assertIn("actionField.name = 'action'", template)
        self.assertIn("submitter.value !== 'execute'", template)
        self.assertIn('name="action" value="preview"', template)
        self.assertIn('name="action" value="execute"', template)


if __name__ == "__main__":
    unittest.main()
