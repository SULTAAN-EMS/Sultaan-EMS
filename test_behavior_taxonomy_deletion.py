import unittest

from flask_login import login_user

from app import create_app, db
from app.models import (
    AcademicYear,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorActionChoice,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorSubCategory,
    User,
)
from app.routes_behavior import delete_action, delete_category, delete_subcategory


class TestBehaviorTaxonomyDeletion(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "behavior-taxonomy-deletion-test"
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

        self.admin = User(
            username="taxonomy-delete-admin",
            full_name="Taxonomy Delete Admin",
            role="admin",
            is_active=True,
        )
        self.admin.set_password("test-password")
        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.level = AcademicYearLevel(name="Form One", academic_year=self.year, sort_order=1)
        self.subject = AcademicYearSubject(
            name="Dabeecad",
            subject_kind="behavior",
            max_score=0,
            academic_year=self.year,
            academic_year_level=self.level,
            sort_order=1,
        )
        self.config = BehaviorConfiguration(
            academic_year=self.year,
            academic_year_level=self.level,
            behavior_subject=self.subject,
            frequency="monthly",
            status="active",
        )
        db.session.add_all([self.admin, self.year, self.level, self.subject, self.config])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _taxonomy(self):
        category = BehaviorCategory(
            configuration=self.config,
            name="Positive",
            polarity="positive",
            is_active=True,
        )
        subcategory = BehaviorSubCategory(
            category=category,
            name="Respect",
            is_active=True,
        )
        direct_action = BehaviorAction(
            category=category,
            name="Helpful",
            points=2,
            frequency="ad_hoc",
            is_active=True,
        )
        nested_action = BehaviorAction(
            category=category,
            subcategory=subcategory,
            name="Kind",
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        direct_choice = BehaviorActionChoice(
            action=direct_action,
            label="Always",
            points=2,
        )
        nested_choice = BehaviorActionChoice(
            action=nested_action,
            label="Usually",
            points=1,
        )
        db.session.add_all([
            category,
            subcategory,
            direct_action,
            nested_action,
            direct_choice,
            nested_choice,
        ])
        db.session.commit()
        return category, subcategory, direct_action, nested_action, direct_choice, nested_choice

    def _call_delete(self, route, item_id):
        with self.app.test_request_context(
            "/admin/behavior/taxonomy",
            method="POST",
            data={"config_id": str(self.config.id)},
        ):
            login_user(self.admin)
            return route(item_id)

    def test_action_delete_removes_action_and_choices(self):
        _, _, direct_action, _, _, _ = self._taxonomy()

        self._call_delete(delete_action, direct_action.id)

        self.assertIsNone(db.session.get(BehaviorAction, direct_action.id))
        self.assertEqual(
            BehaviorActionChoice.query.filter_by(behavior_action_id=direct_action.id).count(),
            0,
        )

    def test_subcategory_delete_removes_nested_actions_and_choices(self):
        category, subcategory, direct_action, nested_action, _, _ = self._taxonomy()

        self._call_delete(delete_subcategory, subcategory.id)

        self.assertIsNone(db.session.get(BehaviorSubCategory, subcategory.id))
        self.assertIsNone(db.session.get(BehaviorAction, nested_action.id))
        self.assertEqual(
            BehaviorActionChoice.query.filter_by(behavior_action_id=nested_action.id).count(),
            0,
        )
        self.assertIsNotNone(db.session.get(BehaviorCategory, category.id))
        self.assertIsNotNone(db.session.get(BehaviorAction, direct_action.id))

    def test_category_delete_removes_direct_and_nested_taxonomy(self):
        category, subcategory, direct_action, nested_action, direct_choice, nested_choice = self._taxonomy()

        self._call_delete(delete_category, category.id)

        self.assertIsNone(db.session.get(BehaviorCategory, category.id))
        self.assertIsNone(db.session.get(BehaviorSubCategory, subcategory.id))
        self.assertIsNone(db.session.get(BehaviorAction, direct_action.id))
        self.assertIsNone(db.session.get(BehaviorAction, nested_action.id))
        self.assertIsNone(db.session.get(BehaviorActionChoice, direct_choice.id))
        self.assertIsNone(db.session.get(BehaviorActionChoice, nested_choice.id))


if __name__ == "__main__":
    unittest.main()
