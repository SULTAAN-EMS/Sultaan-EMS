"""Regression coverage for the production startup database boundary."""

import app as app_module
import unittest
from unittest.mock import patch
from app.startup_migrations import apply_teacher_result_report_migrations


class ProductionTestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_INIT_DB = False


class ProductionStartupTests(unittest.TestCase):
    def test_teacher_report_migrations_run_only_for_postgres(self):
        with patch("migrations.phase_27_teacher_missing_mark_reports.upgrade") as create_reports, patch(
            "migrations.phase_28_teacher_missing_mark_unreadable_reason.upgrade"
        ) as extend_reasons:
            self.assertFalse(apply_teacher_result_report_migrations("sqlite:///local.db"))
            self.assertTrue(apply_teacher_result_report_migrations("postgresql+psycopg://db"))

        create_reports.assert_called_once_with()
        extend_reasons.assert_called_once_with()

    def test_production_app_creation_does_not_bootstrap_database(self):
        def unexpected_database_bootstrap():
            raise AssertionError("production startup must not call db.create_all()")

        with patch.object(app_module.db, "create_all", unexpected_database_bootstrap):
            application = app_module.create_app(ProductionTestConfig)

        self.assertFalse(application.config["AUTO_INIT_DB"])
        self.assertIsNotNone(application)


if __name__ == "__main__":
    unittest.main()
