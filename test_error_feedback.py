import unittest

from app import create_app
from app import db
from flask import flash, render_template
from werkzeug.routing import BuildError


class TestErrorFeedback(unittest.TestCase):
    class TestConfig:
        TESTING = True
        PROPAGATE_EXCEPTIONS = False
        SECRET_KEY = "error-feedback-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        with self.app.app_context():
            db.create_all()

        def missing_behavior_route():
            raise BuildError("behavior.bulk_events", {"config_id": 7}, "GET")

        def unexpected_json_error():
            raise RuntimeError("private credential fragment")

        def flash_error_notice():
            flash("Ficilka waa la diiday sababtoo ah xogta lama waafaqin.", "danger")
            return render_template("base.html")

        def flash_success_notice():
            flash("Isbeddelka si sax ah ayaa loo kaydiyey.", "success")
            return render_template("base.html")

        self.app.add_url_rule(
            "/_test/missing-behavior-route",
            view_func=missing_behavior_route,
        )
        self.app.add_url_rule(
            "/_test/json-error",
            view_func=unexpected_json_error,
        )
        self.app.add_url_rule(
            "/_test/flash-error",
            view_func=flash_error_notice,
        )
        self.app.add_url_rule(
            "/_test/flash-success",
            view_func=flash_success_notice,
        )
        self.client = self.app.test_client()

    def test_route_mismatch_uses_helpful_somali_error_page(self):
        response = self.client.get("/_test/missing-behavior-route")

        self.assertEqual(response.status_code, 500)
        self.assertIn("Bogga lama diyaarin".encode(), response.data)
        self.assertIn("Dib u bilow server-ka mashruuca".encode(), response.data)
        self.assertIn("Lambarka tixraaca".encode(), response.data)
        self.assertEqual(response.headers.get("X-Error-Reference"), response.text.split("Lambarka tixraaca: <code>", 1)[1].split("</code>", 1)[0])
        self.assertNotIn("Could not build url", response.text)

    def test_json_error_has_safe_message_and_reference_without_exception_details(self):
        response = self.client.get(
            "/_test/json-error",
            headers={"Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json["success"])
        self.assertIn("message", response.json)
        self.assertEqual(response.json["reference"], response.headers.get("X-Error-Reference"))
        self.assertNotIn("private credential fragment", response.get_data(as_text=True))
        self.assertNotIn("details", response.json)

    def test_flash_failure_uses_colored_notice_with_clear_reason(self):
        response = self.client.get("/_test/flash-error")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="toast app-notice app-notice--error"', response.text)
        self.assertIn("Hawlgalku wuu fashilmay", response.text)
        self.assertIn("Ficilka waa la diiday sababtoo ah xogta lama waafaqin.", response.text)
        self.assertIn('data-auto-dismiss="8500"', response.text)

    def test_flash_success_uses_success_notice(self):
        response = self.client.get("/_test/flash-success")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="toast app-notice app-notice--success"', response.text)
        self.assertIn("Waa lagu guuleystay", response.text)
        self.assertIn("Isbeddelka si sax ah ayaa loo kaydiyey.", response.text)


if __name__ == "__main__":
    unittest.main()
