"""Regression tests for year/level/exam-scoped full-mark defaults."""

import unittest

from app import create_app, db
from app.models import AcademicYear, AcademicYearLevel, Exam, ExamMarkingConfiguration, Subject
from app.services import calculate_score_totals, resolved_subject_maxima


class TestExamMarkingConfiguration(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "exam-marking-configuration-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2027-2028", is_current=True)
        self.other_year = AcademicYear(name="2028-2029", is_current=False)
        db.session.add_all([self.year, self.other_year])
        db.session.flush()
        self.level = AcademicYearLevel(academic_year_id=self.year.id, name="Secondary")
        self.other_level = AcademicYearLevel(academic_year_id=self.other_year.id, name="Secondary")
        db.session.add_all([self.level, self.other_level])
        db.session.flush()
        self.subjects = [
            Subject(name="SUBJECT 9", max_score=100),
            Subject(name="SUBJECT 22", max_score=100),
            Subject(name="SUBJECT 42", max_score=100),
            Subject(name="SUBJECT 100", max_score=100),
        ]
        db.session.add_all(self.subjects)
        self.exam_9 = Exam(name="Exam 9", academic_year_id=self.year.id)
        self.exam_22 = Exam(name="Exam 22", academic_year_id=self.year.id)
        self.exam_other_year = Exam(name="Exam Other Year", academic_year_id=self.other_year.id)
        db.session.add_all([self.exam_9, self.exam_22, self.exam_other_year])
        db.session.flush()
        db.session.add_all([
            ExamMarkingConfiguration(
                academic_year_id=self.year.id,
                academic_year_level_id=self.level.id,
                exam_id=self.exam_9.id,
                default_full_marks=9,
            ),
            ExamMarkingConfiguration(
                academic_year_id=self.year.id,
                academic_year_level_id=self.level.id,
                exam_id=self.exam_22.id,
                default_full_marks=22,
            ),
            ExamMarkingConfiguration(
                academic_year_id=self.other_year.id,
                academic_year_level_id=self.other_level.id,
                exam_id=self.exam_other_year.id,
                default_full_marks=42,
            ),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_resolver_uses_exact_exam_context_and_legacy_fallback(self):
        maxima_9 = resolved_subject_maxima(
            self.subjects,
            exam=self.exam_9,
            academic_year_level_id=self.level.id,
        )
        maxima_22 = resolved_subject_maxima(
            self.subjects,
            exam=self.exam_22,
            academic_year_level_id=self.level.id,
        )
        maxima_legacy = resolved_subject_maxima(
            self.subjects,
            exam=Exam(name="Unconfigured", academic_year_id=self.year.id),
            academic_year_level_id=self.level.id,
        )

        self.assertEqual(set(maxima_9.values()), {9})
        self.assertEqual(set(maxima_22.values()), {22})
        self.assertEqual(set(maxima_legacy.values()), {100})

    def test_same_exam_id_cannot_use_another_year_level_configuration(self):
        maxima = resolved_subject_maxima(
            self.subjects,
            exam=self.exam_other_year,
            academic_year_level_id=self.level.id,
        )
        self.assertEqual(set(maxima.values()), {100})

    def test_calculation_supports_9_22_42_and_100_marks(self):
        total, maximum, percentage = calculate_score_totals(
            [(9, 9), (22, 22), (42, 42), (100, 100)]
        )
        self.assertEqual(float(total), 173.0)
        self.assertEqual(float(maximum), 173.0)
        self.assertEqual(percentage, 100.0)


if __name__ == "__main__":
    unittest.main()
