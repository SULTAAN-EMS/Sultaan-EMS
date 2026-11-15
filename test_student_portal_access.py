import unittest

from flask import g

from app import create_app, db
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicLevel,
    Student,
    StudentEnrollment,
    StudentPortalAccessCode,
    User,
)
from app.student_portal_access import generate_access_code, set_code_value, valid_access_code, verify_code


class TestStudentPortalAccess(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "student-portal-access-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.owner = User(username="access-owner", full_name="Access Owner", role="super_admin")
        self.owner.set_password("OwnerPass123")
        level = AcademicLevel(name="Dugsi Sare")
        db.session.add(level)
        db.session.flush()
        year = AcademicYear(name="2026-2027", is_current=True)
        db.session.add(year)
        db.session.flush()
        year_level = AcademicYearLevel(academic_year_id=year.id, name="Dugsi Sare")
        db.session.add(year_level)
        db.session.flush()
        year_class = AcademicYearClass(academic_year_level_id=year_level.id, name="Form 4", school_shift="morning")
        student = Student(student_code="TIS-ACCESS-01", full_name="Access Student")
        db.session.add_all([year_class, student, self.owner])
        db.session.flush()
        db.session.add(StudentEnrollment(
            student_id=student.id,
            academic_year_id=year.id,
            academic_year_level_id=year_level.id,
            academic_year_class_id=year_class.id,
            status="active",
        ))
        db.session.commit()
        self.year = year
        self.student = student
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login(self):
        response = self.client.post(
            "/admin/login",
            data={"username": "access-owner", "password": "OwnerPass123"},
        )
        self.assertEqual(response.status_code, 302)

    def test_codes_are_four_characters_and_require_approval(self):
        code = generate_access_code()
        self.assertTrue(valid_access_code(code))
        self.assertFalse(valid_access_code("IO01"))

    def test_admin_generates_approves_and_public_portal_requires_code(self):
        self.login()
        enabled = self.client.post(
            "/admin/advanced-results/settings/student-portal-access/global",
            data={"student_portal_access_codes_enabled": "on"},
        )
        self.assertEqual(enabled.status_code, 302)
        # The test keeps one outer app context alive; production requests get
        # a fresh request-local settings cache on every request.
        g._sultaan_settings = None

        challenge = self.client.get("/result?student_id=TIS-ACCESS-01")
        self.assertEqual(challenge.status_code, 200)
        self.assertIn("Geli lambarka sireedka ardayga", challenge.get_data(as_text=True))
        self.assertIn('name="csrf_token"', challenge.get_data(as_text=True))

        generated = self.client.post(
            "/admin/advanced-results/settings/student-portal-access/generate",
            data={"access_year_id": str(self.year.id)},
        )
        self.assertEqual(generated.status_code, 302)
        record = StudentPortalAccessCode.query.filter_by(student_id=self.student.id).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "pending")
        self.assertFalse(record.is_active)

        denied = self.client.post(
            "/result",
            data={"access_code_form": "1", "student_id": self.student.student_code, "access_code": record.code_value},
        )
        self.assertEqual(denied.status_code, 200)
        self.assertIn("lambarka sireedku waa khaldan yahay", denied.get_data(as_text=True).lower())

        approved = self.client.post(
            "/admin/advanced-results/settings/student-portal-access/approve",
            data={"access_year_id": str(self.year.id), "approve_scope": "1"},
        )
        self.assertEqual(approved.status_code, 302)
        db.session.refresh(record)
        self.assertEqual(record.status, "approved")
        self.assertTrue(record.is_active)
        self.assertTrue(verify_code(record, record.code_value))

        unlocked = self.client.post(
            "/result",
            data={"access_code_form": "1", "student_id": self.student.student_code, "access_code": record.code_value},
        )
        self.assertEqual(unlocked.status_code, 302)
        follow = self.client.get(unlocked.headers["Location"])
        self.assertEqual(follow.status_code, 200)
        self.assertNotIn("Geli lambarka sireedka ardayga", follow.get_data(as_text=True))

        # Changing or revoking a code must invalidate an already verified
        # browser session instead of allowing the old verification to linger.
        set_code_value(record, "ABCD")
        record.status = "approved"
        record.is_active = True
        db.session.commit()
        g._sultaan_settings = None
        relocked = self.client.get(f"/result?student_id={self.student.student_code}")
        self.assertEqual(relocked.status_code, 200)
        self.assertIn("Geli lambarka sireedka ardayga", relocked.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
