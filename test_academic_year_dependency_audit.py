"""Regression tests for the Setup Academic Year dependency audit."""

import unittest

from app import create_app, db
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    ExamType,
    PromotionEvaluation,
    PromotionOutcomeApplication,
    PromotionRule,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
    Subject,
)
from app.routes_admin import _config_dependencies


class TestAcademicYearDependencyAudit(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "academic-year-dependency-audit"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2026-2027", is_current=False)
        self.legacy_level = AcademicLevel(name="Secondary")
        self.legacy_class = AcademicClass(name="Form Four", academic_level=self.legacy_level)
        self.legacy_subject = Subject(name="Mathematics", academic_level=self.legacy_level)
        db.session.add_all([self.year, self.legacy_level, self.legacy_class, self.legacy_subject])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year_id=self.year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
            is_active=False,
        )
        self.year_class = AcademicYearClass(
            academic_year_level=self.year_level,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
            is_active=False,
        )
        self.year_subject = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level=self.year_level,
            legacy_subject_id=self.legacy_subject.id,
            name="Mathematics",
            is_active=False,
        )
        db.session.add_all([self.year_level, self.year_class, self.year_subject])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_archived_setup_rows_are_real_dependencies(self):
        lines = _config_dependencies("academic-years", self.year.id)

        self.assertTrue(any(line.startswith("Academic Levels: 1") and "archived" in line for line in lines))
        self.assertTrue(any(line.startswith("Academic Classes: 1") and "archived" in line for line in lines))
        self.assertTrue(any(line.startswith("Academic Subjects: 1") and "archived" in line for line in lines))

    def test_promotion_and_historical_dependencies_are_reported(self):
        exam = Exam(name="Final", academic_year_id=self.year.id, is_final_evaluation=True)
        exam_type = ExamType(name="Final", academic_year_id=self.year.id, is_active=False)
        rule = PromotionRule(
            academic_year_id=self.year.id,
            academic_year_level_id=self.year_level.id,
            exam=exam,
            is_active=False,
        )
        student = Student(student_code="DEP001", full_name="Dependency Student", academic_year_id=self.year.id)
        db.session.add_all([exam, exam_type, rule, student])
        db.session.flush()
        enrollment = StudentEnrollment(
            student_id=student.id,
            academic_year_id=self.year.id,
            academic_year_level_id=self.year_level.id,
            academic_year_class_id=self.year_class.id,
            status="archived",
        )
        db.session.add(enrollment)
        db.session.flush()
        evaluation = PromotionEvaluation(
            student_id=student.id,
            student_enrollment_id=enrollment.id,
            academic_year_id=self.year.id,
            academic_year_level_id=self.year_level.id,
            exam_id=exam.id,
            promotion_rule_id=rule.id,
            base_outcome="PASS",
            final_outcome="PASS",
            evaluation_status="EVALUATED",
        )
        db.session.add(evaluation)
        db.session.flush()
        db.session.add(
            PromotionOutcomeApplication(
                promotion_evaluation_id=evaluation.id,
                student_id=student.id,
                source_enrollment_id=enrollment.id,
                applied_outcome="passed",
                application_status="APPLIED",
            )
        )
        db.session.commit()

        lines = _config_dependencies("academic-years", self.year.id)
        self.assertTrue(any(line.startswith("Promotion Rules: 1") for line in lines))
        self.assertTrue(any(line.startswith("Student Enrollments: 1") for line in lines))
        self.assertTrue(any(line.startswith("Promotion Evaluation History: 1") for line in lines))
        self.assertTrue(any(line.startswith("Promotion Outcome Applications: 1") for line in lines))
        self.assertTrue(any(line.startswith("Exams: 1") for line in lines))

    def test_enrollment_movement_is_reported_for_either_year_side(self):
        destination_year = AcademicYear(name="2027-2028", is_current=False)
        destination_level = AcademicYearLevel(academic_year=destination_year, name="Secondary")
        destination_class = AcademicYearClass(academic_year_level=destination_level, name="Form Four")
        student = Student(student_code="DEP002", full_name="Movement Student", academic_year_id=self.year.id)
        db.session.add_all([destination_year, destination_level, destination_class, student])
        db.session.flush()
        source = StudentEnrollment(
            student_id=student.id,
            academic_year_id=self.year.id,
            academic_year_level_id=self.year_level.id,
            academic_year_class_id=self.year_class.id,
        )
        destination = StudentEnrollment(
            student_id=student.id,
            academic_year_id=destination_year.id,
            academic_year_level_id=destination_level.id,
            academic_year_class_id=destination_class.id,
        )
        db.session.add_all([source, destination])
        db.session.flush()
        db.session.add(
            StudentEnrollmentMovement(
                student_id=student.id,
                enrollment_id=destination.id,
                movement_type="promotion",
                from_academic_year_id=self.year.id,
                from_academic_year_level_id=self.year_level.id,
                from_academic_year_class_id=self.year_class.id,
                to_academic_year_id=destination_year.id,
                to_academic_year_level_id=destination_level.id,
                to_academic_year_class_id=destination_class.id,
            )
        )
        db.session.commit()

        lines = _config_dependencies("academic-years", self.year.id)
        self.assertTrue(any(line.startswith("Enrollment Movements: 1") for line in lines))

    def test_archived_year_without_dependencies_has_no_false_blocker(self):
        db.session.delete(self.year_subject)
        db.session.delete(self.year_class)
        db.session.delete(self.year_level)
        db.session.commit()
        self.assertEqual(_config_dependencies("academic-years", self.year.id), [])

        # The normal delete path can now remove the archived year; no special
        # password, force flag, or alternate deletion workflow is involved.
        db.session.delete(self.year)
        db.session.commit()
        self.assertIsNone(db.session.get(AcademicYear, self.year.id))


if __name__ == "__main__":
    unittest.main()
