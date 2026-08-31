import json
import unittest
from datetime import datetime
from decimal import Decimal

from flask_login import login_user, logout_user

from app import create_app, db
from app.audit import audit
from app.behavior_service import (
    BehaviorValidationError,
    calculate_session_score,
    edit_event,
    record_event,
    validate_enrollment_scope,
    validate_session_scope,
    void_event,
)
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    BehaviorSession,
    ExamType,
    Student,
    StudentEnrollment,
    User,
)


class TestPhase2CBehaviorEvents(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2c-test"
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

        self.admin = User(username="phase2c-admin", full_name="Phase 2C Admin", role="admin", is_active=True)
        self.admin.set_password("test-password")
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.edit", "behavior.void", "behavior.configure",
        ])
        self.staff = User(username="phase2c-staff", full_name="Phase 2C Staff", role="admin", is_active=True)
        self.staff.set_password("test-password")
        self.year_one = AcademicYear(name="2026-2027", is_current=True)
        self.year_two = AcademicYear(name="2027-2028", is_current=False)
        db.session.add_all([self.admin, self.staff, self.year_one, self.year_two])
        db.session.flush()

        self.level_one = AcademicYearLevel(name="Form One", academic_year_id=self.year_one.id)
        self.level_two = AcademicYearLevel(name="Form One", academic_year_id=self.year_two.id)
        self.class_one = AcademicYearClass(name="1A", academic_year_level=self.level_one)
        self.subject_one = AcademicYearSubject(
            name="Dabeecad", subject_kind="behavior", max_score=0,
            academic_year=self.year_one, academic_year_level=self.level_one,
        )
        self.subject_two = AcademicYearSubject(
            name="Dabeecad", subject_kind="behavior", max_score=0,
            academic_year=self.year_two, academic_year_level=self.level_two,
        )
        self.student_one = Student(student_code="BHV-C-001", full_name="Behavior One", is_active=True)
        self.student_two = Student(student_code="BHV-C-002", full_name="Behavior Two", is_active=True)
        db.session.add_all([
            self.level_one, self.level_two, self.class_one,
            self.subject_one, self.subject_two, self.student_one, self.student_two,
        ])
        db.session.flush()
        self.enrollment_one = StudentEnrollment(
            student_id=self.student_one.id, academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id, academic_year_class_id=self.class_one.id,
            status="active", academic_outcome="pending", enrollment_source="manual",
        )
        self.enrollment_two = StudentEnrollment(
            student_id=self.student_two.id, academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id, academic_year_class_id=self.class_one.id,
            status="active", academic_outcome="pending", enrollment_source="manual",
        )
        db.session.add_all([self.enrollment_one, self.enrollment_two])
        db.session.flush()

        self.config_one = BehaviorConfiguration(
            academic_year_id=self.year_one.id, academic_year_level_id=self.level_one.id,
            academic_year_subject_id=self.subject_one.id, frequency="monthly", status="active",
        )
        self.config_two = BehaviorConfiguration(
            academic_year_id=self.year_two.id, academic_year_level_id=self.level_two.id,
            academic_year_subject_id=self.subject_two.id, frequency="monthly", status="active",
        )
        db.session.add_all([self.config_one, self.config_two])
        db.session.flush()

        self.exam_a = ExamType(academic_year_id=self.year_one.id, name="1st Monthly", sort_order=1)
        self.exam_b = ExamType(academic_year_id=self.year_one.id, name="2nd Monthly", sort_order=2)
        self.exam_other_year = ExamType(academic_year_id=self.year_two.id, name="1st Monthly", sort_order=1)
        db.session.add_all([self.exam_a, self.exam_b, self.exam_other_year])
        db.session.flush()
        self.session_a = BehaviorSession(
            behavior_configuration_id=self.config_one.id, exam_type_id=self.exam_a.id,
            session_label="1st Monthly", maximum_score=17, sort_order=1, is_active=True,
        )
        self.session_b = BehaviorSession(
            behavior_configuration_id=self.config_one.id, exam_type_id=self.exam_b.id,
            session_label="2nd Monthly", maximum_score=15, sort_order=2, is_active=True,
        )
        self.session_other_year = BehaviorSession(
            behavior_configuration_id=self.config_two.id, exam_type_id=self.exam_other_year.id,
            session_label="1st Monthly", maximum_score=20, sort_order=1, is_active=True,
        )
        self.positive = BehaviorCategory(behavior_configuration_id=self.config_one.id, name="Positive", polarity="positive", is_active=True)
        self.negative = BehaviorCategory(behavior_configuration_id=self.config_one.id, name="Negative", polarity="negative", is_active=True)
        self.positive_other_year = BehaviorCategory(behavior_configuration_id=self.config_two.id, name="Positive", polarity="positive", is_active=True)
        self.positive_action = BehaviorAction(category=self.positive, name="Helpful", level_number=1, points=2, frequency="ad_hoc", is_active=True)
        self.high_positive_action = BehaviorAction(category=self.positive, name="Outstanding", level_number=2, points=20, frequency="ad_hoc", is_active=True)
        self.negative_action = BehaviorAction(category=self.negative, name="Late", level_number=1, points=1, frequency="daily", is_active=True)
        self.high_negative_action = BehaviorAction(category=self.negative, name="Serious", level_number=2, points=20, frequency="ad_hoc", is_active=True)
        self.other_year_action = BehaviorAction(category=self.positive_other_year, name="Helpful", level_number=1, points=3, frequency="ad_hoc", is_active=True)
        db.session.add_all([
            self.session_a, self.session_b, self.session_other_year,
            self.positive, self.negative, self.positive_other_year,
            self.positive_action, self.high_positive_action,
            self.negative_action, self.high_negative_action, self.other_year_action,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _login(self, user):
        with self.app.test_request_context("/"):
            login_user(user)

    def test_no_events_base_and_percentage_use_session_maximum(self):
        score_a = calculate_session_score(self.config_one, self.session_a, self.enrollment_one)
        score_b = calculate_session_score(self.config_one, self.session_b, self.enrollment_one)
        self.assertEqual(score_a["base_score"], Decimal("8.500"))
        self.assertEqual(score_a["final_score"], Decimal("8.500"))
        self.assertEqual(score_a["percentage"], Decimal("50.000"))
        self.assertEqual(score_b["base_score"], Decimal("7.500"))
        self.assertEqual(score_b["percentage"], Decimal("50.000"))

    def test_positive_negative_separation_capacity_and_floor(self):
        record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="sep-1")
        record_event(self.config_one, self.enrollment_one, self.session_a, self.negative, self.negative_action, idempotency_key="sep-2")
        db.session.commit()
        score = calculate_session_score(self.config_one, self.session_a, self.enrollment_one)
        self.assertEqual(score["positive_raw_points"], Decimal("2.000"))
        self.assertEqual(score["positive_applied_points"], Decimal("2.000"))
        self.assertEqual(score["negative_points"], Decimal("1.000"))
        self.assertEqual(score["final_score"], Decimal("9.500"))

        with self.assertRaises(BehaviorValidationError):
            record_event(
                self.config_one,
                self.enrollment_two,
                self.session_a,
                self.positive,
                self.high_positive_action,
                idempotency_key="too-large-1",
            )
        self.high_positive_action.points = Decimal("8.500")
        self.high_negative_action.points = Decimal("8.500")
        record_event(self.config_one, self.enrollment_two, self.session_a, self.positive, self.high_positive_action, idempotency_key="cap-1")
        record_event(self.config_one, self.enrollment_two, self.session_a, self.positive, self.high_positive_action, idempotency_key="cap-2")
        record_event(self.config_one, self.enrollment_two, self.session_a, self.negative, self.high_negative_action, idempotency_key="floor-1")
        record_event(self.config_one, self.enrollment_two, self.session_a, self.negative, self.high_negative_action, idempotency_key="floor-2")
        db.session.commit()
        capped = calculate_session_score(self.config_one, self.session_a, self.enrollment_two)
        self.assertEqual(capped["positive_raw_points"], Decimal("17.000"))
        self.assertEqual(capped["positive_applied_points"], Decimal("8.500"))
        self.assertEqual(capped["final_score"], Decimal("0.000"))

    def test_session_isolation(self):
        record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="session-a")
        db.session.commit()
        score_b = calculate_session_score(self.config_one, self.session_b, self.enrollment_one)
        self.assertEqual(score_b["event_count"], 0)
        self.assertEqual(score_b["final_score"], Decimal("7.500"))

    def test_year_and_enrollment_isolation(self):
        with self.assertRaises(BehaviorValidationError):
            validate_enrollment_scope(self.config_two, self.enrollment_one.id)
        with self.assertRaises(BehaviorValidationError):
            validate_session_scope(self.config_one, self.exam_other_year.id)
        with self.assertRaises(BehaviorValidationError):
            calculate_session_score(self.config_two, self.session_other_year, self.enrollment_one)

    def test_duplicate_submission_returns_existing_event(self):
        first = record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="same-submit")
        db.session.commit()
        second = record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="same-submit")
        self.assertEqual(first.id, second.id)
        self.assertEqual(BehaviorEvent.query.filter_by(idempotency_key="same-submit").count(), 1)

    def test_snapshot_void_edit_and_audit(self):
        event = record_event(
            self.config_one, self.enrollment_one, self.session_a,
            self.positive, self.positive_action,
            occurred_at=datetime(2026, 8, 29, 12, 0), idempotency_key="history-1",
        )
        db.session.commit()
        self.positive_action.points = 9
        db.session.commit()
        db.session.refresh(event)
        self.assertEqual(event.points_applied, Decimal("2.000"))
        self.assertEqual(event.action_level_snapshot, 1)

        edited_action = BehaviorAction(
            behavior_category_id=self.positive.id, name="Revised", level_number=3,
            points=4, frequency="ad_hoc", is_active=True,
        )
        db.session.add(edited_action)
        db.session.flush()
        _, old_values, new_values = edit_event(
            event, self.config_one, self.enrollment_one, self.session_a,
            self.positive, edited_action, occurred_at=event.occurred_at,
            notes="Corrected", reason="Corrected action",
        )
        with self.app.test_request_context("/"):
            login_user(self.admin)
            audit(
                "Behavior Events",
                f"Edited Behavior event {event.id}; old={json.dumps(old_values, sort_keys=True)}; new={json.dumps(new_values, sort_keys=True)}",
            )
            logout_user()
        db.session.commit()
        self.assertEqual(event.points_applied, Decimal("4.000"))
        self.assertEqual(event.action_level_snapshot, 3)
        self.assertEqual(event.status, "active")
        self.assertGreaterEqual(len([row for row in event.action.events if row.id == event.id]), 1)
        void_event(event, self.admin.id, "Correction complete")
        db.session.commit()
        self.assertEqual(db.session.get(BehaviorEvent, event.id).status, "voided")
        self.assertEqual(calculate_session_score(self.config_one, self.session_a, self.enrollment_one)["event_count"], 0)
        self.assertTrue(any(row.action == "Behavior Events" for row in __import__("app.models", fromlist=["AuditLog"]).AuditLog.query.all()))

    def test_invalid_relationship_and_direction_are_rejected(self):
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_one, self.enrollment_one, self.session_a, self.negative, self.negative_action, direction="positive")
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.negative_action)
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_two, self.enrollment_one, self.session_other_year, self.positive_other_year, self.other_year_action)

    def test_server_side_permissions_protect_event_and_edit_routes(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.staff.id)
            session["_fresh"] = True
        self.assertEqual(client.get(f"/admin/behavior/students?config_id={self.config_one.id}").status_code, 403)
        self.staff.set_permissions(["behavior.record"])
        db.session.commit()
        self.assertEqual(client.get(f"/admin/behavior/students?config_id={self.config_one.id}").status_code, 200)

    def test_student_route_persists_form_datetime_and_scope(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        response = client.post(
            f"/admin/behavior/students?config_id={self.config_one.id}",
            data={
                "csrf_token": "",
                "config_id": str(self.config_one.id),
                "student_enrollment_id": str(self.enrollment_one.id),
                "behavior_session_id": str(self.session_a.id),
                "direction": "positive",
                "behavior_category_id": str(self.positive.id),
                "behavior_action_id": str(self.positive_action.id),
                "occurred_at": "2026-08-29T12:34",
                "notes": "Recorded from the scoped form",
                "idempotency_key": "route-form-time-1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        event = BehaviorEvent.query.filter_by(idempotency_key="route-form-time-1").one()
        self.assertEqual(event.occurred_at, datetime(2026, 8, 29, 12, 34))
        self.assertEqual(event.student_enrollment_id, self.enrollment_one.id)


if __name__ == "__main__":
    unittest.main()
