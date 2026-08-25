import unittest

from app import db
from app.models import Exam, PromotionOutcomeApplication, StudentEnrollment, StudentEnrollmentMovement, User
from app.promotion_service import (
    PromotionValidationError,
    apply_academic_outcome,
    plan_evaluation_transition,
    portal_academic_outcome,
    promotion_consistency_audit,
    promotion_operational_status,
    promotion_scope_summary,
    set_promotion_rules_enabled,
    transition_applied_outcome,
    upsert_promotion_rule,
)
from app.enrollment_service import transition_student_enrollment
from app.enrollment_service import EnrollmentValidationError
from test_phase3c_promotion_evaluation import TestPhase3CPromotionEvaluation
from test_phase3d_promotion_integration import TestPhase3DPromotionIntegration


class TestPhase3EOperationalWorkflow(TestPhase3DPromotionIntegration):
    """Phase 3E operational checks, using the isolated Phase 3D fixture."""

    def test_01_status_not_evaluated(self):
        self.assertEqual(promotion_operational_status(self.source)["code"], "NOT_EVALUATED")

    def test_02_status_evaluated_not_applied(self):
        self._evaluation(self.source)
        self.assertEqual(promotion_operational_status(self.source)["code"], "EVALUATED_NOT_APPLIED")

    def test_03_status_outcome_applied(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        status = promotion_operational_status(self.source)
        self.assertEqual(status["code"], "OUTCOME_APPLIED")
        self.assertEqual(status["eligibility_code"], "ELIGIBLE_FOR_TRANSITION")

    def test_04_status_eligible_for_promotion(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        self.assertIn("promotion", promotion_operational_status(self.source)["eligible_actions"])

    def test_05_status_eligible_for_repeat(self):
        evaluation = self._evaluation(self.source, outcome="FAIL")
        apply_academic_outcome(evaluation.id)
        self.assertIn("repeat", promotion_operational_status(self.source)["eligible_actions"])

    def test_06_rules_on_blocks_manual_promotion_but_not_transfer(self):
        set_promotion_rules_enabled(True)
        upsert_promotion_rule(self.year.id, self.year_level.id, critical_subject_ids=[])
        with self.assertRaisesRegex(EnrollmentValidationError, "Promotion Rules are active"):
            transition_student_enrollment(
                self.student.id,
                self.source.id,
                self.next_year.id,
                self.next_level.id,
                self.next_class.id,
                action="promotion",
            )

    def test_transfer_preserves_existing_academic_outcome(self):
        self.source.academic_outcome = "passed"
        transition_student_enrollment(
            self.student.id,
            self.source.id,
            self.next_year.id,
            self.next_level.id,
            self.next_class.id,
            action="transfer",
        )
        self.assertEqual(self.source.academic_outcome, "passed")

    def test_06_status_eligible_for_graduation(self):
        student = self._student("P3E-GRAD")
        source = self._enrollment(student, self.year, self.year_final_level, self.final_class)
        evaluation = self._evaluation(source)
        apply_academic_outcome(evaluation.id)
        self.assertIn("graduation", promotion_operational_status(source)["eligible_actions"])

    def test_07_status_transition_completed(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        transition_applied_outcome(
            evaluation.id,
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        self.assertEqual(promotion_operational_status(self.source)["code"], "TRANSITION_COMPLETED")

    def test_08_incomplete_blocks_transition(self):
        self._evaluation(self.source, status="INCOMPLETE")
        self.assertEqual(promotion_operational_status(self.source)["code"], "BLOCKED")

    def test_09_invalid_blocks_transition(self):
        self._evaluation(self.source, status="INVALID")
        self.assertEqual(promotion_operational_status(self.source)["code"], "BLOCKED")

    def test_10_not_evaluated_blocks_whole_class_transition(self):
        plan = plan_evaluation_transition(
            self.year.id, self.year_level.id, self.source_class.id, self.exam.id,
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        self.assertEqual(plan["items"][0]["classification"], "NOT_EVALUATED")
        self.assertFalse(plan["items"][0]["eligible"])

    def test_11_bulk_preview_is_read_only_and_marks_outcome_not_applied(self):
        self._evaluation(self.source)
        before = (PromotionOutcomeApplication.query.count(), StudentEnrollment.query.count(), StudentEnrollmentMovement.query.count())
        plan = plan_evaluation_transition(
            self.year.id, self.year_level.id, self.source_class.id, self.exam.id,
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        after = (PromotionOutcomeApplication.query.count(), StudentEnrollment.query.count(), StudentEnrollmentMovement.query.count())
        self.assertEqual(plan["items"][0]["classification"], "OUTCOME_NOT_APPLIED")
        self.assertEqual(before, after)

    def test_12_cross_year_student_is_excluded_from_scope_summary(self):
        other = self._student("P3E-NEXT")
        self._enrollment(other, self.next_year, self.next_level, self.next_class)
        summary = promotion_scope_summary(self.year.id, self.year_level.id)
        self.assertEqual(summary["counts"]["total_students"], 1)
        self.assertEqual(summary["rows"][0]["enrollment"].academic_year_id, self.year.id)

    def test_13_wrong_level_evaluation_cannot_authorize(self):
        evaluation = self._evaluation(self.source)
        evaluation.academic_year_level_id = self.year_final_level.id
        db.session.flush()
        self.assertEqual(promotion_operational_status(self.source)["code"], "NOT_EVALUATED")
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)

    def test_14_duplicate_apply_is_safe(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)
        self.assertEqual(PromotionOutcomeApplication.query.count(), 1)

    def test_15_duplicate_promotion_is_safe(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        kwargs = dict(
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        transition_applied_outcome(evaluation.id, **kwargs)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, **kwargs)
        self.assertEqual(StudentEnrollment.query.filter_by(academic_year_id=self.next_year.id).count(), 1)

    def test_16_duplicate_repeat_is_safe(self):
        evaluation = self._evaluation(self.source, outcome="FAIL")
        apply_academic_outcome(evaluation.id)
        kwargs = dict(
            action="repeat",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        transition_applied_outcome(evaluation.id, **kwargs)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, **kwargs)

    def test_17_duplicate_graduation_is_safe(self):
        student = self._student("P3E-GRAD-DUP")
        source = self._enrollment(student, self.year, self.year_final_level, self.final_class)
        evaluation = self._evaluation(source)
        apply_academic_outcome(evaluation.id)
        transition_applied_outcome(evaluation.id, action="graduation")
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="graduation")

    def test_18_historical_audit_detects_scope_anomaly(self):
        evaluation = self._evaluation(self.source)
        evaluation.academic_year_level_id = self.year_final_level.id
        db.session.flush()
        result = promotion_consistency_audit(academic_year_id=self.year.id)
        codes = {item["code"] for item in result["anomalies"]}
        self.assertIn("EVALUATION_SCOPE_MISMATCH", codes)

    def test_19_summary_counts_are_year_level_scoped(self):
        self._evaluation(self.source)
        other = self._student("P3E-OTHER-YEAR")
        other_enrollment = self._enrollment(other, self.next_year, self.next_level, self.next_class)
        self._evaluation(other_enrollment, exam=self.exam)
        summary = promotion_scope_summary(self.year.id, self.year_level.id)
        self.assertEqual(summary["counts"]["evaluated"], 1)
        self.assertEqual(summary["counts"]["total_students"], 1)

    def test_20_portal_does_not_infer_promoted_from_stale_enrollment(self):
        self.source.academic_outcome = "promoted"
        self.assertEqual(
            portal_academic_outcome(self.source, exam_id=self.exam.id)["code"],
            "NOT_EVALUATED",
        )

    def test_21_portal_uses_gudbay_for_exact_pass_evaluation(self):
        self._evaluation(self.source, outcome="PASS")
        outcome = portal_academic_outcome(self.source, exam_id=self.exam.id)
        self.assertEqual(outcome["code"], "PASSED")
        self.assertEqual(outcome["label"], "GUDBAY")

    def test_22_portal_uses_hadhay_for_critical_subject_fail_before_apply(self):
        self._evaluation(self.source, outcome="FAIL")
        outcome = portal_academic_outcome(self.source, exam_id=self.exam.id)
        self.assertEqual(outcome["code"], "FAILED")
        self.assertEqual(outcome["label"], "HADHAY")

    def test_23_portal_shows_promoted_only_after_completed_transition(self):
        evaluation = self._evaluation(self.source, outcome="PASS")
        apply_academic_outcome(evaluation.id)
        transition_applied_outcome(
            evaluation.id,
            action="promotion",
            destination_academic_year_id=self.next_year.id,
            destination_academic_year_level_id=self.next_level.id,
            destination_academic_year_class_id=self.next_class.id,
        )
        outcome = portal_academic_outcome(self.source, exam_id=self.exam.id)
        self.assertEqual(outcome["code"], "PROMOTED")

    def test_24_portal_requires_the_selected_exam_evaluation(self):
        self._evaluation(self.source, outcome="PASS", exam=self.other_exam)
        outcome = portal_academic_outcome(self.source, exam_id=self.exam.id)
        self.assertEqual(outcome["code"], "NOT_EVALUATED")

    def test_25_apply_outcomes_resolves_source_class_after_year_and_level(self):
        user = User.query.filter_by(username="admin").first()
        if user is None:
            user = User(username="admin", full_name="Test Admin", role="super_admin", is_active=True)
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True
        response = client.post(
            "/admin/promotion-rules/bulk-transition",
            data={
                "source_academic_year_id": self.year.id,
                "source_academic_year_level_id": self.year_level.id,
                "exam_id": self.exam.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Form Four", response.data)

    def test_26_portal_rejects_evaluation_exam_from_another_year(self):
        next_exam = Exam(name="Next Year Exam", academic_year_id=self.next_year.id)
        db.session.add(next_exam)
        db.session.flush()
        self._evaluation(self.source, outcome="PASS", exam=next_exam)
        outcome = portal_academic_outcome(self.source, exam_id=next_exam.id)
        self.assertEqual(outcome["code"], "NOT_EVALUATED")

    def test_27_apply_outcomes_preview_renders_plan_items_without_method_collision(self):
        """The read-only preview must render the plan's ``items`` key."""
        self._evaluation(self.source, outcome="PASS")
        user = User.query.filter_by(username="admin").first()
        if user is None:
            user = User(username="admin", full_name="Test Admin", role="super_admin", is_active=True)
            user.set_password("test-password")
            db.session.add(user)
            db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True
        response = client.post(
            "/admin/promotion-rules/bulk-transition",
            data={
                "source_academic_year_id": self.year.id,
                "source_academic_year_level_id": self.year_level.id,
                "source_academic_year_class_id": self.source_class.id,
                "exam_id": self.exam.id,
                "action": "promotion",
                "destination_academic_year_id": self.next_year.id,
                "destination_academic_year_level_id": self.next_level.id,
                "destination_academic_year_class_id": self.next_class.id,
                "mode": "preview",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Read-only preview", response.data)
        self.assertNotIn(b"Internal Server Error", response.data)


class TestPhase3EPassFailConsistency(TestPhase3CPromotionEvaluation):
    """Phase 3E regression anchors for the approved PASS/FAIL rules."""

    def test_20_rules_off_uses_approved_50_percent_baseline(self):
        self._result(self.student, self.exam_a, self.subject_math, 50)
        from app.promotion_service import set_promotion_rules_enabled, evaluate_student_promotion
        set_promotion_rules_enabled(False)
        snapshot = evaluate_student_promotion(self.enrollment, self._context(subjects=[self.math_a]), persist=False)
        self.assertEqual(snapshot.overall_percentage, 50)
        self.assertEqual(snapshot.final_outcome, "PASS")

    def test_21_rules_on_critical_subject_override_remains_correct(self):
        self._result(self.student, self.exam_a, self.subject_math, 40)
        self._result(self.student, self.exam_a, self.subject_english, 100)
        from app.promotion_service import set_promotion_rules_enabled, upsert_promotion_rule, evaluate_student_promotion
        set_promotion_rules_enabled(True)
        upsert_promotion_rule(self.year_a.id, self.level_a.id, critical_subject_ids=[self.math_a.id])
        snapshot = evaluate_student_promotion(self.enrollment, self._context(subjects=[self.math_a, self.english_a]), persist=False)
        self.assertEqual(snapshot.final_outcome, "FAIL")
        self.assertEqual(snapshot.override_reason, "FAILED_CRITICAL_SUBJECT")


if __name__ == "__main__":
    unittest.main()
