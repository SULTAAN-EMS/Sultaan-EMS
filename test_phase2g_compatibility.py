import unittest

from app import create_app, db
from app.enrollment_service import (
    audit_student_enrollment_consistency,
    create_enrollment,
    resolve_student_academic_context,
    transition_student_enrollment,
)
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
)


class TestPhase2GCompatibility(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2g-test"
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

        self.year_a = AcademicYear(name="2025-2026", is_current=True)
        self.year_b = AcademicYear(name="2026-2027", is_current=False)
        self.level_a = AcademicLevel(name="Secondary", sort_order=1)
        self.level_b = AcademicLevel(name="Primary", sort_order=2)
        self.class_a = AcademicClass(academic_level=self.level_a, name="Form Four", sort_order=1)
        self.class_b = AcademicClass(academic_level=self.level_b, name="Class Eight", sort_order=1)
        db.session.add_all([
            self.year_a,
            self.year_b,
            self.level_a,
            self.level_b,
            self.class_a,
            self.class_b,
        ])
        db.session.flush()
        self.year_level_a = AcademicYearLevel(
            academic_year=self.year_a,
            legacy_level=self.level_a,
            name="Secondary",
        )
        self.year_level_b = AcademicYearLevel(
            academic_year=self.year_b,
            legacy_level=self.level_b,
            name="Primary",
        )
        self.year_class_a = AcademicYearClass(
            academic_year_level=self.year_level_a,
            legacy_class=self.class_a,
            name="Form Four",
        )
        self.year_class_b = AcademicYearClass(
            academic_year_level=self.year_level_b,
            legacy_class=self.class_b,
            name="Class Eight",
        )
        db.session.add_all([
            self.year_level_a,
            self.year_level_b,
            self.year_class_a,
            self.year_class_b,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_enrollment_is_authoritative_for_requested_historical_year(self):
        student = Student(
            student_code="P2G001",
            full_name="Historical Placement",
            academic_year_id=self.year_b.id,
            academic_level_id=self.level_b.id,
            academic_class_id=self.class_b.id,
            level=self.level_b.name,
        )
        db.session.add(student)
        db.session.flush()
        enrollment = create_enrollment(
            student.id,
            self.year_a.id,
            self.year_level_a.id,
            self.year_class_a.id,
        )
        db.session.commit()

        resolved = resolve_student_academic_context(student, self.year_a.id)
        self.assertEqual(resolved["context_status"], "enrollment")
        self.assertEqual(resolved["class_name"], "Form Four")
        self.assertEqual(resolved["enrollment"].id, enrollment.id)

    def test_legacy_fallback_is_explicit_and_read_only(self):
        student = Student(
            student_code="P2G002",
            full_name="Legacy Placement",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_a.id,
            academic_class_id=self.class_a.id,
            level=self.level_a.name,
        )
        db.session.add(student)
        db.session.commit()

        before = StudentEnrollment.query.filter_by(student_id=student.id).count()
        resolved = resolve_student_academic_context(student, self.year_a.id)
        after = StudentEnrollment.query.filter_by(student_id=student.id).count()

        self.assertEqual(resolved["context_status"], "legacy_compatible")
        self.assertEqual(resolved["class_name"], "Form Four")
        self.assertEqual(before, 0)
        self.assertEqual(after, 0)
        self.assertIsNone(resolve_student_academic_context(student, self.year_b.id))

    def test_mismatched_legacy_hierarchy_is_unresolved(self):
        student = Student(
            student_code="P2G003",
            full_name="Mismatched Legacy Placement",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_a.id,
            academic_class_id=self.class_b.id,
            level=self.level_a.name,
        )
        db.session.add(student)
        db.session.commit()

        resolved = resolve_student_academic_context(student, self.year_a.id)
        self.assertEqual(resolved["context_status"], "unresolved")
        self.assertIsNone(resolved["academic_year_level_id"])
        self.assertIsNone(resolved["academic_year_class_id"])
        self.assertIsNone(resolved["academic_class_id"])

    def test_consistency_audit_is_read_only_and_reports_disagreement(self):
        matching = Student(
            student_code="P2G004",
            full_name="Matching Enrollment",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_a.id,
            academic_class_id=self.class_a.id,
            level=self.level_a.name,
        )
        mismatch = Student(
            student_code="P2G005",
            full_name="Stale Snapshot",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_b.id,
            academic_class_id=self.class_b.id,
            level=self.level_b.name,
        )
        legacy_only = Student(
            student_code="P2G006",
            full_name="Unresolved Legacy",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_a.id,
            academic_class_id=999999,
            level=self.level_a.name,
        )
        db.session.add_all([matching, mismatch, legacy_only])
        db.session.flush()
        create_enrollment(matching.id, self.year_a.id, self.year_level_a.id, self.year_class_a.id)
        create_enrollment(mismatch.id, self.year_a.id, self.year_level_a.id, self.year_class_a.id)
        db.session.commit()

        before = StudentEnrollment.query.count()
        report = audit_student_enrollment_consistency()
        after = StudentEnrollment.query.count()

        self.assertEqual(report["students_with_enrollment"], 2)
        self.assertEqual(report["students_without_enrollment"], 1)
        self.assertEqual(report["legacy_placement_disagreements"], 1)
        self.assertEqual(report["legacy_only_records_requiring_review"], 1)
        self.assertEqual(report["repairs_performed"], 0)
        self.assertEqual(before, after)

    def test_cross_year_transition_preserves_history_and_syncs_legacy_snapshot(self):
        student = Student(
            student_code="P2G007",
            full_name="Transition Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level_a.id,
            academic_class_id=self.class_a.id,
            level=self.level_a.name,
        )
        db.session.add(student)
        db.session.flush()
        source = create_enrollment(
            student.id,
            self.year_a.id,
            self.year_level_a.id,
            self.year_class_a.id,
        )
        db.session.commit()

        _, destination = transition_student_enrollment(
            student.id,
            source.id,
            self.year_b.id,
            self.year_level_b.id,
            self.year_class_b.id,
            action="transfer",
        )
        db.session.commit()

        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 2)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(student_id=student.id).count(), 1)
        self.assertEqual(destination.previous_enrollment_id, source.id)
        self.assertEqual(student.academic_year_id, self.year_b.id)
        self.assertEqual(student.academic_level_id, self.level_b.id)
        self.assertEqual(student.academic_class_id, self.class_b.id)
        self.assertEqual(resolve_student_academic_context(student, self.year_a.id)["class_name"], "Form Four")
        self.assertEqual(resolve_student_academic_context(student, self.year_b.id)["class_name"], "Class Eight")


if __name__ == "__main__":
    unittest.main()
