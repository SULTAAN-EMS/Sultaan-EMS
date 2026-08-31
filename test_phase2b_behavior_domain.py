import unittest
from decimal import Decimal

from app import create_app, db
from app.academic_hierarchy import year_subjects
from app.behavior_service import (
    BehaviorValidationError,
    allocation_total,
    calculate_session_score,
    record_event,
    validate_configuration_ready,
    validate_behavior_scope,
    void_event,
)
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    BehaviorSession,
    ExamType,
    Student,
    StudentEnrollment,
    User,
)
from app.routes_behavior import _scope_payload, _selected_config
from app.services import scoped_legacy_subjects


class TestPhase2BBehaviorDomain(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "phase-2b-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.admin = User(username="behavior-admin", full_name="Behavior Admin", role="admin", is_active=True)
        self.admin.set_password("test-password")
        self.year_one = AcademicYear(name="2026-2027", is_current=True)
        self.year_two = AcademicYear(name="2027-2028", is_current=False)
        self.level_one = AcademicYearLevel(name="Form One", sort_order=1)
        self.level_two = AcademicYearLevel(name="Form One", sort_order=1)
        self.subject_one = AcademicYearSubject(name="Dabeecad", subject_kind="behavior", max_score=0, sort_order=1)
        self.subject_two = AcademicYearSubject(name="Dabeecad", subject_kind="behavior", max_score=0, sort_order=1)
        db.session.add_all([self.admin, self.year_one, self.year_two])
        db.session.flush()
        self.level_one.academic_year_id = self.year_one.id
        self.level_two.academic_year_id = self.year_two.id
        db.session.add_all([self.level_one, self.level_two])
        db.session.flush()
        self.subject_one.academic_year_id = self.year_one.id
        self.subject_one.academic_year_level_id = self.level_one.id
        self.subject_two.academic_year_id = self.year_two.id
        self.subject_two.academic_year_level_id = self.level_two.id
        self.class_one = AcademicYearClass(name="1A", academic_year_level_id=self.level_one.id)
        self.student = Student(student_code="BHV001", full_name="Behavior Student", is_active=True)
        db.session.add_all([self.subject_one, self.subject_two, self.class_one, self.student])
        db.session.flush()
        self.enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_class_id=self.class_one.id,
            status="active",
            academic_outcome="pending",
            enrollment_source="manual",
        )
        db.session.add(self.enrollment)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _config(self, subject=None):
        config = BehaviorConfiguration(
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_subject_id=(subject or self.subject_one).id,
            frequency="monthly",
            status="draft",
        )
        db.session.add(config)
        db.session.flush()
        return config

    def test_scope_is_year_aware_and_behavior_is_excluded_from_exam_subjects(self):
        with self.assertRaises(BehaviorValidationError):
            validate_behavior_scope(self.year_one.id, self.level_two.id, self.subject_one.id)
        self.assertEqual(year_subjects(self.year_one.id, self.level_one.id, subject_kind="exam"), [])
        self.assertEqual(scoped_legacy_subjects([self.subject_one]), [])

    def test_route_scope_does_not_leak_subjects_or_configurations_between_years(self):
        config_two = BehaviorConfiguration(
            academic_year_id=self.year_two.id,
            academic_year_level_id=self.level_two.id,
            academic_year_subject_id=self.subject_two.id,
            frequency="monthly",
            status="draft",
        )
        db.session.add(config_two)
        db.session.flush()
        self.assertEqual(_scope_payload(self.year_one.id)["subjects"], [])
        self.assertIsNone(_selected_config(config_two.id, self.year_one.id))
        self.assertEqual(_selected_config(config_two.id).id, config_two.id)

    def test_any_number_of_sessions_requires_exactly_100_for_activation(self):
        config = self._config()
        maxima = [20, 20, 20, 20, 10, 10]
        for index, maximum in enumerate(maxima, start=1):
            exam = ExamType(academic_year_id=self.year_one.id, name=f"Behavior Exam {index}", sort_order=index)
            db.session.add(exam)
            db.session.flush()
            db.session.add(BehaviorSession(
                behavior_configuration_id=config.id,
                exam_type_id=exam.id,
                session_label=f"Session {index}",
                maximum_score=maximum,
                sort_order=index,
                is_active=True,
            ))
        db.session.flush()
        self.assertEqual(allocation_total(config), Decimal("100.000"))
        category = BehaviorCategory(
            configuration=config,
            name="Positive activation",
            polarity="positive",
            is_active=True,
        )
        action = BehaviorAction(
            category=category,
            name="Helpful action",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([category, action])
        db.session.flush()
        self.assertIs(validate_configuration_ready(config), config)
        config.sessions[-1].maximum_score = 9
        with self.assertRaises(BehaviorValidationError):
            validate_configuration_ready(config)

    def test_base_positive_negative_and_clamped_score(self):
        config = self._config()
        exam = ExamType(academic_year_id=self.year_one.id, name="Behavior Exam", sort_order=1)
        session = BehaviorSession(
            configuration=config,
            exam_type=exam,
            session_label="Monthly",
            maximum_score=100,
            sort_order=1,
            is_active=True,
        )
        positive = BehaviorCategory(configuration=config, name="Positive", polarity="positive", is_active=True)
        negative = BehaviorCategory(configuration=config, name="Negative", polarity="negative", is_active=True)
        positive_action = BehaviorAction(category=positive, name="Excellent", level_number=1, points=70, frequency="ad_hoc", is_active=True)
        negative_action = BehaviorAction(category=negative, name="Late", level_number=1, points=10, frequency="ad_hoc", is_active=True)
        db.session.add_all([exam, session, positive, negative, positive_action, negative_action])
        db.session.flush()
        config.status = "active"
        db.session.commit()

        empty = calculate_session_score(config, session, self.enrollment)
        self.assertEqual(empty["base"], Decimal("50.000"))
        record_event(config, self.enrollment, session, positive, positive_action)
        record_event(config, self.enrollment, session, negative, negative_action)
        db.session.commit()
        score = calculate_session_score(config, session, self.enrollment)
        self.assertEqual(score["positive_raw_points"], Decimal("70.000"))
        self.assertEqual(score["positive_applied_points"], Decimal("50.000"))
        self.assertEqual(score["positive"], Decimal("50.000"))
        self.assertEqual(score["negative"], Decimal("10.000"))
        self.assertEqual(score["final"], Decimal("90.000"))

    def test_event_keeps_historical_action_snapshot_and_void_is_not_delete(self):
        config = self._config()
        exam = ExamType(academic_year_id=self.year_one.id, name="Behavior Exam", sort_order=1)
        session = BehaviorSession(configuration=config, exam_type=exam, session_label="Monthly", maximum_score=100, is_active=True)
        category = BehaviorCategory(configuration=config, name="Positive", polarity="positive", is_active=True)
        action = BehaviorAction(category=category, name="Helpful", level_number=1, points=5, frequency="ad_hoc", is_active=True)
        db.session.add_all([exam, session, category, action])
        db.session.flush()
        config.status = "active"
        db.session.commit()
        event = record_event(config, self.enrollment, session, category, action)
        db.session.commit()
        action.name = "Renamed Helpful"
        action.points = 9
        db.session.commit()
        db.session.refresh(event)
        self.assertEqual(event.action_name_snapshot, "Helpful")
        self.assertEqual(event.points_applied, Decimal("5.000"))
        void_event(event, self.admin.id, "Correction")
        db.session.commit()
        self.assertEqual(db.session.get(BehaviorEvent, event.id).status, "voided")


if __name__ == "__main__":
    unittest.main()
