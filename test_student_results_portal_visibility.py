import json
import unittest

from app import create_app, db
from app.models import AuditLog, Setting, User
from app.services import get_settings


class TestStudentResultsPortalVisibility(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "student-results-visibility-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.owner = User(
            username="results-visibility-owner",
            full_name="Owner Test",
            role="super_admin",
        )
        self.owner.set_password("OwnerPass123")
        self.results_admin = User(
            username="results-visibility-admin",
            full_name="Results Admin Test",
            role="admin",
            permissions=json.dumps(["results"]),
        )
        self.results_admin.set_password("AdminPass123")
        db.session.add_all([self.owner, self.results_admin])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login_as(self, user):
        password = "OwnerPass123" if user is self.owner else "AdminPass123"
        response = self.client.post(
            "/admin/login",
            data={"username": user.username, "password": password},
        )
        self.assertEqual(response.status_code, 302)

    def test_visibility_defaults_to_open_for_existing_installations(self):
        self.assertEqual(get_settings()["student_results_portal_enabled"], "on")
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RAADI", response.data)
        self.assertNotIn(b"NATIIJOOYINKA IMTIXAANKA EE DHAMMAAN ARDEYDA HADDA WEY XIDHAN YIHIIN.", response.data)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_only_system_settings_admin_can_close_or_open_student_results(self):
        self.login_as(self.results_admin)
        denied = self.client.post(
            "/admin/settings/student-results-portal",
            data={"enabled": "off"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(db.session.get(Setting, "student_results_portal_enabled"))

        self.client.post("/admin/logout")
        self.login_as(self.owner)
        settings_page = self.client.get("/admin/advanced-results/settings")
        self.assertEqual(settings_page.status_code, 200)
        self.assertIn(b"XIDH NATIIJADA IMTIXAANKA", settings_page.data)
        self.assertIn(b"studentResultsConfirmDialog", settings_page.data)
        self.assertNotIn(b"onsubmit=\"return confirm(", settings_page.data)

        closed = self.client.post(
            "/admin/settings/student-results-portal",
            data={"enabled": "off"},
        )
        self.assertEqual(closed.status_code, 302)
        self.assertEqual(
            db.session.get(Setting, "student_results_portal_enabled").value,
            "off",
        )
        audit_entry = AuditLog.query.filter_by(
            action="Student Results Portal Visibility"
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertIn("on to off", audit_entry.details)

        public_page = self.client.get("/result?student_id=TIS-PRIVATE-CHECK")
        self.assertEqual(public_page.status_code, 200)
        self.assertIn(b"NATIIJOOYINKA IMTIXAANKA EE DHAMMAAN ARDEYDA HADDA WEY XIDHAN YIHIIN.", public_page.data)
        self.assertIn(b"DUGSIYADA CAALAMIGA AH EE TAYSIIR", public_page.data)
        self.assertIn(b"WAAXDA ARRIMAHA ARDEYDA", public_page.data)
        self.assertIn(b"Xafiiska Imtixaannadu weli ma fasixin aragtida natiijooyinka", public_page.data)
        self.assertIn(b"ardaydu waxay halakan ka arki doonaan natiijooyinka la daabacay", public_page.data)
        self.assertNotIn(b"TIS-PRIVATE-CHECK", public_page.data)
        self.assertIn("no-store", public_page.headers.get("Cache-Control", ""))

        admin_result_entry = self.client.get("/admin/teachers/result-entry")
        self.assertEqual(admin_result_entry.status_code, 200)
        teacher_login = self.client.get("/teacher/login?next=/teacher/result-entry")
        self.assertEqual(teacher_login.status_code, 200)
        scan_page = self.client.get("/scan")
        self.assertEqual(scan_page.status_code, 200)

        for path in (
            "/result/view/TIS-PRIVATE-CHECK/999",
            "/print/TIS-PRIVATE-CHECK?exam_id=999",
            "/download/TIS-PRIVATE-CHECK?exam_id=999",
            "/behavior/TIS-PRIVATE-CHECK/999/1/1/read",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b"NATIIJOOYINKA IMTIXAANKA EE DHAMMAAN ARDEYDA HADDA WEY XIDHAN YIHIIN.", response.data)

        for path in (
            "/api/results/TIS-PRIVATE-CHECK",
            "/api/top-students/TIS-PRIVATE-CHECK?exam_id=999",
            "/api/falcelin/result-summary",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 423, path)
            self.assertEqual(response.get_json()["code"], "student_results_closed")
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))

        opened = self.client.post(
            "/admin/settings/student-results-portal",
            data={"enabled": "on"},
        )
        self.assertEqual(opened.status_code, 302)
        self.assertEqual(
            db.session.get(Setting, "student_results_portal_enabled").value,
            "on",
        )
        reopened_api = self.client.get("/api/results/TIS-PRIVATE-CHECK")
        self.assertEqual(reopened_api.status_code, 404)
        self.assertNotEqual(reopened_api.get_json().get("code"), "student_results_closed")


if __name__ == "__main__":
    unittest.main()
