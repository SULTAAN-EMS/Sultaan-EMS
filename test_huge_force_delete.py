"""Regression coverage for the archived Academic Year final purge."""

import unittest
from datetime import date
from unittest.mock import patch

from app import create_app, db
from app.deletion_service import (
    PurgeValidationError,
    _delete_fk_graph,
    purge_academic_year,
    scan_academic_year,
)
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorAction,
    BehaviorActionChoice,
    BehaviorAttendanceDay,
    BehaviorAttendanceRecord,
    BehaviorAttendanceStatus,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorEvent,
    BehaviorSession,
    BehaviorSubCategory,
    Exam,
    ExamMarkingConfiguration,
    PromotionEvaluation,
    PromotionRule,
    Student,
    StudentEnrollment,
    User,
    Subject,
)


class TestHugeForceDelete(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "huge-force-delete"
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

        legacy_level = AcademicLevel(name="Secondary")
        legacy_class = AcademicClass(name="Form Four", academic_level=legacy_level)
        legacy_subject = Subject(name="Mathematics", academic_level=legacy_level)
        self.target = AcademicYear(name="2025-2026", is_current=False)
        self.destination = AcademicYear(name="2026-2027", is_current=True)
        db.session.add_all([
            legacy_level,
            legacy_class,
            legacy_subject,
            self.target,
            self.destination,
        ])
        db.session.flush()
        admin = User(
            username="purge-admin",
            full_name="Purge Administrator",
            role="super_admin",
            is_active=True,
        )
        admin.set_password("Correct-Purge-Password")
        db.session.add(admin)
        db.session.flush()
        self.admin_id = admin.id

        self.target_level = AcademicYearLevel(
            academic_year_id=self.target.id,
            legacy_level_id=legacy_level.id,
            name="Secondary",
            is_active=False,
        )
        self.target_class = AcademicYearClass(
            academic_year_level=self.target_level,
            legacy_class_id=legacy_class.id,
            name="Form Four",
            is_active=False,
        )
        self.target_subject = AcademicYearSubject(
            academic_year_id=self.target.id,
            academic_year_level=self.target_level,
            legacy_subject_id=legacy_subject.id,
            name="Mathematics",
            is_active=False,
        )
        self.destination_level = AcademicYearLevel(
            academic_year_id=self.destination.id,
            legacy_level_id=legacy_level.id,
            name="Secondary",
        )
        self.destination_class = AcademicYearClass(
            academic_year_level=self.destination_level,
            legacy_class_id=legacy_class.id,
            name="Form Four",
        )
        self.student = Student(
            student_code="PURGE-001",
            full_name="Purge Student",
            academic_year_id=self.target.id,
        )
        db.session.add_all([
            self.target_level,
            self.target_class,
            self.target_subject,
            self.destination_level,
            self.destination_class,
            self.student,
        ])
        db.session.flush()
        self.source_enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.target.id,
            academic_year_level_id=self.target_level.id,
            academic_year_class_id=self.target_class.id,
            status="completed",
        )
        self.destination_enrollment = StudentEnrollment(
            student_id=self.student.id,
            academic_year_id=self.destination.id,
            academic_year_level_id=self.destination_level.id,
            academic_year_class_id=self.destination_class.id,
            previous_enrollment=self.source_enrollment,
            status="active",
        )
        db.session.add_all([self.source_enrollment, self.destination_enrollment])
        db.session.commit()
        self.target_id = self.target.id
        self.destination_id = self.destination.id
        self.student_id = self.student.id
        self.source_enrollment_id = self.source_enrollment.id
        self.destination_enrollment_id = self.destination_enrollment.id

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_active_year_is_rejected(self):
        with self.assertRaises(PurgeValidationError):
            purge_academic_year(self.destination_id)

    def test_scan_reports_year_aware_and_promotion_dependencies(self):
        exam = Exam(
            name="Final Evaluation",
            academic_year_id=self.target_id,
            is_final_evaluation=True,
        )
        rule = PromotionRule(
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            exam=exam,
            is_active=False,
        )
        db.session.add_all([exam, rule])
        db.session.flush()
        evaluation = PromotionEvaluation(
            student_id=self.student_id,
            student_enrollment_id=self.source_enrollment_id,
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            exam_id=exam.id,
            promotion_rule_id=rule.id,
            base_outcome="PASS",
            final_outcome="PASS",
            evaluation_status="EVALUATED",
        )
        db.session.add(evaluation)
        db.session.commit()

        report = scan_academic_year(self.target_id)
        categories = {item["category"]: item["count"] for item in report["dependencies"]}
        self.assertEqual(categories["Academic year levels"], 1)
        self.assertEqual(categories["Promotion rules"], 1)
        self.assertEqual(categories["Promotion evaluation history"], 1)
        self.assertEqual(categories["Student enrollments"], 1)
        self.assertEqual(categories["Student identities retained (other academic years)"], 1)
        self.assertTrue(report["eligible"])

    def test_successful_purge_preserves_student_and_other_year_enrollment(self):
        report, deleted = purge_academic_year(self.target_id)
        db.session.commit()

        self.assertGreaterEqual(deleted, 1)
        self.assertIsNone(db.session.get(AcademicYear, self.target_id))
        self.assertIsNotNone(db.session.get(Student, self.student_id))
        self.assertEqual(
            db.session.get(Student, self.student_id).academic_year_id,
            self.destination_id,
        )
        self.assertIsNone(db.session.get(StudentEnrollment, self.source_enrollment_id))
        surviving = db.session.get(StudentEnrollment, self.destination_enrollment_id)
        self.assertIsNotNone(surviving)
        self.assertIsNone(surviving.previous_enrollment_id)
        self.assertEqual(report["target_name"], "2025-2026")

    def test_behavior_and_marking_dependencies_are_purged(self):
        config = BehaviorConfiguration(
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            academic_year_subject_id=self.target_subject.id,
            frequency="monthly",
            status="archived",
            annual_allocation=20,
        )
        session = BehaviorSession(
            configuration=config,
            session_label="Term 1",
            maximum_score=25,
            behavior_allocation=20,
            attendance_allocation=5,
        )
        category = BehaviorCategory(
            configuration=config,
            name="Good Conduct",
            polarity="positive",
        )
        subcategory = BehaviorSubCategory(category=category, name="Participation")
        action = BehaviorAction(
            category=category,
            subcategory=subcategory,
            name="Helping",
            points=1,
            frequency="ad_hoc",
            behavior_type="choice",
        )
        choice = BehaviorActionChoice(action=action, label="Yes", points=1)
        status = BehaviorAttendanceStatus(
            configuration=config,
            key="present",
            label="Present",
            polarity="positive",
            points=1,
        )
        day = BehaviorAttendanceDay(
            configuration=config,
            weekday=0,
            label="Monday",
        )
        marked_exam = Exam(
            name="Marked Exam",
            academic_year_id=self.target_id,
            is_final_evaluation=False,
        )
        marking = ExamMarkingConfiguration(
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            exam=marked_exam,
            default_full_marks=100,
        )
        db.session.add_all([
            config,
            session,
            category,
            subcategory,
            action,
            choice,
            status,
            day,
            marked_exam,
            marking,
        ])
        db.session.flush()
        attendance = BehaviorAttendanceRecord(
            student_id=self.student_id,
            student_enrollment_id=self.source_enrollment_id,
            behavior_configuration_id=config.id,
            behavior_session_id=session.id,
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            academic_year_class_id=self.target_class.id,
            attendance_date=date(2025, 9, 1),
            status_id=status.id,
            status_key_snapshot="present",
            status_label_snapshot="Present",
            polarity="positive",
            points_applied=1,
        )
        event = BehaviorEvent(
            student_id=self.student_id,
            student_enrollment_id=self.source_enrollment_id,
            behavior_configuration_id=config.id,
            behavior_session_id=session.id,
            behavior_category_id=category.id,
            behavior_action_id=action.id,
            polarity="positive",
            points_applied=1,
            category_name_snapshot=category.name,
            action_name_snapshot=action.name,
            action_level_snapshot=1,
            session_label_snapshot=session.session_label,
        )
        db.session.add_all([attendance, event])
        db.session.commit()

        report = scan_academic_year(self.target_id)
        categories = {item["category"]: item["count"] for item in report["dependencies"]}
        self.assertEqual(categories["Behavior configurations"], 1)
        self.assertEqual(categories["Behavior sessions"], 1)
        self.assertEqual(categories["Behavior categories"], 1)
        self.assertEqual(categories["Behavior sub-categories"], 1)
        self.assertEqual(categories["Behavior actions"], 1)
        self.assertEqual(categories["Behavior action choices"], 1)
        self.assertEqual(categories["Behavior events"], 1)
        self.assertEqual(categories["Behavior attendance statuses"], 1)
        self.assertEqual(categories["Behavior attendance days"], 1)
        self.assertEqual(categories["Behavior attendance records"], 1)
        self.assertEqual(categories["Exam marking configurations"], 1)
        self.assertFalse(report["unsupported_direct_dependencies"])
        self.assertTrue(report["eligible"])

        purge_academic_year(self.target_id)
        db.session.commit()

        self.assertEqual(BehaviorConfiguration.query.count(), 0)
        self.assertEqual(BehaviorSession.query.count(), 0)
        self.assertEqual(BehaviorCategory.query.count(), 0)
        self.assertEqual(BehaviorSubCategory.query.count(), 0)
        self.assertEqual(BehaviorAction.query.count(), 0)
        self.assertEqual(BehaviorActionChoice.query.count(), 0)
        self.assertEqual(BehaviorEvent.query.count(), 0)
        self.assertEqual(BehaviorAttendanceStatus.query.count(), 0)
        self.assertEqual(BehaviorAttendanceDay.query.count(), 0)
        self.assertEqual(BehaviorAttendanceRecord.query.count(), 0)
        self.assertEqual(ExamMarkingConfiguration.query.count(), 0)

    def test_purge_deletes_student_owned_only_by_archived_year(self):
        orphan = Student(
            student_code="PURGE-ONLY-001",
            full_name="Archived Year Only",
            academic_year_id=self.target_id,
        )
        db.session.add(orphan)
        db.session.flush()
        orphan_enrollment = StudentEnrollment(
            student_id=orphan.id,
            academic_year_id=self.target_id,
            academic_year_level_id=self.target_level.id,
            academic_year_class_id=self.target_class.id,
            status="completed",
        )
        db.session.add(orphan_enrollment)
        db.session.commit()
        orphan_id = orphan.id

        report, _ = purge_academic_year(self.target_id)
        db.session.commit()

        self.assertEqual(report["deletable_student_identities"], 1)
        self.assertIsNone(db.session.get(Student, orphan_id))

    def test_failure_is_rollback_safe(self):
        with patch("app.deletion_service._delete_fk_graph", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                purge_academic_year(self.target_id)
        db.session.rollback()

        self.assertIsNotNone(db.session.get(AcademicYear, self.target_id))
        self.assertIsNotNone(db.session.get(AcademicYearLevel, self.target_level.id))
        self.assertIsNotNone(db.session.get(AcademicYearClass, self.target_class.id))
        self.assertIsNotNone(db.session.get(AcademicYearSubject, self.target_subject.id))
        self.assertIsNotNone(db.session.get(StudentEnrollment, self.source_enrollment_id))
        self.assertEqual(db.session.get(Student, self.student_id).academic_year_id, self.target_id)

    def test_delete_graph_uses_bulk_deletes_without_leaf_probe_selects(self):
        level_id = self.target_level.id
        class_id = self.target_class.id
        subject_id = self.target_subject.id
        graph = {
            "academic_year_levels": {level_id},
            "academic_year_classes": {class_id},
            "academic_year_subjects": {subject_id},
        }
        with patch.object(db.session, "execute", wraps=db.session.execute) as execute:
            deleted = _delete_fk_graph(graph)

        self.assertEqual(deleted, 3)
        statements = [str(call.args[0]).upper() for call in execute.call_args_list]
        self.assertTrue(statements)
        self.assertFalse(any(statement.lstrip().startswith("SELECT") for statement in statements))
        db.session.expire_all()
        self.assertIsNone(db.session.get(AcademicYearClass, class_id))
        self.assertIsNone(db.session.get(AcademicYearSubject, subject_id))
        self.assertIsNone(db.session.get(AcademicYearLevel, level_id))

    def test_http_confirmation_guards_run_before_purge(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        endpoint = f"/admin/config-center/api/academic-years/{self.target_id}/huge-force-delete"
        missing_ack = client.post(endpoint, json={
            "acknowledged": False,
            "confirmation": "HUGE FORCE DELETE",
            "password": "Correct-Purge-Password",
        })
        self.assertEqual(missing_ack.status_code, 400)

        wrong_phrase = client.post(endpoint, json={
            "acknowledged": True,
            "confirmation": "DELETE",
            "password": "Correct-Purge-Password",
        })
        self.assertEqual(wrong_phrase.status_code, 400)

        wrong_password = client.post(endpoint, json={
            "acknowledged": True,
            "confirmation": "HUGE FORCE DELETE",
            "password": "wrong",
        })
        self.assertEqual(wrong_password.status_code, 400)
        self.assertIsNotNone(db.session.get(AcademicYear, self.target_id))

    def test_http_huge_force_delete_completes_for_archived_year(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        endpoint = f"/admin/config-center/api/academic-years/{self.target_id}/huge-force-delete"
        response = client.post(endpoint, json={
            "acknowledged": True,
            "confirmation": "HUGE FORCE DELETE",
            "password": "Correct-Purge-Password",
        })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertIsNone(db.session.get(AcademicYear, self.target_id))


if __name__ == "__main__":
    unittest.main()
