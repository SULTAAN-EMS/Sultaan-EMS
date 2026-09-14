"""Focused Phase 2F response-contract and persistence coverage."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from app import db
from app.behavior_service import BehaviorValidationError, calculate_session_score, edit_event, record_event
from app.behavior_attendance import mark_attendance
from app.models import BehaviorAction, BehaviorActionChoice, BehaviorAttendanceStatus
from test_phase2c_behavior_events import TestPhase2CBehaviorEvents


class TestPhase2FBehaviorResponses(TestPhase2CBehaviorEvents):
    def setUp(self):
        super().setUp()
        self.choice_action = BehaviorAction(category=self.positive, name="Choice action", level_number=3, points=Decimal("3"), frequency="ad_hoc", behavior_type="choice")
        self.choice_a = BehaviorActionChoice(action=self.choice_action, label="Aad u wanaagsan", points=Decimal("2"), sort_order=1)
        self.choice_b = BehaviorActionChoice(action=self.choice_action, label="Wanaagsan", points=Decimal("1"), sort_order=2)
        db.session.add_all([self.choice_a, self.choice_b])
        db.session.commit()

    def test_multiple_choice_is_single_and_snapshotted(self):
        event = record_event(
            self.config_one, self.enrollment_one, self.session_a, self.positive,
            self.choice_action, choices=[self.choice_a], idempotency_key="phase2f-choice",
        )
        db.session.commit()
        self.assertEqual(event.response_type_snapshot, "multiple_choice")
        self.assertEqual(event.response_display_snapshot, "Aad u wanaagsan")
        self.assertEqual(event.response_points, Decimal("2.000"))
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_one, self.enrollment_two, self.session_a, self.positive, self.choice_action, idempotency_key="phase2f-choice-missing")

    def test_multiple_choice_at_action_maximum_is_valid(self):
        self.choice_a.points = self.choice_action.points
        db.session.commit()
        event = record_event(
            self.config_one, self.enrollment_one, self.session_a,
            self.positive, self.choice_action, choices=[self.choice_a],
            idempotency_key="phase4a-choice-at-max",
        )
        self.assertEqual(event.response_points, Decimal("3.000"))

    def test_checkboxes_are_multiple_and_capped_by_action(self):
        action = BehaviorAction(category=self.positive, name="Participation", level_number=4, points=3, frequency="ad_hoc", behavior_type="selection")
        choices = [
            BehaviorActionChoice(action=action, label="Homework", points=1, sort_order=1),
            BehaviorActionChoice(action=action, label="Group work", points=2, sort_order=2),
        ]
        db.session.add_all([action, *choices])
        db.session.commit()
        event = record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, action, choices=choices, idempotency_key="phase2f-checkbox")
        db.session.commit()
        self.assertEqual(event.response_type_snapshot, "checkboxes")
        self.assertEqual(event.response_points, Decimal("3.000"))
        too_high = BehaviorAction(category=self.positive, name="Over limit", level_number=5, points=3, frequency="ad_hoc", behavior_type="selection")
        high_choices = [
            BehaviorActionChoice(action=too_high, label="One", points=2),
            BehaviorActionChoice(action=too_high, label="Two", points=2),
        ]
        db.session.add_all([too_high, *high_choices])
        db.session.flush()
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_one, self.enrollment_two, self.session_a, self.positive, too_high, choices=high_choices, idempotency_key="phase2f-checkbox-over")

    def test_checkbox_quarter_points_sum_and_edit_recomputes_selection(self):
        action = BehaviorAction(
            category=self.positive,
            name="Quarter point checklist",
            level_number=4,
            points=Decimal("0.500"),
            frequency="ad_hoc",
            behavior_type="selection",
        )
        first = BehaviorActionChoice(action=action, label="First", points=Decimal("0.250"), sort_order=1)
        second = BehaviorActionChoice(action=action, label="Second", points=Decimal("0.250"), sort_order=2)
        db.session.add_all([action, first, second])
        db.session.commit()

        event = record_event(
            self.config_one,
            self.enrollment_one,
            self.session_a,
            self.positive,
            action,
            choices=[first, second],
            idempotency_key="phase2f-quarter-sum",
        )
        self.assertEqual(event.response_points, Decimal("0.500"))
        self.assertEqual(event.points_applied, Decimal("0.500"))

        edit_event(
            event,
            self.config_one,
            self.enrollment_one,
            self.session_a,
            self.positive,
            action,
            choices=[first],
            reason="Remove the second selected response",
        )
        self.assertEqual(event.response_points, Decimal("0.250"))
        self.assertEqual(event.points_applied, Decimal("0.250"))
        self.assertEqual(event.response_display_snapshot, "First")

    def test_rating_uses_configured_scale_and_proportional_points(self):
        action = BehaviorAction(category=self.positive, name="Effort rating", level_number=6, points=3, frequency="ad_hoc", behavior_type="rating", rating_scale=7)
        db.session.add(action)
        db.session.commit()
        event = record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, action, rating=7, idempotency_key="phase2f-rating")
        db.session.commit()
        self.assertEqual(event.response_rating, 7)
        self.assertEqual(event.response_rating_scale, 7)
        self.assertEqual(event.response_points, Decimal("3.000"))

    def test_text_and_official_note_both_reach_report(self):
        action = BehaviorAction(category=self.positive, name="Written reflection", level_number=7, points=2, frequency="ad_hoc", behavior_type="text_note", response_required=True)
        db.session.add(action)
        db.session.commit()
        event = record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, action, response_text="Wuxuu caawiyey kooxdiisa.", notes="Macallinku wuu xaqiijiyey.", idempotency_key="phase2f-text")
        db.session.commit()
        self.assertEqual(event.response_text, "Wuxuu caawiyey kooxdiisa.")
        self.assertEqual(event.notes, "Macallinku wuu xaqiijiyey.")
        self.assertIn("Wuxuu caawiyey kooxdiisa.", event.response_snapshot)

    def test_legacy_multiple_choice_cannot_bypass_action_maximum(self):
        self.choice_a.points = Decimal("4")
        self.choice_action.response_type = None
        db.session.commit()
        with self.assertRaises(BehaviorValidationError):
            record_event(
                self.config_one, self.enrollment_one, self.session_a,
                self.positive, self.choice_action, choices=[self.choice_a],
                idempotency_key="phase4a-legacy-choice-over-max",
            )

    def test_normalized_behavior_ledger_rejects_but_attendance_accepts_daily_rows(self):
        self.session_a.behavior_allocation = Decimal("10")
        self.session_a.attendance_allocation = Decimal("7")
        self.positive_action.points = Decimal("2")
        db.session.commit()

        record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="phase4a-behavior-1")
        record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="phase4a-behavior-2")
        with self.assertRaises(BehaviorValidationError):
            record_event(self.config_one, self.enrollment_one, self.session_a, self.positive, self.positive_action, idempotency_key="phase4a-behavior-3")

        status = BehaviorAttendanceStatus(
            behavior_configuration_id=self.config_one.id,
            key="present", label="Joogid", polarity="positive",
            points=Decimal("1"), contributes_to_behavior=True, is_active=True,
        )
        db.session.add(status)
        db.session.commit()
        for offset in range(3):
            mark_attendance(
                self.config_one, self.session_a, self.enrollment_one, status.id,
                date(2026, 8, 1) + timedelta(days=offset),
            )
        db.session.commit()
        mark_attendance(
            self.config_one, self.session_a, self.enrollment_one, status.id,
            date(2026, 8, 4),
        )
        db.session.commit()
        score = calculate_session_score(self.config_one, self.session_a, self.enrollment_one)
        self.assertEqual(score["ledger"]["behavior"]["allocation"], Decimal("10.000"))
        self.assertEqual(score["ledger"]["behavior"]["remaining"], Decimal("1.000"))
        self.assertEqual(score["ledger"]["attendance"]["allocation"], Decimal("7.000"))
        self.assertGreater(score["attendance_score"], Decimal("0.000"))
        self.assertLess(score["attendance_score"], Decimal("7.000"))
        self.assertEqual(
            score["ledger"]["attendance"]["remaining"],
            Decimal("7.000") - score["attendance_score"],
        )

        absent = BehaviorAttendanceStatus(
            behavior_configuration_id=self.config_one.id,
            key="absent", label="Maqnaansho", polarity="negative",
            points=Decimal("1"), contributes_to_behavior=True, is_active=True,
        )
        db.session.add(absent)
        db.session.commit()
        for offset in range(3):
            mark_attendance(
                self.config_one, self.session_a, self.enrollment_one, absent.id,
                date(2026, 8, 10) + timedelta(days=offset),
            )
        db.session.commit()
        mark_attendance(
            self.config_one, self.session_a, self.enrollment_one, absent.id,
            date(2026, 8, 13),
        )
        db.session.commit()
        score = calculate_session_score(self.config_one, self.session_a, self.enrollment_one)
        self.assertLessEqual(score["attendance_score"], Decimal("7.000"))

    def test_edit_event_excludes_existing_points_from_capacity(self):
        self.session_a.behavior_allocation = Decimal("10")
        self.session_a.attendance_allocation = Decimal("7")
        self.positive_action.points = Decimal("2")
        edit_action = BehaviorAction(
            category=self.positive, name="Boundary action", level_number=8,
            points=Decimal("3"), frequency="ad_hoc", behavior_type="direct_action",
        )
        over_action = BehaviorAction(
            category=self.positive, name="Over boundary action", level_number=9,
            points=Decimal("4"), frequency="ad_hoc", behavior_type="direct_action",
        )
        db.session.add_all([edit_action, over_action])
        db.session.commit()
        event = record_event(
            self.config_one, self.enrollment_one, self.session_a,
            self.positive, self.positive_action, idempotency_key="phase4a-edit-target",
        )
        record_event(
            self.config_one, self.enrollment_one, self.session_a,
            self.positive, edit_action, idempotency_key="phase4a-edit-other",
        )
        db.session.commit()
        edit_event(
            event, self.config_one, self.enrollment_one, self.session_a,
            self.positive, self.positive_action, reason="Keep exact boundary",
        )
        db.session.rollback()
        with self.assertRaises(BehaviorValidationError):
            edit_event(
                event, self.config_one, self.enrollment_one, self.session_a,
                self.positive, over_action, reason="Would exceed capacity",
            )


if __name__ == "__main__":
    unittest.main()
