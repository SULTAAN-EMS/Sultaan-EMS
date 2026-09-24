"""Focused Phase 1 tests for Behavior taxonomy and daily attendance."""

import unittest
from datetime import date
from decimal import Decimal

from werkzeug.datastructures import MultiDict
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.behavior_attendance import (
    attendance_days,
    attendance_score_adjustments,
    attendance_statuses,
    ensure_attendance_defaults,
    update_attendance_active_days,
    generate_daily_roster,
    mark_attendance,
)
from app.behavior_service import (
    BehaviorValidationError,
    attendance_points_projection,
    attendance_score_projection,
    calculate_session_score,
    record_event,
    restore_attendance_record,
    void_attendance_record,
)
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearLevelAttendanceDay,
    AcademicYearSubject,
    AuditLog,
    BehaviorAction,
    BehaviorActionChoice,
    BehaviorAttendanceDay,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorAttendanceRecord,
    BehaviorAttendanceDeletion,
    BehaviorEvent,
    BehaviorSession,
    ExamType,
    Student,
    StudentEnrollment,
    User,
)
from migrations.phase_1d_behavior_level_attendance_days import _backfill


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

    def test_attendance_scoring_is_automatic_without_legacy_toggle_or_status_switch(self):
        ensure_attendance_defaults(self.config)
        self.config.behavior_attendance_scoring_enabled = False
        db.session.commit()

        statuses = {item.key: item for item in attendance_statuses(self.config)}
        present = mark_attendance(
            self.config,
            self.session,
            self.enrollment,
            statuses["present"].id,
            date(2026, 8, 29),
        )
        projection = attendance_score_adjustments(
            self.config,
            self.session,
            self.enrollment,
            attendance_records=[present],
        )

        self.assertEqual(projection["positive_points"], Decimal("1.000"))
        self.assertEqual(projection["record_count"], 1)
        ensure_attendance_defaults(self.config)
        self.assertTrue(self.config.behavior_attendance_scoring_enabled)
        self.assertTrue(all(item.is_active for item in attendance_statuses(self.config)))

    def test_status_polarity_update_survives_refresh_and_controls_new_records(self):
        ensure_attendance_defaults(self.config)
        late = next(item for item in attendance_statuses(self.config) if item.key == "late")
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        response = client.post(
            f"/admin/behavior/attendance/statuses/{late.id}",
            data={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "attendance_date": "2026-08-29",
                "label": "Daahid",
                "polarity": "neutral",
                "points": "0.5",
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire(late)
        ensure_attendance_defaults(self.config)
        db.session.expire(late)
        self.assertEqual(late.polarity, "neutral")
        refreshed = client.get(
            "/admin/behavior/attendance",
            query_string={"config_id": self.config.id, "session_id": self.session.id},
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn('name="polarity"><option value="neutral" selected', refreshed.get_data(as_text=True))
        record = mark_attendance(
            self.config,
            self.session,
            self.enrollment,
            late.id,
            date(2026, 8, 29),
            attendance_time="07:30",
            arrival_time="08:00",
        )
        self.assertEqual(record.polarity, "neutral")
        self.assertEqual(
            attendance_points_projection([record])["negative_points"],
            Decimal("0.000"),
        )

    def test_active_days_are_scoped_to_year_level_and_not_display_name(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        before = {item.weekday for item in attendance_days(self.config)}
        self.assertEqual(before, {0, 1, 2, 3, 5, 6})

        self.config.academic_year_level.name = "Dugsi Sare"
        old_days, saved_days = update_attendance_active_days(
            self.config, {0, 1, 2, 5, 6}
        )
        db.session.commit()
        self.assertEqual(old_days, before)
        self.assertEqual(saved_days, {0, 1, 2, 5, 6})
        self.assertEqual(
            {item.weekday for item in attendance_days(self.config)},
            {0, 1, 2, 5, 6},
        )
        self.assertEqual(
            AcademicYearLevelAttendanceDay.query.filter_by(
                academic_year_level_id=self.config.academic_year_level_id,
                is_active=True,
            ).count(),
            5,
        )

    def test_active_days_isolate_two_year_levels_and_reject_invalid_weekdays(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        other_level = AcademicYearLevel(name="Another Track", academic_year=self.config.academic_year)
        other_subject = AcademicYearSubject(
            name="Other Anshax", subject_kind="behavior", max_score=0,
            academic_year=self.config.academic_year, academic_year_level=other_level,
        )
        db.session.add_all([other_level, other_subject])
        db.session.flush()
        other_config = BehaviorConfiguration(
            academic_year=self.config.academic_year,
            academic_year_level=other_level,
            behavior_subject=other_subject,
            frequency="monthly",
            status="active",
        )
        db.session.add(other_config)
        db.session.commit()
        ensure_attendance_defaults(other_config)
        update_attendance_active_days(other_config, {5})
        db.session.commit()

        self.assertEqual({item.weekday for item in attendance_days(other_config)}, {5})
        self.assertEqual(
            {item.weekday for item in attendance_days(self.config)},
            {0, 1, 2, 3, 5, 6},
        )
        with self.assertRaises(BehaviorValidationError):
            update_attendance_active_days(self.config, {7})

    def test_same_level_name_in_different_academic_years_has_an_independent_schedule(self):
        ensure_attendance_defaults(self.config)
        update_attendance_active_days(self.config, {5, 6, 0, 1, 2})

        next_year = AcademicYear(name="2027-2028", is_current=False)
        next_level = AcademicYearLevel(name="Form One", academic_year=next_year)
        next_subject = AcademicYearSubject(
            name="Anshax Next Year", subject_kind="behavior", max_score=0,
            academic_year=next_year, academic_year_level=next_level,
        )
        next_config = BehaviorConfiguration(
            academic_year=next_year,
            academic_year_level=next_level,
            behavior_subject=next_subject,
            frequency="monthly",
            status="active",
        )
        db.session.add_all([next_year, next_level, next_subject, next_config])
        db.session.flush()
        ensure_attendance_defaults(next_config)
        update_attendance_active_days(next_config, {5, 6, 0, 1, 2, 3})
        db.session.commit()

        self.assertEqual(
            {item.weekday for item in attendance_days(self.config)},
            {0, 1, 2, 5, 6},
        )
        self.assertEqual(
            {item.weekday for item in attendance_days(next_config)},
            {0, 1, 2, 3, 5, 6},
        )

    def test_database_prevents_duplicate_level_weekday_rows(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        db.session.add(AcademicYearLevelAttendanceDay(
            academic_year_level_id=self.config.academic_year_level_id,
            weekday=5,
            label="Saturday",
            is_active=True,
        ))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_attendance_date_validation_uses_the_enrollment_year_level_schedule(self):
        ensure_attendance_defaults(self.config)
        update_attendance_active_days(self.config, {5, 6, 0, 1, 2})
        db.session.commit()
        thursday = date(2026, 9, 17)
        with self.assertRaises(BehaviorValidationError):
            generate_daily_roster(self.config, self.session, thursday)

        update_attendance_active_days(self.config, {5, 6, 0, 1, 2, 3})
        db.session.commit()
        self.assertEqual(generate_daily_roster(self.config, self.session, thursday), 1)

    def test_level_schedule_migration_seeds_from_legacy_configuration_days(self):
        db.session.add_all([
            BehaviorAttendanceDay(
                behavior_configuration_id=self.config.id,
                weekday=5,
                label="Saturday",
                is_active=True,
            ),
            BehaviorAttendanceDay(
                behavior_configuration_id=self.config.id,
                weekday=4,
                label="Friday",
                is_active=False,
            ),
        ])
        db.session.commit()
        connection = db.engine.connect()
        transaction = connection.begin()
        _backfill(connection)
        transaction.commit()
        connection.close()
        self.assertEqual(
            {
                item.weekday
                for item in AcademicYearLevelAttendanceDay.query.filter_by(
                    academic_year_level_id=self.config.academic_year_level_id,
                    is_active=True,
                ).all()
            },
            {5},
        )

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
        self.assertIn(b"Sub-categories", subcategory_response.data)
        self.assertIn(b"Structure overview", taxonomy_response.data)

    def test_void_is_auditable_excluded_from_scoring_and_duplicate_safe(self):
        ensure_attendance_defaults(self.config)
        db.session.commit()
        self.session.behavior_allocation = Decimal("15.000")
        self.session.attendance_allocation = Decimal("5.000")
        db.session.commit()
        statuses = {item.key: item for item in self.config.attendance_statuses}
        records = []
        for index, key in enumerate(("present", "late", "absent", "excused", "official_leave")):
            records.append(mark_attendance(
                self.config,
                self.session,
                self.enrollment,
                statuses[key].id,
                date(2026, 8, 24 + index),
                marked_by_id=self.admin.id,
                arrival_time="09:15" if key == "late" else None,
            ))
        db.session.commit()

        projection = attendance_points_projection(records + [records[0]])
        self.assertEqual(projection["positive_points"], Decimal("1.000"))
        self.assertEqual(projection["negative_points"], Decimal("1.500"))
        self.assertEqual(projection["record_count"], 5)

        void_attendance_record(records[0], self.admin.id, "Duplicate daily mark")
        db.session.commit()
        voided = db.session.get(BehaviorAttendanceRecord, records[0].id)
        self.assertEqual(voided.status, "voided")
        self.assertEqual(voided.voided_by, self.admin.id)
        self.assertEqual(voided.void_reason, "Duplicate daily mark")
        self.assertIsNotNone(voided.voided_at)

        after_void = attendance_points_projection(BehaviorAttendanceRecord.query.all())
        self.assertEqual(after_void["positive_points"], Decimal("0.000"))
        self.assertEqual(after_void["negative_points"], Decimal("1.500"))
        self.assertEqual(after_void["record_count"], 4)
        with self.assertRaises(BehaviorValidationError):
            mark_attendance(
                self.config,
                self.session,
                self.enrollment,
                statuses["present"].id,
                records[0].attendance_date,
                marked_by_id=self.admin.id,
            )

        restore_attendance_record(voided)
        db.session.commit()
        self.assertEqual(db.session.get(BehaviorAttendanceRecord, records[0].id).status, "active")
        restored = attendance_points_projection(BehaviorAttendanceRecord.query.all())
        self.assertEqual(restored["positive_points"], Decimal("1.000"))
        self.assertEqual(restored["negative_points"], Decimal("1.500"))

    def test_void_route_allows_optional_reason_and_keeps_record_in_history(self):
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.configure", "behavior.void",
        ])
        db.session.commit()
        ensure_attendance_defaults(self.config)
        db.session.commit()
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        record = mark_attendance(
            self.config, self.session, self.enrollment, present.id,
            date(2026, 8, 29), marked_by_id=self.admin.id,
        )
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

        response = client.post(
            f"/admin/behavior/attendance/records/{record.id}/void",
            data={"config_id": self.config.id, "session_id": self.session.id, "reason": ""},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        persisted = db.session.get(BehaviorAttendanceRecord, record.id)
        self.assertEqual(persisted.status, "voided")
        self.assertIsNone(persisted.void_reason)
        records_page = client.get(
            "/admin/behavior/attendance",
            query_string={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "attendance_view": "records",
            },
        )
        self.assertEqual(records_page.status_code, 200)
        self.assertIn(b"Xaaladda diiwaanka", records_page.data)
        self.assertIn(b"Baabi'i Xaadirka", records_page.data)
        self.assertIn(b"record_status", records_page.data)
        self.assertTrue(any(
            row.action == "Behavior Attendance"
            and "Voided Attendance record" in (row.details or "")
            for row in __import__("app.models", fromlist=["AuditLog"]).AuditLog.query.all()
        ))

    def test_restore_route_preserves_identity_context_and_audit(self):
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.configure", "behavior.void",
        ])
        ensure_attendance_defaults(self.config)
        db.session.commit()
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        record = mark_attendance(
            self.config,
            self.session,
            self.enrollment,
            present.id,
            date(2026, 9, 19),
            marked_by_id=self.admin.id,
        )
        db.session.commit()
        original_id = record.id
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

        client.post(
            f"/admin/behavior/attendance/records/{original_id}/void",
            data={
                "year_id": self.config.academic_year_id,
                "level_id": self.config.academic_year_level_id,
                "class_id": self.enrollment.academic_year_class_id,
                "config_id": self.config.id,
                "session_id": self.session.id,
                "reason": "Mistaken mark",
            },
            follow_redirects=False,
        )
        self.assertEqual(db.session.get(BehaviorAttendanceRecord, original_id).status, "voided")
        voided_projection = attendance_points_projection(BehaviorAttendanceRecord.query.all())
        self.assertEqual(voided_projection["positive_points"], Decimal("0.000"))

        response = client.post(
            f"/admin/behavior/attendance/records/{original_id}/restore",
            data={
                "year_id": self.config.academic_year_id,
                "level_id": self.config.academic_year_level_id,
                "class_id": self.enrollment.academic_year_class_id,
                "config_id": self.config.id,
                "session_id": self.session.id,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("year_id=", location)
        self.assertIn("level_id=", location)
        self.assertIn("class_id=", location)
        self.assertIn("config_id=", location)
        self.assertIn("session_id=", location)
        restored = db.session.get(BehaviorAttendanceRecord, original_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.id, original_id)
        self.assertEqual(restored.status, "active")
        self.assertEqual(BehaviorAttendanceRecord.query.count(), 1)
        restored_projection = attendance_points_projection(BehaviorAttendanceRecord.query.all())
        self.assertEqual(restored_projection["positive_points"], Decimal("1.000"))
        self.assertTrue(any(
            row.action == "Behavior Attendance"
            and f"Restored Attendance record {original_id}" in (row.details or "")
            and "previous_state=voided" in (row.details or "")
            and "new_state=active" in (row.details or "")
            for row in AuditLog.query.all()
        ))

        records_page = client.get(
            "/admin/behavior/attendance",
            query_string={
                "year_id": self.config.academic_year_id,
                "level_id": self.config.academic_year_level_id,
                "class_id": self.enrollment.academic_year_class_id,
                "config_id": self.config.id,
                "session_id": self.session.id,
                "attendance_view": "records",
            },
        )
        self.assertEqual(records_page.status_code, 200)
        body = records_page.get_data(as_text=True)
        self.assertIn('data-att-action="restore"', body)
        self.assertIn("data-action-restore", body)

        response = client.get(
            "/admin/behavior/attendance/records",
            query_string={
                "year_id": self.config.academic_year_id,
                "level_id": self.config.academic_year_level_id,
                "class_id": self.enrollment.academic_year_class_id,
                "config_id": self.config.id,
                "session_id": self.session.id,
                "state": "active",
                "q": "BHV-P1-001",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selected"]["year_id"], self.config.academic_year_id)
        self.assertEqual(payload["selected"]["level_id"], self.config.academic_year_level_id)
        self.assertEqual(payload["selected"]["class_id"], self.enrollment.academic_year_class_id)
        self.assertEqual(payload["selected"]["config_id"], self.config.id)
        self.assertEqual(payload["selected"]["session_id"], self.session.id)
        self.assertEqual([item["record_id"] for item in payload["rows"]], [original_id])

    def test_delete_route_soft_deletes_record_and_keeps_read_only_history(self):
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.configure", "behavior.void",
        ])
        self.session.maximum_score = Decimal("20.000")
        self.session.behavior_allocation = Decimal("15.000")
        self.session.attendance_allocation = Decimal("5.000")
        db.session.commit()
        ensure_attendance_defaults(self.config)
        db.session.commit()
        present = next(item for item in self.config.attendance_statuses if item.key == "present")
        record = mark_attendance(
            self.config, self.session, self.enrollment, present.id,
            date(2026, 8, 29), marked_by_id=self.admin.id, note="On time",
        )
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

        rejected = client.post(
            f"/admin/behavior/attendance/records/{record.id}/delete",
            data={"config_id": self.config.id, "session_id": self.session.id, "reason": "Cleanup"},
            follow_redirects=False,
        )
        self.assertEqual(rejected.status_code, 302)
        self.assertIsNotNone(db.session.get(BehaviorAttendanceRecord, record.id))
        self.assertEqual(BehaviorAttendanceDeletion.query.count(), 0)

        response = client.post(
            f"/admin/behavior/attendance/records/{record.id}/delete",
            data={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "reason": "Duplicate daily mark",
                "acknowledged": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        persisted = db.session.get(BehaviorAttendanceRecord, record.id)
        self.assertIsNotNone(persisted)
        self.assertIsNotNone(persisted.deleted_at)
        self.assertEqual(persisted.deleted_by, self.admin.id)
        self.assertEqual(persisted.deletion_reason, "Duplicate daily mark")
        self.assertEqual(BehaviorAttendanceDeletion.query.count(), 0)

        records_page = client.get(
            "/admin/behavior/attendance",
            query_string={
                "config_id": self.config.id,
                "session_id": self.session.id,
                "attendance_view": "records",
            },
        )
        self.assertEqual(records_page.status_code, 200)
        body = records_page.get_data(as_text=True)
        self.assertIn('La Tirtiray', body)
        self.assertIn('"record_status": "deleted"', body)
        self.assertIn("read-only", body)

        score = calculate_session_score(self.config, self.session, self.enrollment)
        self.assertEqual(score["attendance_record_count"], 0)
        self.assertEqual(score["attendance_status"], "INCOMPLETE")

    def test_voided_late_and_absent_are_removed_from_each_month_and_session_total(self):
        ensure_attendance_defaults(self.config)
        self.session.behavior_allocation = Decimal("15.000")
        self.session.attendance_allocation = Decimal("5.000")
        db.session.commit()
        statuses = {item.key: item for item in self.config.attendance_statuses}
        september_present = mark_attendance(
            self.config, self.session, self.enrollment, statuses["present"].id,
            date(2026, 9, 1), marked_by_id=self.admin.id,
        )
        october_late = mark_attendance(
            self.config, self.session, self.enrollment, statuses["late"].id,
            date(2026, 10, 1), marked_by_id=self.admin.id, arrival_time="09:15",
        )
        november_absent = mark_attendance(
            self.config, self.session, self.enrollment, statuses["absent"].id,
            date(2026, 11, 1), marked_by_id=self.admin.id,
        )
        db.session.commit()

        self.assertEqual(attendance_points_projection([september_present])["positive_points"], Decimal("1.000"))
        self.assertEqual(attendance_points_projection([october_late])["negative_points"], Decimal("0.500"))
        self.assertEqual(attendance_points_projection([november_absent])["negative_points"], Decimal("1.000"))
        before = attendance_score_projection(
            self.config, self.session, self.enrollment,
            attendance_records=[september_present, october_late, november_absent],
        )
        self.assertEqual(before["allocation"], Decimal("5.000"))
        self.assertEqual(before["attendance_score"], Decimal("0.000"))

        void_attendance_record(october_late, self.admin.id, "Wrong late status")
        void_attendance_record(november_absent, self.admin.id, "Duplicate absence")
        db.session.commit()
        after = attendance_score_projection(
            self.config, self.session, self.enrollment,
            attendance_records=[september_present, october_late, november_absent],
        )
        self.assertEqual(after["allocation"], Decimal("5.000"))
        self.assertEqual(after["attendance_score"], Decimal("1.000"))
        active_projection = attendance_points_projection(
            [september_present, october_late, november_absent]
        )
        self.assertEqual(active_projection["positive_points"], Decimal("1.000"))
        self.assertEqual(active_projection["negative_points"], Decimal("0.000"))
        self.assertEqual(active_projection["record_count"], 1)

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
