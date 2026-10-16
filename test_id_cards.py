import unittest
from datetime import date, timedelta
from html.parser import HTMLParser

from sqlalchemy import event

from app import create_app, db
from app.routes_id_cards import add_months
from app.verification import id_card_qr_payload
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Exam,
    ExamHall,
    ExamHallVersion,
    IdCardIssue,
    SeatMixerAssignment,
    Setting,
    Student,
    StudentEnrollment,
    User,
)


class FormNestingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.has_nested_form = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.has_nested_form = self.has_nested_form or self.form_depth > 0
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth = max(0, self.form_depth - 1)


class OptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.options = []

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            self.current = {"attrs": dict(attrs), "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self.current is not None:
            self.options.append(self.current)
            self.current = None


class TestIdCards(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "id-cards-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.other_year = AcademicYear(name="2027-2028", is_current=False)
        self.level = AcademicLevel(name="Dugsi Sare", sort_order=1)
        db.session.add_all([self.year, self.other_year, self.level])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year=self.year,
            legacy_level=self.level,
            name="Dugsi Sare",
            sort_order=1,
        )
        self.other_year_level = AcademicYearLevel(
            academic_year=self.other_year,
            legacy_level=self.level,
            name="Dugsi Sare",
            sort_order=1,
        )
        self.legacy_class = AcademicClass(
            academic_level=self.level,
            name="Form 1",
            sort_order=1,
        )
        db.session.add_all([self.year_level, self.other_year_level, self.legacy_class])
        db.session.flush()
        self.year_class = AcademicYearClass(
            academic_year_level=self.year_level,
            legacy_class=self.legacy_class,
            name="Form 1",
            sort_order=1,
        )
        self.other_year_class = AcademicYearClass(
            academic_year_level=self.other_year_level,
            legacy_class=self.legacy_class,
            name="Form 1",
            sort_order=1,
        )
        self.exam = Exam(name="Imtixaan Bileedka 1aad", academic_year=self.year, is_active=True)
        self.other_exam = Exam(name="Imtixaan Bileedka 1aad", academic_year=self.other_year, is_active=True)
        db.session.add_all([self.year_class, self.other_year_class, self.exam, self.other_exam])
        db.session.flush()
        self.first_student = self._student("ID-001", "Ardayga Koowaad", self.year, self.year_level, self.year_class)
        self.second_student = self._student("ID-002", "Ardayga Labaad", self.year, self.year_level, self.year_class)
        self.other_student = self._student("ID-003", "Arday Sannad Kale", self.other_year, self.other_year_level, self.other_year_class)

        admin = User(username="id-cards-admin", full_name="ID Cards Admin", role="super_admin", is_active=True)
        admin.set_password("CardAdmin123")
        db.session.add(admin)
        db.session.commit()

        self.client = self.app.test_client()
        response = self.client.post("/admin/login", data={
            "username": "id-cards-admin",
            "password": "CardAdmin123",
        })
        self.assertIn(response.status_code, (302, 303))

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    @staticmethod
    def _student(code, name, year, year_level, year_class):
        student = Student(student_code=code, full_name=name, is_active=True)
        db.session.add(student)
        db.session.flush()
        db.session.add(StudentEnrollment(
            student=student,
            academic_year=year,
            academic_year_level=year_level,
            academic_year_class=year_class,
            status="active",
            academic_outcome="pending",
            enrollment_source="manual",
        ))
        return student

    def test_qr_landing_uses_the_students_photo_for_background_floaters(self):
        self.first_student.photo_path = "students/qr-photo.jpg"
        issue = IdCardIssue(
            token="qr-landing-photo-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=date.today() + timedelta(days=365),
            status="Active",
        )
        db.session.add(issue)
        db.session.commit()

        response = self.client.get(f"/qr/{issue.token}?exam_id={self.exam.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'data-photo-url="/static/uploads/students/qr-photo.jpg"',
            response.data,
        )
        self.assertIn(b"qr-landing.css?v=qr-student-photo-20261016-1", response.data)
        self.assertIn(b"qr-landing.js?v=qr-student-photo-20261016-1", response.data)

    def test_exam_filter_shows_correct_year_roster_without_duplicate_bulk_panel_or_nested_forms(self):
        db.session.add(IdCardIssue(
            token="history-photo-test-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=date.today() + timedelta(days=365),
            status="Active",
        ))
        db.session.commit()

        response = self.client.get(
            f"/admin/id-cards/?year_id={self.year.id}&exam_id={self.exam.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ardayga Koowaad", response.data)
        self.assertIn(b"Ardayga Labaad", response.data)
        self.assertNotIn(b"Arday Sannad Kale", response.data)
        self.assertNotIn(b"Bulk Operations", response.data)
        self.assertNotIn(b"Hal Exam Type dooro, ardayda ku habboon soo saar", response.data)
        self.assertNotIn(b"Hal mar dooro Exam Type-ka", response.data)
        self.assertNotIn(b"DIIWAANGELIN ARDAYDA", response.data)
        self.assertNotIn(b"Daabac, soo dejiso PDF ama xaqiiji kaadhadhkii ugu dambeeyey", response.data)
        self.assertEqual(response.data.count(b'<select name="exam_id"'), 1)
        self.assertIn(b"<th>Ardeyga</th><th>Heerka</th><th>Fasalka</th><th>Qeybta</th>", response.data)
        self.assertIn(b'class="idc-action-col">Kaadhka</th>', response.data)
        self.assertIn(b'<th>Sawirka</th>', response.data)
        self.assertIn(b'data-label="Sawirka"', response.data)
        self.assertIn(
            "<th>Sawirka</th><th>Ardeyga</th><th>Nuuca Imtixaanka</th><th>Heerka</th>"
            "<th>Fasalka</th><th>Tr. Bixidda</th><th>Tr. Dhicidda</th><th>Xaalad</th><th>Ficillo</th>".encode(),
            response.data,
        )
        self.assertIn("Hawl-geli kuwa la xushay".encode(), response.data)
        self.assertIn("Hawl-geli Dhammaan".encode(), response.data)
        self.assertIn(">Hawl-geli</span>".encode(), response.data)
        self.assertNotIn(b"Generate Selected", response.data)
        self.assertNotIn(b"Generate All Filtered", response.data)
        self.assertIn("Xulo dhammaan inta muuqata".encode(), response.data)
        self.assertIn("0 la xushay".encode(), response.data)
        for label in (
            "Raadi ardayda", "Sannad-dugsiyeedka", "Dhammaan sannadaha",
            "Nuuca imtixaanka", "Dooro nuuca imtixaanka", "Heerka",
            "Dhammaan heerarka", "Fasalka", "Dhammaan fasallada",
            "Qeybta", "Dhammaan qaybaha", "Shaandhee", "Nadiifi",
        ):
            self.assertIn(label.encode(), response.data)

        parser = FormNestingParser()
        parser.feed(response.get_data(as_text=True))
        self.assertFalse(parser.has_nested_form)

    def test_initial_history_load_uses_bounded_query_count(self):
        for index in range(30):
            student = self._student(
                f"LOAD-{index:03}",
                f"Arday Tijaabo {index:03}",
                self.year,
                self.year_level,
                self.year_class,
            )
            db.session.add(IdCardIssue(
                token=f"history-load-token-{index}",
                student=student,
                academic_year=self.year,
                exam=self.exam,
                issue_date=date.today(),
                expiry_date=date.today() + timedelta(days=365),
                status="Active",
            ))
        db.session.commit()

        statements = []
        engine = db.engine
        def count_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith(("SELECT", "UPDATE", "INSERT", "DELETE")):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_statement)
        try:
            response = self.client.get("/admin/id-cards/")
        finally:
            event.remove(engine, "before_cursor_execute", count_statement)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Arday Tijaabo 029", response.data)
        self.assertLess(len(statements), 40, f"Expected batched history queries, got {len(statements)}")

    def test_validity_setting_saves_independently_and_updates_new_card_expiry(self):
        db.session.add(Setting(key="id_card_primary_color", value="#123456"))
        db.session.commit()

        response = self.client.post("/admin/id-cards/", data={
            "action": "save_validity",
            "id_card_issue_months": "24",
        })
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(db.session.get(Setting, "id_card_issue_months").value, "24")
        self.assertEqual(db.session.get(Setting, "id_card_primary_color").value, "#123456")

        page = self.client.get("/admin/id-cards/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="idcValidityMonths"', page.data)
        self.assertIn(b'value="24"', page.data)
        self.assertIn(b'data-validity-preset="24"', page.data)

        designer_save = self.client.post("/admin/id-cards/", data={
            "id_card_primary_color": "#654321",
        })
        self.assertIn(designer_save.status_code, (302, 303))
        self.assertEqual(db.session.get(Setting, "id_card_issue_months").value, "24")

        generated = self.client.post("/admin/id-cards/bulk-generate", data={
            "scope": "filtered",
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "q": "",
            "level": "",
            "academic_year_class_id": "",
            "section": "",
        })
        self.assertIn(generated.status_code, (302, 303))
        issue = IdCardIssue.query.filter_by(student_id=self.first_student.id, exam_id=self.exam.id).one()
        self.assertEqual(issue.expiry_date, add_months(date.today(), 24))

    def test_validity_setting_rejects_values_outside_supported_range(self):
        for value in ("0", "121", "not-a-number"):
            with self.subTest(value=value):
                response = self.client.post("/admin/id-cards/", data={
                    "action": "save_validity",
                    "id_card_issue_months": value,
                })
                self.assertIn(response.status_code, (302, 303))
                self.assertIsNone(db.session.get(Setting, "id_card_issue_months"))

    def test_reissuing_active_card_applies_new_validity_period(self):
        saved = self.client.post("/admin/id-cards/", data={
            "action": "save_validity",
            "id_card_issue_months": "6",
        })
        self.assertIn(saved.status_code, (302, 303))

        old_issue_date = date.today() - timedelta(days=20)
        issue = IdCardIssue(
            token="active-card-old-validity-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=old_issue_date,
            expiry_date=add_months(old_issue_date, 12),
            status="Active",
        )
        db.session.add(issue)
        db.session.commit()
        old_token = issue.token

        response = self.client.post(f"/admin/id-cards/generate/{self.first_student.id}", data={
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
        })
        self.assertIn(response.status_code, (302, 303))

        refreshed = db.session.get(IdCardIssue, issue.id)
        self.assertEqual(refreshed.issue_date, date.today())
        self.assertEqual(refreshed.expiry_date, add_months(date.today(), 6))
        self.assertNotEqual(refreshed.token, old_token)

    def test_card_qr_opens_action_chooser_and_old_qr_is_kept_compatible(self):
        issue = IdCardIssue(
            token="public-id-card-qr-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=add_months(date.today(), 12),
            status="Active",
        )
        db.session.add(issue)
        db.session.commit()

        with self.app.test_request_context():
            qr_url = id_card_qr_payload(issue)["url"]
        self.assertIn(f"/qr/{issue.token}", qr_url)
        self.assertNotIn("/admin/", qr_url)

        anonymous_client = self.app.test_client()
        old_card_response = anonymous_client.get(
            f"/verify-id/{issue.token}?exam_id={self.exam.id}"
        )
        self.assertEqual(old_card_response.status_code, 302)
        self.assertIn(f"/qr/{issue.token}", old_card_response.location)
        self.assertIn(f"exam_id={self.exam.id}", old_card_response.location)

        response = anonymous_client.get(qr_url.replace("http://localhost", ""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Xaqiiji Ardeyga".encode(), response.data)
        self.assertIn("Gudbi Warbixin Dhacdo".encode(), response.data)
        self.assertIn("action=verify".encode(), response.data)

        verify_response = anonymous_client.get(
            f"/verify-id/{issue.token}?action=verify&exam_id={self.exam.id}"
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertIn(b"Ardayga Koowaad", verify_response.data)

    def test_same_card_qr_shows_seat_added_after_card_was_issued(self):
        issue = IdCardIssue(
            token="seat-mixer-stable-qr-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=add_months(date.today(), 12),
            status="Active",
        )
        db.session.add(issue)
        db.session.commit()

        with self.app.test_request_context():
            qr_url = id_card_qr_payload(issue)["url"]
        local_path = qr_url.replace("http://localhost", "")
        anonymous_client = self.app.test_client()
        landing = anonymous_client.get(local_path)
        self.assertEqual(landing.status_code, 200)
        verify_href = f"/verify-id/{issue.token}?action=verify&exam_id={self.exam.id}"
        before_seating = anonymous_client.get(verify_href)
        self.assertEqual(before_seating.status_code, 200)
        self.assertNotIn("HOOLKA IMTIXAANKA".encode(), before_seating.data)

        hall = ExamHall(
            name="Hoolka A",
            code="IDQR-HALL-A",
            capacity=30,
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
        )
        db.session.add(hall)
        db.session.flush()
        version = ExamHallVersion(exam_hall_id=hall.id, version_number=1, label="Version 1")
        db.session.add(version)
        db.session.flush()
        db.session.add(SeatMixerAssignment(
            version_id=version.id,
            student_id=self.first_student.id,
            row_number=0,
            table_number=1,
            seat_number=0,
        ))
        db.session.commit()

        after_seating = anonymous_client.get(local_path)
        self.assertEqual(after_seating.status_code, 200)
        verify_after_seating = anonymous_client.get(verify_href)
        self.assertEqual(verify_after_seating.status_code, 200)
        self.assertIn("Hoolka A".encode(), verify_after_seating.data)
        self.assertIn("Saf 1 · Miis 2 · Kursi 1".encode(), verify_after_seating.data)

    def test_scoped_exam_only_shows_and_generates_students_in_its_configured_class(self):
        second_legacy_class = AcademicClass(
            academic_level=self.level,
            name="Form 2",
            sort_order=2,
        )
        db.session.add(second_legacy_class)
        db.session.flush()
        second_year_class = AcademicYearClass(
            academic_year_level=self.year_level,
            legacy_class=second_legacy_class,
            name="Form 2",
            sort_order=2,
        )
        db.session.add(second_year_class)
        db.session.flush()
        other_class_student = self._student(
            "ID-004", "Arday Fasalka Kale", self.year, self.year_level, second_year_class
        )
        self.exam.academic_level_id = self.level.id
        self.exam.academic_class_id = self.legacy_class.id
        db.session.commit()

        response = self.client.get(
            f"/admin/id-cards/?year_id={self.year.id}&exam_id={self.exam.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ardayga Koowaad", response.data)
        self.assertNotIn(b"Arday Fasalka Kale", response.data)

        self.client.post("/admin/id-cards/bulk-generate", data={
            "scope": "filtered",
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "q": "",
            "level": "",
            "academic_year_class_id": "",
            "section": "",
        })
        self.assertEqual(
            IdCardIssue.query.filter_by(academic_year_id=self.year.id, exam_id=self.exam.id).count(),
            2,
        )
        self.assertEqual(
            IdCardIssue.query.filter_by(student_id=other_class_student.id, exam_id=self.exam.id).count(),
            0,
        )

    def test_year_enrollment_wins_over_stale_student_level_snapshot(self):
        legacy_other_level = AcademicLevel(name="Dugsi Dhexe", sort_order=2)
        db.session.add(legacy_other_level)
        db.session.flush()
        db.session.add(AcademicYearLevel(
            academic_year=self.year,
            legacy_level=legacy_other_level,
            name="Dugsi Dhexe",
            sort_order=2,
        ))
        self.first_student.academic_year_id = self.year.id
        self.first_student.academic_level_id = legacy_other_level.id
        self.first_student.level = legacy_other_level.name
        db.session.commit()

        response = self.client.get(
            f"/admin/id-cards/?year_id={self.year.id}&exam_id={self.exam.id}&level=Dugsi+Dhexe"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Ardayga Koowaad", response.data)

    def test_level_selection_limits_class_options_and_rejects_mismatched_class(self):
        other_level = AcademicLevel(name="Dugsi Dhexe", sort_order=2)
        db.session.add(other_level)
        db.session.flush()
        other_year_level = AcademicYearLevel(
            academic_year=self.year,
            legacy_level=other_level,
            name=other_level.name,
            sort_order=2,
        )
        db.session.add(other_year_level)
        other_legacy_class = AcademicClass(
            academic_level=other_level,
            name="7aad",
            sort_order=1,
        )
        db.session.add(other_legacy_class)
        db.session.flush()
        other_year_class = AcademicYearClass(
            academic_year_level=other_year_level,
            legacy_class=other_legacy_class,
            name="7aad",
            sort_order=1,
        )
        db.session.add(other_year_class)
        db.session.flush()
        other_level_student = self._student(
            "ID-005", "Arday Dugsi Dhexe", self.year, other_year_level, other_year_class
        )
        db.session.commit()

        response = self.client.get(
            f"/admin/id-cards/?year_id={self.year.id}&exam_id={self.exam.id}&level=Dugsi+Dhexe"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Arday Dugsi Dhexe", response.data)
        self.assertNotIn(b"Ardayga Koowaad", response.data)
        options = OptionParser()
        options.feed(response.get_data(as_text=True))
        form_one = next(option for option in options.options if option["text"].strip() == "Form 1")
        class_seven = next(option for option in options.options if option["text"].strip() == "7aad")
        self.assertEqual(form_one["attrs"].get("data-level"), "Dugsi Sare")
        self.assertIn("hidden", form_one["attrs"])
        self.assertIn("disabled", form_one["attrs"])
        self.assertEqual(class_seven["attrs"].get("data-level"), "Dugsi Dhexe")
        self.assertNotIn("hidden", class_seven["attrs"])

        mismatched_response = self.client.get(
            f"/admin/id-cards/?year_id={self.year.id}&exam_id={self.exam.id}"
            f"&level=Dugsi+Dhexe&academic_year_class_id={self.year_class.id}"
        )
        self.assertEqual(mismatched_response.status_code, 200)
        self.assertNotIn(b"Arday Dugsi Dhexe", mismatched_response.data)

    def test_bulk_generation_skips_blocked_student_and_keeps_successful_cards(self):
        blocked_issue = IdCardIssue(
            token="blocked-student-card-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=date.today() + timedelta(days=30),
            status="Blocked",
        )
        db.session.add(blocked_issue)
        db.session.commit()

        response = self.client.post("/admin/id-cards/bulk-generate", data={
            "scope": "filtered",
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "return_year_id": str(self.year.id),
            "return_exam_id": str(self.exam.id),
            "q": "",
            "level": "Dugsi Sare",
            "academic_year_class_id": str(self.year_class.id),
            "section": "",
        })

        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/admin/id-cards/print?", response.location)
        print_page = self.client.get(response.location)
        self.assertEqual(print_page.status_code, 200)
        self.assertIn(b"Waxaa la diyaariyey 1 kaadh", print_page.data)
        self.assertIn(b"1 arday waa laga booday", print_page.data)

        self.assertEqual(
            IdCardIssue.query.filter_by(student_id=self.first_student.id, exam_id=self.exam.id).count(),
            1,
        )
        self.assertEqual(
            IdCardIssue.query.filter_by(student_id=self.second_student.id, exam_id=self.exam.id).count(),
            1,
        )

    def test_failed_generation_redirect_preserves_roster_filters(self):
        response = self.client.post("/admin/id-cards/bulk-generate", data={
            "single_student_id": str(self.other_student.id),
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "return_year_id": str(self.year.id),
            "return_exam_id": str(self.exam.id),
            "q": "Arday",
            "level": "Dugsi Sare",
            "academic_year_class_id": str(self.year_class.id),
            "section": "A",
        })

        self.assertIn(response.status_code, (302, 303))
        self.assertIn(f"year_id={self.year.id}", response.location)
        self.assertIn(f"exam_id={self.exam.id}", response.location)
        self.assertIn("level=Dugsi+Sare", response.location)
        self.assertIn(f"academic_year_class_id={self.year_class.id}", response.location)
        self.assertIn("section=A", response.location)
        self.assertIn("q=Arday", response.location)

    def test_blocked_single_student_returns_to_same_filters_with_reason(self):
        db.session.add(IdCardIssue(
            token="blocked-single-card-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today(),
            expiry_date=date.today() + timedelta(days=30),
            status="Blocked",
        ))
        db.session.commit()

        response = self.client.post("/admin/id-cards/bulk-generate", data={
            "single_student_id": str(self.first_student.id),
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "return_year_id": str(self.year.id),
            "return_exam_id": str(self.exam.id),
            "q": "ID-001",
            "level": "Dugsi Sare",
            "academic_year_class_id": str(self.year_class.id),
            "section": "",
        })

        self.assertIn(response.status_code, (302, 303))
        self.assertIn("level=Dugsi+Sare", response.location)
        self.assertIn(f"academic_year_class_id={self.year_class.id}", response.location)
        dashboard = self.client.get(response.location)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Kaadh lama hawlgelin", dashboard.data)
        self.assertIn(b"1 kaadh xanniban", dashboard.data)

    def test_one_exam_type_generates_all_filtered_students_and_reuses_expired_first_card(self):
        old_issue = IdCardIssue(
            token="old-expired-first-card-token",
            student=self.first_student,
            academic_year=self.year,
            exam=self.exam,
            issue_date=date.today() - timedelta(days=400),
            expiry_date=date.today() - timedelta(days=30),
            status="Expired",
        )
        db.session.add(old_issue)
        db.session.commit()
        old_issue_id = old_issue.id

        response = self.client.post("/admin/id-cards/bulk-generate", data={
            "scope": "filtered",
            "academic_year_id": str(self.year.id),
            "exam_id": str(self.exam.id),
            "return_year_id": str(self.year.id),
            "return_exam_id": str(self.exam.id),
            "q": "",
            "level": "",
            "academic_year_class_id": "",
            "section": "",
        })

        self.assertIn(response.status_code, (302, 303))
        self.assertIn("back_exam_id", response.location)
        print_page = self.client.get(response.location)
        self.assertEqual(print_page.status_code, 200)
        self.assertIn(b"Dib-u-noqo", print_page.data)
        self.assertIn(f"year_id={self.year.id}".encode(), print_page.data)
        self.assertIn(f"exam_id={self.exam.id}".encode(), print_page.data)
        issues = IdCardIssue.query.filter_by(academic_year_id=self.year.id, exam_id=self.exam.id).all()
        self.assertEqual(len(issues), 2)
        refreshed = db.session.get(IdCardIssue, old_issue_id)
        self.assertEqual(refreshed.status, "Active")
        self.assertGreaterEqual(refreshed.expiry_date, date.today())
        self.assertNotEqual(refreshed.token, "old-expired-first-card-token")
        self.assertEqual(
            IdCardIssue.query.filter_by(student_id=self.other_student.id, exam_id=self.exam.id).count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
