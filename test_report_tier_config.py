"""Regression coverage for Whole-Class report tier configuration."""

import json
import unittest
from pathlib import Path

from app import create_app, db
from app.models import Setting
from app.routes_advanced_results import get_report_tier_configs, save_report_tier_configs
from app.services import performance_tier_for


class TestReportTierConfig(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "report-tier-config-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_defaults_are_available_and_invalid_stored_values_are_safe(self):
        db.session.add(Setting(
            key="weak_tier_1_2_3",
            value=json.dumps({"min": "not-a-number", "max": 60, "bg_color": "javascript:bad"}),
        ))
        db.session.commit()

        configs = get_report_tier_configs(1, 2, 3)

        self.assertEqual(configs["weak"]["min"], 50.0)
        self.assertEqual(configs["weak"]["max"], 60.0)
        self.assertEqual(configs["weak"]["bg_color"], "#F5A400")
        self.assertEqual(configs["fail"]["min"], 0.0)
        self.assertEqual(configs["fail"]["max"], 49.99)

    def test_saved_values_are_scoped_and_round_trip(self):
        save_report_tier_configs(
            1, 2, 3,
            "55", "65", "#123456", "#ffffff",
            "0", "54.99", "#abcdef", "#111111",
        )

        scoped = get_report_tier_configs(1, 2, 3)
        other_scope = get_report_tier_configs(1, 2, 4)
        self.assertEqual(scoped["weak"]["min"], 55.0)
        self.assertEqual(scoped["weak"]["max"], 65.0)
        self.assertEqual(scoped["fail"]["bg_color"], "#abcdef")
        self.assertEqual(other_scope["weak"]["min"], 50.0)
        self.assertEqual(other_scope["fail"]["max"], 49.99)

    def test_invalid_values_are_rejected_before_database_write(self):
        with self.assertRaises(ValueError):
            save_report_tier_configs(
                1, 2, 3,
                "101", "110", "#123456", "#ffffff",
                "0", "49.99", "#dc2626", "#ffffff",
            )
        with self.assertRaises(ValueError):
            save_report_tier_configs(
                1, 2, 3,
                "50", "59.99", "#12345", "#ffffff",
                "0", "49.99", "#dc2626", "#ffffff",
            )
        self.assertIsNone(db.session.get(Setting, "weak_tier_1_2_3"))
        self.assertIsNone(db.session.get(Setting, "fail_tier_1_2_3"))

    def test_fail_tier_has_precedence_when_ranges_overlap(self):
        result = performance_tier_for(
            50,
            {"min": 50, "max": 60},
            {"min": 0, "max": 50},
        )
        self.assertTrue(result["is_fail"])
        self.assertFalse(result["is_weak"])

    def test_natural_score_banner_is_removed_from_grade_management_template(self):
        template = Path("app/templates/admin/grade_management.html").read_text(encoding="utf-8")
        self.assertNotIn("Natural score ranges:", template)
        self.assertIn("Whole-Class Report", template)
        self.assertIn("name=\"weak_min\"", template)
        self.assertIn("name=\"fail_min\"", template)


if __name__ == "__main__":
    unittest.main()
