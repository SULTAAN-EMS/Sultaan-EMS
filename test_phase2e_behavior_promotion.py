"""Phase 2E integration coverage for Behavior inside Promotion Rules."""

import json
import unittest

from app import db
from app.behavior_service import record_event
from app.models import (
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorGradeScale,
    Exam,
    StudentEnrollment,
)
from app.promotion_service import (
    PromotionValidationError,
    evaluate_student_promotion,
    set_promotion_rules_enabled,
    upsert_promotion_rule,
)
from test_phase2e_behavior_reporting import TestPhase2EBehaviorReporting


class TestPhase2EBehaviorPromotion(TestPhase2EBehaviorReporting):
    """Use the established isolated Behavior fixture and add Promotion checks."""

    def setUp(self):
        super().setUp()
        set_promotion_rules_enabled(True)
        db.session.commit()

    def _context(self, exam, subjects):
        return {
            "academic_year_id": self.year_one.id,
            "academic_year_level_id": self.year_level_one.id,
            "exam_id": exam.id,
            "subject_ids": [subject.id for subject in subjects],
        }

    def _behavior_grades(self, session=None):
        session = session or self.session_one
        db.session.add_all([
            BehaviorGradeScale(
                configuration=self.configuration,
                session=session,
                grade="F",
                min_score=0,
                max_score=24.999,
                grade_point=0,
                description="Behavior fail",
                sort_order=1,
                is_active=True,
                is_pass=False,
            ),
            BehaviorGradeScale(
                configuration=self.configuration,
                session=session,
                grade="A",
                min_score=25,
                max_score=50,
                grade_point=4,
                description="Behavior pass",
                sort_order=2,
                is_active=True,
                is_pass=True,
            ),
        ])
        db.session.commit()

    def _event(self, polarity, points, name):
        category = BehaviorCategory(
            configuration=self.configuration,
            name=name,
            polarity=polarity,
            is_active=True,
        )
        action = BehaviorAction(
            category=category,
            name=f"{name} action",
            level_number=1,
            points=points,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([category, action])
        db.session.flush()
        record_event(
            self.configuration,
            self.enrollment,
            self.session_one,
            category,
            action,
            idempotency_key=f"phase2e-{polarity}-{name}",
        )
        db.session.commit()

    def test_no_events_are_a_valid_baseline_and_can_pass(self):
        self._behavior_grades()

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        result = json.loads(evaluation.evaluation_context_json)["results"][0]

        self.assertEqual(evaluation.evaluation_status, "EVALUATED")
        self.assertEqual(evaluation.overall_percentage, 50)
        self.assertEqual(evaluation.final_outcome, "PASS")
        self.assertEqual(result["score"], 25)
        self.assertEqual(result["maximum"], 50)
        self.assertEqual(result["status"], "VALID_BASELINE")
        self.assertEqual(result["source"], "behavior_service")
        self.assertEqual(result["grade"]["grade"], "A")
        self.assertEqual(result["grade_point"], 4.0)

    def test_negative_event_below_baseline_fails(self):
        self._behavior_grades()
        self._event("negative", 1, "Late")

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        result = json.loads(evaluation.evaluation_context_json)["results"][0]

        self.assertEqual(evaluation.evaluation_status, "EVALUATED")
        self.assertEqual(result["score"], 24)
        self.assertEqual(result["percentage"], 48)
        self.assertEqual(evaluation.final_outcome, "FAIL")
        self.assertEqual(result["grade"]["grade"], "F")

    def test_positive_event_increases_behavior_score(self):
        self._behavior_grades()
        self._event("positive", 2, "Helpful")

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        result = json.loads(evaluation.evaluation_context_json)["results"][0]

        self.assertEqual(result["score"], 27)
        self.assertEqual(result["percentage"], 54)
        self.assertEqual(evaluation.final_outcome, "PASS")

    def test_behavior_critical_subject_uses_existing_critical_rule(self):
        self._behavior_grades()
        rule = upsert_promotion_rule(
            self.year_one.id,
            self.year_level_one.id,
            exam_id=self.exam_one.id,
            critical_subject_ids=[self.behavior_subject.id],
        )
        db.session.commit()
        self._event("negative", 1, "Late")

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        critical = json.loads(evaluation.critical_subject_results_json)[0]

        self.assertEqual(evaluation.promotion_rule_id, rule.id)
        self.assertEqual(critical["maximum"], 50)
        self.assertEqual(critical["percentage"], 48)
        self.assertEqual(critical["status"], "FAIL")
        self.assertEqual(evaluation.final_outcome, "FAIL")

    def test_ordinary_and_behavior_subjects_use_separate_sources(self):
        self._behavior_grades()

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(
                self.exam_one,
                [self.ordinary_year_subject, self.behavior_subject],
            ),
            persist=False,
        )
        results = json.loads(evaluation.evaluation_context_json)["results"]
        by_kind = {row["subject_kind"]: row for row in results}

        self.assertEqual(evaluation.evaluation_status, "EVALUATED")
        self.assertEqual(by_kind["exam"]["source"], "ordinary_result")
        self.assertEqual(by_kind["behavior"]["source"], "behavior_service")
        self.assertEqual(evaluation.overall_percentage, 70)

    def test_missing_behavior_session_is_incomplete_without_invented_score(self):
        self._behavior_grades()
        exam_without_session = Exam(
            name="Final Without Behavior",
            academic_year=self.year_one,
            academic_level=self.level_one,
            academic_class=self.class_one,
            is_active=True,
            is_published=True,
        )
        db.session.add(exam_without_session)
        db.session.commit()

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(exam_without_session, [self.behavior_subject]),
            persist=False,
        )

        self.assertEqual(evaluation.evaluation_status, "INCOMPLETE")
        self.assertIsNone(evaluation.overall_percentage)
        self.assertIsNone(evaluation.final_outcome)
        self.assertIn("No active Behavior Session", evaluation.override_reason)

    def test_missing_behavior_configuration_is_incomplete(self):
        subject = AcademicYearSubject(
            academic_year=self.year_one,
            academic_year_level=self.year_level_one,
            name="Unconfigured Behavior",
            subject_kind="behavior",
            max_score=0,
            is_active=True,
        )
        db.session.add(subject)
        db.session.commit()

        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [subject]),
            persist=False,
        )

        self.assertEqual(evaluation.evaluation_status, "INCOMPLETE")
        self.assertIsNone(evaluation.overall_percentage)
        self.assertIsNone(evaluation.final_outcome)
        self.assertIn("No Behavior configuration", evaluation.override_reason)

    def test_exact_exam_id_maps_each_behavior_session_without_name_fallback(self):
        self._behavior_grades()
        self._behavior_grades(self.session_two)
        first = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        second = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_two, [self.behavior_subject]),
            persist=False,
        )
        first_row = json.loads(first.evaluation_context_json)["results"][0]
        second_row = json.loads(second.evaluation_context_json)["results"][0]

        self.assertEqual(first_row["behavior_session_id"], self.session_one.id)
        self.assertEqual(second_row["behavior_session_id"], self.session_two.id)
        self.assertNotEqual(first_row["behavior_session_id"], second_row["behavior_session_id"])

    def test_wrong_year_and_level_are_rejected(self):
        with self.assertRaises(PromotionValidationError):
            evaluate_student_promotion(
                self.enrollment,
                {
                    "academic_year_id": self.year_two.id,
                    "academic_year_level_id": self.year_level_one.id,
                    "exam_id": self.exam_one.id,
                    "subject_ids": [self.behavior_subject.id],
                },
                persist=False,
            )

        with self.assertRaises(PromotionValidationError):
            evaluate_student_promotion(
                self.enrollment,
                {
                    "academic_year_id": self.year_one.id,
                    "academic_year_level_id": self.year_two.id,
                    "exam_id": self.exam_one.id,
                    "subject_ids": [self.behavior_subject.id],
                },
                persist=False,
            )

    def test_reporting_and_promotion_use_the_same_behavior_score(self):
        self._behavior_grades()
        from app.behavior_reporting import get_behavior_report_data

        report = get_behavior_report_data(self.student, self.exam_one)[0]
        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )
        result = json.loads(evaluation.evaluation_context_json)["results"][0]

        self.assertEqual(report["session_score"], result["score"])
        self.assertEqual(report["session_maximum"], result["maximum"])
        self.assertEqual(report["percentage"], result["percentage"])

    def test_promotion_scope_exposes_behavior_but_does_not_auto_select_it(self):
        client = self._client_as_admin()
        with client.session_transaction() as session:
            session["config_center_authenticated"] = True
        response = client.get(
            "/admin/promotion-rules/api/scope"
            f"?year_id={self.year_one.id}&level_id={self.year_level_one.id}"
            f"&exam_id={self.exam_one.id}"
        )

        self.assertEqual(response.status_code, 200)
        subjects = response.get_json()["subjects"]
        behavior = next(item for item in subjects if item["id"] == self.behavior_subject.id)
        ordinary = next(item for item in subjects if item["id"] == self.ordinary_year_subject.id)
        self.assertEqual(behavior["subject_kind"], "behavior")
        self.assertEqual(behavior["max_score"], 50)
        self.assertEqual(ordinary["subject_kind"], "exam")

        from app.routes_admin import _promotion_evaluation_page_data

        page_data = _promotion_evaluation_page_data(
            self.year_one.id,
            self.year_level_one.id,
            self.exam_one.id,
        )
        self.assertIn(self.ordinary_year_subject.id, page_data["default_subject_ids"])
        self.assertNotIn(self.behavior_subject.id, page_data["default_subject_ids"])

        upsert_promotion_rule(
            self.year_one.id,
            self.year_level_one.id,
            exam_id=self.exam_one.id,
            critical_subject_ids=[self.behavior_subject.id],
        )
        db.session.commit()
        page_data = _promotion_evaluation_page_data(
            self.year_one.id,
            self.year_level_one.id,
            self.exam_one.id,
        )
        self.assertIn(self.behavior_subject.id, page_data["default_subject_ids"])

    def test_same_exam_name_in_another_year_cannot_cross_resolve(self):
        from app.behavior_promotion_adapter import resolve_behavior_promotion_context

        other_level = AcademicYearLevel(
            academic_year=self.year_two,
            legacy_level=self.level_one,
            name="Secondary",
            is_active=True,
        )
        db.session.add(other_level)
        db.session.flush()
        other_class = AcademicYearClass(
            academic_year_level=other_level,
            legacy_class=self.class_one,
            name="Form Four",
            is_active=True,
        )
        other_subject = AcademicYearSubject(
            academic_year_id=self.year_two.id,
            academic_year_level_id=other_level.id,
            name="Dabeecad",
            subject_kind="behavior",
            max_score=0,
            is_active=True,
        )
        other_exam = Exam(
            name=self.exam_one.name,
            academic_year_id=self.year_two.id,
            academic_level_id=self.level_one.id,
            academic_class_id=self.class_one.id,
            is_active=True,
            is_published=True,
        )
        db.session.add_all([other_class, other_subject, other_exam])
        db.session.flush()
        other_config = self.configuration.__class__(
            academic_year_id=self.year_two.id,
            academic_year_level_id=other_level.id,
            academic_year_subject_id=other_subject.id,
            frequency="monthly",
            status="active",
        )
        db.session.add(other_config)
        db.session.flush()
        other_session = self.session_one.__class__(
            configuration=other_config,
            exam=other_exam,
            session_label="1st Monthly",
            maximum_score=60,
            sort_order=1,
            is_active=True,
        )
        db.session.add(other_session)
        db.session.commit()

        resolved = resolve_behavior_promotion_context(
            self.year_two.id,
            other_level.id,
            other_exam.id,
            other_subject.id,
        )
        self.assertEqual(resolved["status"], "VALID")
        self.assertEqual(resolved["session"].id, other_session.id)
        wrong_scope = resolve_behavior_promotion_context(
            self.year_one.id,
            self.year_level_one.id,
            other_exam.id,
            self.behavior_subject.id,
        )
        self.assertEqual(wrong_scope["status"], "INVALID")

    def test_dynamic_number_of_exam_sessions_uses_exact_ids(self):
        from app.behavior_promotion_adapter import resolve_behavior_promotion_context

        created = []
        for index in range(3, 9):
            exam = Exam(
                name=f"Monthly {index}",
                academic_year=self.year_one,
                academic_level=self.level_one,
                academic_class=self.class_one,
                is_active=True,
                is_published=True,
            )
            db.session.add(exam)
            db.session.flush()
            session = self.session_one.__class__(
                configuration=self.configuration,
                exam=exam,
                session_label=f"Monthly {index}",
                maximum_score=10 + index,
                sort_order=index,
                is_active=True,
            )
            db.session.add(session)
            created.append((exam, session))
        db.session.commit()

        for exam, session in created:
            context = resolve_behavior_promotion_context(
                self.year_one.id,
                self.year_level_one.id,
                exam.id,
                self.behavior_subject.id,
            )
            with self.subTest(exam=exam.name):
                self.assertEqual(context["status"], "VALID")
                self.assertEqual(context["session"].id, session.id)

    def test_saved_promotion_snapshot_keeps_original_behavior_evidence(self):
        self._behavior_grades()
        first = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=True,
        )
        db.session.commit()
        original = json.loads(first.evaluation_context_json)["results"][0]
        self._event("negative", 1, "Late")
        current = evaluate_student_promotion(
            self.enrollment,
            self._context(self.exam_one, [self.behavior_subject]),
            persist=False,
        )

        stored = db.session.get(type(first), first.id)
        stored_result = json.loads(stored.evaluation_context_json)["results"][0]
        current_result = json.loads(current.evaluation_context_json)["results"][0]
        self.assertEqual(original["score"], 25)
        self.assertEqual(stored_result["score"], 25)
        self.assertEqual(current_result["score"], 24)


if __name__ == "__main__":
    unittest.main()
