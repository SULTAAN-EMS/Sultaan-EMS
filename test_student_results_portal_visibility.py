import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

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

    def test_portal_prefills_student_code_for_return_navigation(self):
        response = self.client.get("/?student_id=TIS-BACK-TEST")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="student_id"'.encode(), response.data)
        self.assertIn('value="TIS-BACK-TEST"'.encode(), response.data)
        self.assertIn("returnedStudentIdInput.focus".encode(), response.data)
        self.assertIn("setSelectionRange(caretPosition, caretPosition)".encode(), response.data)
        self.assertIn("returnedStudentIdCard.scrollIntoView".encode(), response.data)
        self.assertIn("block: 'center'".encode(), response.data)

    def test_homepage_exposes_student_services_social_preview(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('property="og:title" content="WAAXDA ADEEGGA ARDEYGA', html)
        self.assertIn('property="og:type" content="website"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertIn("https://www.sultaan-ems.com/static/images/student-services-share.jpg?v=fd81476", html)

        image = self.client.get("/static/images/student-services-share.jpg")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.mimetype, "image/jpeg")
        self.assertTrue(image.data.startswith(b"\xff\xd8\xff"))
        image.close()

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
        self.assertIn(b"Waqtiga shaacinta natiijooyinka weli lama cayimin", public_page.data)
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

    def test_announcement_schedule_is_restricted_and_does_not_open_results(self):
        self.login_as(self.results_admin)
        denied = self.client.post(
            "/admin/settings/student-results-announcement",
            data={"announcement_at_eat": "2026-11-04T06:00"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(db.session.get(Setting, "student_results_announcement_at_eat"))

        self.client.post("/admin/logout")
        self.login_as(self.owner)
        settings_page = self.client.get("/admin/advanced-results/settings")
        self.assertEqual(settings_page.status_code, 200)
        self.assertIn(b"Jadwal u samee shaacinta natiijooyinka", settings_page.data)
        self.assertIn(b"Waqtiga shaacinta", settings_page.data)

        saved = self.client.post(
            "/admin/settings/student-results-announcement",
            data={"announcement_at_eat": "2026-11-04T06:00"},
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(
            db.session.get(Setting, "student_results_announcement_at_eat").value,
            "2026-11-04T06:00",
        )

        self.client.post(
            "/admin/settings/student-results-portal",
            data={"enabled": "off"},
        )
        public_page = self.client.get("/result")
        self.assertEqual(public_page.status_code, 200)
        self.assertIn("Arbaca, November 04, 2026 · 06:00 AM EAT".encode(), public_page.data)
        self.assertIn(b'data-results-announcement', public_page.data)
        expected_timestamp = int(
            datetime(2026, 11, 4, 6, 0, tzinfo=ZoneInfo("Africa/Mogadishu")).timestamp()
            * 1000
        )
        self.assertIn(f'data-target-ms="{expected_timestamp}"'.encode(), public_page.data)
        self.assertEqual(
            db.session.get(Setting, "student_results_portal_enabled").value,
            "off",
        )

        cleared = self.client.post(
            "/admin/settings/student-results-announcement",
            data={"announcement_at_eat": ""},
        )
        self.assertEqual(cleared.status_code, 302)
        self.assertEqual(
            db.session.get(Setting, "student_results_announcement_at_eat").value,
            "",
        )
        cleared_page = self.client.get("/result")
        self.assertNotIn(b'data-results-announcement', cleared_page.data)
        self.assertIn(b"Waqtiga shaacinta natiijooyinka weli lama cayimin", cleared_page.data)


if __name__ == "__main__":
    unittest.main()
