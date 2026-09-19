"""Phase 2E coverage for Behavior portal and academic report projections."""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import re

from sqlalchemy import event

from app import create_app, db
from app.behavior_grading import behavior_grade_for_score, behavior_grade_scales
from sqlalchemy.exc import OperationalError
from app.behavior_attendance import ensure_attendance_defaults, mark_attendance
from app.behavior_reporting import build_behavior_report_categories, get_behavior_report_data
from app.behavior_service import (
    attendance_points_projection,
    calculate_annual_behavior_score,
    calculate_session_score,
    record_event,
    void_event,
)
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAttendanceRecord,
    BehaviorConfiguration,
    BehaviorAttendanceStatus,
    BehaviorAction,
    BehaviorCategory,
    BehaviorEvent,
    BehaviorGradeScale,
    BehaviorSession,
    BehaviorSubCategory,
    Exam,
    GradeScale,
    Result,
    Setting,
    Student,
    StudentEnrollment,
    Subject,
    User,
)
from app.services import result_payload


class TestPhase2EBehaviorReporting(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2e-reporting-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.admin = User(
            username="phase2e-admin",
            full_name="Phase 2E Admin",
            role="admin",
            is_active=True,
        )
        self.admin.set_password("test-password")
        self.year_one = AcademicYear(name="2026-2027", is_current=True)
        self.year_two = AcademicYear(name="2027-2028", is_current=False)
        self.level_one = AcademicLevel(name="Secondary", sort_order=1, is_active=True)
        self.class_one = AcademicClass(
            name="Form Four",
            academic_level=self.level_one,
            sort_order=1,
            is_active=True,
        )
        db.session.add_all([
            self.admin,
            self.year_one,
            self.year_two,
            self.level_one,
            self.class_one,
        ])
        db.session.flush()

        self.year_level_one = AcademicYearLevel(
            academic_year=self.year_one,
            legacy_level=self.level_one,
            name="Secondary",
            sort_order=1,
            is_active=True,
        )
        self.year_class_one = AcademicYearClass(
            academic_year_level=self.year_level_one,
            legacy_class=self.class_one,
            name="Form Four",
            sort_order=1,
            is_active=True,
        )
        self.ordinary_subject = Subject(
            name="Mathematics",
            academic_level=self.level_one,
            max_score=100,
            is_active=True,
        )
        self.behavior_subject = AcademicYearSubject(
            academic_year=self.year_one,
            academic_year_level=self.year_level_one,
            name="Dabeecad",
            subject_kind="behavior",
            max_score=0,
            is_active=True,
        )
        self.ordinary_year_subject = AcademicYearSubject(
            academic_year=self.year_one,
            academic_year_level=self.year_level_one,
            legacy_subject=self.ordinary_subject,
            name="Mathematics",
            subject_kind="exam",
            max_score=100,
            is_active=True,
        )
        self.exam_one = Exam(
            name="1st Monthly",
            academic_year=self.year_one,
            academic_level=self.level_one,
            academic_class=self.class_one,
            is_active=True,
            is_published=True,
        )
        self.exam_two = Exam(
            name="2nd Monthly",
            academic_year=self.year_one,
            academic_level=self.level_one,
            academic_class=self.class_one,
            is_active=True,
            is_published=True,
        )
        self.student = Student(
            student_code="PHASE2E001",
            full_name="Behavior Report Student",
            mother_name="Report Parent",
            is_active=True,
        )
        db.session.add_all([
            self.year_level_one,
            self.year_class_one,
            self.ordinary_subject,
            self.behavior_subject,
            self.ordinary_year_subject,
            self.exam_one,
            self.exam_two,
            self.student,
        ])
        db.session.flush()

        self.enrollment = StudentEnrollment(
            student=self.student,
            academic_year=self.year_one,
            academic_year_level=self.year_level_one,
            academic_year_class=self.year_class_one,
            status="active",
            academic_outcome="pending",
            enrollment_source="manual",
        )
        self.configuration = BehaviorConfiguration(
            academic_year=self.year_one,
            academic_year_level=self.year_level_one,
            behavior_subject=self.behavior_subject,
            frequency="monthly",
            status="active",
        )
        db.session.add_all([self.enrollment, self.configuration])
        db.session.flush()
        self.session_one = BehaviorSession(
            configuration=self.configuration,
            exam=self.exam_one,
            session_label="1st Monthly",
            maximum_score=50,
            sort_order=1,
            is_active=True,
        )
        self.session_two = BehaviorSession(
            configuration=self.configuration,
            exam=self.exam_two,
            session_label="2nd Monthly",
            maximum_score=50,
            sort_order=2,
            is_active=True,
        )
        self.result = Result(
            student=self.student,
            exam=self.exam_one,
            subject=self.ordinary_subject,
            score=80,
            is_published=True,
        )
        db.session.add_all([self.session_one, self.session_two, self.result])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        return client

    def test_adapter_uses_service_base_score_and_dynamic_sessions(self):
        reports = get_behavior_report_data(self.student, self.exam_one)

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report["subject_name"], "Dabeecad")
        self.assertTrue(report["available"])
        self.assertEqual(report["annual_maximum"], 50)
        self.assertEqual(report["annual_score"], 25)
        self.assertEqual(report["percentage"], 50)
        self.assertEqual(len(report["session_results"]), 1)
        self.assertEqual(report["session_results"][0]["session_label"], "1st Monthly")
        self.assertEqual(report["session_results"][0]["final_score"], 25)
        self.assertTrue(report["is_pass"])
        self.assertEqual(report["score_tone"], "pass")
        self.assertEqual(len(report["current_sessions"]), 1)
        self.assertTrue(report["current_sessions"][0]["is_current"])

    def test_behavior_events_remain_visible_when_attendance_is_pending(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 12
        self.session_one.attendance_allocation = 8
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive",
            polarity="positive",
            is_active=True,
        )
        db.session.add(category)
        db.session.flush()
        action = BehaviorAction(
            behavior_category_id=category.id,
            name="Helpful",
            level_number=1,
            points=3,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add(action)
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            idempotency_key="phase2e-partial-behavior",
        )
        db.session.commit()

        score = calculate_session_score(self.configuration, self.session_one, self.enrollment)
        self.assertEqual(score["behavior_status"], "VALID")
        self.assertEqual(score["attendance_status"], "INCOMPLETE")
        self.assertEqual(score["behavior_score"], Decimal("9.000"))
        self.assertIsNone(score["final_score"])

        report = get_behavior_report_data(self.student, self.exam_one)[0]
        session = report["current_sessions"][0]
        self.assertEqual(session["event_count"], 1)
        self.assertEqual(session["behavior_score"], Decimal("9.000"))
        self.assertEqual(session["behavior_status"], "VALID")
        self.assertEqual(session["attendance"]["scoring_status"], "INCOMPLETE")

        portal = self.app.test_client().post(
            "/result",
            data={
                "student_id": self.student.student_code,
                "year_id": self.year_one.id,
                "exam_id": self.exam_one.id,
            },
        )
        self.assertEqual(portal.status_code, 200)
        portal_body = portal.get_data(as_text=True)
        self.assertIn("9.00", portal_body)
        self.assertNotIn("Hab-dhaqan waa la diiwaangeliyey", portal_body)

    def test_attendance_combines_with_behavior_baseline_when_behavior_is_pending(self):
        self.session_one.maximum_score = 25
        self.session_one.behavior_allocation = 15
        self.session_one.attendance_allocation = 10
        ensure_attendance_defaults(self.configuration)
        db.session.flush()
        present = next(
            item for item in self.configuration.attendance_statuses if item.key == "present"
        )
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            present.id,
            date.today(),
            attendance_time="07:30",
        )
        db.session.commit()

        score = calculate_session_score(self.configuration, self.session_one, self.enrollment)
        self.assertEqual(score["behavior_status"], "VALID")
        self.assertEqual(score["attendance_status"], "VALID")
        self.assertEqual(score["attendance_score"], Decimal("1.000"))
        self.assertEqual(score["base_score"], Decimal("7.500"))
        self.assertEqual(score["final_score"], Decimal("8.500"))

        report = get_behavior_report_data(self.student, self.exam_one)[0]
        session = report["current_sessions"][0]
        self.assertEqual(session["event_count"], 0)
        self.assertEqual(len(session["attendance_records"]), 1)
        self.assertEqual(session["attendance_score"], Decimal("1.000"))
        self.assertEqual(session["behavior_status"], "VALID")
        self.assertEqual(session["final_score"], Decimal("8.500"))

        portal = self.app.test_client().post(
            "/result",
            data={
                "student_id": self.student.student_code,
                "year_id": self.year_one.id,
                "exam_id": self.exam_one.id,
            },
        )
        self.assertEqual(portal.status_code, 200)
        portal_body = portal.get_data(as_text=True)
        self.assertIn("8.50", portal_body)
        self.assertNotIn("Xaadir waa la diiwaangeliyey", portal_body)

    def test_voided_behavior_event_keeps_baseline_and_attendance_score(self):
        self.session_one.maximum_score = 25
        self.session_one.behavior_allocation = 15
        self.session_one.attendance_allocation = 10
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive",
            polarity="positive",
            is_active=True,
        )
        db.session.add(category)
        db.session.flush()
        action = BehaviorAction(
            behavior_category_id=category.id,
            name="Helpful",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add(action)
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            idempotency_key="phase2e-voided-baseline",
        )
        ensure_attendance_defaults(self.configuration)
        db.session.flush()
        present = next(
            item for item in self.configuration.attendance_statuses if item.key == "present"
        )
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            present.id,
            date.today(),
            attendance_time="07:30",
        )
        db.session.flush()
        event = BehaviorEvent.query.filter_by(
            student_enrollment_id=self.enrollment.id,
            behavior_session_id=self.session_one.id,
            status="active",
        ).one()
        void_event(event, self.admin.id, "Correction")
        db.session.commit()

        score = calculate_session_score(self.configuration, self.session_one, self.enrollment)
        self.assertEqual(score["base_score"], Decimal("7.500"))
        self.assertEqual(score["behavior_score"], Decimal("7.500"))
        self.assertEqual(score["attendance_score"], Decimal("1.000"))
        self.assertEqual(score["final_score"], Decimal("8.500"))
        self.assertEqual(score["positive_applied_points"], Decimal("0.000"))

    def test_behavior_and_attendance_combine_after_both_are_recorded(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 12
        self.session_one.attendance_allocation = 8
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive",
            polarity="positive",
            is_active=True,
        )
        db.session.add(category)
        db.session.flush()
        action = BehaviorAction(
            behavior_category_id=category.id,
            name="Helpful",
            level_number=1,
            points=3,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add(action)
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            idempotency_key="phase2e-combined-components",
        )
        ensure_attendance_defaults(self.configuration)
        db.session.flush()
        present = next(
            item for item in self.configuration.attendance_statuses if item.key == "present"
        )
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            present.id,
            date.today(),
            attendance_time="07:30",
        )
        db.session.commit()

        score = calculate_session_score(self.configuration, self.session_one, self.enrollment)
        self.assertEqual(score["behavior_status"], "VALID")
        self.assertEqual(score["attendance_status"], "VALID")
        self.assertEqual(score["final_score"], Decimal("10.000"))
        self.assertEqual(score["scoring_status"], "COMPLETE")

    def test_result_payload_includes_behavior_in_report_totals(self):
        payload = result_payload(self.student, exam=self.exam_one, public_only=False)

        self.assertEqual(payload["total"], 105)
        self.assertEqual(payload["max_total"], 150)
        self.assertEqual(len(payload["subjects"]), 1)
        self.assertEqual(payload["behavior_reports"][0]["annual_score"], 25)
        self.assertEqual(payload["behavior_reports"][0]["annual_maximum"], 50)

    def test_behavior_uses_selected_exam_grade_scale_across_reports(self):
        # Keep an ordinary grade with a deliberately different result. The
        # Behavior report must use only its configuration-owned scale.
        ordinary_grade = GradeScale(
            grade="C",
            min_score=50,
            max_score=59.999,
            comment="Needs improvement",
            grade_point=2.0,
            is_pass=True,
            exam_id=self.exam_one.id,
            sort_order=1,
        )
        behavior_grade = BehaviorGradeScale(
            configuration=self.configuration,
            session=self.session_one,
            grade="F",
            min_score=0,
            max_score=50,
            grade_point=0.0,
            description="Behavior fail",
            sort_order=1,
            is_active=True,
            is_pass=False,
        )
        db.session.add_all([ordinary_grade, behavior_grade])
        db.session.commit()

        report = get_behavior_report_data(self.student, self.exam_one)[0]
        self.assertEqual(report["grade"]["grade"], "F")
        self.assertEqual(report["grade"]["grade_point"], 0.0)
        self.assertEqual(report["session_results"][0]["grade"]["grade"], "F")

        payload = result_payload(self.student, exam=self.exam_one, public_only=False)
        self.assertEqual(payload["behavior_reports"][0]["grade"]["grade"], "F")
        self.assertEqual(payload["behavior_reports"][0]["grade_point"], 0.0)

        client = self._client_as_admin()
        class_pdf = client.get(
            "/admin/advanced-results/export-class-pdf"
            f"?year_id={self.year_one.id}&exam_id={self.exam_one.id}"
            f"&level_id={self.level_one.id}&class_id={self.class_one.id}"
        )
        self.assertEqual(class_pdf.status_code, 200)
        class_pdf_body = class_pdf.get_data(as_text=True)
        self.assertIn(
            '<td class="behavior-class-cell"><strong>25.00</strong></td>',
            class_pdf_body,
        )
        self.assertNotIn("F · GP 0.00", class_pdf_body)

    def test_behavior_grade_scales_are_loaded_once_per_request_scope(self):
        db.session.add(
            BehaviorGradeScale(
                configuration=self.configuration,
                session=self.session_one,
                grade="A",
                min_score=25,
                max_score=50,
                grade_point=4.0,
                description="Behavior excellent",
                sort_order=1,
                is_active=True,
                is_pass=True,
            )
        )
        db.session.commit()
        db.session.info.pop("_behavior_grade_scales_cache", None)
        statements = []

        def count_grade_scale_query(conn, cursor, statement, parameters, context, executemany):
            if "behavior_grade_scales" in statement.lower():
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", count_grade_scale_query)
        try:
            first = behavior_grade_for_score(self.session_one, Decimal("25"))
            second = behavior_grade_for_score(self.session_one, Decimal("25"))
            self.assertEqual(first["grade"], "A")
            self.assertEqual(second["grade"], "A")
            self.assertEqual(behavior_grade_scales(self.session_one, active_only=True), behavior_grade_scales(self.session_one, active_only=True))
        finally:
            event.remove(db.engine, "before_cursor_execute", count_grade_scale_query)

        self.assertEqual(len(statements), 1)

    def test_transient_grade_lookup_failure_does_not_break_behavior_page(self):
        error = OperationalError("SELECT behavior grade scales", {}, Exception("proxy timeout"))
        with patch("app.behavior_grading.behavior_grade_scales", side_effect=error):
            payload = behavior_grade_for_score(self.session_one, Decimal("25"))

        self.assertEqual(payload["grade"], "NOT CONFIGURED")
        self.assertIn("temporarily unavailable", payload["description"])

    def test_behavior_grade_management_is_scoped_and_does_not_use_ordinary_scale(self):
        self.admin.role = "super_admin"
        db.session.commit()
        client = self._client_as_admin()
        page = client.get(
            f"/admin/behavior/grade-management?config_id={self.configuration.id}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Scale editor", page.data)
        response = client.post(
            "/admin/behavior/grade-management",
            data={
                "config_id": self.configuration.id,
                "session_id": self.session_one.id,
                "grade": "A",
                "min_score": "40",
                "max_score": "50",
                "grade_point": "4",
                "description": "Behavior excellent",
                "sort_order": "1",
                "is_active": "on",
                "is_pass": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        saved = BehaviorGradeScale.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            behavior_session_id=self.session_one.id,
            grade="A",
        ).one()
        self.assertEqual(float(saved.min_score), 40.0)
        self.assertEqual(float(saved.max_score), 50.0)
        self.assertEqual(float(saved.grade_point), 4.0)

    def test_print_and_download_keep_one_behavior_row_without_duplicate_panel(self):
        client = self.app.test_client()
        print_body = client.get(
            f"/print/{self.student.student_code}?exam_id={self.exam_one.id}"
        )
        download_body = client.get(
            f"/download/{self.student.student_code}?exam_id={self.exam_one.id}"
        )

        for response in (print_body, download_body):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn("Dabeecad", body)
                self.assertIn("behavior-print-diamond", body)
                self.assertNotIn("behavior-subject-heart", body)
                self.assertNotIn("behavior-print-heart", body)
                self.assertNotIn("fa-heart-pulse", body)
                self.assertIn("25.00", body)
                self.assertIn("50.00", body)
                self.assertNotIn("Behavior Sessions", body)
                self.assertNotIn("tis-behavior-box", body)

    def test_student_portal_renders_one_behavior_row_and_detail_for_selected_exam(self):
        response = self.app.test_client().post(
            "/result",
            data={
                "student_id": self.student.student_code,
                "year_id": self.year_one.id,
                "exam_id": self.exam_one.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Dabeecad", body)
        self.assertIn('class="behavior-result-row"', body)
        self.assertIn('class="behavior-subject-trigger"', body)
        self.assertIn('class="behavior-subject-diamond"', body)
        self.assertIn('dynamic-grade-badge mark-badge behavior-score-badge', body)
        self.assertIn('id="behaviorChoiceDialog1"', body)
        self.assertIn("Open official read-only report", body)
        self.assertNotIn("behavior-subject-heart", body)
        self.assertNotIn("fa-heart-pulse", body)
        self.assertNotIn("DABEECADDA ARDEYGA", body)
        self.assertNotIn("Behavior score from configured sessions", body)

        api_response = self.app.test_client().get(
            f"/api/results/{self.student.student_code}"
        )
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(
            api_response.get_json()["behavior_reports"][0]["annual_maximum"],
            50,
        )

    def test_step4_portal_and_reading_views_use_the_canonical_combined_projection(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 12
        self.session_one.attendance_allocation = 8
        ensure_attendance_defaults(self.configuration)
        db.session.flush()
        present = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="present",
        ).one()
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            present.id,
            date.today(),
            note="On time",
            attendance_time="07:30",
        )
        db.session.commit()

        payload = result_payload(self.student, exam=self.exam_one, public_only=False)
        report = payload["behavior_reports"][0]
        session = report["session_results"][0]
        ledger = session["ledger"]
        self.assertEqual(ledger["session"]["session_maximum"], Decimal("20.000"))
        self.assertEqual(ledger["session"]["behavior_allocation"], Decimal("12.000"))
        self.assertEqual(ledger["session"]["attendance_allocation"], Decimal("8.000"))
        self.assertEqual(ledger["session"]["grand_total"], Decimal("7.000"))
        self.assertEqual(ledger["behavior"]["earned_score"], session["behavior_score"])
        self.assertEqual(ledger["attendance"]["earned_score"], session["attendance_score"])
        self.assertEqual(session["final_score"], Decimal("7.000"))
        self.assertEqual(session["behavior_status"], "VALID")
        self.assertEqual(session["attendance_status"], "VALID")
        self.assertEqual(session["attendance_records"][0]["status_label"], "Joogid")

        portal = self.app.test_client().post(
            "/result",
            data={"student_id": self.student.student_code, "year_id": self.year_one.id, "exam_id": self.exam_one.id},
        )
        self.assertEqual(portal.status_code, 200)
        portal_body = portal.get_data(as_text=True)
        self.assertIn("Behavior + Attendance", portal_body)
        self.assertNotIn("Behavior <b>", portal_body)
        self.assertIn("Open official read-only report", portal_body)
        self.assertNotIn("Attendance reading view", portal_body)
        self.assertNotIn("Attendance records", portal_body)
        self.assertNotIn("Grand <b>", portal_body)

        behavior_view = self.app.test_client().get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/read"
        )
        self.assertEqual(behavior_view.status_code, 200)
        behavior_body = behavior_view.get_data(as_text=True)
        self.assertIn("STUDENT BEHAVIOR REPORT", behavior_body)
        self.assertIn("Behavior Allocation", behavior_body)
        self.assertIn("Positive Ledger", behavior_body)
        self.assertIn("Negative Ledger", behavior_body)
        self.assertIn("Download PDF", behavior_body)
        self.assertIn(f"{float(session['behavior_score']):.2f}", behavior_body)
        self.assertIn(f"{float(ledger['session']['session_maximum']):.2f}", behavior_body)

        behavior_download = self.app.test_client().get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/read?download=1"
        )
        self.assertEqual(behavior_download.status_code, 200)
        self.assertEqual(behavior_download.mimetype, "application/pdf")
        self.assertTrue(behavior_download.data.startswith(b"%PDF"))
        self.assertIn(
            "Behavior Report - Diiwaanka Hab-dhaqanka - (2026-2027).pdf",
            behavior_download.headers["Content-Disposition"],
        )
        from app.routes_public import _behavior_download_filename

        self.assertEqual(
            _behavior_download_filename(self.student, self.exam_one),
            "Behavior Report - Diiwaanka Hab-dhaqanka - (2026-2027).pdf",
        )

        attendance_view = self.app.test_client().get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/attendance/read"
        )
        self.assertEqual(attendance_view.status_code, 200)
        attendance_body = attendance_view.get_data(as_text=True)
        self.assertIn("MONTHLY ATTENDANCE REPORT", attendance_body)
        self.assertIn("MONTHLY ATTENDANCE CALENDAR", attendance_body)
        self.assertIn("ATTENDANCE BREAKDOWN", attendance_body)
        self.assertIn("Download PDF", attendance_body)
        self.assertIn("Joogid", attendance_body)

        attendance_download = self.app.test_client().get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/attendance/read"
            "?download=1&attendance_date=2026-09-11"
        )
        self.assertEqual(attendance_download.status_code, 200)
        self.assertEqual(attendance_download.mimetype, "application/pdf")
        self.assertTrue(attendance_download.data.startswith(b"%PDF"))
        self.assertIn(
            "Behavior Report - Diiwaanka Xaadirka - Sep - (2026-2027).pdf",
            attendance_download.headers["Content-Disposition"],
        )
        from app.routes_public import _attendance_download_filename

        self.assertEqual(
            _attendance_download_filename(
                self.student,
                self.exam_one,
                date(2026, 9, 11),
                month_keys=[(2026, 9), (2026, 10)],
            ),
            "Behavior Report - Diiwaanka Xaadirka - Sep-Oct - (2026-2027).pdf",
        )

        wrong_scope = self.app.test_client().get(
            f"/behavior/{self.student.student_code}/{self.exam_two.id}/"
            f"{self.configuration.id}/{self.session_one.id}/read"
        )
        self.assertEqual(wrong_scope.status_code, 404)

        for path in (
            f"/print/{self.student.student_code}?exam_id={self.exam_one.id}",
            f"/download/{self.student.student_code}?exam_id={self.exam_one.id}",
        ):
            response = self.app.test_client().get(path)
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn(f"{float(session['final_score']):.2f}", body)
            self.assertIn(f"{float(session['maximum_score']):.2f}", body)

    def test_subcategory_reaches_admin_report_portal_and_pdf_without_new_column(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 12
        self.session_one.attendance_allocation = 8
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive Conduct",
            polarity="positive",
            is_active=True,
        )
        subcategory = BehaviorSubCategory(
            category=category,
            name="Respectful Conduct",
            is_active=True,
        )
        action = BehaviorAction(
            category=category,
            subcategory=subcategory,
            name="Respectful Communication",
            level_number=1,
            points=3,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([category, subcategory, action])
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            notes="Sub-category report coverage",
            idempotency_key="subcategory-report-1",
        )
        db.session.commit()

        client = self._client_as_admin()
        report_response = client.get(
            f"/admin/behavior/students/{self.enrollment.id}/report"
            f"?config_id={self.configuration.id}&session_id={self.session_one.id}"
        )
        self.assertEqual(report_response.status_code, 200)
        report_body = report_response.get_data(as_text=True)
        self.assertIn("Respectful Conduct", report_body)
        self.assertNotRegex(report_body, r"<th[^>]*>Sub-category")
        self.assertIn("Action / Behavior Details", report_body)

        portal_response = client.get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/read"
        )
        self.assertEqual(portal_response.status_code, 200)
        self.assertIn("Respectful Conduct", portal_response.get_data(as_text=True))

        pdf_response = client.get(
            f"/behavior/{self.student.student_code}/{self.exam_one.id}/"
            f"{self.configuration.id}/{self.session_one.id}/read?download=1"
        )
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, "application/pdf")
        self.assertTrue(pdf_response.data.startswith(b"%PDF"))

    def test_report_grouping_consolidates_unique_subcategories_and_preserves_identity(self):
        events = [
            {"category_id": 1, "category_name": "Positive Conduct", "subcategory_id": 11, "subcategory_name": "Respectful Conduct", "action_name": "Action 1", "polarity": "positive", "points": Decimal("1.00")},
            {"category_id": 1, "category_name": "Positive Conduct", "subcategory_id": 12, "subcategory_name": "Care for School Property", "action_name": "Action 2", "polarity": "positive", "points": Decimal("0.50")},
            {"category_id": 1, "category_name": "Positive Conduct", "subcategory_id": 11, "subcategory_name": "Respectful Conduct", "action_name": "Action 3", "polarity": "positive", "points": Decimal("0.75")},
            {"category_id": 1, "category_name": "Positive Conduct", "subcategory_id": None, "subcategory_name": None, "action_name": "Action 4", "polarity": "positive", "points": Decimal("1.25")},
            {"category_id": 1, "category_name": "Positive Conduct", "subcategory_id": None, "subcategory_name": None, "action_name": "Action 5", "polarity": "positive", "points": Decimal("0.25")},
        ]

        categories = build_behavior_report_categories(events)

        self.assertEqual(len(categories), 1)
        category = categories[0]
        self.assertEqual(category["name"], "Positive Conduct")
        self.assertEqual(
            [group["name"] for group in category["subgroups"]],
            ["Respectful Conduct", "Care for School Property", None],
        )
        self.assertEqual([len(group["events"]) for group in category["subgroups"]], [2, 1, 2])
        self.assertEqual(
            [event["action_name"] for event in category["subgroups"][0]["events"]],
            ["Action 1", "Action 3"],
        )
        self.assertEqual(
            [event["action_name"] for event in category["subgroups"][2]["events"]],
            ["Action 4", "Action 5"],
        )
        self.assertEqual(category["total"], Decimal("3.750"))
        self.assertNotIn(" · ", category["name"])
        self.assertEqual(len(category["subgroups"]), 3)

        same_name_events = [
            {"category_id": 1, "category_name": "Conduct", "subcategory_id": 11, "subcategory_name": "Respect", "action_name": "A", "polarity": "positive", "points": Decimal("1")},
            {"category_id": 2, "category_name": "Conduct", "subcategory_id": 21, "subcategory_name": "Respect", "action_name": "B", "polarity": "positive", "points": Decimal("1")},
        ]
        same_name_categories = build_behavior_report_categories(same_name_events)
        self.assertEqual(len(same_name_categories), 2)
        self.assertEqual([category["id"] for category in same_name_categories], [1, 2])

    def test_report_grouping_is_polarity_agnostic_and_preserves_category_total(self):
        events = [
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 11, "subcategory_name": "Respect", "action_name": "Positive A", "polarity": "positive", "points": Decimal("1.00")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 11, "subcategory_name": "Respect", "action_name": "Negative A", "polarity": "negative", "points": Decimal("0.50")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 11, "subcategory_name": "Respect", "action_name": "Positive B", "polarity": "positive", "points": Decimal("0.75")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 11, "subcategory_name": "Respect", "action_name": "Negative B", "polarity": "negative", "points": Decimal("1.00")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 12, "subcategory_name": "Care", "action_name": "Negative C", "polarity": "negative", "points": Decimal("1.00")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 12, "subcategory_name": "Care", "action_name": "Negative D", "polarity": "negative", "points": Decimal("0.25")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": 13, "subcategory_name": "Responsibility", "action_name": "Positive C", "polarity": "positive", "points": Decimal("1.00")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": None, "subcategory_name": None, "action_name": "Negative E", "polarity": "negative", "points": Decimal("0.50")},
            {"category_id": 1, "category_name": "ANSHAXA SUUBAN", "subcategory_id": None, "subcategory_name": None, "action_name": "Positive D", "polarity": "positive", "points": Decimal("0.25")},
        ]

        category = build_behavior_report_categories(events)[0]

        self.assertEqual(
            [group["name"] for group in category["subgroups"]],
            ["Respect", "Care", "Responsibility", None],
        )
        self.assertEqual([len(group["events"]) for group in category["subgroups"]], [4, 2, 1, 2])
        self.assertEqual(
            [event["polarity"] for event in category["subgroups"][0]["events"]],
            ["positive", "negative", "positive", "negative"],
        )
        self.assertEqual(category["total"], Decimal("-0.250"))

    def test_admin_report_renders_separate_subcategory_rowspans(self):
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive Conduct",
            polarity="positive",
            is_active=True,
        )
        first_subcategory = BehaviorSubCategory(category=category, name="Respectful Conduct", is_active=True)
        second_subcategory = BehaviorSubCategory(category=category, name="Care for School Property", is_active=True)
        first_action = BehaviorAction(
            category=category,
            subcategory=first_subcategory,
            name="Action One",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        second_action = BehaviorAction(
            category=category,
            subcategory=first_subcategory,
            name="Action Two",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        third_action = BehaviorAction(
            category=category,
            subcategory=second_subcategory,
            name="Action Three",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        fourth_action = BehaviorAction(
            category=category,
            name="Action Four",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([
            category,
            first_subcategory,
            second_subcategory,
            first_action,
            second_action,
            third_action,
            fourth_action,
        ])
        db.session.flush()
        for action, key in (
            (first_action, "grouped-report-1"),
            (third_action, "grouped-report-2"),
            (second_action, "grouped-report-3"),
            (fourth_action, "grouped-report-4"),
        ):
            record_event(
                self.configuration,
                self.enrollment,
                self.session_one,
                category,
                action,
                idempotency_key=key,
            )
        db.session.commit()

        response = self._client_as_admin().get(
            f"/admin/behavior/students/{self.enrollment.id}/report"
            f"?config_id={self.configuration.id}&session_id={self.session_one.id}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('rowspan="2"', body)
        self.assertIn('rowspan="1"', body)
        self.assertIn("Respectful Conduct", body)
        self.assertIn("Care for School Property", body)
        self.assertIn("Action One", body)
        self.assertIn("Action Two", body)
        self.assertIn("Action Three", body)
        self.assertIn("Action Four", body)
        self.assertEqual(body.count("Respectful Conduct"), 1)
        self.assertEqual(body.count("No sub-category"), 1)
        self.assertNotIn("Respectful Conduct · Care for School Property", body)
        self.assertEqual(body.count("Category Total"), 1)

    def test_whole_class_pdf_contains_behavior_column_and_combined_total(self):
        client = self._client_as_admin()
        response = client.get(
            "/admin/advanced-results/export-class-pdf"
            f"?year_id={self.year_one.id}&exam_id={self.exam_one.id}"
            f"&level_id={self.level_one.id}&class_id={self.class_one.id}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("DABEECAD", body)
        self.assertIn("BEHAVIOR / 50.00", body)
        self.assertIn("25.00", body)
        self.assertIn("Total<small>(150.00)</small>", body)

    def test_partial_annual_allocation_does_not_hide_selected_session(self):
        self.session_two.maximum_score = 25
        db.session.commit()

        report = get_behavior_report_data(self.student, self.exam_one)[0]

        self.assertTrue(report["available"])
        self.assertEqual(report["session_label"], "1st Monthly")
        self.assertEqual(report["session_maximum"], 50)
        self.assertEqual(report["session_score"], 25)
        self.assertEqual(len(report["session_results"]), 1)

    def test_other_exam_session_is_not_leaked_into_selected_exam(self):
        first_report = get_behavior_report_data(self.student, self.exam_one)[0]
        second_report = get_behavior_report_data(self.student, self.exam_two)[0]

        self.assertEqual(first_report["session_label"], "1st Monthly")
        self.assertEqual(second_report["session_label"], "2nd Monthly")
        self.assertEqual(
            [item["session_label"] for item in second_report["session_results"]],
            ["2nd Monthly"],
        )

    def test_missing_selected_exam_session_is_explicitly_unavailable(self):
        exam_without_behavior_session = Exam(
            name="Final Examination",
            academic_year=self.year_one,
            academic_level=self.level_one,
            academic_class=self.class_one,
            is_active=True,
            is_published=True,
        )
        db.session.add(exam_without_behavior_session)
        db.session.commit()

        report = get_behavior_report_data(
            self.student,
            exam_without_behavior_session,
        )[0]

        self.assertFalse(report["available"])
        self.assertIsNone(report["session_score"])
        self.assertEqual(
            report["message"],
            "Behavior assessment is not yet available for this examination.",
        )

    def test_detail_projection_contains_only_selected_enrollment_events(self):
        category = BehaviorCategory(
            behavior_configuration_id=self.configuration.id,
            name="Positive",
            polarity="positive",
            is_active=True,
        )
        db.session.add(category)
        db.session.flush()
        action = BehaviorAction(
            behavior_category_id=category.id,
            name="Helpful",
            level_number=1,
            points=3,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add(action)
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            idempotency_key="phase2e-detail-event",
        )
        db.session.commit()

        report = get_behavior_report_data(self.student, self.exam_one)[0]
        session = report["session_results"][0]

        self.assertEqual(session["event_count"], 1)
        self.assertEqual(len(session["events"]), 1)
        self.assertEqual(session["events"][0]["action_name"], "Helpful")
        self.assertEqual(session["positive_points"], 3)
        self.assertEqual(session["final_score"], 28)

        api_response = self.app.test_client().get(
            f"/api/results/{self.student.student_code}"
        )
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(
            api_response.get_json()["behavior_reports"][0]["events"][0]["points"],
            3.0,
        )

    def test_behavior_configuration_isolated_to_selected_academic_year(self):
        year_two_level = AcademicYearLevel(
            academic_year=self.year_two,
            name="Secondary",
            sort_order=1,
            is_active=True,
        )
        year_two_subject = AcademicYearSubject(
            academic_year=self.year_two,
            academic_year_level=year_two_level,
            name="Dabeecad",
            subject_kind="behavior",
            max_score=0,
            is_active=True,
        )
        year_two_exam = Exam(
            name="1st Monthly",
            academic_year=self.year_two,
            academic_level=self.level_one,
            academic_class=self.class_one,
            is_active=True,
            is_published=True,
        )
        year_two_configuration = BehaviorConfiguration(
            academic_year=self.year_two,
            academic_year_level=year_two_level,
            behavior_subject=year_two_subject,
            frequency="monthly",
            status="active",
        )
        db.session.add_all([
            year_two_level,
            year_two_subject,
            year_two_exam,
            year_two_configuration,
        ])
        db.session.flush()
        db.session.add(
            BehaviorSession(
                configuration=year_two_configuration,
                exam=year_two_exam,
                session_label="1st Monthly",
                maximum_score=100,
                sort_order=1,
                is_active=True,
            )
        )
        db.session.commit()

        reports = get_behavior_report_data(self.student, self.exam_one)

        self.assertEqual([report["subject_name"] for report in reports], ["Dabeecad"])
        self.assertTrue(all(
            report["academic_year_id"] == self.year_one.id
            for report in reports
        ))

    def test_phase3_variable_maximums_use_natural_score_grades_and_annual_weight(self):
        self.session_one.maximum_score = 17
        self.session_one.behavior_allocation = 17
        self.session_one.attendance_allocation = 0
        self.session_two.maximum_score = 15
        self.session_two.behavior_allocation = 15
        self.session_two.attendance_allocation = 0
        for session in (self.session_one, self.session_two):
            db.session.add_all([
                BehaviorGradeScale(
                    configuration=self.configuration,
                    session=session,
                    grade="F",
                    min_score=0,
                    max_score=10 if session is self.session_one else 8,
                    grade_point=0,
                    description="Fail",
                    sort_order=1,
                    is_active=True,
                    is_pass=False,
                ),
                BehaviorGradeScale(
                    configuration=self.configuration,
                    session=session,
                    grade="A",
                    min_score=10.001 if session is self.session_one else 8.001,
                    max_score=session.maximum_score,
                    grade_point=4,
                    description="Excellent",
                    sort_order=2,
                    is_active=True,
                    is_pass=True,
                ),
            ])
        db.session.commit()

        first_grade = behavior_grade_for_score(self.session_one, 15.3)
        second_grade = behavior_grade_for_score(self.session_two, 13.5)
        self.assertEqual(first_grade["grade"], "A")
        self.assertEqual(second_grade["grade"], "A")
        self.assertEqual(first_grade["grade_point"], second_grade["grade_point"])

        annual = calculate_annual_behavior_score(self.configuration, self.enrollment)
        self.assertEqual(annual["status"], "COMPLETE")
        self.assertEqual(annual["total_score"], 16)
        self.assertEqual(annual["total_maximum"], 32)
        self.assertEqual(annual["percentage"], 50)

    def test_phase3a_behavior_grade_boundaries_cover_a_to_f(self):
        self.session_one.maximum_score = 50
        self.session_one.behavior_allocation = 50
        self.session_one.attendance_allocation = 0
        bands = [
            ("F", Decimal("0"), Decimal("24.999")),
            ("E", Decimal("25"), Decimal("29.999")),
            ("D", Decimal("30"), Decimal("34.999")),
            ("C", Decimal("35"), Decimal("39.999")),
            ("B", Decimal("40"), Decimal("44.999")),
            ("A", Decimal("45"), Decimal("50")),
        ]
        db.session.add_all([
            BehaviorGradeScale(
                configuration=self.configuration,
                session=self.session_one,
                grade=grade,
                min_score=minimum,
                max_score=maximum,
                grade_point=0 if grade == "F" else 4,
                description=grade,
                sort_order=index,
                is_active=True,
                is_pass=grade != "F",
            )
            for index, (grade, minimum, maximum) in enumerate(bands, start=1)
        ])
        db.session.commit()

        for index, (grade, minimum, maximum) in enumerate(bands):
            exact_score = minimum
            self.assertEqual(behavior_grade_for_score(self.session_one, exact_score)["grade"], grade)
            above = min(maximum, minimum + Decimal("0.01"))
            self.assertEqual(behavior_grade_for_score(self.session_one, above)["grade"], grade)
            if index:
                below = minimum - Decimal("0.01")
                self.assertEqual(
                    behavior_grade_for_score(self.session_one, below)["grade"],
                    bands[index - 1][0],
                )
            else:
                self.assertEqual(
                    behavior_grade_for_score(self.session_one, Decimal("-0.001"))["grade"],
                    "INVALID",
                )

    def test_behavior_grade_management_uses_session_natural_score_bounds(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 12
        self.session_one.attendance_allocation = 8
        db.session.commit()
        self.admin.role = "super_admin"
        db.session.commit()
        client = self._client_as_admin()

        page = client.get(
            f"/admin/behavior/grade-management?config_id={self.configuration.id}"
            f"&session_id={self.session_one.id}"
        )
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Minimum Score", body)
        self.assertIn("Maximum Score", body)
        self.assertNotIn("Minimum Percentage", body)
        self.assertNotIn("Maximum Percentage", body)
        self.assertIn('max="20.000"', body)

        saved = client.post(
            "/admin/behavior/grade-management",
            data={
                "config_id": self.configuration.id,
                "session_id": self.session_one.id,
                "grade": "A",
                "min_score": "10",
                "max_score": "20",
                "grade_point": "4",
                "description": "Natural score excellent",
                "sort_order": "1",
                "is_active": "on",
                "is_pass": "on",
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        scale = BehaviorGradeScale.query.filter_by(
            behavior_session_id=self.session_one.id,
            grade="A",
        ).one()
        self.assertEqual(scale.min_score, Decimal("10.000"))
        self.assertEqual(scale.max_score, Decimal("20.000"))
        self.assertEqual(behavior_grade_for_score(self.session_one, Decimal("15"))["grade"], "A")

        rejected = client.post(
            "/admin/behavior/grade-management",
            data={
                "config_id": self.configuration.id,
                "session_id": self.session_one.id,
                "grade": "B",
                "min_score": "20.001",
                "max_score": "21",
                "grade_point": "3",
                "sort_order": "2",
                "is_active": "on",
                "is_pass": "on",
            },
            follow_redirects=True,
        )
        self.assertIn("cannot exceed the session maximum", rejected.get_data(as_text=True))

    def test_phase3a_public_scoring_status_is_clear(self):
        self.session_one.maximum_score = 17
        self.session_one.behavior_allocation = 17
        self.session_one.attendance_allocation = 0
        db.session.commit()
        score = calculate_session_score(self.configuration, self.session_one, self.enrollment)
        annual = calculate_annual_behavior_score(self.configuration, self.enrollment)
        self.assertEqual(score["scoring_status"], "NOT_APPLICABLE")
        self.assertEqual(annual["status"], "COMPLETE")

    def test_attendance_monthly_total_uses_only_visible_month_records_once(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = 10
        self.session_one.attendance_allocation = 10
        ensure_attendance_defaults(self.configuration)
        present = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="present",
        ).one()
        for day_number in range(1, 14):
            mark_attendance(
                self.configuration,
                self.session_one,
                self.enrollment,
                present.id,
                date(2026, 9, day_number),
                attendance_time="07:30",
            )
        # These records belong to the same session but a different month. They
        # must not inflate the September Monthly Total.
        for day_number in range(1, 6):
            mark_attendance(
                self.configuration,
                self.session_one,
                self.enrollment,
                present.id,
                date(2026, 8, day_number),
                attendance_time="07:30",
            )
        db.session.commit()

        records = BehaviorAttendanceRecord.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            behavior_session_id=self.session_one.id,
            student_enrollment_id=self.enrollment.id,
        ).order_by(BehaviorAttendanceRecord.attendance_date).all()
        projection = attendance_points_projection(records)
        self.assertEqual(projection["record_count"], 18)
        self.assertEqual(projection["positive_points"], Decimal("18.000"))
        self.assertEqual(
            attendance_points_projection(records[:1] * 2)["positive_points"],
            Decimal("1.000"),
        )

        client = self._client_as_admin()
        response = client.get(
            "/admin/behavior/attendance/students/%s/report"
            "?year_id=%s&level_id=%s&config_id=%s&session_id=%s&attendance_date=2026-09-13"
            % (
                self.enrollment.id,
                self.year_one.id,
                self.year_level_one.id,
                self.configuration.id,
                self.session_one.id,
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        positive_card = re.search(r'class="t-card pos".*?</div></div>', body, re.S)
        grand_card = re.search(r'class="t-card grand".*?</div></div>', body, re.S)
        self.assertIsNotNone(positive_card)
        self.assertIsNotNone(grand_card)
        self.assertIn("13.00", positive_card.group(0))
        self.assertIn("10.00 / 10.00", grand_card.group(0))
        self.assertNotIn("18.00", positive_card.group(0))
        self.assertNotIn("18.00", grand_card.group(0))
        self.assertEqual(body.count('class="page '), 2)
        self.assertIn("AUGUST 2026", body)
        self.assertIn("SEPTEMBER 2026", body)
        self.assertEqual(body.count('class="footer-row"'), 2)
        self.assertEqual(body.count('class="bottom-bar"'), 2)
        self.assertIn("overflow:visible", body)

        portal_response = client.get(
            "/admin/behavior/attendance/students/%s/report"
            "?year_id=%s&level_id=%s&config_id=%s&session_id=%s"
            "&attendance_date=2026-09-13&portal_read_only=1"
            % (
                self.enrollment.id,
                self.year_one.id,
                self.year_level_one.id,
                self.configuration.id,
                self.session_one.id,
            )
        )
        self.assertEqual(portal_response.status_code, 200)
        portal_body = portal_response.get_data(as_text=True)
        self.assertEqual(len(re.findall(r'class="page portal-report(?: last-page)?"', portal_body)), 2)
        self.assertEqual(portal_body.count('class="footer-row"'), 2)

    def test_attendance_monthly_projection_preserves_negative_and_neutral_statuses(self):
        ensure_attendance_defaults(self.configuration)
        late = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="late",
        ).one()
        absent = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="absent",
        ).one()
        excused = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="excused",
        ).one()
        official_leave = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="official_leave",
        ).one()
        late.polarity = "negative"
        late.points = Decimal("0.500")
        db.session.flush()

        late_record = mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            late.id,
            date(2026, 9, 1),
            attendance_time="07:30",
            arrival_time="08:00",
        )
        absent_record = mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            absent.id,
            date(2026, 9, 2),
        )
        excused_record = mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            excused.id,
            date(2026, 9, 3),
        )
        leave_record = mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            official_leave.id,
            date(2026, 9, 4),
        )
        projection = attendance_points_projection(
            [late_record, absent_record, excused_record, leave_record]
        )
        self.assertEqual(projection["positive_points"], Decimal("0.000"))
        self.assertEqual(projection["negative_points"], Decimal("1.500"))
        self.assertEqual(projection["signed_total"], Decimal("-1.500"))
        self.assertEqual(projection["record_count"], 4)

    def test_negative_only_attendance_report_uses_allocation_baseline(self):
        self.session_one.maximum_score = 20
        self.session_one.behavior_allocation = Decimal("15.000")
        self.session_one.attendance_allocation = Decimal("5.000")
        ensure_attendance_defaults(self.configuration)
        late = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="late",
        ).one()
        absent = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="absent",
        ).one()
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            late.id,
            date(2026, 9, 1),
            arrival_time="08:15",
        )
        mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            absent.id,
            date(2026, 9, 2),
        )
        db.session.commit()

        score = calculate_session_score(
            self.configuration,
            self.session_one,
            self.enrollment,
        )
        self.assertEqual(score["attendance_positive_points"], Decimal("0.000"))
        self.assertEqual(score["attendance_negative_points"], Decimal("1.500"))
        self.assertEqual(score["attendance_score"], Decimal("3.500"))
        self.assertEqual(score["ledger"]["attendance"]["earned_score"], Decimal("3.500"))

        response = self._client_as_admin().get(
            "/admin/behavior/attendance/students/%s/report"
            "?year_id=%s&level_id=%s&config_id=%s&session_id=%s&attendance_date=2026-09-02"
            % (
                self.enrollment.id,
                self.year_one.id,
                self.year_level_one.id,
                self.configuration.id,
                self.session_one.id,
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        grand_card = re.search(r'class="t-card grand".*?</div></div>', body, re.S)
        self.assertIsNotNone(grand_card)
        self.assertIn("3.50 / 5.00", grand_card.group(0))
        self.assertNotIn("0.00 / 5.00", grand_card.group(0))

    def test_late_polarity_is_configurable_for_new_and_legacy_rows(self):
        ensure_attendance_defaults(self.configuration)
        late = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.configuration.id,
            key="late",
        ).one()
        self.assertEqual(late.polarity, "negative")
        late.polarity = "neutral"
        db.session.commit()
        db.session.expire(late)
        ensure_attendance_defaults(self.configuration)
        db.session.expire(late)
        self.assertEqual(late.polarity, "neutral")
        db.session.flush()
        record = mark_attendance(
            self.configuration,
            self.session_one,
            self.enrollment,
            late.id,
            date(2026, 9, 5),
            attendance_time="07:30",
            arrival_time="08:00",
        )
        self.assertEqual(record.polarity, "neutral")
        projection = attendance_points_projection([record])
        self.assertEqual(projection["positive_points"], Decimal("0.000"))
        self.assertEqual(projection["negative_points"], Decimal("0.000"))

    def test_attendance_report_keeps_uploaded_school_logo(self):
        logo_url = "https://res.cloudinary.com/example/image/upload/v1/school-logo.png"
        db.session.add(Setting(key="logo_path", value=logo_url))
        db.session.commit()
        response = self._client_as_admin().get(
            "/admin/behavior/attendance/students/%s/report"
            "?year_id=%s&level_id=%s&config_id=%s&session_id=%s&attendance_date=2026-09-01"
            % (
                self.enrollment.id,
                self.year_one.id,
                self.year_level_one.id,
                self.configuration.id,
                self.session_one.id,
            )
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(f'<img src="{logo_url}"', body)
        self.assertIn("if (!node.querySelector('img')) node.innerHTML = icons.cap", body)


if __name__ == "__main__":
    unittest.main()
