"""Regression coverage for natural-score Grade Management ranges."""

import unittest
from decimal import Decimal

from app import create_app, db
from app.models import AcademicYear, AcademicYearLevel, Exam, ExamMarkingConfiguration, GradeScale
from app.services import grade_for, grade_scale_input_bounds, load_grade_scale_cache


class TestNaturalScoreGradeScales(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "natural-grade-scale"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        year = AcademicYear(name="2027-2028", is_current=True)
        db.session.add(year)
        db.session.flush()
        level = AcademicYearLevel(academic_year_id=year.id, name="Secondary")
        exam = Exam(name="First Monthly", academic_year_id=year.id)
        exam_20 = Exam(name="Third Monthly", academic_year_id=year.id)
        db.session.add_all([level, exam, exam_20])
        db.session.flush()
        db.session.add(ExamMarkingConfiguration(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            exam_id=exam.id,
            default_full_marks=10,
        ))
        db.session.add(ExamMarkingConfiguration(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            exam_id=exam_20.id,
            default_full_marks=20,
        ))
        self.exam = exam
        self.exam_20 = exam_20
        self.exam_id = exam.id
        self.exam_20_id = exam_20.id
        self.scales = [
            GradeScale(
                exam_id=exam.id,
                grade="A+",
                min_score=9.5,
                max_score=10,
                grade_point=4,
                comment="Outstanding",
                is_pass=True,
                sort_order=1,
            ),
            GradeScale(
                exam_id=exam.id,
                grade="B-",
                min_score=7,
                max_score=7.49,
                grade_point=3.19,
                comment="Average",
                is_pass=True,
                sort_order=2,
            ),
            GradeScale(
                exam_id=exam.id,
                grade="F",
                min_score=0,
                max_score=1.99,
                grade_point=0,
                comment="Fail",
                is_pass=False,
                sort_order=3,
            ),
        ]
        db.session.add_all(self.scales)
        db.session.add_all([
            GradeScale(
                exam_id=exam_20.id,
                grade="A+",
                min_score=19,
                max_score=20,
                grade_point=4,
                comment="Outstanding",
                is_pass=True,
                sort_order=1,
            ),
            GradeScale(
                exam_id=exam_20.id,
                grade="B-",
                min_score=14,
                max_score=14.99,
                grade_point=3.19,
                comment="Average",
                is_pass=True,
                sort_order=2,
            ),
            GradeScale(
                exam_id=exam_20.id,
                grade="F",
                min_score=0,
                max_score=3.99,
                grade_point=0,
                comment="Fail",
                is_pass=False,
                sort_order=3,
            ),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_natural_score_is_converted_before_grade_lookup(self):
        with self.app.test_request_context("/results"):
            grade = grade_for(70, exam_id=self.exam_id)

        self.assertEqual(grade["grade"], "B-")
        self.assertEqual(grade["grade_point"], 3.19)
        self.assertTrue(grade["is_pass"])

    def test_different_exam_full_marks_use_the_same_percentage_rule(self):
        with self.app.test_request_context("/results"):
            grade = grade_for(70, exam_id=self.exam_20_id)

        self.assertEqual(grade["grade"], "B-")
        self.assertEqual(grade["grade_point"], 3.19)

    def test_natural_ranges_are_shown_in_grade_management(self):
        cache = load_grade_scale_cache(self.exam_id)
        self.assertTrue(cache["natural_score_bands"])

        normalized_b_minus = next(
            row for row in cache["exam"] if row["payload"]["grade"] == "B-"
        )
        self.assertEqual(normalized_b_minus["min_score"], Decimal("70"))
        self.assertEqual(normalized_b_minus["max_score"], Decimal("74.9"))

        bounds = grade_scale_input_bounds(
            self.scales[0],
            self.scales,
            full_marks=10,
        )
        self.assertEqual(bounds["min"], 9.5)
        self.assertEqual(bounds["max"], 10)

    def test_percentage_scale_remains_compatible(self):
        db.session.query(GradeScale).delete()
        db.session.expunge_all()
        db.session.add_all([
            GradeScale(
                exam_id=self.exam_id,
                grade="B-",
                min_score=70,
                max_score=74.99,
                grade_point=3.19,
                comment="Average",
                is_pass=True,
                sort_order=1,
            ),
            GradeScale(
                exam_id=self.exam_id,
                grade="F",
                min_score=0,
                max_score=19.99,
                grade_point=0,
                comment="Fail",
                is_pass=False,
                sort_order=2,
            ),
        ])
        db.session.commit()

        with self.app.test_request_context("/results"):
            grade = grade_for(70, exam_id=self.exam_id)

        self.assertEqual(grade["grade"], "B-")
        self.assertFalse(load_grade_scale_cache(self.exam_id)["natural_score_bands"])


if __name__ == "__main__":
    unittest.main()
