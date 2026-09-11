"""Focused Phase 1 tests for Behavior taxonomy and daily attendance."""

import unittest
from datetime import date
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.behavior_attendance import (
    attendance_days,
    attendance_statuses,
    ensure_attendance_defaults,
    generate_daily_roster,
    mark_attendance,
)
from app.behavior_service import BehaviorValidationError, calculate_session_score, record_event
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorActionChoice,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorAttendanceRecord,
    BehaviorEvent,
    BehaviorSession,
    ExamType,
    Student,
    StudentEnrollment,
    User,
)


class TestPhase1BehaviorAttendance(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-1-behavior-test"
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

        self.admin = User(username="phase1-admin", full_name="Phase 1 Admin", role="admin", is_active=True)
        self.admin.set_password("test-password")
        self.admin.set_permissions(["behavior.view", "behavior.record", "behavior.configure"])
        year = AcademicYear(name="2026-2027", is_current=True)
        level = AcademicYearLevel(name="Form One", academic_year=year)
        academic_class = AcademicYearClass(name="1A", academic_year_level=level)
        subject = AcademicYearSubject(
            name="Anshax", subject_kind="behavior", max_score=0,
            academic_year=year, academic_year_level=level,
        )
        exam = ExamType(name="1st Monthly", academic_year=year, is_active=True)
        student = Student(student_code="BHV-P1-001", full_name="Phase One Student", is_active=True)
        db.session.add_all([self.admin, year, level, academic_class, subject, exam, student])
        db.session.flush()
        enrollment = StudentEnrollment(
            student=student, academic_year=year, academic_year_level=level,
            academic_year_class=academic_class, status="active",
            academic_outcome="pending", enrollment_source="manual",
        )
        config = BehaviorConfiguration(
            academic_year=year, academic_year_level=level,
            behavior_subject=subject, frequency="monthly", status="active",
        )
        session = BehaviorSession(
            configuration=config, exam_type=exam, session_label="1st Monthly",
            maximum_score=20, sort_order=1, is_active=True,
        )
        positive = BehaviorCategory(
            configuration=config, name="Positive", polarity="positive", is_active=True,
        )
        action = BehaviorAction(
            category=positive, name="Helpful", level_number=1, points=2,
            frequency="ad_hoc", is_active=True,
        )
        db.session.add_all([enrollment, config, session, positive, action])
        db.session.commit()
        self.config = config
        self.session = session
        self.enrollment = enrollment
        self.action = action
        self.positive = positive

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_defaults_are_idempotent_and_school_day_is_configurable(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        self.assertEqual(len(attendance_statuses(self.config)), 5)
        self.assertEqual(len(attendance_days(self.config)), 6)
        ensure_attendance_defaults(self.config)
        db.session.commit()
        self.assertEqual(len(self.config.attendance_statuses), 5)
        self.assertEqual(len(self.config.attendance_days), 6)
        saturday = date(2026, 8, 29)
        self.assertEqual(saturday.weekday(), 5)
        self.assertEqual(generate_daily_roster(self.config, self.session, saturday), 1)
        self.assertEqual(generate_daily_roster(self.config, self.session, saturday), 0)
        self.assertEqual(BehaviorAttendanceRecord.query.count(), 1)

    def test_marking_upserts_one_record_and_feeds_score(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        absent = next(item for item in self.config.attendance_statuses if item.key == "absent")
        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 29))
        db.session.commit()
        mark_attendance(self.config, self.session, self.enrollment, absent.id, date(2026, 8, 29), note="Updated")
        db.session.commit()
        self.assertEqual(BehaviorAttendanceRecord.query.count(), 1)
        self.assertEqual(BehaviorAttendanceRecord.query.one().note, "Updated")
        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_negative_points"], Decimal("1.000"))
        self.assertEqual(score["final_score"], Decimal("9.000"))

    def test_choice_action_is_scored_and_snapshotted(self):
        self.action.behavior_type = "choice"
        choice = BehaviorActionChoice(
            action=self.action, label="Excellent", points=2, sort_order=1, is_active=True,
        )
        db.session.add(choice)
        db.session.flush()
        event = record_event(
            self.config, self.enrollment, self.session, self.positive, self.action,
            choice=choice, idempotency_key="phase1-choice-1",
        )
        db.session.commit()
        self.assertEqual(event.behavior_action_choice_id, choice.id)
        self.assertEqual(event.points_applied, Decimal("2.000"))
        self.assertEqual(event.action_name_snapshot, "Helpful: Excellent")
        self.assertEqual(BehaviorEvent.query.count(), 1)

    def test_wrong_enrollment_scope_is_rejected(self):
        other_year = AcademicYear(name="2027-2028", is_current=False)
        other_level = AcademicYearLevel(name="Form One", academic_year=other_year)
        other_class = AcademicYearClass(name="1A", academic_year_level=other_level)
        other_student = Student(student_code="BHV-P1-002", full_name="Other Year Student", is_active=True)
        db.session.add_all([other_year, other_level, other_class, other_student])
        db.session.flush()
        other_enrollment = StudentEnrollment(
            student=other_student, academic_year=other_year,
            academic_year_level=other_level, academic_year_class=other_class,
            status="active", academic_outcome="pending", enrollment_source="manual",
        )
        db.session.add(other_enrollment)
        db.session.commit()
        ensure_attendance_defaults(self.config)
        db.session.commit()
        db.session.expire(self.config, ["attendance_statuses"])
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        with self.assertRaises(BehaviorValidationError):
            mark_attendance(self.config, self.session, other_enrollment, present.id, date(2026, 8, 29))

    def test_attendance_and_subcategory_pages_render_for_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        attendance_response = client.get(
            "/admin/behavior/attendance",
            query_string={"config_id": self.config.id, "session_id": self.session.id},
        )
        subcategory_response = client.get(
            "/admin/behavior/subcategories",
            query_string={"config_id": self.config.id},
        )
        taxonomy_response = client.get(
            "/admin/behavior/taxonomy",
            query_string={"config_id": self.config.id},
        )
        self.assertEqual(attendance_response.status_code, 200)
        self.assertEqual(subcategory_response.status_code, 200)
        self.assertEqual(taxonomy_response.status_code, 200)
        self.assertIn(b"Behavior Attendance", attendance_response.data)
        self.assertIn(b"Behavior Sub-categories", subcategory_response.data)
        self.assertIn(b"Behavior Taxonomy", taxonomy_response.data)

    def test_attendance_save_post_persists_exact_scope(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        self.assertEqual(client.get(
            "/admin/behavior/attendance",
            query_string={"config_id": self.config.id, "session_id": self.session.id},
        ).status_code, 200)
        db.session.expire(self.config, ["attendance_statuses"])
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        response = client.post(
            "/admin/behavior/attendance",
            data={
                "action": "save_all",
                "config_id": self.config.id,
                "session_id": self.session.id,
                "attendance_date": "2026-08-29",
                f"status_{self.enrollment.id}": str(present.id),
                f"note_{self.enrollment.id}": "Present",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302, response.data[-5000:])
        row = BehaviorAttendanceRecord.query.one()
        self.assertEqual(row.student_enrollment_id, self.enrollment.id)
        self.assertEqual(row.behavior_configuration_id, self.config.id)
        self.assertEqual(row.behavior_session_id, self.session.id)
        self.assertEqual(row.academic_year_id, self.config.academic_year_id)

    def test_action_builder_saves_all_choices_in_one_request(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        response = client.post(
            "/admin/behavior/actions",
            data=MultiDict([
                ("config_id", str(self.config.id)),
                ("behavior_category_id", str(self.positive.id)),
                ("behavior_subcategory_id", ""),
                ("name", "Participation level"),
                ("behavior_type", "choice"),
                ("level_number", "1"),
                ("points", "5"),
                ("frequency", "daily"),
                ("sort_order", "0"),
                ("description", "Choose the observed level"),
                ("choices_builder", "1"),
                ("choice_id", ""),
                ("choice_label", "Excellent"),
                ("choice_points", "5"),
                ("choice_description", "Consistently excellent"),
                ("choice_id", ""),
                ("choice_label", "Good"),
                ("choice_points", "3"),
                ("choice_description", "Usually good"),
                ("choice_id", ""),
                ("choice_label", "Needs support"),
                ("choice_points", "1"),
                ("choice_description", "Requires follow-up"),
            ]),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302, response.data[-5000:])
        action = BehaviorAction.query.filter_by(name="Participation level").one()
        self.assertEqual(
            [(item.label, item.points) for item in action.choices],
            [("Excellent", Decimal("5.000")), ("Good", Decimal("3.000")), ("Needs support", Decimal("1.000"))],
        )
        page = client.get("/admin/behavior/actions", query_string={"config_id": self.config.id})
        self.assertNotIn(b"Label | Points | Description", page.data)
        self.assertNotIn(b"Drop-down", page.data)
        self.assertNotIn(b"Linear scale", page.data)


if __name__ == "__main__":
    unittest.main()
