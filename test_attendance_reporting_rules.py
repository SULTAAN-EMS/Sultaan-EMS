"""Regression tests for Attendance as the Results Analytics sitting source."""

import io
import os
import sys
import unittest

from openpyxl import Workbook

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from app.models import AcademicClass, AcademicLevel, AcademicYear, AttendanceRecord, Exam, GradeScale, Result, Student, Subject, User
from app.routes_advanced_results import build_analytics_results_report_data
from app.attendance_rules import counts_as_exam_sitting
from app.import_wizard import process_student_import
from config import Config


class ReportingTestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}


class AttendanceReportingRuleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ReportingTestConfig)
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2030-2031", is_current=True)
        self.level = AcademicLevel(name="Secondary", sort_order=1)
        db.session.add_all([self.year, self.level])
        db.session.flush()
        self.academic_class = AcademicClass(academic_level_id=self.level.id, name="Form One")
        self.exam = Exam(name="Final", academic_year_id=self.year.id, is_active=True)
        self.subject = Subject(name="Mathematics", academic_level_id=self.level.id, max_score=100)
        db.session.add_all([self.academic_class, self.exam, self.subject])
        db.session.flush()
        db.session.add(GradeScale(grade="A", min_score=0, max_score=100, comment="Pass", is_pass=True))
        self.present_student = Student(student_code="R-001", full_name="Ayaan", gender="Male", academic_year_id=self.year.id, academic_level_id=self.level.id, academic_class_id=self.academic_class.id)
        self.sick_student = Student(student_code="R-002", full_name="Hodan", gender="Female", academic_year_id=self.year.id, academic_level_id=self.level.id, academic_class_id=self.academic_class.id)
        self.admin = User(username="report-admin", full_name="Report Admin", role="super_admin")
        self.admin.set_password("correct-password")
        db.session.add_all([self.present_student, self.sick_student, self.admin])
        db.session.flush()
        db.session.add_all([
            Result(student_id=self.present_student.id, exam_id=self.exam.id, subject_id=self.subject.id, score=80, is_published=True),
            Result(student_id=self.sick_student.id, exam_id=self.exam.id, subject_id=self.subject.id, score=95, is_published=True),
            AttendanceRecord(student_id=self.present_student.id, academic_year_id=self.year.id, exam_id=self.exam.id, subject_id=self.subject.id, status="present"),
            AttendanceRecord(student_id=self.sick_student.id, academic_year_id=self.year.id, exam_id=self.exam.id, subject_id=self.subject.id, status="sick"),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_only_present_and_late_count_as_exam_sitting(self):
        self.assertTrue(counts_as_exam_sitting("present"))
        self.assertTrue(counts_as_exam_sitting("Daahid"))
        for status in ("absent", "excused", "sick", "emergency", "Maqnaansho", "La fasaxay"):
            self.assertFalse(counts_as_exam_sitting(status))

    def test_analytics_uses_subject_attendance_as_its_denominator(self):
        levels = build_analytics_results_report_data(self.year, self.exam)
        self.assertEqual(len(levels), 1)
        class_row = levels[0]["classes"][0]
        subject_row = levels[0]["subjects"][0]
        self.assertEqual(class_row["mApp"], 1)
        self.assertEqual(class_row["fApp"], 0)
        self.assertEqual(class_row["fAbsent"], 1)
        self.assertEqual(subject_row["appeared"], 1)
        self.assertEqual(subject_row["passed"], 1)
        self.assertEqual(subject_row["avg"], 80.0)

    def test_printable_analytics_report_renders_with_attendance_data(self):
        """The existing print/PDF template continues to render the corrected payload."""
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        response = self.client.get(
            f"/admin/advanced-results/analytics/results-report?year_id={self.year.id}&exam_id={self.exam.id}"
        )
        self.assertEqual(response.status_code, 200)
        # The report builds headings in browser-side JavaScript.  Assert the
        # server-rendered page carries the actual, attendance-filtered payload.
        self.assertIn(b"const LEVELS =", response.data)
        self.assertIn(b'"appeared": 1', response.data)

    def test_student_excel_import_requires_and_persists_gender(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Students"
        sheet.append(["student_id", "full_name", "mother_name", "phone", "gender", "class", "academic_year"])
        sheet.append(["900001", "Import Student", "Import Mother", "+252615551234", "Female", "Form One", "2030-2031"])
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        summary = process_student_import(stream)
        self.assertEqual(summary["success_count"], 1, summary)
        self.assertEqual(Student.query.filter_by(student_code="900001").one().gender, "Female")

    def test_bulk_delete_preview_and_confirm_protect_linked_records(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        scope = {"academic_year_id": self.year.id, "academic_class_id": self.academic_class.id}
        preview = self.client.post("/admin/advanced-results/students/bulk-delete/preview", json=scope)
        self.assertTrue(preview.get_json()["success"])
        self.assertGreater(preview.get_json()["dependencies"].get("results", 0), 0)
        blocked = self.client.post(
            "/admin/advanced-results/students/bulk-delete/confirm",
            json={**scope, "password": "correct-password"},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(Student.query.filter_by(student_code="R-001").count(), 1)

    def test_bulk_delete_removes_unlinked_class_after_password_confirmation(self):
        empty_class = AcademicClass(academic_level_id=self.level.id, name="Form Two")
        db.session.add(empty_class)
        db.session.flush()
        removable = Student(
            student_code="R-003",
            full_name="Removable Student",
            gender="Male",
            academic_year_id=self.year.id,
            academic_level_id=self.level.id,
            academic_class_id=empty_class.id,
        )
        db.session.add(removable)
        db.session.commit()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        scope = {"academic_year_id": self.year.id, "academic_class_id": empty_class.id}
        preview = self.client.post("/admin/advanced-results/students/bulk-delete/preview", json=scope).get_json()
        self.assertEqual(preview["student_count"], 1)
        self.assertEqual(preview["dependencies"], {})
        deleted = self.client.post(
            "/admin/advanced-results/students/bulk-delete/confirm",
            json={**scope, "password": "correct-password"},
        )
        self.assertTrue(deleted.get_json()["success"], deleted.get_json())
        self.assertIsNone(db.session.get(Student, removable.id))


if __name__ == "__main__":
    unittest.main()
