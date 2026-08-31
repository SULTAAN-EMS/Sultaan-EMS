import unittest

from flask_login import login_user

from app import create_app, db
from app.academic_hierarchy import year_subjects
from app.models import (
    AcademicLevel,
    AcademicYear,
    AcademicYearLevel,
    AcademicYearSubject,
    Subject,
    User,
)
from app.routes_admin import config_create_subject, config_update_subject
from app.services import scoped_legacy_subjects


class TestPhase2ABehaviorSubjects(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2a-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.admin = User(
            username="phase2a-admin",
            full_name="Phase 2A Admin",
            role="admin",
            is_active=True,
        )
        self.admin.set_password("test-password")
        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.legacy_level = AcademicLevel(name="Secondary Level", sort_order=1)
        db.session.add_all([self.admin, self.year, self.legacy_level])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year_id=self.year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary Level",
            sort_order=1,
        )
        db.session.add(self.year_level)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _call(self, route, method, path, payload, item_id=None):
        with self.app.test_request_context(path, method=method, json=payload):
            login_user(self.admin)
            response = route(item_id) if item_id is not None else route()
            return response[0] if isinstance(response, tuple) else response

    def test_existing_style_subject_creates_exam_bridge(self):
        response = self._call(
            config_create_subject,
            "POST",
            "/admin/config-center/api/subjects",
            {
                "name": "Mathematics",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.year_level.id,
                "subject_kind": "exam",
                "max_score": 100,
            },
        )
        self.assertTrue(response.get_json()["success"])
        item = AcademicYearSubject.query.filter_by(name="Mathematics").one()
        self.assertEqual(item.subject_kind, "exam")
        self.assertIsNotNone(item.legacy_subject_id)
        self.assertEqual(Subject.query.filter_by(name="Mathematics").count(), 1)
        self.assertEqual(len(scoped_legacy_subjects([item])), 1)

    def test_behavior_subject_has_no_legacy_bridge(self):
        response = self._call(
            config_create_subject,
            "POST",
            "/admin/config-center/api/subjects",
            {
                "name": "Dabeecad",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.year_level.id,
                "subject_kind": "behavior",
            },
        )
        self.assertTrue(response.get_json()["success"])
        item = AcademicYearSubject.query.filter_by(name="Dabeecad").one()
        self.assertEqual(item.subject_kind, "behavior")
        self.assertIsNone(item.legacy_subject_id)
        self.assertEqual(Subject.query.filter_by(name="Dabeecad").count(), 0)
        self.assertEqual(scoped_legacy_subjects([item]), [])
        self.assertEqual(year_subjects(self.year.id, self.year_level.id, subject_kind="exam"), [])
        self.assertEqual(
            [row.name for row in year_subjects(self.year.id, self.year_level.id)],
            ["Dabeecad"],
        )

    def test_invalid_subject_kind_is_rejected(self):
        response = self._call(
            config_create_subject,
            "POST",
            "/admin/config-center/api/subjects",
            {
                "name": "Invalid Kind",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.year_level.id,
                "subject_kind": "attendance",
            },
        )
        self.assertFalse(response.get_json()["success"])
        self.assertIsNone(AcademicYearSubject.query.filter_by(name="Invalid Kind").first())

    def test_behavior_to_exam_update_restores_bridge(self):
        self._call(
            config_create_subject,
            "POST",
            "/admin/config-center/api/subjects",
            {
                "name": "Dabeecad",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.year_level.id,
                "subject_kind": "behavior",
            },
        )
        item = AcademicYearSubject.query.filter_by(name="Dabeecad").one()
        response = self._call(
            config_update_subject,
            "PUT",
            f"/admin/config-center/api/subjects/{item.id}",
            {
                "subject_kind": "exam",
                "name": "Dabeecad",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.year_level.id,
            },
            item.id,
        )
        self.assertTrue(response.get_json()["success"])
        db.session.refresh(item)
        self.assertEqual(item.subject_kind, "exam")
        self.assertIsNotNone(item.legacy_subject_id)


if __name__ == "__main__":
    unittest.main()
