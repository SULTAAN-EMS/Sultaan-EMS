import unittest

from app import create_app, db
from app.academic_hierarchy import students_for_year_scope_query
from app.enrollment_service import create_enrollment, student_enrollment_scope_query
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    Result,
    Student,
    Subject,
)
from app.services import result_payload


class TestPhase2FHistoricalCutover(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2f-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year_a = AcademicYear(name="2026-2027", is_current=True)
        self.year_b = AcademicYear(name="2027-2028", is_current=False)
        self.level_a = AcademicLevel(name="Secondary A", sort_order=1)
        self.level_b = AcademicLevel(name="Secondary B", sort_order=2)
        self.class_a = AcademicClass(academic_level=self.level_a, name="Form Four A", sort_order=1)
        self.class_b = AcademicClass(academic_level=self.level_b, name="Form Four B", sort_order=1)
        db.session.add_all([self.year_a, self.year_b, self.level_a, self.level_b, self.class_a, self.class_b])
        db.session.flush()
        self.year_level_a = AcademicYearLevel(academic_year=self.year_a, legacy_level=self.level_a, name="Secondary A")
        self.year_level_b = AcademicYearLevel(academic_year=self.year_b, legacy_level=self.level_a, name="Secondary A")
        self.year_class_a = AcademicYearClass(academic_year_level=self.year_level_a, legacy_class=self.class_a, name="Form Four A")
        self.year_class_b = AcademicYearClass(academic_year_level=self.year_level_b, legacy_class=self.class_a, name="Form Four A")
        db.session.add_all([self.year_level_a, self.year_level_b, self.year_class_a, self.year_class_b])
        db.session.flush()
        self.subject_a = Subject(name="Mathematics A", academic_level=self.level_a, max_score=100)
        self.subject_b = Subject(name="Mathematics B", academic_level=self.level_b, max_score=100)
        db.session.add_all([self.subject_a, self.subject_b])
        db.session.flush()
        db.session.add(AcademicYearSubject(
            academic_year=self.year_a,
            academic_year_level=self.year_level_a,
            legacy_subject=self.subject_a,
            name="Mathematics A",
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_selected_year_scope_does_not_use_newer_legacy_placement(self):
        student = Student(
            student_code="PHASE2F001",
            full_name="Historical Student",
            academic_year_id=self.year_b.id,
            academic_level_id=self.level_b.id,
            academic_class_id=self.class_b.id,
        )
        db.session.add(student)
        db.session.flush()
        create_enrollment(student.id, self.year_a.id, self.year_level_a.id, self.year_class_a.id)
        db.session.commit()

        self.assertEqual(
            students_for_year_scope_query(self.year_a.id, self.year_level_a.id, self.year_class_a.id).count(),
            1,
        )
        self.assertEqual(
            student_enrollment_scope_query(self.year_b.id, academic_year_level_id=self.year_level_b.id).count(),
            0,
        )

    def test_result_payload_uses_selected_enrollment_subject_scope(self):
        student = Student(
            student_code="PHASE2F002",
            full_name="Historical Result Student",
            academic_year_id=self.year_b.id,
            academic_level_id=self.level_b.id,
            academic_class_id=self.class_b.id,
        )
        exam = Exam(name="Midterm", academic_year=self.year_a, is_published=True, is_active=True)
        db.session.add_all([student, exam])
        db.session.flush()
        create_enrollment(student.id, self.year_a.id, self.year_level_a.id, self.year_class_a.id)
        db.session.add_all([
            Result(student_id=student.id, exam_id=exam.id, subject_id=self.subject_a.id, score=80, is_published=True),
            Result(student_id=student.id, exam_id=exam.id, subject_id=self.subject_b.id, score=95, is_published=True),
        ])
        db.session.commit()

        payload = result_payload(student, exam=exam, public_only=True)
        self.assertEqual([row["subject_id"] for row in payload["subjects"]], [self.subject_a.id])


if __name__ == "__main__":
    unittest.main()
