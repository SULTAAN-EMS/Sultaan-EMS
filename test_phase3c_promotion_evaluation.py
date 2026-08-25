import json
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
    Result,
    Student,
    StudentEnrollment,
    StudentEnrollmentMovement,
    Subject,
    User,
)
from app.promotion_service import (
    PromotionValidationError,
    evaluate_promotion_scope,
    evaluate_student_promotion,
    set_promotion_rules_enabled,
    upsert_promotion_rule,
)


class TestPhase3CPromotionEvaluation(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-3c-test"
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
        self.year_a = AcademicYear(name="2026-2027", is_current=True)
        self.year_b = AcademicYear(name="2027-2028", is_current=False)
        db.session.add_all([
            self.legacy_level,
            self.legacy_class,
            self.subject_math,
            self.subject_english,
            self.year_a,
            self.year_b,
        ])
        db.session.flush()
        self.level_a = AcademicYearLevel(
            academic_year_id=self.year_a.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
        )
        self.level_b = AcademicYearLevel(
            academic_year_id=self.year_b.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
        )
        db.session.add_all([self.level_a, self.level_b])
        db.session.flush()
        self.class_a = AcademicYearClass(
            academic_year_level_id=self.level_a.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
        )
        self.class_b = AcademicYearClass(
            academic_year_level_id=self.level_b.id,
            legacy_class_id=self.legacy_class.id,
            name="Form Four",
        )
        self.math_a = AcademicYearSubject(
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            legacy_subject_id=self.subject_math.id,
            name="Mathematics",
            max_score=100,
        )
        self.english_a = AcademicYearSubject(
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            legacy_subject_id=self.subject_english.id,
            name="English",
            max_score=100,
        )
        self.math_b = AcademicYearSubject(
            academic_year_id=self.year_b.id,
            academic_year_level_id=self.level_b.id,
            legacy_subject_id=self.subject_math.id,
            name="Mathematics",
            max_score=100,
        )
        db.session.add_all([self.class_a, self.class_b, self.math_a, self.english_a, self.math_b])
        db.session.flush()
        self.exam_a = Exam(name="Midterm A", academic_year_id=self.year_a.id, academic_level_id=self.legacy_level.id)
        self.exam_a_other = Exam(name="Final A", academic_year_id=self.year_a.id, academic_level_id=self.legacy_level.id)
        self.exam_b = Exam(name="Midterm B", academic_year_id=self.year_b.id, academic_level_id=self.legacy_level.id)
        self.student = Student(student_code="P3C001", full_name="Phase Three C Student", academic_year_id=self.year_a.id)
        self.incomplete_student = Student(student_code="P3C002", full_name="Incomplete Student", academic_year_id=self.year_a.id)
        db.session.add_all([self.exam_a, self.exam_a_other, self.exam_b, self.student, self.incomplete_student])
        db.session.flush()
        self.enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            academic_year_class_id=self.class_a.id,
        )
        self.incomplete_enrollment = StudentEnrollment(
            student_id=self.incomplete_student.id,
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            academic_year_class_id=self.class_a.id,
        )
        db.session.add_all([self.enrollment, self.incomplete_enrollment])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _result(self, student, exam, subject, score):
        row = Result(student_id=student.id, exam_id=exam.id, subject_id=subject.id, score=score)
        db.session.add(row)
        db.session.commit()
        return row

    def _context(self, exam="default", level=None, subjects=None):
        selected_exam = self.exam_a if exam == "default" else exam
        return {
            "academic_year_id": self.year_a.id,
            "academic_year_level_id": (level or self.level_a).id,
            "exam_id": selected_exam.id if selected_exam else None,
            "subject_ids": [item.id for item in subjects] if subjects is not None else None,
        }

    def test_explicit_exam_is_required_and_latest_is_never_inferred(self):
        with self.assertRaises(PromotionValidationError):
            evaluate_student_promotion(self.enrollment, self._context(exam=None, subjects=[self.math_a]))
        self._result(self.student, self.exam_a, self.subject_math, 80)
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(exam=self.exam_a, subjects=[self.math_a]),
            persist=False,
        )
        self.assertEqual(snapshot.exam_id, self.exam_a.id)
        self.assertEqual(json.loads(snapshot.evaluation_context_json)["exam_id"], self.exam_a.id)

    def test_valid_result_uses_exact_year_level_and_50_percent_baseline(self):
        self._result(self.student, self.exam_a, self.subject_math, 50)
        set_promotion_rules_enabled(False)
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(subjects=[self.math_a]),
            persist=False,
        )
        self.assertEqual(snapshot.evaluation_status, "EVALUATED")
        self.assertEqual(snapshot.overall_percentage, 50)
        self.assertEqual(snapshot.base_outcome, "PASS")
        self.assertEqual(snapshot.final_outcome, "PASS")
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_below_50_fails_and_mg_like_missing_data_is_not_pass(self):
        self._result(self.student, self.exam_a, self.subject_math, 49.99)
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(subjects=[self.math_a]),
            persist=False,
        )
        self.assertAlmostEqual(float(snapshot.overall_percentage), 49.99, places=2)
        self.assertEqual(snapshot.final_outcome, "FAIL")
        incomplete = evaluate_student_promotion(
            self.incomplete_enrollment,
            self._context(subjects=[self.math_a]),
            persist=False,
        )
        self.assertEqual(incomplete.evaluation_status, "INCOMPLETE")
        self.assertIsNone(incomplete.final_outcome)

    def test_rules_off_ignores_critical_failure(self):
        self._result(self.student, self.exam_a, self.subject_math, 40)
        self._result(self.student, self.exam_a, self.subject_english, 100)
        set_promotion_rules_enabled(False)
        upsert_promotion_rule(self.year_a.id, self.level_a.id, exam_id=self.exam_a.id, critical_subject_ids=[self.math_a.id])
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(subjects=[self.math_a, self.english_a]),
            persist=False,
        )
        self.assertEqual(snapshot.final_outcome, "PASS")
        self.assertIsNone(snapshot.promotion_rule_id)

    def test_rules_on_failed_critical_subject_overrides_pass(self):
        self._result(self.student, self.exam_a, self.subject_math, 40)
        self._result(self.student, self.exam_a, self.subject_english, 100)
        set_promotion_rules_enabled(True)
        rule = upsert_promotion_rule(self.year_a.id, self.level_a.id, exam_id=self.exam_a.id, critical_subject_ids=[self.math_a.id])
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(subjects=[self.math_a, self.english_a]),
            persist=False,
        )
        self.assertEqual(snapshot.promotion_rule_id, rule.id)
        self.assertEqual(snapshot.base_outcome, "PASS")
        self.assertEqual(snapshot.final_outcome, "FAIL")
        self.assertEqual(snapshot.override_reason, "FAILED_CRITICAL_SUBJECT")

    def test_missing_subject_set_is_incomplete_not_silent_pass(self):
        self._result(self.student, self.exam_a, self.subject_math, 90)
        snapshot = evaluate_student_promotion(
            self.enrollment,
            self._context(subjects=[self.math_a, self.english_a]),
            persist=False,
        )
        self.assertEqual(snapshot.evaluation_status, "INCOMPLETE")
        self.assertIsNone(snapshot.base_outcome)
        self.assertIn("MISSING_RESULTS", snapshot.override_reason)

    def test_wrong_year_and_level_subject_cannot_be_used(self):
        with self.assertRaises(PromotionValidationError):
            evaluate_student_promotion(
                self.enrollment,
                self._context(subjects=[self.math_b]),
                persist=False,
            )
        with self.assertRaises(PromotionValidationError):
            evaluate_student_promotion(
                self.enrollment,
                self._context(exam=self.exam_b, subjects=[self.math_a]),
                persist=False,
            )

    def test_preview_is_read_only_and_execution_persists_history(self):
        self._result(self.student, self.exam_a, self.subject_math, 75)
        before = PromotionEvaluation.query.count()
        preview = evaluate_promotion_scope(
            self.year_a.id,
            self.level_a.id,
            self.exam_a.id,
            academic_year_class_id=self.class_a.id,
            subject_ids=[self.math_a.id],
            persist=False,
        )
        self.assertEqual(PromotionEvaluation.query.count(), before)
        self.assertEqual(preview["counts"]["pass"], 1)
        executed = evaluate_promotion_scope(
            self.year_a.id,
            self.level_a.id,
            self.exam_a.id,
            academic_year_class_id=self.class_a.id,
            subject_ids=[self.math_a.id],
            persist=True,
        )
        db.session.commit()
        self.assertEqual(executed["counts"]["evaluated"], 1)
        self.assertEqual(executed["counts"]["outcomes_saved"], 0)
        self.assertEqual(PromotionEvaluation.query.count(), 2)
        self.assertEqual(StudentEnrollmentMovement.query.count(), 0)
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_rule_edit_and_re_evaluation_preserve_history(self):
        self._result(self.student, self.exam_a, self.subject_math, 40)
        self._result(self.student, self.exam_a, self.subject_english, 100)
        set_promotion_rules_enabled(True)
        upsert_promotion_rule(self.year_a.id, self.level_a.id, exam_id=self.exam_a.id, critical_subject_ids=[self.math_a.id], critical_subject_pass_threshold=50)
        first = evaluate_student_promotion(self.enrollment, self._context(subjects=[self.math_a, self.english_a]), persist=True)
        db.session.commit()
        first_rule_snapshot = first.promotion_rule_snapshot_json
        upsert_promotion_rule(self.year_a.id, self.level_a.id, exam_id=self.exam_a.id, critical_subject_ids=[], critical_subject_pass_threshold=30)
        second = evaluate_student_promotion(self.enrollment, self._context(subjects=[self.math_a, self.english_a]), persist=True)
        db.session.commit()
        self.assertEqual(PromotionEvaluation.query.count(), 2)
        self.assertEqual(db.session.get(PromotionEvaluation, first.id).promotion_rule_snapshot_json, first_rule_snapshot)
        self.assertEqual(second.final_outcome, "PASS")

    def test_evaluation_page_preview_is_read_only(self):
        self._result(self.student, self.exam_a, self.subject_math, 75)
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
        before = PromotionEvaluation.query.count()
        response = client.post(
            "/admin/promotion-rules/evaluate",
            data={
                "academic_year_id": self.year_a.id,
                "academic_year_level_id": self.level_a.id,
                "exam_id": self.exam_a.id,
                "academic_year_class_id": self.class_a.id,
                "subject_ids": [str(self.math_a.id)],
                "action": "preview",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Read-only preview", response.data)
        self.assertEqual(PromotionEvaluation.query.count(), before)

        response = client.post(
            "/admin/promotion-rules/evaluate",
            data={
                "academic_year_id": self.year_a.id,
                "academic_year_level_id": self.level_a.id,
                "exam_id": self.exam_a.id,
                "academic_year_class_id": self.class_a.id,
                "subject_ids": [str(self.math_a.id)],
                "action": "execute",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enrollment outcomes applied", response.data)
        self.assertIn(b"Evaluation saved successfully", response.data)
        self.assertEqual(self.enrollment.academic_outcome, "pending")


if __name__ == "__main__":
    unittest.main()
