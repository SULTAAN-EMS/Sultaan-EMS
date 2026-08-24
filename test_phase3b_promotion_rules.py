import json
import unittest
from decimal import Decimal

from sqlalchemy import inspect

from app import create_app, db
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    PromotionEvaluation,
    PromotionRule,
    Student,
    StudentEnrollment,
    User,
)
from app.promotion_service import (
    PromotionValidationError,
    evaluate_promotion,
    get_promotion_rule,
    promotion_rules_enabled,
    set_promotion_rules_enabled,
    upsert_promotion_rule,
    valid_critical_subjects,
)


class TestPhase3BPromotionRules(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-3b-test"
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

        self.year_a = AcademicYear(name="2026-2027", is_current=True)
        self.year_b = AcademicYear(name="2027-2028", is_current=False)
        db.session.add_all([self.year_a, self.year_b])
        db.session.flush()

        self.level_a = AcademicYearLevel(
            academic_year_id=self.year_a.id, name="Secondary", sort_order=1
        )
        self.level_b = AcademicYearLevel(
            academic_year_id=self.year_b.id, name="Secondary", sort_order=1
        )
        db.session.add_all([self.level_a, self.level_b])
        db.session.flush()

        self.class_a = AcademicYearClass(
            academic_year_level_id=self.level_a.id, name="Form Four", sort_order=1
        )
        self.class_b = AcademicYearClass(
            academic_year_level_id=self.level_b.id, name="Form Four", sort_order=1
        )
        self.subject_a = AcademicYearSubject(
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            name="Mathematics",
            max_score=100,
        )
        self.subject_b = AcademicYearSubject(
            academic_year_id=self.year_b.id,
            academic_year_level_id=self.level_b.id,
            name="Mathematics",
            max_score=100,
        )
        db.session.add_all([self.class_a, self.class_b, self.subject_a, self.subject_b])
        db.session.flush()

        self.student = Student(
            student_code="P3B001",
            full_name="Phase Three B Student",
            academic_year_id=self.year_a.id,
        )
        db.session.add(self.student)
        db.session.flush()
        self.enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.year_a.id,
            academic_year_level_id=self.level_a.id,
            academic_year_class_id=self.class_a.id,
        )
        db.session.add(self.enrollment)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_phase3b_tables_are_present(self):
        tables = set(inspect(db.engine).get_table_names())
        self.assertTrue({
            "promotion_rules",
            "promotion_rule_critical_subjects",
            "promotion_evaluations",
        }.issubset(tables))

    def test_global_off_uses_baseline_and_does_not_mutate_enrollment(self):
        set_promotion_rules_enabled(False)
        snapshot = evaluate_promotion(
            self.enrollment,
            {"overall_percentage": 49.999, "subject_results": []},
        )
        db.session.commit()
        self.assertEqual(snapshot.final_outcome, "FAIL")
        self.assertIsNone(snapshot.promotion_rule_id)
        self.assertEqual(self.enrollment.academic_outcome, "pending")

    def test_global_on_applies_scoped_rule_and_critical_subject_override(self):
        set_promotion_rules_enabled(True)
        rule = upsert_promotion_rule(
            self.year_a.id,
            self.level_a.id,
            critical_subject_ids=[self.subject_a.id],
        )
        snapshot = evaluate_promotion(
            self.enrollment,
            {
                "overall_percentage": 80,
                "subject_results": [
                    {"academic_year_subject_id": self.subject_a.id, "percentage": 49.999}
                ],
            },
        )
        db.session.commit()
        self.assertEqual(snapshot.base_outcome, "PASS")
        self.assertEqual(snapshot.final_outcome, "FAIL")
        self.assertEqual(snapshot.override_reason, "FAILED_CRITICAL_SUBJECT")
        self.assertEqual(snapshot.promotion_rule_id, rule.id)

    def test_year_level_and_subject_scope_isolated(self):
        self.assertEqual(valid_critical_subjects(self.year_a.id, self.level_a.id), [self.subject_a])
        with self.assertRaises(PromotionValidationError):
            upsert_promotion_rule(
                self.year_a.id,
                self.level_a.id,
                critical_subject_ids=[self.subject_b.id],
            )
        with self.assertRaises(PromotionValidationError):
            get_promotion_rule(self.year_a.id, self.level_b.id)

    def test_duplicate_critical_subjects_are_rejected(self):
        with self.assertRaises(PromotionValidationError):
            upsert_promotion_rule(
                self.year_a.id,
                self.level_a.id,
                critical_subject_ids=[self.subject_a.id, self.subject_a.id],
            )

    def test_explicit_exam_scope_is_validated(self):
        from app.models import Exam

        other_year_exam = Exam(name="Other Year Exam", academic_year_id=self.year_b.id)
        db.session.add(other_year_exam)
        db.session.commit()
        with self.assertRaises(PromotionValidationError):
            evaluate_promotion(
                self.enrollment,
                {"overall_percentage": 75, "exam_id": other_year_exam.id},
            )

    def test_mg_uf_does_not_automatically_fail_critical_subject(self):
        set_promotion_rules_enabled(True)
        upsert_promotion_rule(
            self.year_a.id,
            self.level_a.id,
            critical_subject_ids=[self.subject_a.id],
        )
        snapshot = evaluate_promotion(
            self.enrollment,
            {
                "overall_percentage": 75,
                "subject_results": [
                    {"academic_year_subject_id": self.subject_a.id, "is_uf": True}
                ],
            },
        )
        self.assertEqual(snapshot.final_outcome, "PASS")
        self.assertEqual(json.loads(snapshot.critical_subject_results_json)[0]["status"], "MG/UF")

    def test_rule_edits_do_not_rewrite_historical_snapshot(self):
        set_promotion_rules_enabled(True)
        upsert_promotion_rule(
            self.year_a.id,
            self.level_a.id,
            critical_subject_ids=[self.subject_a.id],
            critical_subject_pass_threshold=50,
        )
        first = evaluate_promotion(
            self.enrollment,
            {
                "overall_percentage": 75,
                "subject_results": [
                    {"academic_year_subject_id": self.subject_a.id, "percentage": 40}
                ],
            },
        )
        db.session.commit()
        original_snapshot = json.loads(first.promotion_rule_snapshot_json)

        upsert_promotion_rule(
            self.year_a.id,
            self.level_a.id,
            critical_subject_ids=[],
            critical_subject_pass_threshold=30,
        )
        db.session.commit()
        saved = db.session.get(PromotionEvaluation, first.id)
        self.assertEqual(json.loads(saved.promotion_rule_snapshot_json), original_snapshot)
        self.assertEqual(saved.final_outcome, "FAIL")

    def test_same_scope_is_editable_but_not_duplicated(self):
        first = upsert_promotion_rule(self.year_a.id, self.level_a.id)
        second = upsert_promotion_rule(
            self.year_a.id,
            self.level_a.id,
            overall_pass_threshold=Decimal("55"),
        )
        db.session.commit()
        self.assertEqual(first.id, second.id)
        self.assertEqual(PromotionRule.query.count(), 1)

    def test_rule_active_checkbox_save_persists_on_and_off(self):
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

        form = {
            "academic_year_id": str(self.year_a.id),
            "academic_year_level_id": str(self.level_a.id),
            "overall_pass_threshold": "50",
            "critical_subject_pass_threshold": "50",
            "critical_subject_ids": [str(self.subject_a.id)],
        }
        response = client.post(
            "/admin/promotion-rules/configure",
            data={**form, "is_active": "on"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        rule = get_promotion_rule(self.year_a.id, self.level_a.id)
        self.assertIsNotNone(rule)
        self.assertTrue(rule.is_active)

        response = client.post(
            "/admin/promotion-rules/configure",
            data=form,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertFalse(get_promotion_rule(self.year_a.id, self.level_a.id).is_active)

    def test_global_settings_save_persists_on_and_off(self):
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
            "/admin/promotion-rules/global-settings",
            data={"enabled": "on"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(promotion_rules_enabled())

        response = client.post(
            "/admin/promotion-rules/global-settings",
            data={},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(promotion_rules_enabled())


if __name__ == "__main__":
    unittest.main()
