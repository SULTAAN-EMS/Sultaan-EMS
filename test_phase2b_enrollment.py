import tempfile
import unittest

from sqlalchemy import inspect

from app import create_app, db
from app.enrollment_service import (
    EnrollmentValidationError,
    backfill_ready_students,
    create_enrollment,
    dry_run_legacy_backfill,
    get_enrollment_for_student_year,
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
)


class TestPhase2BEnrollment(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2b-test"
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
        self.level = AcademicLevel(name="Secondary", sort_order=1)
        self.legacy_class = AcademicClass(academic_level=self.level, name="Form Four", sort_order=1)
        db.session.add_all([self.year_a, self.year_b, self.level, self.legacy_class])
        db.session.flush()
        self.section = AcademicSection(academic_class_id=self.legacy_class.id, name="A", sort_order=1)
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
        db.session.add_all([self.section, self.level_a, self.level_b])
        db.session.flush()
        self.class_a = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
            sort_order=1,
        )
        self.class_b = AcademicYearClass(
            academic_year_level_id=self.level_b.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
            sort_order=1,
        )
        db.session.add_all([self.class_a, self.class_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _student(self, code="TIS001"):
        student = Student(
            student_code=code,
            full_name="Test Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level.id,
            academic_class_id=self.legacy_class.id,
            academic_section_id=self.section.id,
        )
        db.session.add(student)
        db.session.commit()
        return student

    def test_schema_has_constraints_foreign_keys_and_indexes(self):
        inspector = inspect(db.engine)
        self.assertIn("student_enrollments", inspector.get_table_names())
        columns = {item["name"] for item in inspector.get_columns("student_enrollments")}
        self.assertTrue({
            "student_id",
            "academic_year_id",
            "academic_year_level_id",
            "academic_year_class_id",
            "academic_section_id",
            "previous_enrollment_id",
        }.issubset(columns))
        foreign_keys = {
            (item["constrained_columns"][0], item["referred_table"])
            for item in inspector.get_foreign_keys("student_enrollments")
        }
        self.assertIn(("student_id", "students"), foreign_keys)
        self.assertIn(("academic_year_id", "academic_years"), foreign_keys)
        self.assertIn(("academic_year_level_id", "academic_year_levels"), foreign_keys)
        self.assertIn(("academic_year_class_id", "academic_year_classes"), foreign_keys)
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("student_enrollments")}
        self.assertIn("uq_student_enrollment_student_year", unique_names)
        index_names = {item["name"] for item in inspector.get_indexes("student_enrollments")}
        self.assertIn("idx_student_enrollment_year_level_class", index_names)

    def test_valid_enrollment_and_duplicate_same_year_rejection(self):
        student = self._student()
        enrollment = create_enrollment(
            student.id,
            self.year_a.id,
            self.level_a.id,
            self.class_a.id,
            self.section.id,
            enrollment_source="manual",
        )
        db.session.commit()
        self.assertEqual(get_enrollment_for_student_year(student.id, self.year_a.id).id, enrollment.id)
        with self.assertRaises(EnrollmentValidationError):
            create_enrollment(student.id, self.year_a.id, self.level_a.id, self.class_a.id)

    def test_same_student_can_have_two_years_and_history_link(self):
        student = self._student()
        first = create_enrollment(student.id, self.year_a.id, self.level_a.id, self.class_a.id)
        db.session.commit()
        second = create_enrollment(
            student.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b.id,
            previous_enrollment_id=first.id,
        )
        db.session.commit()
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 2)
        self.assertEqual(second.previous_enrollment_id, first.id)
        self.assertEqual(db.session.get(StudentEnrollment, first.id).academic_year_id, self.year_a.id)

    def test_invalid_year_level_and_class_are_rejected(self):
        student = self._student()
        with self.assertRaises(EnrollmentValidationError):
            create_enrollment(student.id, self.year_a.id, self.level_b.id, self.class_a.id)
        with self.assertRaises(EnrollmentValidationError):
            create_enrollment(student.id, self.year_a.id, self.level_a.id, self.class_b.id)

    def test_invalid_section_is_rejected(self):
        other_level = AcademicLevel(name="Primary", sort_order=2)
        other_class = AcademicClass(academic_level=other_level, name="Grade Five", sort_order=1)
        db.session.add_all([other_level, other_class])
        db.session.flush()
        other_section = AcademicSection(academic_class_id=other_class.id, name="A", sort_order=1)
        db.session.add(other_section)
        db.session.commit()
        student = self._student()
        with self.assertRaises(EnrollmentValidationError):
            create_enrollment(student.id, self.year_a.id, self.level_a.id, self.class_a.id, other_section.id)

    def test_backfill_ready_student_preserves_legacy_fields(self):
        student = self._student()
        legacy_snapshot = (student.academic_year_id, student.academic_level_id, student.academic_class_id, student.academic_section_id)
        report = dry_run_legacy_backfill()
        item = next(item for item in report["students"] if item["student_id"] == student.id)
        self.assertEqual(item["classification"], "READY_TO_BACKFILL")
        result = backfill_ready_students(report)
        self.assertEqual(result["backfilled_student_ids"], [student.id])
        refreshed = db.session.get(Student, student.id)
        self.assertEqual(
            legacy_snapshot,
            (refreshed.academic_year_id, refreshed.academic_level_id, refreshed.academic_class_id, refreshed.academic_section_id),
        )
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=student.id).count(), 1)

    def test_backfill_reports_ambiguous_and_missing_mappings(self):
        ambiguous_level = AcademicYearLevel(
            academic_year_id=self.year_a.id,
            legacy_level_id=self.level.id,
            name="Secondary duplicate",
            sort_order=2,
        )
        orphan_level = AcademicLevel(name="Orphan Level", sort_order=9)
        db.session.add_all([ambiguous_level, orphan_level])
        db.session.flush()
        ambiguous = Student(
            student_code="TIS002",
            full_name="Ambiguous Student",
            academic_year_id=self.year_a.id,
            academic_level_id=self.level.id,
            academic_class_id=self.legacy_class.id,
        )
        missing = Student(
            student_code="TIS003",
            full_name="Missing Mapping Student",
            academic_year_id=self.year_a.id,
            academic_level_id=orphan_level.id,
            academic_class_id=self.legacy_class.id,
        )
        db.session.add_all([ambiguous, missing])
        db.session.commit()
        with tempfile.TemporaryDirectory() as directory:
            report = dry_run_legacy_backfill(f"{directory}/phase2b.json")
        by_code = {item["student_code"]: item["classification"] for item in report["students"]}
        self.assertEqual(by_code["TIS002"], "AMBIGUOUS")
        self.assertEqual(by_code["TIS003"], "MISSING_LEVEL_MAPPING")
        self.assertEqual(StudentEnrollment.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
