import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.models import AcademicYear
from app.teacher_analytics import filter_options


class TeacherPortalFilterOptionsTest(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "teacher-filter-options-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.year = AcademicYear(name="2026-2027", is_current=True)
        db.session.add(self.year)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_ignores_students_without_academic_year_when_building_year_filters(self):
        students = [
            SimpleNamespace(academic_year_id=self.year.id, section=None),
            SimpleNamespace(academic_year_id=None, section=None),
        ]
        assignments = ([], [], [], [], set(), set(), set(), set())

        with patch("app.teacher_analytics.teacher_assignments", return_value=assignments), patch(
            "app.teacher_analytics.scoped_students", side_effect=[students, students]
        ):
            options = filter_options(SimpleNamespace(id=1), {})

        self.assertEqual(options["academic_years"], [self.year])
