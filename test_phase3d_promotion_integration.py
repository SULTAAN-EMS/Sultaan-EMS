import unittest

from app import create_app, db
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Exam,
    PromotionEvaluation,
    PromotionOutcomeApplication,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
)
from app.promotion_service import (
    PromotionValidationError,
    apply_academic_outcome,
    execute_evaluation_transition_plan,
    is_final_academic_year_level,
    plan_evaluation_transition,
    transition_applied_outcome,
)


class TestPhase3DPromotionIntegration(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-3d-test"
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
        self.level = AcademicLevel(name="Secondary", sort_order=1)
        self.final_level = AcademicLevel(name="Graduation", sort_order=2)
        self.legacy_class = AcademicClass(name="Form Four", academic_level=self.level)
        self.final_legacy_class = AcademicClass(name="Final", academic_level=self.final_level)
        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.next_year = AcademicYear(name="2027-2028", is_current=False)
        db.session.add_all([self.level, self.final_level, self.legacy_class, self.final_legacy_class, self.year, self.next_year])
        db.session.flush()
        self.year_level = AcademicYearLevel(academic_year_id=self.year.id, legacy_level_id=self.level.id, name="Secondary", sort_order=1)
        self.year_final_level = AcademicYearLevel(academic_year_id=self.year.id, legacy_level_id=self.final_level.id, name="Graduation", sort_order=2)
        self.next_level = AcademicYearLevel(academic_year_id=self.next_year.id, legacy_level_id=self.level.id, name="Secondary", sort_order=1)
        db.session.add_all([self.year_level, self.year_final_level, self.next_level])
        db.session.flush()
        self.source_class = AcademicYearClass(academic_year_level_id=self.year_level.id, legacy_class_id=self.legacy_class.id, name="Form Four")
        self.final_class = AcademicYearClass(academic_year_level_id=self.year_final_level.id, legacy_class_id=self.final_legacy_class.id, name="Final")
        self.next_class = AcademicYearClass(academic_year_level_id=self.next_level.id, legacy_class_id=self.legacy_class.id, name="Form Four")
        self.exam = Exam(name="Final Exam", academic_year_id=self.year.id, is_final_evaluation=True)
        self.other_exam = Exam(name="Other Exam", academic_year_id=self.year.id)
        db.session.add_all([self.source_class, self.final_class, self.next_class, self.exam, self.other_exam])
        db.session.flush()
        self.student = self._student("P3D001")
        self.source = self._enrollment(self.student, self.year, self.year_level, self.source_class)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _student(self, code):
        student = Student(student_code=code, full_name=f"Student {code}", academic_year_id=self.year.id)
        db.session.add(student)
        db.session.flush()
        return student

    def _enrollment(self, student, year, level, academic_class):
        enrollment = StudentEnrollment(
            student_id=student.id,
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            academic_year_class_id=academic_class.id,
        )
        db.session.add(enrollment)
        db.session.flush()
        return enrollment

    def _evaluation(self, enrollment, outcome="PASS", exam=None, status="EVALUATED"):
        evaluation = PromotionEvaluation(
            student_id=enrollment.student_id,
            student_enrollment_id=enrollment.id,
            academic_year_id=enrollment.academic_year_id,
            academic_year_level_id=enrollment.academic_year_level_id,
            exam_id=(exam or self.exam).id if exam is not False else None,
            promotion_rule_snapshot_json="{}",
            evaluation_context_json="{}",
            critical_subject_results_json="[]",
            evaluation_status=status,
            base_outcome=outcome if status == "EVALUATED" else None,
            final_outcome=outcome if status == "EVALUATED" else None,
        )
        db.session.add(evaluation)
        db.session.flush()
        return evaluation

    def test_01_valid_evaluation_applies_outcome_only(self):
        evaluation = self._evaluation(self.source)
        application = apply_academic_outcome(evaluation.id)
        self.assertEqual(application.applied_outcome, "passed")
        self.assertEqual(self.source.academic_outcome, "passed")
        self.assertEqual(self.source.status, "active")
        self.assertIsNone(application.destination_enrollment_id)

    def test_02_incomplete_evaluation_cannot_authorize(self):
        evaluation = self._evaluation(self.source, status="INCOMPLETE")
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)
        self.assertEqual(self.source.academic_outcome, "pending")

    def test_03_invalid_evaluation_cannot_authorize(self):
        evaluation = self._evaluation(self.source, status="INVALID")
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)

    def test_04_not_evaluated_cannot_authorize(self):
        evaluation = self._evaluation(self.source, status="NOT_EVALUATED")
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)

    def test_05_legacy_snapshot_without_exam_cannot_authorize(self):
        evaluation = self._evaluation(self.source, exam=False)
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)

    def test_06_wrong_enrollment_scope_cannot_authorize(self):
        evaluation = self._evaluation(self.source)
        evaluation.student_enrollment_id = self.source.id + 999
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)

    def test_07_duplicate_apply_is_rejected(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            apply_academic_outcome(evaluation.id)
        self.assertEqual(PromotionOutcomeApplication.query.count(), 1)

    def test_08_transition_requires_explicit_apply(self):
        evaluation = self._evaluation(self.source)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)

    def test_09_promotion_creates_destination_and_movement(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        source, destination, application = transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(source.academic_outcome, "promoted")
        self.assertEqual(destination.previous_enrollment_id, source.id)
        self.assertEqual(application.application_status, "TRANSITIONED")
        self.assertEqual(StudentEnrollmentMovement.query.filter_by(movement_type="promotion").count(), 1)

    def test_10_repeat_creates_destination_for_fail(self):
        evaluation = self._evaluation(self.source, outcome="FAIL")
        apply_academic_outcome(evaluation.id)
        source, destination, application = transition_applied_outcome(evaluation.id, action="repeat", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(source.academic_outcome, "repeated")
        self.assertEqual(destination.enrollment_source, "repeat")
        self.assertEqual(application.action, "repeat")

    def test_11_fail_cannot_promote(self):
        evaluation = self._evaluation(self.source, outcome="FAIL")
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)

    def test_12_pass_cannot_repeat(self):
        evaluation = self._evaluation(self.source, outcome="PASS")
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="repeat", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)

    def test_13_promotion_requires_new_year(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.year.id, destination_academic_year_level_id=self.year_level.id, destination_academic_year_class_id=self.source_class.id)

    def test_14_final_level_pass_graduates_without_destination(self):
        final_student = self._student("P3D-FINAL")
        final_source = self._enrollment(final_student, self.year, self.year_final_level, self.final_class)
        evaluation = self._evaluation(final_source)
        evaluation.academic_year_level_id = self.year_final_level.id
        apply_academic_outcome(evaluation.id)
        source, destination, application = transition_applied_outcome(evaluation.id, action="graduation")
        self.assertIsNone(destination)
        self.assertEqual(source.academic_outcome, "graduated")
        self.assertEqual(application.application_status, "GRADUATED")

    def test_15_non_final_level_cannot_graduate(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="graduation")

    def test_16_fail_cannot_graduate(self):
        final_student = self._student("P3D-FINAL-FAIL")
        final_source = self._enrollment(final_student, self.year, self.year_final_level, self.final_class)
        evaluation = self._evaluation(final_source, outcome="FAIL")
        evaluation.academic_year_level_id = self.year_final_level.id
        apply_academic_outcome(evaluation.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="graduation")

    def test_17_final_level_detection_is_year_aware(self):
        self.assertFalse(is_final_academic_year_level(self.year_level.id))
        self.assertTrue(is_final_academic_year_level(self.year_final_level.id))
        self.assertTrue(is_final_academic_year_level(self.next_level.id))

    def test_18_bulk_plan_requires_exact_evaluation(self):
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(plan["counts"]["eligible"], 0)
        self.assertEqual(plan["items"][0]["classification"], "NOT_EVALUATED")

    def test_19_bulk_plan_is_year_and_exam_isolated(self):
        evaluation = self._evaluation(self.source, exam=self.other_exam)
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(plan["counts"]["eligible"], 0)
        self.assertNotEqual(plan["items"][0]["evaluation"], evaluation)

    def test_20_bulk_plan_only_reads_exact_source_class(self):
        other_student = self._student("P3D-OTHER")
        self._enrollment(other_student, self.year, self.year_level, self.final_class)
        evaluation = self._evaluation(self.source)
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(plan["items"][0]["evaluation"], evaluation)

    def test_21_bulk_execute_applies_and_transitions_atomically(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        created = execute_evaluation_transition_plan(plan)
        self.assertEqual(len(created), 1)
        self.assertEqual(PromotionOutcomeApplication.query.count(), 1)
        self.assertEqual(StudentEnrollment.query.filter_by(academic_year_id=self.next_year.id).count(), 1)

    def test_22_bulk_execute_does_not_create_for_invalid_rows(self):
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(execute_evaluation_transition_plan(plan), [])
        self.assertEqual(PromotionOutcomeApplication.query.count(), 0)

    def test_23_snapshot_remains_immutable_after_promotion(self):
        evaluation = self._evaluation(self.source)
        original_outcome = evaluation.final_outcome
        apply_academic_outcome(evaluation.id)
        transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(evaluation.final_outcome, original_outcome)
        self.assertEqual(evaluation.student_enrollment_id, self.source.id)

    def test_24_existing_destination_is_rejected_before_execution(self):
        self._enrollment(self.student, self.next_year, self.next_level, self.next_class)
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        plan = plan_evaluation_transition(self.year.id, self.year_level.id, self.source_class.id, self.exam.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        self.assertEqual(plan["counts"]["eligible"], 0)
        self.assertIn("already has", plan["items"][0]["reason"])

    def test_25_transition_application_is_not_reusable(self):
        evaluation = self._evaluation(self.source)
        apply_academic_outcome(evaluation.id)
        transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)
        with self.assertRaises(PromotionValidationError):
            transition_applied_outcome(evaluation.id, action="promotion", destination_academic_year_id=self.next_year.id, destination_academic_year_level_id=self.next_level.id, destination_academic_year_class_id=self.next_class.id)


if __name__ == "__main__":
    unittest.main()
