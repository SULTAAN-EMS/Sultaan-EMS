import unittest

from app import create_app, db
from app.enrollment_service import (
    EnrollmentValidationError,
    create_enrollment,
    execute_bulk_transition,
    plan_bulk_transition,
    transition_student_enrollment,
)
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicSection,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
)


class TestPhase2DTransitions(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2d-test"
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

        self.section = AcademicSection(academic_class_id=self.form_four.id, name="A", sort_order=1)
        self.level_a = AcademicYearLevel(academic_year_id=self.year_a.id, legacy_level_id=self.level.id, name="Secondary", sort_order=1)
        self.level_b = AcademicYearLevel(academic_year_id=self.year_b.id, legacy_level_id=self.level.id, name="Secondary", sort_order=1)
        db.session.add_all([self.section, self.level_a, self.level_b])
        db.session.flush()
        self.class_a_four = AcademicYearClass(academic_year_level_id=self.level_a.id, legacy_class_id=self.form_four.id, name="Form Four", sort_order=1)
        self.class_a_five = AcademicYearClass(academic_year_level_id=self.level_a.id, legacy_class_id=self.form_five.id, name="Form Five", sort_order=2)
        self.class_b_four = AcademicYearClass(academic_year_level_id=self.level_b.id, legacy_class_id=self.form_four.id, name="Form Four", sort_order=1)
        self.class_b_five = AcademicYearClass(academic_year_level_id=self.level_b.id, legacy_class_id=self.form_five.id, name="Form Five", sort_order=2)
        db.session.add_all([self.class_a_four, self.class_a_five, self.class_b_four, self.class_b_five])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _student_with_source(self, code="P2D001", *, outcome="pending", status="active"):
        student = Student(
            student_code=code,
            full_name="Phase Two D Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level.id,
            academic_class_id=self.form_four.id,
            academic_section_id=self.section.id,
            level=self.level.name,
            section=self.section.name,
        )
        db.session.add(student)
        db.session.flush()
        enrollment = create_enrollment(
            student.id,
            self.year_a.id,
            self.level_a.id,
            self.class_a_four.id,
            self.section.id,
            status=status,
            academic_outcome=outcome,
        )
        db.session.commit()
        return student, enrollment

    def test_local_transfer_updates_one_enrollment_and_records_history(self):
        student, source = self._student_with_source()
        old_id = source.id

        _, destination = transition_student_enrollment(
            student.id,
            source.id,
            self.year_a.id,
            self.level_a.id,
            self.class_a_five.id,
            action="local_transfer",
        )
        db.session.commit()

        self.assertEqual(destination.id, old_id)
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 1)
        self.assertEqual(destination.academic_year_class_id, self.class_a_five.id)
        movement = StudentEnrollmentMovement.query.filter_by(student_id=student.id).one()
        self.assertEqual(movement.movement_type, "local_transfer")
        self.assertEqual(movement.enrollment_id, old_id)
        self.assertEqual(movement.from_academic_year_class_id, self.class_a_four.id)
        self.assertEqual(movement.to_academic_year_class_id, self.class_a_five.id)

    def test_cross_year_transfer_creates_linked_enrollment(self):
        student, source = self._student_with_source("P2D002")
        _, destination = transition_student_enrollment(
            student.id,
            source.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b_four.id,
            action="transfer",
        )
        db.session.commit()

        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 2)
        self.assertEqual(db.session.get(StudentEnrollment, source.id).status, "transferred")
        self.assertEqual(destination.previous_enrollment_id, source.id)
        movement = StudentEnrollmentMovement.query.filter_by(student_id=student.id).one()
        self.assertEqual(movement.movement_type, "cross_year_transfer")

    def test_promotion_creates_new_year_enrollment(self):
        student, source = self._student_with_source("P2D003", status="completed")
        _, destination = transition_student_enrollment(
            student.id,
            source.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b_five.id,
            action="promotion",
        )
        db.session.commit()

        self.assertEqual(db.session.get(StudentEnrollment, source.id).academic_outcome, "promoted")
        self.assertEqual(destination.enrollment_source, "promotion")
        self.assertEqual(destination.previous_enrollment_id, source.id)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="promotion").count(), 1)

    def test_repeat_requires_failed_source_and_creates_new_year_enrollment(self):
        student, source = self._student_with_source("P2D004", outcome="failed", status="completed")
        _, destination = transition_student_enrollment(
            student.id,
            source.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b_four.id,
            action="repeat",
        )
        db.session.commit()

        self.assertEqual(db.session.get(StudentEnrollment, source.id).academic_outcome, "repeated")
        self.assertEqual(destination.enrollment_source, "repeat")
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="repeat").count(), 1)

    def test_bulk_local_transfer_updates_each_existing_enrollment(self):
        first, first_source = self._student_with_source("P2D-BULK-1")
        second, second_source = self._student_with_source("P2D-BULK-2")
        plan = plan_bulk_transition(
            self.year_a.id,
            self.level_a.id,
            self.class_a_four.id,
            self.year_a.id,
            self.level_a.id,
            self.class_a_five.id,
            action="local_transfer",
        )
        self.assertEqual(sum(item["eligible"] for item in plan["items"]), 2)
        changed = execute_bulk_transition(plan, action="local_transfer")
        db.session.commit()

        self.assertEqual({item.id for item in changed}, {first_source.id, second_source.id})
        self.assertEqual(StudentEnrollment.query.filter_by(academic_year_id=self.year_a.id).count(), 2)
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="local_transfer").count(), 2)
        self.assertEqual(getattr(first, "academic_year_id"), self.year_a.id)
        self.assertEqual(getattr(second, "academic_year_id"), self.year_a.id)

    def test_invalid_modes_are_rejected_without_mutation(self):
        student, source = self._student_with_source("P2D005")
        with self.assertRaises(EnrollmentValidationError):
            transition_student_enrollment(
                student.id, source.id, self.year_b.id, self.level_b.id, self.class_b_four.id, action="local_transfer"
            )
        with self.assertRaises(EnrollmentValidationError):
            transition_student_enrollment(
                student.id, source.id, self.year_a.id, self.level_a.id, self.class_a_five.id, action="promotion"
            )
        with self.assertRaises(EnrollmentValidationError):
            transition_student_enrollment(
                student.id, source.id, self.year_a.id, self.level_a.id, self.class_a_five.id, action="repeat"
            )
        db.session.rollback()
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 1)
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
