"""Phase 2D route and workflow coverage for the Behavior admin UX."""

import unittest
import re
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event

from app import db
from app.behavior_attendance import attendance_statuses, ensure_attendance_defaults, mark_attendance, update_attendance_active_days
from app.behavior_service import attendance_policy_for_session, record_event
from app.models import (
    AcademicYearClass,
    BehaviorAttendanceClosure,
    BehaviorAttendanceRecord,
    BehaviorAttendanceStatus,
    BehaviorEvent,
    BehaviorGradeScale,
    BehaviorSessionRevision,
)
from test_phase2c_behavior_events import TestPhase2CBehaviorEvents


class TestPhase2DBehaviorUX(TestPhase2CBehaviorEvents):
    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        return client

    def _prepare_session_revision(self):
        self.admin.set_permissions(["behavior.view", "behavior.configure", "behavior.audit"])
        self.session_a.maximum_score = Decimal("30.000")
        self.session_a.behavior_allocation = Decimal("21.000")
        self.session_a.attendance_allocation = Decimal("9.000")
        self.session_a.attendance_present_weight = Decimal("0.150")
        ensure_attendance_defaults(self.config_one)
        update_attendance_active_days(
            self.config_one,
            {5, 6, 0, 1, 2},
            academic_year_level_id=self.level_one.id,
        )
        present = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.config_one.id,
            key="present",
        ).one()
        self.session_a.attendance_policy_snapshot = json.dumps({
            "version": "test-v1",
            "weights": {"present": "0.150", "late": "0.500", "absent": "1.000"},
        })
        self.session_a.period_start_date = None
        self.session_a.period_end_date = None
        grade_scale = BehaviorGradeScale(
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            grade="A",
            min_score=Decimal("24.000"),
            max_score=Decimal("30.000"),
            grade_point=Decimal("4.000"),
            sort_order=1,
            is_active=True,
            is_pass=True,
        )
        attendance = BehaviorAttendanceRecord(
            student_id=self.student_one.id,
            student_enrollment_id=self.enrollment_one.id,
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=self.class_one.id,
            attendance_date=date(2026, 9, 15),
            status_id=present.id,
            status_key_snapshot="present",
            status_label_snapshot="Joogid",
            polarity="positive",
            points_applied=Decimal("0.150"),
            status="active",
            marked_by_id=self.admin.id,
        )
        db.session.add_all([grade_scale, attendance])
        record_event(
            self.config_one,
            self.enrollment_one,
            self.session_a,
            self.positive,
            self.positive_action,
            occurred_at=datetime(2026, 9, 15, 10, 0),
            idempotency_key="session-revision-audit-event",
        )
        db.session.commit()
        return present

    def _revision_payload(self, **overrides):
        payload = {
            "csrf_token": "",
            "period_start_date": "2026-09-05",
            "period_end_date": "2026-11-04",
            "maximum_score": "20",
            "behavior_allocation": "11",
            "attendance_allocation": "9",
            "attendance_present_weight": "0.2",
            "closure_date": "2026-09-16",
            "closure_reason": "Fasax guud oo la ansixiyey.",
            "reason": "Goluhu wuxuu xilliga ku soo koobay 4 November.",
        }
        payload.update(overrides)
        return payload

    def test_session_revision_preview_and_apply_preserve_auditable_scores(self):
        present = self._prepare_session_revision()
        excused = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.config_one.id,
            key="excused",
        ).one()
        official_leave = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.config_one.id,
            key="official_leave",
        ).one()
        for status, marked_date in (
            (excused, date(2026, 9, 19)),
            (official_leave, date(2026, 9, 20)),
        ):
            db.session.add(BehaviorAttendanceRecord(
                student_id=self.student_one.id,
                student_enrollment_id=self.enrollment_one.id,
                behavior_configuration_id=self.config_one.id,
                behavior_session_id=self.session_a.id,
                academic_year_id=self.year_one.id,
                academic_year_level_id=self.level_one.id,
                academic_year_class_id=self.class_one.id,
                attendance_date=marked_date,
                status_id=status.id,
                status_key_snapshot=status.key,
                status_label_snapshot=status.label,
                polarity="neutral",
                points_applied=Decimal("0.000"),
                status="active",
                marked_by_id=self.admin.id,
            ))
        db.session.commit()
        client = self._client_as_admin()
        path = f"/admin/behavior/sessions/{self.session_a.id}/revise"
        response = client.post(path, data=self._revision_payload())
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("45", body)
        self.assertIn("Cudurdaar, Fasaxid Rasmi ah iyo Maalin Dugsi Xiran", body)
        self.assertIn("<td>9</td>", body)
        self.assertIn('name="revision_action" value="apply"', body)
        self.assertEqual(BehaviorSessionRevision.query.count(), 0)
        self.assertEqual(self.session_a.maximum_score, Decimal("30.000"))

        revision_form = self._revision_payload(
            revision_action="apply",
            expected_updated_at=self.session_a.updated_at.isoformat(),
            expected_revision_count="0",
            confirm_recalculation="yes",
            confirm_closure="yes",
            confirm_annual_total="yes",
        )
        response = client.post(path, data=revision_form, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session_a.maximum_score, Decimal("20.000"))
        self.assertEqual(self.session_a.behavior_allocation, Decimal("11.000"))
        self.assertEqual(self.session_a.attendance_allocation, Decimal("9.000"))
        self.assertEqual(self.session_a.attendance_present_weight, Decimal("0.200"))
        self.assertEqual(self.session_a.period_start_date, date(2026, 9, 5))
        self.assertEqual(self.session_a.period_end_date, date(2026, 11, 4))
        for status_key in ("excused", "official_leave"):
            revised_status_record = BehaviorAttendanceRecord.query.filter_by(
                behavior_session_id=self.session_a.id,
                student_enrollment_id=self.enrollment_one.id,
                status_key_snapshot=status_key,
            ).one()
            self.assertEqual(revised_status_record.points_applied, Decimal("0.200"))
            self.assertEqual(revised_status_record.polarity, "positive")
        self.assertEqual(BehaviorAttendanceRecord.query.filter_by(
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 15),
        ).one().points_applied, Decimal("0.200"))
        closure_record = BehaviorAttendanceRecord.query.filter_by(
            behavior_session_id=self.session_a.id,
            student_enrollment_id=self.enrollment_one.id,
            attendance_date=date(2026, 9, 16),
        ).one()
        self.assertEqual(closure_record.status_key_snapshot, "school_closure")
        self.assertEqual(closure_record.points_applied, Decimal("0.200"))
        self.assertEqual(BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.config_one.id,
            key="present",
        ).one().points, present.points)
        effective_present = next(
            status for status in attendance_statuses(
                self.config_one,
                academic_year_level_id=self.level_one.id,
                session=self.session_a,
            )
            if status.key == "present"
        )
        self.assertEqual(effective_present.effective_points, Decimal("0.200"))
        policy = attendance_policy_for_session(self.session_a)
        for status_key in ("present", "excused", "official_leave"):
            self.assertEqual(policy["weights"][status_key], Decimal("0.200"))

        mark_attendance(
            self.config_one,
            self.session_a,
            self.enrollment_two,
            excused.id,
            date(2026, 9, 19),
            marked_by_id=self.admin.id,
        )
        mark_attendance(
            self.config_one,
            self.session_a,
            self.enrollment_two,
            official_leave.id,
            date(2026, 9, 20),
            marked_by_id=self.admin.id,
        )
        db.session.commit()
        new_positive_marks = BehaviorAttendanceRecord.query.filter(
            BehaviorAttendanceRecord.behavior_session_id == self.session_a.id,
            BehaviorAttendanceRecord.student_enrollment_id == self.enrollment_two.id,
            BehaviorAttendanceRecord.status_key_snapshot.in_({"excused", "official_leave"}),
        ).all()
        self.assertEqual(len(new_positive_marks), 2)
        self.assertTrue(all(row.polarity == "positive" for row in new_positive_marks))
        self.assertTrue(all(row.points_applied == Decimal("0.200") for row in new_positive_marks))

        revision = BehaviorSessionRevision.query.one()
        before = json.loads(revision.before_snapshot)
        after = json.loads(revision.after_snapshot)
        self.assertEqual(before["session"]["maximum_score"], "30.000")
        self.assertEqual(after["session"]["maximum_score"], "20.000")
        self.assertEqual(before["student_scores"][0]["final_score"], "12.650")
        self.assertEqual(after["student_scores"][0]["final_score"], "8.300")
        self.assertEqual(after["closures"][0]["date"], "2026-09-16")
        self.assertEqual(BehaviorGradeScale.query.filter_by(
            behavior_session_id=self.session_a.id,
            grade="A",
        ).one().max_score, Decimal("20.000"))

    def test_session_revision_blocks_mismatched_calendar_and_outside_history(self):
        self._prepare_session_revision()
        client = self._client_as_admin()
        path = f"/admin/behavior/sessions/{self.session_a.id}/revise"
        payload = self._revision_payload(attendance_present_weight="0.21")
        response = client.post(path, data=payload)
        self.assertEqual(response.status_code, 200)
        mismatch_body = response.get_data(as_text=True)
        self.assertIn("Tirada maalmaha jadwalku", mismatch_body)
        self.assertIn("disabled", mismatch_body)
        self.assertEqual(BehaviorSessionRevision.query.count(), 0)

        payload = self._revision_payload()
        db.session.add(BehaviorAttendanceRecord(
            student_id=self.student_one.id,
            student_enrollment_id=self.enrollment_one.id,
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=self.class_one.id,
            attendance_date=date(2026, 9, 4),
            status_id=BehaviorAttendanceStatus.query.filter_by(
                behavior_configuration_id=self.config_one.id,
                key="present",
            ).one().id,
            status_key_snapshot="present",
            status_label_snapshot="Joogid",
            polarity="positive",
            points_applied=Decimal("0.150"),
            status="active",
            marked_by_id=self.admin.id,
        ))
        db.session.commit()
        response = client.post(path, data=payload)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("1 diiwaan xaadirin", body)
        self.assertIn("disabled", body)
        self.assertEqual(self.session_a.maximum_score, Decimal("30.000"))
        self.assertEqual(BehaviorSessionRevision.query.count(), 0)

    def test_session_revision_preview_explains_attendance_closure_conflicts(self):
        self._prepare_session_revision()
        present = BehaviorAttendanceStatus.query.filter_by(
            behavior_configuration_id=self.config_one.id,
            key="present",
        ).one()
        conflict = BehaviorAttendanceRecord(
            student_id=self.student_one.id,
            student_enrollment_id=self.enrollment_one.id,
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=self.class_one.id,
            attendance_date=date(2026, 9, 16),
            status_id=present.id,
            status_key_snapshot="present",
            status_label_snapshot="Joogid",
            polarity="positive",
            points_applied=Decimal("0.150"),
            status="voided",
            voided_at=datetime(2026, 9, 17, 9, 0),
            void_reason="Tijaabo audit ah",
            marked_by_id=self.admin.id,
        )
        db.session.add(conflict)
        db.session.commit()

        response = self._client_as_admin().post(
            f"/admin/behavior/sessions/{self.session_a.id}/revise",
            data=self._revision_payload(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("1 qodob oo u baahan hubin", body)
        self.assertIn(f"#{conflict.id}", body)
        self.assertIn(self.student_one.student_code, body)
        self.assertIn("Diiwaanka xaadirinta waa laga noqday.", body)
        self.assertIn("akhris-keliya", body.lower())
        self.assertEqual(self.session_a.maximum_score, Decimal("30.000"))
        self.assertEqual(BehaviorSessionRevision.query.count(), 0)

    def test_session_revision_accepts_disjoint_class_closures(self):
        present = self._prepare_session_revision()
        self.session_a.applicable_shift = "afternoon"
        self.class_one.school_shift = "afternoon"
        class_two = AcademicYearClass(
            name="1B",
            academic_year_level_id=self.level_one.id,
            school_shift="afternoon",
        )
        db.session.add(class_two)
        db.session.flush()
        self.enrollment_two.academic_year_class_id = class_two.id
        db.session.flush()
        closure_one = BehaviorAttendanceClosure(
            academic_year_id=self.year_one.id,
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 16),
            scope_type="class",
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=self.class_one.id,
            closure_kind="holiday",
            reason="Fasax guud",
            status="active",
            created_by_id=self.admin.id,
        )
        closure_two = BehaviorAttendanceClosure(
            academic_year_id=self.year_one.id,
            behavior_configuration_id=self.config_one.id,
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 16),
            scope_type="class",
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=class_two.id,
            closure_kind="holiday",
            reason="Fasax guud",
            status="active",
            created_by_id=self.admin.id,
        )
        db.session.add_all([closure_one, closure_two])
        db.session.flush()
        for enrollment, closure in (
            (self.enrollment_one, closure_two),
            (self.enrollment_two, closure_two),
        ):
            db.session.add(BehaviorAttendanceRecord(
                student_id=enrollment.student_id,
                student_enrollment_id=enrollment.id,
                behavior_configuration_id=self.config_one.id,
                behavior_session_id=self.session_a.id,
                academic_year_id=self.year_one.id,
                academic_year_level_id=self.level_one.id,
                academic_year_class_id=enrollment.academic_year_class_id,
                attendance_date=date(2026, 9, 16),
                status_id=present.id,
                behavior_attendance_closure_id=closure.id,
                status_key_snapshot="school_closure",
                status_label_snapshot="Maalin Dugsi Xiran",
                polarity="positive",
                points_applied=Decimal("0.150"),
                status="active",
                marked_by_id=self.admin.id,
            ))
        db.session.commit()

        client = self._client_as_admin()
        path = f"/admin/behavior/sessions/{self.session_a.id}/revise"
        preview = client.post(path, data=self._revision_payload())
        self.assertEqual(preview.status_code, 200)
        preview_body = preview.get_data(as_text=True)
        self.assertNotIn("is-waafaqla'aan fasax ayaa jirta", preview_body)
        self.assertNotIn("Fasax is-dul-saaran", preview_body)
        self.assertIn("1 xiriir ayaa la waafajin doonaa", preview_body)
        self.assertIn(f"Fasax #{closure_two.id}", preview_body)
        self.assertIn(f"Fasax #{closure_one.id}", preview_body)

        apply_payload = self._revision_payload(
            revision_action="apply",
            expected_updated_at=self.session_a.updated_at.isoformat(),
            expected_revision_count="0",
            confirm_recalculation="yes",
            confirm_closure="yes",
            confirm_annual_total="yes",
        )
        response = client.post(path, data=apply_payload, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session_a.maximum_score, Decimal("20.000"))
        self.assertEqual(BehaviorAttendanceClosure.query.filter_by(
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 16),
            status="active",
        ).count(), 2)
        self.assertEqual(BehaviorAttendanceRecord.query.filter_by(
            student_enrollment_id=self.enrollment_one.id,
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 16),
        ).one().behavior_attendance_closure_id, closure_one.id)
        self.assertEqual(BehaviorAttendanceRecord.query.filter_by(
            student_enrollment_id=self.enrollment_two.id,
            behavior_session_id=self.session_a.id,
            attendance_date=date(2026, 9, 16),
        ).one().behavior_attendance_closure_id, closure_two.id)
        revision = BehaviorSessionRevision.query.one()
        before_snapshot = json.loads(revision.before_snapshot)
        after_snapshot = json.loads(revision.after_snapshot)
        self.assertEqual(len(after_snapshot["closures"]), 2)
        self.assertEqual(before_snapshot["closure_link_repairs"][0]["from_closure_id"], closure_two.id)
        self.assertEqual(before_snapshot["closure_link_repairs"][0]["to_closure_id"], closure_one.id)
        self.assertEqual(after_snapshot["closure_link_repairs"], before_snapshot["closure_link_repairs"])

    def test_session_revision_rejects_overlapping_class_closures(self):
        self._prepare_session_revision()
        self.session_a.applicable_shift = "afternoon"
        self.class_one.school_shift = "afternoon"
        closures = [
            BehaviorAttendanceClosure(
                academic_year_id=self.year_one.id,
                behavior_configuration_id=self.config_one.id,
                behavior_session_id=self.session_a.id,
                attendance_date=date(2026, 9, 16),
                scope_type="class",
                academic_year_level_id=self.level_one.id,
                academic_year_class_id=self.class_one.id,
                closure_kind="holiday",
                reason="Fasax guud",
                status="active",
                created_by_id=self.admin.id,
            )
            for _ in range(2)
        ]
        db.session.add_all(closures)
        db.session.commit()

        response = self._client_as_admin().post(
            f"/admin/behavior/sessions/{self.session_a.id}/revise",
            data=self._revision_payload(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("1 is-waafaqla'aan fasax", body)
        self.assertIn(self.class_one.name, body)
        self.assertIn("ku dul dhacaysaa", body)
        self.assertEqual(self.session_a.maximum_score, Decimal("30.000"))
        self.assertEqual(BehaviorSessionRevision.query.count(), 0)

    def test_scoped_behavior_pages_render(self):
        client = self._client_as_admin()
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.edit", "behavior.void",
            "behavior.configure", "behavior.audit",
        ])
        db.session.commit()
        query = f"config_id={self.config_one.id}&session_id={self.session_a.id}&class_id={self.class_one.id}"
        paths = [
            f"/admin/behavior/?{query}",
            f"/admin/behavior/students?{query}",
            f"/admin/behavior/events?{query}",
            f"/admin/behavior/history?{query}",
            "/admin/behavior/configuration",
            f"/admin/behavior/sessions?config_id={self.config_one.id}",
            f"/admin/behavior/categories?config_id={self.config_one.id}",
            f"/admin/behavior/actions?config_id={self.config_one.id}",
            "/admin/behavior/audit",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

        configuration = client.get(f"/admin/behavior/configuration?config_id={self.config_one.id}")
        configuration_body = configuration.get_data(as_text=True)
        self.assertNotIn("Only active Academic Levels from the selected Academic Year are available.", configuration_body)
        self.assertNotIn("Save Configuration", configuration_body)
        self.assertIn('data-config-autosave', configuration_body)
        self.assertIn('data-config-autosave-status', configuration_body)

        dashboard = client.get(f"/admin/behavior/?{query}").get_data(as_text=True)
        self.assertIn("Behavior One", dashboard)
        self.assertIn("Behavior Two", dashboard)
        self.assertIn("Ardayga La Diiwaan Geliyey", dashboard)
        self.assertIn("Ardayda iyo Dhibcaha Hadda", dashboard)
        self.assertIn("Ugu Badnaan", dashboard)

        allocation = client.get(
            f"/admin/behavior/session-allocation?config_id={self.config_one.id}"
            f"&session_id={self.session_a.id}&enrollment_id={self.enrollment_one.id}"
        )
        self.assertEqual(allocation.status_code, 200)
        allocation_body = allocation.get_data(as_text=True)
        attendance_start = allocation_body.index(
            'class="allocation-ledger allocation-ledger--attendance"'
        )
        attendance_end = allocation_body.index(
            '<div class="allocation-summary"', attendance_start
        )
        attendance_ledger = allocation_body[attendance_start:attendance_end]
        self.assertIn("Positive Evidence", attendance_ledger)
        self.assertIn("Negative Evidence", attendance_ledger)
        self.assertNotIn("Shared total allocation", allocation_body)
        self.assertNotIn("Positive Used / Remaining", attendance_ledger)

    def test_configuration_autosave_returns_json(self):
        client = self._client_as_admin()
        self.admin.set_permissions(["behavior.view", "behavior.configure"])
        db.session.commit()

        response = client.post(
            "/admin/behavior/configuration",
            data={
                "csrf_token": "",
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": str(self.level_one.id),
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "exam_ref": "",
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["config_id"], self.config_one.id)

    def test_behavior_navigation_active_state_and_page_persistence(self):
        client = self._client_as_admin()
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.edit", "behavior.void",
            "behavior.configure", "behavior.audit",
        ])
        db.session.commit()
        config_query = f"?config_id={self.config_one.id}"
        pages = [
            ("/admin/behavior/", "/admin/behavior/", "Wajahada Hore"),
            ("/admin/behavior/configuration", "/admin/behavior/configuration", "Dejinta Hab-dhaqanka"),
            ("/admin/behavior/taxonomy" + config_query, "/admin/behavior/taxonomy", "Dejinta Qodobada"),
            ("/admin/behavior/categories" + config_query, "/admin/behavior/taxonomy", "Dejinta Qodobada"),
            ("/admin/behavior/subcategories" + config_query, "/admin/behavior/taxonomy", "Dejinta Qodobada"),
            ("/admin/behavior/actions" + config_query, "/admin/behavior/taxonomy", "Dejinta Qodobada"),
            ("/admin/behavior/sessions" + config_query, "/admin/behavior/sessions", "Dejinta Xilliyada"),
            ("/admin/behavior/session-allocation" + config_query, "/admin/behavior/session-allocation", "Qorshaynta Qoondaynta"),
            ("/admin/behavior/grade-management" + config_query, "/admin/behavior/grade-management", "Maareynta Dhibcaha"),
            ("/admin/behavior/attendance" + config_query, "/admin/behavior/attendance", "Xaadirka"),
            ("/admin/behavior/students" + config_query, "/admin/behavior/students", "Diiwaanka Hab-dhaqanka Ardeyga"),
            ("/admin/behavior/events", "/admin/behavior/events", "Dhacdooyinka"),
            ("/admin/behavior/history", "/admin/behavior/history", "Taariikhda"),
            ("/admin/behavior/audit", "/admin/behavior/audit", "Diiwaanka Dabagalka"),
        ]
        for path, active_href, active_label in pages:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertRegex(
                    body,
                    r'<a href="/admin/behavior/" class="is-active">.*?<span class="nav-label">Behavior</span></a>',
                )
                self.assertRegex(
                    body,
                    rf'<a href="{re.escape(active_href)}[^\"]*" class="is-active">.*?{re.escape(active_label)}',
                )

        client.get("/admin/behavior/")
        events_response = client.get("/admin/behavior/events")
        self.assertEqual(events_response.status_code, 200)
        self.assertRegex(
            events_response.get_data(as_text=True),
            r'href="/admin/behavior/events[^\"]*" class="is-active"',
        )

    def test_dashboard_uses_batched_event_and_attendance_queries(self):
        client = self._client_as_admin()
        self.admin.set_permissions(["behavior.view"])
        db.session.commit()
        statements = []

        def count_scoped_queries(conn, cursor, statement, parameters, context, executemany):
            normalized = " ".join(statement.lower().split())
            if "from behavior_events" in normalized or "from behavior_attendance_records" in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", count_scoped_queries)
        try:
            response = client.get(
                "/admin/behavior/?"
                f"config_id={self.config_one.id}&session_id={self.session_a.id}"
                f"&class_id={self.class_one.id}"
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", count_scoped_queries)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sum("from behavior_events" in statement for statement in statements),
            1,
        )
        self.assertEqual(
            sum("from behavior_attendance_records" in statement for statement in statements),
            1,
        )

    def test_record_edit_void_and_detail_workflow(self):
        client = self._client_as_admin()
        query = f"config_id={self.config_one.id}&session_id={self.session_a.id}&class_id={self.class_one.id}"
        response = client.post(
            f"/admin/behavior/students?{query}",
            data={
                "csrf_token": "",
                "config_id": str(self.config_one.id),
                "class_id": str(self.class_one.id),
                "student_enrollment_id": str(self.enrollment_one.id),
                "behavior_session_id": str(self.session_a.id),
                "direction": "positive",
                "behavior_category_id": str(self.positive.id),
                "behavior_action_id": str(self.positive_action.id),
                "occurred_at": "2026-08-29T13:15",
                "notes": "Recorded from the Phase 2D board",
                "idempotency_key": "phase2d-board-workflow",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        event = BehaviorEvent.query.filter_by(idempotency_key="phase2d-board-workflow").one()

        for path in (
            f"/admin/behavior/students/{self.enrollment_one.id}?config_id={self.config_one.id}&session_id={self.session_a.id}",
            f"/admin/behavior/events/{event.id}",
            f"/admin/behavior/events/{event.id}/edit",
        ):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

        response = client.post(
            f"/admin/behavior/events/{event.id}/edit",
            data={
                "csrf_token": "",
                "behavior_session_id": str(self.session_a.id),
                "behavior_category_id": str(self.positive.id),
                "behavior_action_id": str(self.positive_action.id),
                "direction": "positive",
                "occurred_at": "2026-08-29T13:20",
                "notes": "Corrected from the event detail page",
                "reason": "Phase 2D workflow test correction",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(event)
        self.assertEqual(str(event.points_applied), "2.000")

        response = client.post(
            f"/admin/behavior/events/{event.id}/void",
            data={"csrf_token": "", "config_id": str(self.config_one.id), "reason": "Phase 2D void test"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(event)
        self.assertEqual(event.status, "voided")
        history = client.get(f"/admin/behavior/history?{query}&status=voided").get_data(as_text=True)
        self.assertIn("Voided", history)

    def test_year_aware_board_does_not_leak_other_year_configuration(self):
        client = self._client_as_admin()
        other_class = __import__("app.models", fromlist=["AcademicYearClass"]).AcademicYearClass(
            name="2A", academic_year_level=self.level_two,
        )
        other_student = __import__("app.models", fromlist=["Student"]).Student(
            student_code="BHV-OTHER-001", full_name="Other Year Student", is_active=True,
        )
        db.session.add_all([other_class, other_student])
        db.session.flush()
        db.session.add(__import__("app.models", fromlist=["StudentEnrollment"]).StudentEnrollment(
            student_id=other_student.id, academic_year_id=self.year_two.id,
            academic_year_level_id=self.level_two.id, academic_year_class_id=other_class.id,
            status="active", academic_outcome="pending", enrollment_source="manual",
        ))
        db.session.commit()
        response = client.get(
            f"/admin/behavior/?year_id={self.year_one.id}&level_id={self.level_one.id}"
            f"&config_id={self.config_one.id}&session_id={self.session_a.id}"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("2026-2027", body)
        self.assertIn("Behavior One", body)
        self.assertIn("Behavior Two", body)
        self.assertNotIn("Other Year Student", body)

    def test_invalid_explicit_scope_does_not_fallback_to_another_scope(self):
        client = self._client_as_admin()

        wrong_config_response = client.get(
            f"/admin/behavior/?year_id={self.year_one.id}"
            f"&level_id={self.level_one.id}&config_id={self.config_two.id}"
            f"&session_id={self.session_a.id}"
        )
        wrong_config_body = wrong_config_response.get_data(as_text=True)
        self.assertEqual(wrong_config_response.status_code, 200)
        self.assertNotIn("Behavior One", wrong_config_body)
        self.assertNotIn("Behavior Two", wrong_config_body)

        wrong_session_response = client.get(
            f"/admin/behavior/?year_id={self.year_one.id}"
            f"&level_id={self.level_one.id}&config_id={self.config_one.id}"
            f"&session_id={self.session_other_year.id}"
        )
        wrong_session_body = wrong_session_response.get_data(as_text=True)
        self.assertEqual(wrong_session_response.status_code, 200)
        self.assertNotIn("Behavior One", wrong_session_body)
        self.assertNotIn("Behavior Two", wrong_session_body)


if __name__ == "__main__":
    unittest.main()
