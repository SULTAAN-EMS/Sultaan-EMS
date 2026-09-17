"""Focused Phase 4A Step 2 allocation contract and planning-page tests."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app import create_app, db
from app.behavior_attendance import ensure_attendance_defaults, mark_attendance
from app.behavior_service import (
    BehaviorValidationError,
    calculate_session_score,
    record_event,
    scoring_ledger_projection,
    session_allocation_projection,
)
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    ExamType,
    Student,
    StudentEnrollment,
    User,
)


class TestPhase4AStep2Allocation(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-4a-step2-test"
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

        self.admin = User(username="step2-admin", full_name="Step 2 Admin", role="admin", is_active=True)
        self.admin.set_password("test-password")
        self.admin.set_permissions(["behavior.view", "behavior.record", "behavior.configure"])
        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.level = AcademicYearLevel(name="Form One", academic_year=self.year)
        self.academic_class = AcademicYearClass(name="1A", academic_year_level=self.level)
        self.subject = AcademicYearSubject(
            name="Anshax", subject_kind="behavior", max_score=0,
            academic_year=self.year, academic_year_level=self.level,
        )
        self.exam = ExamType(name="1st Monthly", academic_year=self.year, is_active=True)
        self.student = Student(student_code="STEP2-001", full_name="Step Two Student", is_active=True)
        db.session.add_all([
            self.admin, self.year, self.level, self.academic_class,
            self.subject, self.exam, self.student,
        ])
        db.session.flush()
        self.enrollment = StudentEnrollment(
            student=self.student, academic_year=self.year,
            academic_year_level=self.level, academic_year_class=self.academic_class,
            status="active", academic_outcome="pending", enrollment_source="manual",
        )
        self.config = BehaviorConfiguration(
            academic_year=self.year, academic_year_level=self.level,
            behavior_subject=self.subject, frequency="monthly", status="active",
        )
        self.session = __import__("app.models", fromlist=["BehaviorSession"]).BehaviorSession(
            configuration=self.config, exam_type=self.exam, session_label="1st Monthly",
            maximum_score=20, behavior_allocation=12, attendance_allocation=8,
            sort_order=1, is_active=True,
        )
        self.positive = BehaviorCategory(
            configuration=self.config, name="Positive", polarity="positive", is_active=True,
        )
        self.action = BehaviorAction(
            category=self.positive, name="Helpful", level_number=1,
            points=2, frequency="ad_hoc", is_active=True,
        )
        db.session.add_all([self.enrollment, self.config, self.session, self.positive, self.action])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        return client

    def test_variable_allocations_and_capacities_are_canonical(self):
        for maximum, behavior, attendance in ((17, 10, 7), (20, 12, 8), (15, 9, 6), (23, 11, 12)):
            projection = session_allocation_projection(SimpleNamespace(
                maximum_score=maximum,
                behavior_allocation=behavior,
                attendance_allocation=attendance,
            ))
            self.assertEqual(projection["session_maximum"], Decimal(str(maximum)).quantize(Decimal("0.001")))
            self.assertEqual(projection["behavior_positive_max"], (Decimal(str(behavior)) / 2).quantize(Decimal("0.001")))
            self.assertEqual(projection["behavior_negative_max"], (Decimal(str(behavior)) / 2).quantize(Decimal("0.001")))
            self.assertIsNone(projection["attendance_positive_max"])
            self.assertIsNone(projection["attendance_negative_max"])

    def test_invalid_allocation_totals_are_rejected(self):
        for behavior, attendance in ((12, 7), (12, 9)):
            with self.assertRaises(BehaviorValidationError):
                session_allocation_projection(SimpleNamespace(
                    maximum_score=20,
                    behavior_allocation=behavior,
                    attendance_allocation=attendance,
                ))

    def test_zero_attendance_allocation_is_not_applicable(self):
        self.session.maximum_score = 20
        self.session.behavior_allocation = 20
        self.session.attendance_allocation = 0
        db.session.commit()
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_status"], "NOT_APPLICABLE")
        self.assertEqual(score["ledger"]["attendance"]["status"], "NOT_APPLICABLE")
        self.assertIsNone(score["ledger"]["attendance"]["positive_max"])
        self.assertEqual(score["ledger"]["attendance"]["remaining"], Decimal("0.000"))

    def test_missing_attendance_remains_incomplete_in_projection(self):
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_status"], "INCOMPLETE")
        self.assertEqual(score["ledger"]["attendance"]["status"], "INCOMPLETE")
        self.assertEqual(score["attendance"]["base_score"], Decimal("0.000"))
        self.assertIsNone(score["attendance_score"])
        self.assertIsNone(score["ledger"]["attendance"]["earned_score"])
        self.assertIsNone(score["ledger"]["attendance"]["remaining"])

    def test_dashboard_renders_when_incomplete_scores_are_present(self):
        response = self._admin_client().get(
            f"/admin/behavior/?year_id={self.year.id}&level_id={self.level.id}"
            f"&config_id={self.config.id}&session_id={self.session.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ardayda iyo Dhibcaha Hadda", response.get_data(as_text=True))

    def test_configured_status_points_are_snapshotted_and_used_by_canonical_scoring(self):
        ensure_attendance_defaults(self.config)
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        late = next(item for item in self.config.attendance_statuses if item.key == "late")
        present.points = Decimal("2.000")
        late.points = Decimal("0.500")
        db.session.flush()

        mark_attendance(
            self.config, self.session, self.enrollment, present.id,
            date(2026, 8, 3), attendance_time="07:30",
        )
        mark_attendance(
            self.config, self.session, self.enrollment, late.id,
            date(2026, 8, 4), attendance_time="07:30", arrival_time="08:00",
        )
        db.session.commit()

        self.assertEqual(self.session.attendance_present_weight, Decimal("2.000"))
        self.assertEqual(self.session.attendance_late_weight, Decimal("0.500"))
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_status"], "VALID")
        self.assertEqual(score["attendance"]["positive_evidence"], Decimal("2.500"))
        self.assertEqual(score["attendance_score"], Decimal("2.500"))
        self.assertGreater(score["attendance_score"], Decimal("0.000"))
        self.assertLess(score["attendance_score"], Decimal("8.000"))
        self.assertIsNone(score["attendance"]["positive_capacity"])

    def test_attendance_policy_and_calendar_are_frozen_per_session(self):
        ensure_attendance_defaults(self.config)
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        present.points = Decimal("2.000")
        db.session.flush()
        mark_attendance(
            self.config, self.session, self.enrollment, present.id,
            date(2026, 8, 3), attendance_time="07:20",
        )
        db.session.commit()
        before = calculate_session_score(self.config, self.session, self.enrollment)
        captured_policy = self.session.attendance_policy_snapshot
        captured_days = self.session.attendance_weekdays_snapshot

        # Later configuration edits must not rewrite this session's historical
        # score or its opportunity calendar.
        present.points = Decimal("9.000")
        self.config.frequency = "weekly"
        for item in self.config.attendance_days:
            item.is_active = item.weekday == 6
        db.session.commit()

        after = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(self.session.attendance_policy_snapshot, captured_policy)
        self.assertEqual(self.session.attendance_weekdays_snapshot, captured_days)
        self.assertEqual(self.session.attendance_frequency_snapshot, "monthly")
        self.assertEqual(after["attendance_score"], before["attendance_score"])
        self.assertEqual(after["attendance"]["positive_evidence"], before["attendance"]["positive_evidence"])

    def test_one_present_record_uses_configured_point_directly(self):
        ensure_attendance_defaults(self.config)
        self.session.behavior_allocation = 15
        self.session.attendance_allocation = 5
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        db.session.flush()

        mark_attendance(
            self.config, self.session, self.enrollment, present.id,
            date(2026, 8, 3), attendance_time="07:20",
        )
        db.session.commit()

        score = calculate_session_score(self.config, self.session, self.enrollment)
        attendance = score["attendance"]
        ledger = score["ledger"]["attendance"]
        expected = Decimal("1.000")
        self.assertEqual(attendance["attendance_score"], expected)
        self.assertEqual(attendance["positive_evidence"], expected)
        self.assertEqual(attendance["positive_evidence"], Decimal("1.000"))
        self.assertEqual(attendance["positive_applied"], expected)
        self.assertIsNone(attendance["positive_capacity"])
        self.assertEqual(ledger["positive_used"], expected)
        self.assertIsNone(ledger["positive_remaining"])
        self.assertEqual(ledger["negative_used"], Decimal("0.000"))
        self.assertIsNone(ledger["negative_remaining"])
        self.assertEqual(ledger["earned_score"], expected)
        self.assertEqual(ledger["remaining"], Decimal("5.000") - expected)

    def test_two_present_records_are_two_configured_points_not_period_normalized(self):
        ensure_attendance_defaults(self.config)
        self.session.maximum_score = 25
        self.session.behavior_allocation = 20
        self.session.attendance_allocation = 5
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 3))
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 4))
        db.session.commit()

        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance"]["positive_evidence"], Decimal("2.000"))
        self.assertEqual(score["attendance_score"], Decimal("2.000"))
        self.assertEqual(score["ledger"]["attendance"]["earned_score"], Decimal("2.000"))

    def test_positive_and_negative_configured_points_are_net_directly(self):
        ensure_attendance_defaults(self.config)
        self.session.maximum_score = 25
        self.session.behavior_allocation = 20
        self.session.attendance_allocation = 5
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        absent = next(item for item in self.config.attendance_statuses if item.key == "absent")
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 3))
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 4))
        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 5))
        db.session.commit()

        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance"]["positive_evidence"], Decimal("2.000"))
        self.assertEqual(score["attendance"]["negative_evidence"], Decimal("1.000"))
        self.assertEqual(score["attendance_score"], Decimal("1.000"))

    def test_configured_points_are_capped_by_single_attendance_allocation(self):
        ensure_attendance_defaults(self.config)
        self.session.maximum_score = 25
        self.session.behavior_allocation = 20
        self.session.attendance_allocation = 5
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        present.points = Decimal("7.000")
        db.session.flush()
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 3))
        db.session.commit()

        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance"]["positive_evidence"], Decimal("7.000"))
        self.assertEqual(score["attendance_score"], Decimal("5.000"))
        self.assertEqual(score["ledger"]["attendance"]["remaining"], Decimal("0.000"))

    def test_attendance_daily_records_are_not_limited_by_half_allocation(self):
        ensure_attendance_defaults(self.config)
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        absent = next(item for item in self.config.attendance_statuses if item.key == "absent")
        present.points = Decimal("3.000")
        absent.points = Decimal("3.000")
        db.session.flush()
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 3))
        mark_attendance(self.config, self.session, self.enrollment, present.id, date(2026, 8, 4))

        self.enrollment.status = "active"
        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 5))
        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 6))
        db.session.commit()
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_record_count"], 4)
        self.assertLessEqual(score["attendance_score"], Decimal("8.000"))

    def test_fifty_present_days_normalize_to_the_full_attendance_maximum(self):
        ensure_attendance_defaults(self.config)
        self.session.maximum_score = 25
        self.session.behavior_allocation = 20
        self.session.attendance_allocation = 5
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        active_weekdays = {
            item.weekday for item in self.config.attendance_days if item.is_active
        }
        for day in range(1, 32):
            current = date(2026, 1, day)
            if current.weekday() not in active_weekdays:
                continue
            mark_attendance(
                self.config, self.session, self.enrollment, present.id,
                current,
            )
        db.session.commit()
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_score"], Decimal("5.000"))
        self.assertEqual(score["attendance"]["positive_evidence"], Decimal("26.000"))
        self.assertEqual(score["ledger"]["attendance"]["remaining"], Decimal("0.000"))
        self.assertEqual(score["final_score"], Decimal("15.000"))

    def test_neutral_attendance_is_not_an_opportunity_or_deduction(self):
        ensure_attendance_defaults(self.config)
        excused = next(item for item in self.config.attendance_statuses if item.key == "excused")
        official_leave = next(item for item in self.config.attendance_statuses if item.key == "official_leave")
        mark_attendance(self.config, self.session, self.enrollment, excused.id, date(2026, 8, 3))
        mark_attendance(self.config, self.session, self.enrollment, official_leave.id, date(2026, 8, 4))
        db.session.commit()
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_status"], "VALID_BASELINE")
        self.assertEqual(score["attendance"]["record_count"], 2)
        self.assertNotIn("opportunity_count", score["attendance"])
        self.assertEqual(score["attendance_score"], Decimal("0.000"))

    def test_late_and_absent_follow_configured_weights_without_exceeding_allocation(self):
        ensure_attendance_defaults(self.config)
        late = next(item for item in self.config.attendance_statuses if item.key == "late")
        absent = next(item for item in self.config.attendance_statuses if item.key == "absent")
        mark_attendance(
            self.config, self.session, self.enrollment, late.id,
            date(2026, 8, 3), arrival_time="08:10",
        )
        db.session.commit()
        late_score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(late_score["attendance_score"], Decimal("0.500"))

        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 4))
        db.session.commit()
        mixed_score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(mixed_score["attendance_score"], Decimal("0.000"))
        self.assertLessEqual(mixed_score["attendance_score"], Decimal("8.000"))

    def test_behavior_and_attendance_reports_use_canonical_ledger_fields(self):
        client = self._admin_client()
        behavior_response = client.get(
            "/admin/behavior/students/%s/report" % self.enrollment.id,
            query_string={
                "year_id": self.year.id,
                "config_id": self.config.id,
                "session_id": self.session.id,
            },
        )
        self.assertEqual(behavior_response.status_code, 200)
        behavior_body = behavior_response.get_data(as_text=True)
        self.assertIn("Total Positive Points", behavior_body)
        self.assertIn("behavior-summary-data", behavior_body)
        self.assertIn("strip.remove()", behavior_body)
        self.assertIn("ledger.remove()", behavior_body)
        self.assertIn("summary-row", behavior_body)

        attendance_response = client.get(
            "/admin/behavior/attendance/students/%s/report" % self.enrollment.id,
            query_string={
                "year_id": self.year.id,
                "level_id": self.level.id,
                "config_id": self.config.id,
                "session_id": self.session.id,
            },
        )
        self.assertEqual(attendance_response.status_code, 200)
        attendance_body = attendance_response.get_data(as_text=True)
        self.assertIn("WADARTA GUUD / MONTHLY TOTAL", attendance_body)
        self.assertIn("attendance-summary-data", attendance_body)
        self.assertIn("ledger.remove()", attendance_body)
        self.assertIn("INCOMPLETE", attendance_body)

    def test_used_remaining_and_grand_total_are_one_projection(self):
        record_event(
            self.config, self.enrollment, self.session, self.positive, self.action,
            idempotency_key="step2-event-1",
        )
        record_event(
            self.config, self.enrollment, self.session, self.positive, self.action,
            idempotency_key="step2-event-2",
        )
        ensure_attendance_defaults(self.config)
        db.session.commit()
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        for offset in range(2):
            mark_attendance(
                self.config, self.session, self.enrollment, present.id,
                date(2026, 8, 3) + timedelta(days=offset),
            )
        db.session.commit()

        score = calculate_session_score(self.config, self.session, self.enrollment)
        projection = scoring_ledger_projection(score)
        expected_attendance = score["attendance_score"]
        self.assertEqual(projection["behavior"]["positive_used"], Decimal("4.000"))
        self.assertEqual(projection["behavior"]["positive_remaining"], Decimal("2.000"))
        self.assertEqual(projection["behavior"]["remaining"], Decimal("2.000"))
        self.assertEqual(projection["attendance"]["remaining"], Decimal("8.000") - expected_attendance)
        self.assertEqual(projection["session"]["behavior_earned"], Decimal("10.000"))
        self.assertEqual(projection["session"]["attendance_earned"], expected_attendance)
        self.assertEqual(projection["session"]["grand_total"], Decimal("10.000") + expected_attendance)
        self.assertEqual(projection["session"]["session_remaining"], Decimal("10.000") - expected_attendance)

    def test_historical_session_cannot_be_reallocated(self):
        record_event(
            self.config, self.enrollment, self.session, self.positive, self.action,
            idempotency_key="step2-history-lock",
        )
        db.session.commit()
        client = self._admin_client()
        response = client.post(
            "/admin/behavior/sessions",
            data={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "exam_ref": f"legacy:{self.exam.id}",
                "session_label": "Changed",
                "maximum_score": "20",
                "behavior_allocation": "10",
                "attendance_allocation": "10",
                "sort_order": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"recorded events or Attendance history", response.data)
        db.session.refresh(self.session)
        self.assertEqual(self.session.behavior_allocation, Decimal("12.000"))
        self.assertEqual(self.session.attendance_allocation, Decimal("8.000"))

    def test_dedicated_allocation_page_renders_canonical_values(self):
        response = self._admin_client().get(
            "/admin/behavior/session-allocation",
            query_string={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "enrollment_id": self.enrollment.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Session Allocation", body)
        self.assertIn("Behavior Allocation", body)
        self.assertIn("Attendance Allocation", body)
        self.assertIn("Session Remaining", body)
        self.assertIn("12.000", body)
        self.assertIn("8.000", body)


if __name__ == "__main__":
    unittest.main()
