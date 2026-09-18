"""Regression coverage for request-local settings and label caching."""

import unittest

from sqlalchemy import event

from app import create_app, db
from app.routes_advanced_results import ensure_results_label_seeds
from app.routes_invigilator import incident_setting_value
from app.services import get_label, get_settings
from app.teacher_services import get_teacher_settings
from app.models import IncidentReportSettings, Setting


class TestRequestPerformanceCache(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "request-cache"
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

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_repeated_ui_lookups_use_one_settings_and_label_query_per_request(self):
        statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with self.app.test_request_context("/admin/advanced-results/new-dashboard"):
                first_settings = get_settings()
                second_settings = get_settings()
                self.assertEqual(first_settings, second_settings)
                self.assertEqual(get_label("dashboard.title"), "dashboard.title")
                self.assertEqual(get_label("dashboard.title"), "dashboard.title")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        settings_queries = [sql for sql in statements if "FROM SETTINGS" in sql]
        label_queries = [sql for sql in statements if "FROM LABEL_TRANSLATIONS" in sql]
        self.assertEqual(len(settings_queries), 1)
        self.assertEqual(len(label_queries), 2)

    def test_results_label_seed_check_uses_one_bulk_lookup(self):
        statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with self.app.test_request_context("/admin/advanced-results/new-dashboard"):
                ensure_results_label_seeds()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        label_queries = [sql for sql in statements if "FROM LABEL_TRANSLATIONS" in sql]
        self.assertEqual(len(label_queries), 1)

    def test_teacher_settings_reuse_request_settings_cache(self):
        db.session.add(Setting(key="teacher_theme", value="dark"))
        db.session.commit()
        statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with self.app.test_request_context("/teacher/dashboard"):
                settings = get_teacher_settings()
                self.assertEqual(settings["teacher_theme"], "dark")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        settings_queries = [sql for sql in statements if "FROM SETTINGS" in sql]
        self.assertEqual(len(settings_queries), 1)

    def test_invigilator_setting_lookups_share_one_request_query(self):
        db.session.add_all([
            IncidentReportSettings(setting_key="invigilator_session_timeout", setting_value="30"),
            IncidentReportSettings(setting_key="invigilator_idle_timeout", setting_value="15"),
        ])
        db.session.commit()
        statements = []

        def capture_sql(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.upper())

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with self.app.test_request_context("/invigilator/login"):
                self.assertEqual(incident_setting_value("invigilator_session_timeout"), "30")
                self.assertEqual(incident_setting_value("invigilator_idle_timeout"), "15")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        incident_queries = [sql for sql in statements if "FROM INCIDENT_REPORT_SETTINGS" in sql]
        self.assertEqual(len(incident_queries), 1)


if __name__ == "__main__":
    unittest.main()
