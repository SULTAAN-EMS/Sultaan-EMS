"""Phase 2E coverage for Behavior portal and academic report projections."""

import unittest

from app import create_app, db
from app.behavior_reporting import get_behavior_report_data
from app.behavior_service import record_event
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorConfiguration,
    BehaviorAction,
    BehaviorCategory,
    BehaviorGradeScale,
    BehaviorSession,
    Exam,
    GradeScale,
    Result,
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

    def test_behavior_grade_management_is_scoped_and_does_not_use_ordinary_scale(self):
        self.admin.role = "super_admin"
        db.session.commit()
        client = self._client_as_admin()
        page = client.get(
            f"/admin/behavior/grade-management?config_id={self.configuration.id}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Behavior Grade Management", page.data)
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
        self.assertIn('id="behaviorDetailDialog1"', body)
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


if __name__ == "__main__":
    unittest.main()
