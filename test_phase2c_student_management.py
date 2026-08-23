from io import BytesIO
import unittest

from werkzeug.datastructures import FileStorage
from openpyxl import Workbook

from app import create_app, db
from app.enrollment_service import create_enrollment, get_enrollment_for_student_year
from app.import_wizard import process_student_import
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicSection,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Student,
    StudentEnrollment,
    User,
)


class TestPhase2CStudentManagement(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2c-test"
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
        self.legacy_class = AcademicClass(
            academic_level=self.level,
            name="Form Four",
            sort_order=1,
        )
        db.session.add_all([self.year_a, self.year_b, self.level, self.legacy_class])
        db.session.flush()

        self.section = AcademicSection(
            academic_class_id=self.legacy_class.id,
            name="A",
            sort_order=1,
        )
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
        admin = User(username="phase2c-admin", full_name="Phase 2C Admin", role="super_admin")
        admin.set_password("test-password")
        db.session.add_all([self.class_a, self.class_b, admin])
        db.session.commit()
        self.client = self.app.test_client()
        response = self.client.post(
            "/admin/login",
            data={"username": "phase2c-admin", "password": "test-password"},
        )
        self.assertIn(response.status_code, (302, 303))

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _create_student(self, code="TIS001", year_id=None, year_level_id=None, year_class_id=None):
        response = self.client.post(
            "/admin/advanced-results/students/new",
            data={
                "student_code": code,
                "full_name": "Amina Ali Omar",
                "mother_name": "Sahra Jama",
                "phone": "+252617778847",
                "gender": "Female",
                "academic_year_id": str(year_id or self.year_a.id),
                "academic_year_level_id": str(year_level_id or self.level_a.id),
                "academic_year_class_id": str(year_class_id or self.class_a.id),
                "academic_section_id": str(self.section.id),
                "is_active": "on",
            },
        )
        self.assertIn(response.status_code, (302, 303))
        return Student.query.filter_by(student_code=code).one()

    def test_new_student_creates_year_aware_enrollment(self):
        form_page = self.client.get(
            f"/admin/advanced-results/students/new?year_id={self.year_a.id}"
        )
        self.assertEqual(form_page.status_code, 200)
        self.assertIn(b"Academic Level", form_page.data)
        student = self._create_student()
        edit_page = self.client.get(
            f"/admin/advanced-results/students/{student.id}/edit?year_id={self.year_a.id}"
        )
        self.assertEqual(edit_page.status_code, 200)
        enrollment = get_enrollment_for_student_year(student.id, self.year_a.id)
        self.assertIsNotNone(enrollment)
        self.assertEqual(enrollment.academic_year_level_id, self.level_a.id)
        self.assertEqual(enrollment.academic_year_class_id, self.class_a.id)
        self.assertEqual(enrollment.academic_section_id, self.section.id)
        self.assertEqual(student.academic_class_id, self.legacy_class.id)

    def test_listing_uses_selected_year_enrollment_without_cross_year_leakage(self):
        student = self._create_student()
        create_enrollment(
            student.id,
            self.year_b.id,
            self.level_b.id,
            self.class_b.id,
            self.section.id,
            enrollment_source="promotion",
        )
        db.session.commit()

        year_a_page = self.client.get(
            f"/admin/advanced-results/students-management?year_id={self.year_a.id}&class_id={self.class_a.id}"
        )
        year_b_page = self.client.get(
            f"/admin/advanced-results/students-management?year_id={self.year_b.id}&class_id={self.class_b.id}"
        )
        self.assertEqual(year_a_page.status_code, 200)
        self.assertEqual(year_b_page.status_code, 200)
        self.assertIn(b"TIS001", year_a_page.data)
        self.assertIn(b"TIS001", year_b_page.data)

        other_year_page = self.client.get(
            f"/admin/advanced-results/students-management?year_id={self.year_b.id}&class_id={self.class_a.id}"
        )
        self.assertIn(other_year_page.status_code, (302, 303))

    def test_identity_edit_does_not_overwrite_historical_placement(self):
        student = self._create_student()
        response = self.client.post(
            f"/admin/advanced-results/students/{student.id}/edit?year_id={self.year_a.id}",
            data={
                "student_code": "TIS001-EDITED",
                "full_name": "Amina Changed Name",
                "mother_name": "Sahra Jama",
                "phone": "+252617778847",
                "gender": "Female",
                "is_active": "on",
            },
        )
        self.assertIn(response.status_code, (302, 303))
        enrollment = StudentEnrollment.query.filter_by(student_id=student.id).one()
        self.assertEqual(enrollment.academic_year_id, self.year_a.id)
        self.assertEqual(enrollment.academic_year_class_id, self.class_a.id)
        self.assertEqual(db.session.get(Student, student.id).student_code, "TIS001-EDITED")

    def test_import_accepts_alphanumeric_ids_and_creates_enrollment(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Students"
        worksheet.append([
            "ID", "Name", "Mother", "Mobile", "Academic Level", "Class",
            "Section", "Academic Year", "Gender", "Photo Source",
        ])
        worksheet.append([
            "TIS-002", "Imported Student", "Guardian Name", "+252617778848",
            "Secondary", "Form Four", "A", "2026-2027", "Male", "",
        ])
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        summary = process_student_import(FileStorage(stream=stream, filename="students.xlsx"))
        self.assertEqual(summary["success_count"], 1)
        self.assertEqual(summary["failed_count"], 0)
        student = Student.query.filter_by(student_code="TIS-002").one()
        self.assertIsNotNone(get_enrollment_for_student_year(student.id, self.year_a.id))


if __name__ == "__main__":
    unittest.main()
