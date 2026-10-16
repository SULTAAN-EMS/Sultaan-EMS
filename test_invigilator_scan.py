import unittest
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import event

from app import create_app, db
from app.cloudinary_service import cloudinary_thumbnail_url
from app.models import AcademicYear, Exam, ExamHall, ExamInvigilator, IdCardIssue, IncidentReportSettings, Student, StudentCodeAlias, User


class TestInvigilatorScan(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "invigilator-scan-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.exam = Exam(name="Imtixaan Bileedka 1aad", academic_year=self.year, is_active=True)
        self.student = Student(student_code="TIS138", full_name="Cabdi Xasan", is_active=True)
        self.invigilator = ExamInvigilator(
            invigilator_id="INV-001",
            username="invigilator-scan-test",
            full_name="Macallin Tijaabo",
            role="Invigilator",
            status="Active",
            is_active=True,
        )
        self.invigilator.set_password("TestPass123")
        self.invigilator.visible_password = "TestPass123"
        self.admin = User(username="invigilator-share-admin", full_name="Admin Tijaabo", role="super_admin")
        self.admin.set_password("AdminPass123")
        db.session.add_all([self.year, self.student, self.invigilator, self.admin])
        db.session.flush()
        db.session.add(self.exam)
        db.session.flush()
        self.hall = ExamHall(
            name="Form 1A (Gabdhaha)",
            code="INV-SHARE-HALL-1",
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
            is_active=True,
        )
        db.session.add(self.hall)
        db.session.flush()
        self.issue = self.add_issue("scan-token-001", self.exam)
        db.session.commit()

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def add_issue(self, token, exam):
        issue = IdCardIssue(
            token=token,
            student=self.student,
            academic_year=self.year,
            exam=exam,
            issue_date=date.today(),
            expiry_date=date.today() + timedelta(days=60),
            status="Active",
        )
        db.session.add(issue)
        db.session.flush()
        return issue

    def authenticate_invigilator(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with self.client.session_transaction() as session:
            session["invigilator_id"] = self.invigilator.id
            session["invigilator_logged_in"] = True
            session["invigilator_login_time"] = now
            session["invigilator_last_activity"] = now

    def authenticate_admin(self):
        return self.client.post("/admin/login", data={
            "username": self.admin.username,
            "password": "AdminPass123",
        })

    def test_invigilator_share_data_is_authenticated_uncached_and_uses_real_hall_context(self):
        url = f"/admin/invigilators/{self.invigilator.id}/share-info"
        denied = self.client.get(url)
        self.assertEqual(denied.status_code, 302)
        self.authenticate_admin()

        response = self.client.get(url)
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(data["full_name"], self.invigilator.full_name)
        self.assertEqual(data["invigilator_id"], self.invigilator.invigilator_id)
        self.assertEqual(data["username"], self.invigilator.username)
        self.assertEqual(data["password"], "TestPass123")
        page = self.client.get("/admin/invigilators")
        page_html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Diyaari xogta ilaaliyaha", page_html)
        self.assertNotIn("TestPass123", page_html)
        self.assertEqual(data["halls"], [{
            "id": self.hall.id,
            "hall": self.hall.name,
            "class_name": self.hall.name,
            "exam_name": self.exam.name,
            "year_name": self.year.name,
        }])

    def test_scan_page_requires_invigilator_login_and_preserves_return_path(self):
        response = self.client.get("/scan")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/invigilator/login", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertTrue(session["invigilator_next"].endswith("/scan"))

    def test_authenticated_scan_page_offers_both_entry_methods(self):
        self.authenticate_invigilator()

        response = self.client.get("/scan")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("SCAN QR CODE", html)
        self.assertIn("GELI TIRA-TAXANAHA ARDEYGA", html)
        self.assertIn('data-theme="qrcode"', html)
        self.assertIn('class="fx scanline"', html)
        self.assertIn('class="fx floaters"', html)
        self.assertIn("qr-landing.css", html)
        self.assertIn("qr-landing.js", html)
        self.assertIn("qr-scanner.umd.min.js", html)
        self.assertIn("qr-scanner-worker.min.js", html)

    def test_invigilator_login_opens_the_scan_page_by_default(self):
        response = self.client.post("/invigilator/login", data={
            "username": self.invigilator.username,
            "password": "TestPass123",
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/scan"))
        scan_page = self.client.get(response.headers["Location"])
        refreshed_page = self.client.get(response.headers["Location"])
        self.assertEqual(scan_page.status_code, 200)
        self.assertEqual(refreshed_page.status_code, 200)

    def test_failed_login_redirects_instead_of_rendering_a_post_response(self):
        response = self.client.post("/invigilator/login", data={
            "username": self.invigilator.username,
            "password": "incorrect",
        })

        self.assertEqual(response.status_code, 303)
        login_page = self.client.get(response.headers["Location"])
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("Invalid username or password", login_page.get_data(as_text=True))

    def test_manual_student_code_displays_verification_and_report_actions(self):
        self.authenticate_invigilator()

        post_response = self.client.post("/scan", data={"student_code": "  tis138  "})
        self.assertEqual(post_response.status_code, 303)
        response = self.client.get(post_response.headers["Location"])
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cabdi Xasan", html)
        self.assertNotIn("TIS138", html)
        self.assertNotIn("XAQIIJINTA ARDAYGA", html)
        self.assertNotIn("Ardayga la helay", html)
        self.assertNotIn(self.invigilator.full_name, html)
        self.assertNotIn("NIDAAMKA MAAREYNTA IMTIXAANNADA</footer>", html)
        self.assertNotIn("KAADHKA IMTIXAANKA", html)
        self.assertNotIn("Imtixaan Bileedka 1aad", html)
        self.assertNotIn("2026-2027", html)
        self.assertIn('class="result-action-card result-verify"', html)
        self.assertIn('class="result-action-card result-report"', html)
        self.assertIn("Xaqiiji Ardeyga", html)
        self.assertIn("Gudbi Warbixin Dhacdo", html)
        self.assertIn("/verify-id/scan-token-001", html)
        self.assertIn("/incident-report/scan-token-001", html)
        self.assertIn("from_scan=1", html)
        self.assertEqual(self.client.get("/scan").status_code, 200)

        new_search = self.client.get("/scan?new=1")
        self.assertEqual(new_search.status_code, 303)
        entry_page = self.client.get(new_search.headers["Location"])
        self.assertIn('id="student-code" name="student_code" value="tis138"', entry_page.get_data(as_text=True))

    def test_student_photo_is_bound_to_background_floaters_for_both_lookup_methods(self):
        photo_url = "https://res.cloudinary.com/sultaan/image/upload/v123/students/student.jpg"
        self.student.photo_path = photo_url
        db.session.commit()
        self.authenticate_invigilator()

        lookups = (
            {"student_code": self.student.student_code},
            {"scan_value": f"https://www.sultaan-ems.com/qr/{self.issue.token}?exam_id={self.exam.id}"},
        )
        for lookup in lookups:
            post_response = self.client.post("/scan", data=lookup)
            response = self.client.get(post_response.headers["Location"])

            self.assertEqual(response.status_code, 200)
            self.assertIn(f'data-photo-url="{photo_url}"', response.get_data(as_text=True))
            self.client.get("/scan?new=1")

    def test_manual_student_code_is_remembered_after_failed_lookup(self):
        self.authenticate_invigilator()

        post_response = self.client.post("/scan", data={"student_code": "TIS-MISSING"})
        self.assertEqual(post_response.status_code, 303)
        entry_page = self.client.get(post_response.headers["Location"])

        self.assertEqual(entry_page.status_code, 200)
        self.assertIn('id="student-code" name="student_code" value="TIS-MISSING"', entry_page.get_data(as_text=True))

    def test_verify_back_target_returns_to_the_staff_action_page(self):
        self.authenticate_invigilator()
        self.client.post("/scan", data={"student_code": self.student.student_code})

        response = self.client.get(
            f"/verify-id/{self.issue.token}?action=verify&from_scan=1"
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('const scanReturnUrl = "/scan"', html)

    def test_incident_form_back_target_returns_to_the_staff_action_page(self):
        self.authenticate_invigilator()
        self.client.post("/scan", data={"student_code": self.student.student_code})

        response = self.client.get(
            f"/incident-report/{self.issue.token}?from_scan=1"
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/scan"', html)
        self.assertIn('name="from_scan" value="1"', html)

    def test_incident_form_uses_current_photo_and_loads_saved_signature_on_demand(self):
        self.student.photo_path = "https://res.cloudinary.com/sultaan/image/upload/v123/students/student.jpg"
        self.invigilator.photo_path = "https://images.example.test/invigilator-current.jpg"
        db.session.add(IncidentReportSettings(
            setting_key="show_student_photo",
            setting_value="true",
            setting_type="boolean",
        ))
        signature = "data:image/png;base64," + ("signature-payload-" * 3000)
        self.invigilator.signature_data = signature
        db.session.commit()
        self.authenticate_invigilator()
        with self.client.session_transaction() as session:
            session["invigilator_photo_path"] = "https://images.example.test/stale-session-photo.jpg"

        statements = []
        listener = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            response = self.client.get(f"/incident-report/{self.issue.token}?from_scan=1")
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("invigilator-current.jpg", html)
        self.assertIn("c_fill,w_320,h_320,q_auto,f_auto/v123/students/student.jpg", html)
        self.assertNotIn("stale-session-photo.jpg", html)
        self.assertNotIn(signature, html)
        self.assertIn('id="signatureReuse"', html)
        self.assertIn('id="sigpad"', html)
        self.assertIn('id="sigCanvas"', html)
        self.assertIn('data-color="#16233F"', html)
        self.assertIn('data-color="#1F5349"', html)
        self.assertIn('data-color="#6E2E27"', html)
        self.assertIn('data-width="1.5"', html)
        self.assertIn('data-width="3"', html)
        self.assertIn('data-width="5"', html)
        self.assertNotIn('id="signatureCanvas"', html)
        self.assertNotIn('id="signatureUndo"', html)
        signature_script = self.client.get("/static/js/incident-signature.js")
        self.assertEqual(signature_script.status_code, 200)
        self.assertIn(b"pointerdown", signature_script.data)
        signature_script.close()
        self.assertLessEqual(sum("student_enrollments" in statement.lower() for statement in statements), 1)

        signature_response = self.client.get("/api/invigilator/signature")
        self.assertEqual(signature_response.status_code, 200)
        self.assertEqual(signature_response.get_json()["signature_data"], signature)
        self.assertEqual(signature_response.headers["Cache-Control"], "private, no-store")

        self.invigilator.force_password_change = True
        db.session.commit()
        restricted_response = self.client.get("/api/invigilator/signature")
        self.assertEqual(restricted_response.status_code, 403)
        self.assertNotIn("signature_data", restricted_response.get_json())

    def test_cloudinary_photo_transform_is_limited_to_untransformed_image_urls(self):
        source = "https://res.cloudinary.com/demo/image/upload/v1/students/photo.jpg?x=1"
        expected = "https://res.cloudinary.com/demo/image/upload/c_fill,w_320,h_320,q_auto,f_auto/v1/students/photo.jpg?x=1"
        self.assertEqual(cloudinary_thumbnail_url(source), expected)
        self.assertEqual(
            cloudinary_thumbnail_url("https://res.cloudinary.com/demo/image/upload/c_thumb,w_200/v1/photo.jpg"),
            "https://res.cloudinary.com/demo/image/upload/c_thumb,w_200/v1/photo.jpg",
        )
        self.assertEqual(cloudinary_thumbnail_url("/static/student.jpg"), "/static/student.jpg")
        self.assertEqual(cloudinary_thumbnail_url("https://images.example.test/student.jpg"), "https://images.example.test/student.jpg")

    def test_historical_student_code_resolves_to_the_current_card(self):
        db.session.add(StudentCodeAlias(
            student=self.student,
            old_code="TIS-OLD-138",
            new_code=self.student.student_code,
        ))
        db.session.commit()
        self.authenticate_invigilator()

        post_response = self.client.post("/scan", data={"student_code": "tis-old-138"})
        response = self.client.get(post_response.headers["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cabdi Xasan", response.get_data(as_text=True))

    def test_scanned_qr_keeps_its_exam_scope(self):
        self.authenticate_invigilator()
        qr_value = f"https://www.sultaan-ems.com/qr/{self.issue.token}?exam_id={self.exam.id}"

        post_response = self.client.post("/scan", data={"scan_value": qr_value})
        response = self.client.get(post_response.headers["Location"])
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cabdi Xasan", html)
        self.assertIn(f"exam_id={self.exam.id}", html)

    def test_multiple_active_exam_cards_require_explicit_exam_selection(self):
        second_exam = Exam(name="Imtixaan Bileedka 2aad", academic_year=self.year, is_active=True)
        db.session.add(second_exam)
        db.session.flush()
        second_issue = self.add_issue("scan-token-002", second_exam)
        db.session.commit()
        self.authenticate_invigilator()

        choice_post = self.client.post("/scan", data={"student_code": self.student.student_code})
        choice_response = self.client.get(choice_post.headers["Location"])
        choice_html = choice_response.get_data(as_text=True)
        self.assertEqual(choice_response.status_code, 200)
        self.assertIn("Dooro imtixaanka saxda ah", choice_html)
        self.assertIn("Imtixaan Bileedka 1aad", choice_html)
        self.assertIn("Imtixaan Bileedka 2aad", choice_html)

        selected_post = self.client.post("/scan", data={
            "selected_student_id": self.student.id,
            "issue_choice_id": second_issue.id,
        })
        selected_response = self.client.get(selected_post.headers["Location"])
        selected_html = selected_response.get_data(as_text=True)
        self.assertNotIn("Imtixaan Bileedka 2aad", selected_html)
        self.assertIn("/verify-id/scan-token-002", selected_html)

    def test_unknown_student_code_is_reported_without_exposing_student_data(self):
        self.authenticate_invigilator()

        post_response = self.client.post("/scan", data={"student_code": "TIS-UNKNOWN"})
        self.assertEqual(post_response.status_code, 303)
        response = self.client.get(post_response.headers["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertIn("Ardayga leh tira-taxanahan lama helin", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
