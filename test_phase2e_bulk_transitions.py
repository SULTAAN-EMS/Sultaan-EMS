from unittest import mock
import unittest

from app import create_app, db
from app.enrollment_service import (
    EnrollmentValidationError,
    create_enrollment,
    execute_bulk_transition,
    plan_bulk_transition,
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


class TestPhase2EBulkTransitions(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2e-test"
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

        self.year_a = AcademicYear(name="2026-2027", is_current=True)
        self.year_b = AcademicYear(name="2027-2028", is_current=False)
        self.level = AcademicLevel(name="Secondary", sort_order=1)
        self.form_four = AcademicClass(academic_level=self.level, name="Form Four", sort_order=1)
        self.form_five = AcademicClass(academic_level=self.level, name="Form Five", sort_order=2)
        db.session.add_all([self.year_a, self.year_b, self.level, self.form_four, self.form_five])
        db.session.flush()

        self.level_a = AcademicYearLevel(
            academic_year_id=self.year_a.id,
            legacy_level_id=self.level.id,
            name="Secondary",
            sort_order=1,
        )
        self.level_b = AcademicYearLevel(
            academic_year_id=self.year_b.id,
            legacy_level_id=self.level.id,
            name="Secondary",
            sort_order=1,
        )
        db.session.add_all([self.level_a, self.level_b])
        db.session.flush()
        self.class_a_four = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=self.form_four.id,
            name="Form Four",
            sort_order=1,
        )
        self.class_a_five = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=self.form_five.id,
            name="Form Five",
            sort_order=2,
        )
        self.class_b_four = AcademicYearClass(
            academic_year_level_id=self.level_b.id,
            legacy_class_id=self.form_four.id,
            name="Form Four",
            sort_order=1,
        )
        self.class_b_five = AcademicYearClass(
            academic_year_level_id=self.level_b.id,
            legacy_class_id=self.form_five.id,
            name="Form Five",
            sort_order=2,
        )
        db.session.add_all([
            self.class_a_four,
            self.class_a_five,
            self.class_b_four,
            self.class_b_five,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _student_with_source(self, code, *, outcome="pending", status="active"):
        student = Student(
            student_code=code,
            full_name=f"Student {code}",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level.id,
            academic_class_id=self.form_four.id,
            level=self.level.name,
        )
        db.session.add(student)
        db.session.flush()
        enrollment = create_enrollment(
            student.id,
            self.year_a.id,
            self.level_a.id,
            self.class_a_four.id,
            status=status,
            academic_outcome=outcome,
        )
        db.session.commit()
        return student, enrollment

    def _plan(self, destination_year, destination_level, destination_class, *, action, excluded=None):
        return plan_bulk_transition(
            self.year_a.id,
            self.level_a.id,
            self.class_a_four.id,
            destination_year.id,
            destination_level.id,
            destination_class.id,
            action=action,
            excluded_student_ids=excluded or [],
        )

    def test_whole_class_local_transfer_updates_existing_enrollments(self):
        first, first_source = self._student_with_source("P2E-LOCAL-1")
        second, second_source = self._student_with_source("P2E-LOCAL-2")

        plan = self._plan(self.year_a, self.level_a, self.class_a_five, action="local_transfer")
        self.assertEqual(sum(item["classification"] == "ELIGIBLE" for item in plan["items"]), 2)
        changed = execute_bulk_transition(plan, action="local_transfer")
        db.session.commit()

        self.assertEqual({item.id for item in changed}, {first_source.id, second_source.id})
        self.assertEqual(StudentEnrollment.query.filter_by(academic_year_id=self.year_a.id).count(), 2)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="local_transfer").count(), 2)
        self.assertEqual(db.session.get(StudentEnrollment, first_source.id).academic_year_class_id, self.class_a_five.id)
        self.assertEqual(db.session.get(StudentEnrollment, second_source.id).academic_year_class_id, self.class_a_five.id)
        self.assertEqual(first.academic_class_id, self.form_five.id)

    def test_whole_class_cross_year_transfer_preserves_source_and_links_destinations(self):
        first, first_source = self._student_with_source("P2E-TRANSFER-1")
        second, second_source = self._student_with_source("P2E-TRANSFER-2")

        plan = self._plan(self.year_b, self.level_b, self.class_b_four, action="transfer")
        created = execute_bulk_transition(plan, action="transfer")
        db.session.commit()

        self.assertEqual(len(created), 2)
        for student, source in ((first, first_source), (second, second_source)):
            destination = StudentEnrollment.query.filter_by(student_id=student.id, academic_year_id=self.year_b.id).one()
            self.assertEqual(destination.previous_enrollment_id, source.id)
            self.assertEqual(db.session.get(StudentEnrollment, source.id).status, "transferred")
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="cross_year_transfer").count(), 2)

    def test_whole_class_promotion_records_promotion_history(self):
        first, first_source = self._student_with_source("P2E-PROMOTE-1", outcome="passed", status="completed")
        second, second_source = self._student_with_source("P2E-PROMOTE-2", outcome="passed", status="completed")

        plan = self._plan(self.year_b, self.level_b, self.class_b_five, action="promotion")
        created = execute_bulk_transition(plan, action="promotion")
        db.session.commit()

        self.assertEqual(len(created), 2)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="promotion").count(), 2)
        for source in (first_source, second_source):
            destination = StudentEnrollment.query.filter_by(
                previous_enrollment_id=source.id,
                enrollment_source="promotion",
            ).one()
            self.assertEqual(destination.academic_year_id, self.year_b.id)
            self.assertEqual(db.session.get(StudentEnrollment, source.id).academic_outcome, "promoted")

    def test_whole_class_repeat_requires_failed_source_and_records_repeat_history(self):
        first, first_source = self._student_with_source("P2E-REPEAT-1", outcome="failed", status="completed")
        second, second_source = self._student_with_source("P2E-REPEAT-2", outcome="pending", status="active")

        plan = self._plan(self.year_b, self.level_b, self.class_b_four, action="repeat")
        self.assertEqual(sum(item["classification"] == "ELIGIBLE" for item in plan["items"]), 1)
        self.assertEqual(
            next(item for item in plan["items"] if item["student"].id == second.id)["reason"],
            "repeat_requires_failed",
        )
        created = execute_bulk_transition(plan, action="repeat")
        db.session.commit()

        self.assertEqual(len(created), 1)
        destination = StudentEnrollment.query.filter_by(student_id=first.id, academic_year_id=self.year_b.id).one()
        self.assertEqual(destination.previous_enrollment_id, first_source.id)
        self.assertEqual(destination.enrollment_source, "repeat")
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=second.id, academic_year_id=self.year_b.id).count(), 0)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="repeat").count(), 1)

    def test_duplicate_destination_is_skipped_before_execution(self):
        first, first_source = self._student_with_source("P2E-SKIP-1")
        second, second_source = self._student_with_source("P2E-SKIP-2")
        create_enrollment(
            first.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b_four.id,
            enrollment_source="transfer",
        )
        db.session.commit()

        plan = self._plan(self.year_b, self.level_b, self.class_b_four, action="transfer")
        skipped = next(item for item in plan["items"] if item["student"].id == first.id)
        self.assertEqual(skipped["classification"], "SKIPPED")
        self.assertEqual(skipped["reason"], "already_enrolled")
        self.assertEqual(sum(item["eligible"] for item in plan["items"]), 1)
        created = execute_bulk_transition(plan, action="transfer")
        db.session.commit()
        self.assertEqual(len(created), 1)
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=first.id).count(), 2)
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=second.id).count(), 2)

    def test_excluded_student_remains_unchanged_without_movement(self):
        excluded, excluded_source = self._student_with_source("P2E-EXCLUDE-1")
        included, included_source = self._student_with_source("P2E-EXCLUDE-2")

        plan = self._plan(
            self.year_a,
            self.level_a,
            self.class_a_five,
            action="local_transfer",
            excluded=[excluded.id],
        )
        excluded_item = next(item for item in plan["items"] if item["student"].id == excluded.id)
        self.assertEqual(excluded_item["classification"], "EXCLUDED")
        self.assertEqual(excluded_item["reason"], "Excluded by administrator")
        execute_bulk_transition(plan, action="local_transfer")
        db.session.commit()

        self.assertEqual(db.session.get(StudentEnrollment, excluded_source.id).academic_year_class_id, self.class_a_four.id)
        self.assertEqual(db.session.get(StudentEnrollment, included_source.id).academic_year_class_id, self.class_a_five.id)
        self.assertEqual(StudentEnrollmentMovement.query.count(), 1)

    def test_preview_is_read_only_for_legacy_only_students(self):
        legacy = Student(
            student_code="P2E-LEGACY",
            full_name="Legacy Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level.id,
            academic_class_id=self.form_four.id,
            level=self.level.name,
        )
        db.session.add(legacy)
        db.session.commit()
        before_enrollments = StudentEnrollment.query.count()
        before_movements = StudentEnrollmentMovement.query.count()
        before_class = legacy.academic_class_id

        plan = self._plan(self.year_a, self.level_a, self.class_a_five, action="local_transfer")

        item = next(item for item in plan["items"] if item["student"].id == legacy.id)
        self.assertEqual(item["classification"], "INVALID")
        self.assertEqual(StudentEnrollment.query.count(), before_enrollments)
        self.assertEqual(StudentEnrollmentMovement.query.count(), before_movements)
        self.assertEqual(db.session.get(Student, legacy.id).academic_class_id, before_class)

    def test_invalid_hierarchy_and_same_year_rules_are_rejected(self):
        self._student_with_source("P2E-INVALID")
        with self.assertRaises(EnrollmentValidationError):
            self._plan(self.year_a, self.level_b, self.class_b_four, action="transfer")
        with self.assertRaises(EnrollmentValidationError):
            self._plan(self.year_a, self.level_a, self.class_a_five, action="promotion")
        with self.assertRaises(EnrollmentValidationError):
            self._plan(self.year_b, self.level_b, self.class_b_four, action="local_transfer")

    def test_atomic_failure_rolls_back_all_students(self):
        first, first_source = self._student_with_source("P2E-ROLLBACK-1")
        second, second_source = self._student_with_source("P2E-ROLLBACK-2")
        plan = self._plan(self.year_a, self.level_a, self.class_a_five, action="local_transfer")

        with mock.patch(
            "app.enrollment_service._record_movement",
            side_effect=[None, RuntimeError("forced Phase 2E failure")],
        ):
            with self.assertRaises(RuntimeError):
                execute_bulk_transition(plan, action="local_transfer")
        db.session.rollback()

        self.assertEqual(db.session.get(StudentEnrollment, first_source.id).academic_year_class_id, self.class_a_four.id)
        self.assertEqual(db.session.get(StudentEnrollment, second_source.id).academic_year_class_id, self.class_a_four.id)
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)
        self.assertEqual(StudentEnrollment.query.filter_by(academic_year_class_id=self.class_a_five.id).count(), 0)


if __name__ == "__main__":
    unittest.main()
