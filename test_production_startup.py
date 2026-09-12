"""Regression coverage for the production startup database boundary."""

import app as app_module
import unittest
from unittest.mock import patch


class ProductionTestConfig:
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_INIT_DB = False


class ProductionStartupTests(unittest.TestCase):
    def test_production_app_creation_does_not_bootstrap_database(self):
        def unexpected_database_bootstrap():
            raise AssertionError("production startup must not call db.create_all()")

        with patch.object(app_module.db, "create_all", unexpected_database_bootstrap):
            application = app_module.create_app(ProductionTestConfig)

        self.assertFalse(application.config["AUTO_INIT_DB"])
        self.assertIsNotNone(application)


if __name__ == "__main__":
    unittest.main()
