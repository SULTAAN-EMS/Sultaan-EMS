"""Phase 2D route and workflow coverage for the Behavior admin UX."""

import unittest

from app import db
from app.models import BehaviorEvent
from test_phase2c_behavior_events import TestPhase2CBehaviorEvents


class TestPhase2DBehaviorUX(TestPhase2CBehaviorEvents):
    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True
        return client

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

        dashboard = client.get(f"/admin/behavior/?{query}").get_data(as_text=True)
        self.assertIn("Behavior One", dashboard)
        self.assertIn("Behavior Two", dashboard)
        self.assertIn("Class board", dashboard)
        self.assertIn("Maximum", dashboard)

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
