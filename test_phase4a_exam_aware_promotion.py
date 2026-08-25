import unittest

from app import create_app, db
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    Exam,
    PromotionEvaluation,
    PromotionOutcomeApplication,
    Result,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
    Subject,
)
from app.promotion_service import (
    PromotionValidationError,
    apply_academic_outcome,
    evaluate_student_promotion,
    set_promotion_rules_enabled,
    transition_applied_outcome,
    upsert_promotion_rule,
)


class TestPhase4AExamAwarePromotion(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-4a-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.legacy_level = AcademicLevel(name="Secondary")
        self.legacy_class = AcademicClass(name="Form Four", academic_level=self.legacy_level)
        self.subject_math = Subject(name="Mathematics", academic_level=self.legacy_level, max_score=100)
        self.subject_english = Subject(name="English", academic_level=self.legacy_level, max_score=100)
        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.next_year = AcademicYear(name="2027-2028", is_current=False)
        db.session.add_all([
            self.legacy_level,
            self.legacy_class,
            self.subject_math,
            self.subject_english,
            self.year,
            self.next_year,
        ])
        db.session.flush()

        self.level = AcademicYearLevel(
            academic_year_id=self.year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
        )
        self.next_level = AcademicYearLevel(
            academic_year_id=self.next_year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
        )
        db.session.add_all([self.level, self.next_level])
        db.session.flush()
        self.year_class = AcademicYearClass(
            academic_year_level_id=self.level.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
        )
        self.next_class = AcademicYearClass(
            academic_year_level_id=self.next_level.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
        )
        self.math = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            legacy_subject_id=self.subject_math.id,
            name="Mathematics",
            max_score=100,
        )
        self.english = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            legacy_subject_id=self.subject_english.id,
            name="English",
            max_score=100,
        )
        self.midterm = Exam(
            name="Midterm",
            academic_year_id=self.year.id,
            is_final_evaluation=False,
        )
        self.final_exam = Exam(
            name="Final Exam",
            academic_year_id=self.year.id,
            is_final_evaluation=True,
        )
        # The name must never make an exam final by itself.
        self.named_final_nonfinal = Exam(
            name="Final Exam - Practice",
            academic_year_id=self.year.id,
            is_final_evaluation=False,
        )
        self.student = Student(
            student_code="P4A001",
            full_name="Phase Four A Student",
            academic_year_id=self.year.id,
        )
        db.session.add_all([
            self.year_class,
            self.next_class,
            self.math,
            self.english,
            self.midterm,
            self.final_exam,
            self.named_final_nonfinal,
            self.student,
        ])
        db.session.flush()
        self.enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            academic_year_class_id=self.year_class.id,
        )
        db.session.add(self.enrollment)
        db.session.commit()
        set_promotion_rules_enabled(True)

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _result(self, exam, subject, score):
        db.session.add(
            Result(
                student_id=self.student.id,
                exam_id=exam.id,
                subject_id=subject.legacy_subject_id,
                score=score,
            )
        )
        db.session.commit()

    def _context(self, exam):
        return {
            "academic_year_id": self.year.id,
            "academic_year_level_id": self.level.id,
            "exam_id": exam.id,
            "subject_ids": [self.math.id, self.english.id],
        }

    def test_rules_are_independent_per_exam_type(self):
        midterm_rule = upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[self.math.id],
        )
        final_rule = upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.final_exam.id,
            critical_subject_ids=[self.english.id],
        )
        self.assertNotEqual(midterm_rule.id, final_rule.id)
        self.assertEqual(midterm_rule.exam_id, self.midterm.id)
        self.assertEqual(final_rule.exam_id, self.final_exam.id)
        self.assertEqual(len(self.level.promotion_rules.all()), 2)

    def test_non_final_evaluation_saves_pass_fail_but_cannot_transition(self):
        upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[self.math.id],
        )
        self._result(self.midterm, self.math, 40)
        self._result(self.midterm, self.english, 100)
        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.midterm),
            persist=True,
        )
        db.session.commit()
        self.assertEqual(evaluation.evaluation_status, "EVALUATED")
        self.assertEqual(evaluation.final_outcome, "FAIL")
        self.assertEqual(self.enrollment.academic_outcome, "pending")
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)

        application = apply_academic_outcome(evaluation.id)
        db.session.commit()
        self.assertEqual(application.action, "outcome")
        self.assertIsNone(application.destination_enrollment_id)
        self.assertEqual(self.enrollment.academic_outcome, "failed")
        with self.assertRaisesRegex(PromotionValidationError, "not marked as a Final Evaluation"):
            transition_applied_outcome(
                evaluation.id,
                action="repeat",
                destination_academic_year_id=self.next_year.id,
                destination_academic_year_level_id=self.next_level.id,
                destination_academic_year_class_id=self.next_class.id,
            )
        self.assertEqual(StudentEnrollment.query.filter_by(student_id=self.student.id).count(), 1)

    def test_rule_for_midterm_does_not_leak_into_final_exam(self):
        upsert_promotion_rule(
            self.year.id,
            self.level.id,
            exam_id=self.midterm.id,
            critical_subject_ids=[self.math.id],
        )
        self._result(self.final_exam, self.math, 40)
        self._result(self.final_exam, self.english, 100)
        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.final_exam),
            persist=False,
        )
        self.assertIsNone(evaluation.promotion_rule_id)
        self.assertEqual(evaluation.final_outcome, "PASS")

    def test_non_final_pass_still_records_academic_outcome_when_rules_are_off(self):
        set_promotion_rules_enabled(False)
        self._result(self.midterm, self.math, 90)
        self._result(self.midterm, self.english, 90)
        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.midterm),
            persist=True,
        )
        db.session.commit()
        self.assertEqual(evaluation.final_outcome, "PASS")
        self.assertIsNone(evaluation.promotion_rule_id)
        application = apply_academic_outcome(evaluation.id)
        db.session.commit()
        self.assertEqual(application.action, "outcome")
        self.assertEqual(self.enrollment.academic_outcome, "passed")
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)

    def test_exam_from_another_academic_year_is_rejected(self):
        other_year_exam = Exam(
            name="Other Year Final",
            academic_year_id=self.next_year.id,
            is_final_evaluation=True,
        )
        db.session.add(other_year_exam)
        db.session.commit()
        with self.assertRaisesRegex(PromotionValidationError, "does not belong to the selected Academic Year"):
            evaluate_student_promotion(
                self.enrollment,
                self._context(other_year_exam),
                persist=False,
            )

    def test_only_explicit_final_flag_allows_transition(self):
        self._result(self.named_final_nonfinal, self.math, 90)
        self._result(self.named_final_nonfinal, self.english, 90)
        evaluation = evaluate_student_promotion(
            self.enrollment,
            self._context(self.named_final_nonfinal),
            persist=True,
        )
        db.session.commit()
        apply_academic_outcome(evaluation.id)
        db.session.commit()
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(
                evaluation.id,
                action="promotion",
                destination_academic_year_id=self.next_year.id,
                destination_academic_year_level_id=self.next_level.id,
                destination_academic_year_class_id=self.next_class.id,
            )


if __name__ == "__main__":
    unittest.main()
