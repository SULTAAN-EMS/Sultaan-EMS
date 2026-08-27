"""Phase 4B regression tests for session-vs-final evaluation behavior."""

import unittest

from app import db
from app.models import (
    PromotionEvaluation,
    PromotionOutcomeApplication,
    PromotionRule,
    PromotionRuleCriticalSubject,
    StudentEnrollmentMovement,
    User,
)
from app.promotion_service import (
    PromotionValidationError,
    apply_academic_outcome,
    evaluate_promotion_scope,
    evaluate_student_promotion,
    plan_evaluation_transition,
    portal_academic_outcome,
    promotion_operational_status,
    promotion_scope_summary,
    set_promotion_rules_enabled,
    upsert_promotion_rule,
)
from test_phase4a_exam_aware_promotion import TestPhase4AExamAwarePromotion


class TestPhase4BSessionEvaluation(TestPhase4AExamAwarePromotion):
    """Reuse the isolated Phase 4A fixture and assert the new workflow boundary."""

    def test_session_pass_is_history_only_and_portal_shows_gudbay(self):
        self._result(self.midterm, self.math, 90)
        self._result(self.midterm, self.english, 90)

        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(evaluation.final_outcome, "PASS")
        self.assertEqual(self.enrollment.academic_outcome, "pending")
        self.assertEqual(portal_academic_outcome(self.enrollment, exam_id=self.midterm.id)["label"], "GUDBAY")
        self.assertEqual(promotion_operational_status(self.enrollment, exam_id=self.midterm.id)["evaluation_outcome"], "GUDBAY")
        self.assertEqual(PromotionOutcomeApplication.query.count(), 0)
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)

    def test_session_fail_is_history_only_and_portal_shows_hadhay(self):
        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 40)

        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(evaluation.final_outcome, "FAIL")
        self.assertEqual(self.enrollment.academic_outcome, "pending")
        self.assertEqual(portal_academic_outcome(self.enrollment, exam_id=self.midterm.id)["label"], "HADHAY")
        self.assertEqual(promotion_operational_status(self.enrollment, exam_id=self.midterm.id)["evaluation_outcome"], "HADHAY")
        self.assertEqual(PromotionOutcomeApplication.query.count(), 0)

    def test_session_critical_subject_override_remains_exam_specific(self):
        upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[self.math.id],
        )
        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 100)

        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(evaluation.base_outcome, "PASS")
        self.assertEqual(evaluation.final_outcome, "FAIL")
        self.assertEqual(evaluation.override_reason, "FAILED_CRITICAL_SUBJECT")
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_session_rules_off_uses_baseline_without_transition(self):
        set_promotion_rules_enabled(False)
        self._result(self.midterm, self.math, 90)
        self._result(self.midterm, self.english, 90)

        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(evaluation.final_outcome, "PASS")
        self.assertIsNone(evaluation.promotion_rule_id)
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_scope_execution_never_saves_enrollment_outcome_for_session(self):
        self._result(self.midterm, self.math, 75)
        self._result(self.midterm, self.english, 75)
        executed = evaluate_promotion_scope(
            self.year.id,
            self.level.id,
            self.midterm.id,
            academic_year_class_id=self.year_class.id,
            subject_ids=[self.math.id, self.english.id],
            persist=True,
        )
        db.session.commit()

        self.assertEqual(executed["counts"]["evaluated"], 1)
        self.assertEqual(executed["counts"]["outcomes_saved"], 0)
        self.assertEqual(self.enrollment.academic_outcome, "pending")
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)

    def test_final_scope_save_persists_exact_outcome_and_makes_apply_ready(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        executed = evaluate_promotion_scope(
            self.year.id,
            self.level.id,
            self.final_exam.id,
            academic_year_class_id=self.year_class.id,
            subject_ids=[self.math.id, self.english.id],
            persist=True,
        )
        self.assertEqual(executed["counts"]["evaluated"], 1)
        self.assertEqual(executed["counts"]["outcomes_saved"], 1)
        self.assertEqual(self.enrollment.academic_outcome, "passed")
        db.session.commit()

        saved = PromotionEvaluation.query.filter_by(
            student_enrollment_id=self.enrollment.id,
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            exam_id=self.final_exam.id,
        ).one()
        self.assertEqual(saved.final_outcome, "PASS")
        plan = plan_evaluation_transition(
            self.year.id,
            self.level.id,
            self.year_class.id,
            self.final_exam.id,
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        self.assertEqual(plan["items"][0]["classification"], "READY")
        self.assertTrue(plan["items"][0]["eligible"])

    def test_final_scope_fail_persists_failed_outcome_and_makes_repeat_ready(self):
        self._result(self.final_exam, self.math, 40)
        self._result(self.final_exam, self.english, 40)
        evaluate_promotion_scope(
            self.year.id,
            self.level.id,
            self.final_exam.id,
            academic_year_class_id=self.year_class.id,
            subject_ids=[self.math.id, self.english.id],
            persist=True,
        )
        db.session.commit()
        self.assertEqual(self.enrollment.academic_outcome, "failed")
        plan = plan_evaluation_transition(
            self.year.id,
            self.level.id,
            self.year_class.id,
            self.final_exam.id,
            action="repeat",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        self.assertEqual(plan["items"][0]["classification"], "READY")

    def test_final_scope_incomplete_is_atomic_and_creates_no_partial_rows(self):
        with self.assertRaisesRegex(PromotionValidationError, "No evaluation or academic outcome was committed"):
            evaluate_promotion_scope(
                self.year.id,
                self.level.id,
                self.final_exam.id,
                academic_year_class_id=self.year_class.id,
                subject_ids=[self.math.id, self.english.id],
                persist=True,
            )
        db.session.rollback()
        self.assertEqual(PromotionEvaluation.query.count(), 0)
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_repeated_final_scope_save_does_not_duplicate_evaluation_or_outcome_ledger(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        for _ in range(2):
            evaluate_promotion_scope(
                self.year.id,
                self.level.id,
                self.final_exam.id,
                academic_year_class_id=self.year_class.id,
                subject_ids=[self.math.id, self.english.id],
                persist=True,
            )
            db.session.commit()
        self.assertEqual(self.enrollment.academic_outcome, "passed")
        self.assertEqual(PromotionOutcomeApplication.query.count(), 0)
        self.assertEqual(PromotionEvaluation.query.count(), 1)

    def test_final_evaluate_route_returns_confirmed_feedback_after_commit(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        user = User(username="phase4b-admin", full_name="Phase 4B Admin", role="super_admin", is_active=True)
        user.set_password("phase4b-password")
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True
        response = client.post(
            "/admin/promotion-rules/evaluate",
            data={
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "exam_id": self.final_exam.id,
                "academic_year_class_id": self.year_class.id,
                "subject_ids": [str(self.math.id), str(self.english.id)],
                "action": "execute",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Evaluation saved successfully", response.data)
        self.assertIn(b"matching academic outcome", response.data)
        self.assertIn(b"Apply Outcomes is now available", response.data)
        self.assertEqual(self.enrollment.academic_outcome, "passed")

    def test_configure_route_persists_critical_subjects_for_selected_exam(self):
        user = User(username="phase4b-rule-admin", full_name="Phase 4B Rule Admin", role="super_admin", is_active=True)
        user.set_password("phase4b-rule-password")
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        response = client.post(
            "/admin/promotion-rules/configure",
            data={
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "exam_id": self.named_final_nonfinal.id,
                "overall_pass_threshold": "50",
                "critical_subject_pass_threshold": "50",
                "critical_subject_ids": [str(self.english.id)],
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        rule = PromotionRule.query.filter_by(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            exam_id=self.named_final_nonfinal.id,
        ).one()
        self.assertEqual(
            {
                row.academic_year_subject_id
                for row in PromotionRuleCriticalSubject.query.filter_by(promotion_rule_id=rule.id).all()
            },
            {self.english.id},
        )

    def test_non_final_evaluate_route_confirms_portal_outcome_after_commit(self):
        upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[self.math.id],
        )
        db.session.commit()
        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 100)
        user = User(username="phase4b-session-admin", full_name="Phase 4B Session Admin", role="super_admin", is_active=True)
        user.set_password("phase4b-session-password")
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        response = client.post(
            "/admin/promotion-rules/evaluate",
            data={
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "exam_id": self.midterm.id,
                "academic_year_class_id": self.year_class.id,
                "subject_ids": [str(self.math.id), str(self.english.id)],
                "action": "execute",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Evaluation saved successfully", response.data)
        self.assertIn(b"available to the Student Result Portal", response.data)
        self.assertEqual(
            portal_academic_outcome(self.enrollment, exam_id=self.midterm.id)["label"],
            "HADHAY",
        )

    def test_session_apply_outcome_is_blocked_even_if_called_directly(self):
        self._result(self.midterm, self.math, 90)
        self._result(self.midterm, self.english, 90)
        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        with self.assertRaisesRegex(PromotionValidationError, "Only a Final Evaluation"):
            apply_academic_outcome(evaluation.id)
        self.assertEqual(self.enrollment.academic_outcome, "pending")
        self.assertEqual(PromotionOutcomeApplication.query.count(), 0)

    def test_final_evaluation_can_be_applied_explicitly(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.final_exam), persist=True
        )
        db.session.commit()

        application = apply_academic_outcome(evaluation.id)
        db.session.commit()
        self.assertEqual(application.action, "outcome")
        self.assertEqual(self.enrollment.academic_outcome, "passed")
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)

    def test_final_application_is_idempotency_protected(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.final_exam), persist=True
        )
        db.session.commit()
        apply_academic_outcome(evaluation.id)
        db.session.commit()

        with self.assertRaisesRegex(PromotionValidationError, "already been applied"):
            apply_academic_outcome(evaluation.id)
        self.assertEqual(PromotionOutcomeApplication.query.count(), 1)

    def test_session_history_is_immutable_across_re_evaluation(self):
        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 100)
        first = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()
        first_outcome = first.final_outcome

        upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[],
            critical_subject_pass_threshold=30,
        )
        second = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(PromotionEvaluation.query.count(), 2)
        self.assertEqual(db.session.get(PromotionEvaluation, first.id).final_outcome, first_outcome)
        self.assertEqual(second.final_outcome, "PASS")
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_scope_summary_marks_session_workflow_and_final_workflow(self):
        session_summary = promotion_scope_summary(
            self.year.id, self.level.id, academic_year_class_id=self.year_class.id, exam_id=self.midterm.id
        )
        final_summary = promotion_scope_summary(
            self.year.id, self.level.id, academic_year_class_id=self.year_class.id, exam_id=self.final_exam.id
        )
        self.assertEqual(session_summary["workflow"], "SESSION")
        self.assertEqual(final_summary["workflow"], "FINAL")
        self.assertEqual(session_summary["exam"].id, self.midterm.id)
        self.assertEqual(final_summary["exam"].id, self.final_exam.id)

    def test_cross_year_exam_cannot_be_used_for_session_scope(self):
        with self.assertRaisesRegex(PromotionValidationError, "does not belong to the selected Academic Year"):
            promotion_scope_summary(
                self.year.id,
                self.level.id,
                academic_year_class_id=self.year_class.id,
                exam_id=999999,
            )

    def test_stale_final_transition_status_does_not_leak_into_session_portal(self):
        self._result(self.final_exam, self.math, 90)
        self._result(self.final_exam, self.english, 90)
        final_evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.final_exam), persist=True
        )
        db.session.commit()
        apply_academic_outcome(final_evaluation.id)
        db.session.commit()

        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 40)
        session_evaluation = evaluate_student_promotion(
            self.enrollment, self._context(self.midterm), persist=True
        )
        db.session.commit()

        self.assertEqual(session_evaluation.final_outcome, "FAIL")
        self.assertEqual(portal_academic_outcome(self.enrollment, exam_id=self.midterm.id)["label"], "HADHAY")


if __name__ == "__main__":
    unittest.main()
