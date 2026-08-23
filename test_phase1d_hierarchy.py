import unittest
from datetime import datetime

from app import create_app, db
from app.academic_hierarchy import (
    backfill_year_hierarchy,
    students_for_year_scope_query,
    validate_year_level,
    year_classes,
    year_levels,
    year_subjects,
)
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


class TestPhase1DHierarchy(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-1d-test"
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
        self.legacy_level = AcademicLevel(name="Secondary Level", sort_order=1)
        db.session.add_all([self.year_a, self.year_b, self.legacy_level])
        db.session.flush()
        self.level_a = AcademicYearLevel(
            academic_year_id=self.year_a.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary Level",
            sort_order=1,
        )
        self.level_b = AcademicYearLevel(
            academic_year_id=self.year_b.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary Level",
            sort_order=1,
        )
        db.session.add_all([self.level_a, self.level_b])
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_same_level_name_is_allowed_per_year(self):
        self.assertNotEqual(self.level_a.id, self.level_b.id)
        self.assertEqual(validate_year_level(self.year_a.id, self.level_a.id).id, self.level_a.id)
        self.assertIsNone(validate_year_level(self.year_a.id, self.level_b.id))

    def test_setup_isolation(self):
        self.assertEqual([item.id for item in year_levels(self.year_a.id)], [self.level_a.id])
        self.assertEqual([item.id for item in year_levels(self.year_b.id)], [self.level_b.id])

    def test_class_isolation(self):
        class_a = AcademicYearClass(academic_year_level_id=self.level_a.id, name="Form Four", sort_order=1)
        class_b = AcademicYearClass(academic_year_level_id=self.level_b.id, name="Form Four", sort_order=1)
        db.session.add_all([class_a, class_b])
        db.session.commit()
        self.assertEqual([item.id for item in year_classes(self.level_a.id)], [class_a.id])
        self.assertEqual([item.id for item in year_classes(self.level_b.id)], [class_b.id])

    def test_subject_isolation(self):
        subject_a = AcademicYearSubject(academic_year_id=self.year_a.id, academic_year_level_id=self.level_a.id, name="Mathematics")
        subject_b = AcademicYearSubject(academic_year_id=self.year_b.id, academic_year_level_id=self.level_b.id, name="Mathematics")
        db.session.add_all([subject_a, subject_b])
        db.session.commit()
        self.assertEqual([item.id for item in year_subjects(self.year_a.id, self.level_a.id)], [subject_a.id])
        self.assertEqual([item.id for item in year_subjects(self.year_b.id, self.level_b.id)], [subject_b.id])

    def test_invalid_relationship_rejection(self):
        self.assertIsNone(validate_year_level(self.year_a.id, self.level_b.id))

    def test_analytics_metadata_isolation(self):
        subject_a = AcademicYearSubject(academic_year_id=self.year_a.id, academic_year_level_id=self.level_a.id, name="Mathematics")
        subject_b = AcademicYearSubject(academic_year_id=self.year_b.id, academic_year_level_id=self.level_b.id, name="Physics")
        db.session.add_all([subject_a, subject_b])
        db.session.commit()
        self.assertEqual([item.name for item in year_subjects(self.year_a.id)], ["Mathematics"])

    def test_analytics_historical_result_isolation(self):
        subject = Subject(academic_level_id=self.legacy_level.id, name="Mathematics")
        student_a = Student(student_code="A001", full_name="Year A Student", academic_year_id=self.year_a.id, academic_level_id=self.legacy_level.id)
        student_b = Student(student_code="B001", full_name="Year B Student", academic_year_id=self.year_b.id, academic_level_id=self.legacy_level.id)
        exam_a = Exam(name="Midterm A", academic_year_id=self.year_a.id)
        exam_b = Exam(name="Midterm B", academic_year_id=self.year_b.id)
        db.session.add_all([subject, student_a, student_b, exam_a, exam_b])
        db.session.flush()
        db.session.add_all([
            Result(student_id=student_a.id, exam_id=exam_a.id, subject_id=subject.id, score=80),
            Result(student_id=student_b.id, exam_id=exam_b.id, subject_id=subject.id, score=90),
        ])
        db.session.commit()
        results_a = Result.query.filter_by(exam_id=exam_a.id).all()
        self.assertEqual([item.student_id for item in results_a], [student_a.id])

    def test_legacy_regression(self):
        subject = Subject(academic_level_id=self.legacy_level.id, name="English")
        student = Student(student_code="LEG001", full_name="Legacy Student", academic_year_id=self.year_a.id, academic_level_id=self.legacy_level.id)
        exam = Exam(name="Legacy Exam", academic_year_id=self.year_a.id)
        db.session.add_all([subject, student, exam])
        db.session.flush()
        result = Result(student_id=student.id, exam_id=exam.id, subject_id=subject.id, score=75)
        db.session.add(result)
        db.session.commit()
        self.assertEqual(Result.query.get(result.id).score, 75)

    def test_cross_level_exception_is_preserved(self):
        level_two = AcademicLevel(name="Upper Primary", sort_order=2)
        subject = Subject(academic_level_id=level_two.id if level_two.id else None, name="AF-SOOMAALI")
        student = Student(student_code="X001", full_name="Exception Student", academic_year_id=self.year_a.id, academic_level_id=self.legacy_level.id)
        exam = Exam(name="Exception Exam", academic_year_id=self.year_a.id)
        db.session.add(level_two)
        db.session.flush()
        subject.academic_level_id = level_two.id
        db.session.add_all([subject, student, exam])
        db.session.flush()
        result = Result(student_id=student.id, exam_id=exam.id, subject_id=subject.id, score=50)
        db.session.add(result)
        db.session.commit()
        report = backfill_year_hierarchy()
        self.assertTrue(any(item["result_id"] == result.id for item in report["cross_level_results"]))
        self.assertIsNotNone(db.session.get(Result, result.id))

    def test_class_and_subject_are_year_isolated(self):
        legacy_class = AcademicClass(
            academic_level_id=self.legacy_level.id,
            name="Form Four",
            sort_order=1,
        )
        legacy_subject = Subject(
            academic_level_id=self.legacy_level.id,
            name="Mathematics",
            max_score=100,
            sort_order=1,
        )
        db.session.add_all([legacy_class, legacy_subject])
        db.session.flush()
        class_a = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=legacy_class.id,
            name="Form Four",
            sort_order=1,
        )
        class_b = AcademicYearClass(
            academic_year_level_id=self.level_b.id,
            legacy_class_id=legacy_class.id,
            name="Form Four",
            sort_order=1,
        )
        subject_a = AcademicYearSubject(
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            legacy_subject_id=legacy_subject.id,
            name="Mathematics",
            max_score=100,
            sort_order=1,
        )
        subject_b = AcademicYearSubject(
            academic_year_id=self.year_b.id,
            academic_year_level_id=self.level_b.id,
            legacy_subject_id=legacy_subject.id,
            name="Mathematics",
            max_score=100,
            sort_order=1,
        )
        db.session.add_all([class_a, class_b, subject_a, subject_b])
        db.session.commit()

        self.assertEqual([item.id for item in year_subjects(self.year_a.id, self.level_a.id)], [subject_a.id])
        self.assertEqual([item.id for item in year_subjects(self.year_b.id, self.level_b.id)], [subject_b.id])

    def test_student_scope_and_legacy_result_bridge_remain_readable(self):
        legacy_class = AcademicClass(
            academic_level_id=self.legacy_level.id,
            name="Form Four",
            sort_order=1,
        )
        legacy_subject = Subject(
            academic_level_id=self.legacy_level.id,
            name="Mathematics",
            max_score=100,
            sort_order=1,
        )
        db.session.add_all([legacy_class, legacy_subject])
        db.session.flush()
        year_class = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=legacy_class.id,
            name="Form Four",
            sort_order=1,
        )
        year_subject = AcademicYearSubject(
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            legacy_subject_id=legacy_subject.id,
            name="Mathematics",
            max_score=100,
            sort_order=1,
        )
        exam = Exam(name="Midterm", academic_year_id=self.year_a.id)
        student = Student(
            student_code="TIS001",
            full_name="Test Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.legacy_level.id,
            academic_class_id=legacy_class.id,
        )
        db.session.add_all([year_class, year_subject, exam, student])
        db.session.flush()
        db.session.add(Result(student_id=student.id, exam_id=exam.id, subject_id=legacy_subject.id, score=88))
        db.session.commit()

        scoped = students_for_year_scope_query(self.year_a.id, year_class_id=year_class.id).all()
        self.assertEqual([item.student_code for item in scoped], ["TIS001"])
        self.assertEqual(Result.query.filter_by(subject_id=year_subject.legacy_subject_id).count(), 1)


if __name__ == "__main__":
    unittest.main()
