import unittest

from app import create_app, db
from app.academic_hierarchy import (
    is_year_subject_available_for_class,
    year_subjects,
    year_subjects_for_class_scope,
)
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearClassSubjectExclusion,
    AcademicYearLevel,
    AcademicYearSubject,
    AuditLog,
    Exam,
    Result,
    Student,
    StudentEnrollment,
    Subject,
    User,
)
from app.services import result_payload
from app.routes_attendance import exam_subject_available_for_student, year_class_exam_subject_scope
from app.routes_advanced_results import (
    _student_exam_subject_ids,
    build_analytics_data,
    build_dashboard_stats,
    subjects_for_scope,
)


class TestClassSubjectAvailability(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "class-subject-availability-test"
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

        year = AcademicYear(name="2026-2027", is_current=True)
        db.session.add(year)
        db.session.flush()
        level = AcademicYearLevel(academic_year_id=year.id, name="Dugsi Sare")
        db.session.add(level)
        db.session.flush()
        self.class_a = AcademicYearClass(academic_year_level_id=level.id, name="Form 1A")
        self.class_b = AcademicYearClass(academic_year_level_id=level.id, name="Form 1B")
        subject = AcademicYearSubject(
            academic_year_id=year.id,
            academic_year_level_id=level.id,
            name="English",
            subject_kind="exam",
        )
        db.session.add_all([self.class_a, self.class_b, subject])
        db.session.commit()
        self.year = year
        self.level = level
        self.subject = subject

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_exclusion_is_limited_to_one_class_and_keeps_level_offering(self):
        db.session.add(AcademicYearClassSubjectExclusion(
            academic_year_class_id=self.class_a.id,
            academic_year_subject_id=self.subject.id,
        ))
        db.session.commit()

        level_subjects = year_subjects(self.year.id, self.level.id, subject_kind="exam")
        class_a_subjects = year_subjects(
            self.year.id, self.level.id, subject_kind="exam",
            academic_year_class_id=self.class_a.id,
        )
        class_b_subjects = year_subjects(
            self.year.id, self.level.id, subject_kind="exam",
            academic_year_class_id=self.class_b.id,
        )

        self.assertIn(self.subject.id, {item.id for item in level_subjects})
        self.assertNotIn(self.subject.id, {item.id for item in class_a_subjects})
        self.assertIn(self.subject.id, {item.id for item in class_b_subjects})
        self.assertFalse(is_year_subject_available_for_class(self.subject, self.class_a.id))
        self.assertTrue(is_year_subject_available_for_class(self.subject, self.class_b.id))

    def test_class_from_another_level_cannot_load_subjects(self):
        other_level = AcademicYearLevel(academic_year_id=self.year.id, name="Dugsi Dhexe")
        db.session.add(other_level)
        db.session.flush()
        other_class = AcademicYearClass(academic_year_level_id=other_level.id, name="8aad A")
        db.session.add(other_class)
        db.session.commit()

        self.assertEqual(
            year_subjects(
                self.year.id,
                self.level.id,
                subject_kind="exam",
                academic_year_class_id=other_class.id,
            ),
            [],
        )

    def test_unresolved_student_class_does_not_widen_to_level_subjects(self):
        self.assertEqual(
            year_subjects_for_class_scope(
                self.year.id,
                self.level.id,
                None,
                subject_kind="exam",
            ),
            [],
        )
        self.assertEqual(
            [item.id for item in year_subjects_for_class_scope(
                self.year.id,
                self.level.id,
                self.class_b.id,
                subject_kind="exam",
            )],
            [self.subject.id],
        )

    def test_results_hub_resolves_subjects_for_selected_year_class(self):
        legacy_level = AcademicLevel(name="Legacy Secondary")
        db.session.add(legacy_level)
        db.session.flush()
        legacy_class = AcademicClass(
            academic_level_id=legacy_level.id,
            name="Form 1A",
        )
        legacy_subject = Subject(
            academic_level_id=legacy_level.id,
            name="English",
        )
        db.session.add_all([legacy_class, legacy_subject])
        db.session.flush()

        self.level.legacy_level_id = legacy_level.id
        self.class_a.legacy_class_id = legacy_class.id
        self.subject.legacy_subject_id = legacy_subject.id
        exam = Exam(
            name="Midterm",
            academic_year_id=self.year.id,
            academic_level_id=legacy_level.id,
            academic_class_id=legacy_class.id,
        )
        db.session.add(exam)
        db.session.commit()

        self.assertEqual(
            [
                subject.id
                for subject in subjects_for_scope(
                    exam,
                    level_id=legacy_level.id,
                    class_id=legacy_class.id,
                )
            ],
            [legacy_subject.id],
        )

    def test_inactive_year_offering_cannot_return_through_legacy_attendance_fallback(self):
        legacy_level = AcademicLevel(name="Secondary")
        db.session.add(legacy_level)
        db.session.flush()
        legacy_subject = Subject(
            academic_level_id=legacy_level.id,
            name="English",
            is_active=True,
        )
        db.session.add(legacy_subject)
        db.session.flush()
        self.level.legacy_level_id = legacy_level.id
        self.subject.legacy_subject_id = legacy_subject.id
        self.subject.is_active = False
        db.session.commit()

        scope = year_class_exam_subject_scope(self.year.id)
        placement = {
            "academic_year_level_id": self.level.id,
            "academic_year_class_id": self.class_a.id,
            "academic_level_id": legacy_level.id,
        }
        self.assertFalse(exam_subject_available_for_student(
            scope,
            Student(full_name="Legacy student", student_code="LEGACY-1"),
            placement,
            legacy_subject.id,
        ))

    def test_admin_availability_api_saves_only_valid_same_level_classes_and_audits(self):
        other_level = AcademicYearLevel(academic_year_id=self.year.id, name="Dugsi Dhexe")
        db.session.add(other_level)
        db.session.flush()
        other_class = AcademicYearClass(academic_year_level_id=other_level.id, name="8aad A")
        db.session.add(other_class)
        user = User(username="scope-admin", full_name="Scope Admin", role="super_admin")
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        page = client.get(
            f"/admin/config-center?section=subjects&year_id={self.year.id}&level_id={self.level.id}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'class="config-form-group js-subject-class-group"', page.data)
        self.assertIn(b'id="itemAvailableClasses"', page.data)
        self.assertIn(b'data-select-subject-classes="all"', page.data)
        self.assertNotIn(b'data-open-class-availability', page.data)
        self.assertNotIn(b'config-class-availability-dialog', page.data)

        saved = client.put(
            f"/admin/config-center/api/subjects/{self.subject.id}/class-availability",
            json={"available_class_ids": [self.class_b.id]},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(
            [item.id for item in year_subjects(
                self.year.id,
                self.level.id,
                subject_kind="exam",
                academic_year_class_id=self.class_a.id,
            )],
            [],
        )
        self.assertEqual(
            [item.id for item in year_subjects(
                self.year.id,
                self.level.id,
                subject_kind="exam",
                academic_year_class_id=self.class_b.id,
            )],
            [self.subject.id],
        )
        self.assertEqual(AuditLog.query.filter_by(
            action="Configuration Center Subject Class Availability Updated"
        ).count(), 1)

        rejected = client.put(
            f"/admin/config-center/api/subjects/{self.subject.id}/class-availability",
            json={"available_class_ids": [other_class.id]},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(is_year_subject_available_for_class(self.subject, self.class_a.id))
        self.assertTrue(is_year_subject_available_for_class(self.subject, self.class_b.id))

    def test_subject_form_saves_and_updates_class_choices_in_one_request(self):
        user = User(username="subject-form-admin", full_name="Subject Form Admin", role="super_admin")
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True

        created = client.post(
            "/admin/config-center/api/subjects",
            json={
                "name": "Science",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "subject_kind": "exam",
                "available_class_ids": [self.class_a.id],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        subject_id = created.get_json()["data"]["id"]
        subject = db.session.get(AcademicYearSubject, subject_id)
        self.assertTrue(is_year_subject_available_for_class(subject, self.class_a.id))
        self.assertFalse(is_year_subject_available_for_class(subject, self.class_b.id))

        updated = client.put(
            f"/admin/config-center/api/subjects/{subject_id}",
            json={
                "name": "Science",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "subject_kind": "exam",
                "available_class_ids": [self.class_b.id],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertFalse(is_year_subject_available_for_class(subject, self.class_a.id))
        self.assertTrue(is_year_subject_available_for_class(subject, self.class_b.id))

        other_level = AcademicYearLevel(academic_year_id=self.year.id, name="Dugsi Hoose")
        db.session.add(other_level)
        db.session.flush()
        other_class = AcademicYearClass(academic_year_level_id=other_level.id, name="Grade 1")
        db.session.add(other_class)
        db.session.commit()
        rejected = client.put(
            f"/admin/config-center/api/subjects/{subject_id}",
            json={
                "name": "Science",
                "academic_year_id": self.year.id,
                "academic_year_level_id": self.level.id,
                "subject_kind": "exam",
                "available_class_ids": [other_class.id],
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(is_year_subject_available_for_class(subject, self.class_a.id))
        self.assertTrue(is_year_subject_available_for_class(subject, self.class_b.id))

    def test_excluded_historical_mark_is_retained_but_not_counted_for_class_average(self):
        legacy_level = AcademicLevel(name="Secondary")
        db.session.add(legacy_level)
        db.session.flush()
        self.level.legacy_level_id = legacy_level.id
        legacy_class = AcademicClass(academic_level_id=legacy_level.id, name="Form 1A")
        legacy_other_class = AcademicClass(academic_level_id=legacy_level.id, name="Form 1B")
        english = Subject(academic_level_id=legacy_level.id, name="English", max_score=100)
        mathematics = Subject(academic_level_id=legacy_level.id, name="Mathematics", max_score=100)
        student = Student(
            student_code="CLASS-SCOPE-001",
            full_name="Class Scope Student",
            academic_year_id=self.year.id,
            academic_level_id=legacy_level.id,
        )
        exam = Exam(name="Midterm", academic_year_id=self.year.id)
        db.session.add_all([legacy_class, legacy_other_class, english, mathematics, student, exam])
        db.session.flush()
        self.class_a.legacy_class_id = legacy_class.id
        self.class_b.legacy_class_id = legacy_other_class.id
        self.subject.legacy_subject_id = english.id
        math_offering = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            legacy_subject_id=mathematics.id,
            name="Mathematics",
            subject_kind="exam",
        )
        db.session.add(math_offering)
        db.session.flush()
        student.academic_class_id = legacy_class.id
        db.session.add_all([
            AcademicYearClassSubjectExclusion(
                academic_year_class_id=self.class_a.id,
                academic_year_subject_id=math_offering.id,
            ),
            Result(student_id=student.id, exam_id=exam.id, subject_id=english.id, score=50),
            Result(student_id=student.id, exam_id=exam.id, subject_id=mathematics.id, score=100),
        ])
        db.session.commit()

        payload = result_payload(student, exam=exam, public_only=False)

        self.assertEqual(payload["total"], 50.0)
        self.assertEqual(payload["max_total"], 100.0)
        by_name = {row["subject"]: row for row in payload["subjects"]}
        self.assertIn("Mathematics", by_name)
        self.assertFalse(by_name["Mathematics"]["scope_applicable"])
        self.assertTrue(by_name["English"]["scope_applicable"])

    def test_class_specific_subject_scope_is_used_for_each_students_accounting(self):
        legacy_level = AcademicLevel(name="Secondary")
        db.session.add(legacy_level)
        db.session.flush()
        self.level.legacy_level_id = legacy_level.id
        legacy_class_a = AcademicClass(academic_level_id=legacy_level.id, name="Form 1A")
        legacy_class_b = AcademicClass(academic_level_id=legacy_level.id, name="Form 1B")
        english = Subject(academic_level_id=legacy_level.id, name="English", max_score=100)
        mathematics = Subject(academic_level_id=legacy_level.id, name="Mathematics", max_score=100)
        student_a = Student(student_code="SCOPE-A", full_name="Student A", academic_year_id=self.year.id)
        student_b = Student(student_code="SCOPE-B", full_name="Student B", academic_year_id=self.year.id)
        exam = Exam(name="Midterm", academic_year_id=self.year.id)
        db.session.add_all([legacy_class_a, legacy_class_b, english, mathematics, student_a, student_b, exam])
        db.session.flush()
        self.class_a.legacy_class_id = legacy_class_a.id
        self.class_b.legacy_class_id = legacy_class_b.id
        self.subject.legacy_subject_id = english.id
        math_offering = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            name="Mathematics",
            legacy_subject_id=mathematics.id,
            subject_kind="exam",
        )
        db.session.add(math_offering)
        db.session.flush()
        db.session.add_all([
            StudentEnrollment(
                student_id=student_a.id,
                academic_year_id=self.year.id,
                academic_year_level_id=self.level.id,
                academic_year_class_id=self.class_a.id,
            ),
            StudentEnrollment(
                student_id=student_b.id,
                academic_year_id=self.year.id,
                academic_year_level_id=self.level.id,
                academic_year_class_id=self.class_b.id,
            ),
            AcademicYearClassSubjectExclusion(
                academic_year_class_id=self.class_a.id,
                academic_year_subject_id=math_offering.id,
            ),
            Result(student_id=student_a.id, exam_id=exam.id, subject_id=english.id, score=50, is_published=True),
            Result(student_id=student_a.id, exam_id=exam.id, subject_id=mathematics.id, score=100, is_published=True),
            Result(student_id=student_b.id, exam_id=exam.id, subject_id=english.id, score=60, is_published=True),
            Result(student_id=student_b.id, exam_id=exam.id, subject_id=mathematics.id, score=80, is_published=True),
        ])
        db.session.commit()

        placement_cache = {}
        offering_cache = {}
        subjects_a = _student_exam_subject_ids(
            student_a,
            exam,
            placement_cache=placement_cache,
            offering_cache=offering_cache,
        )
        subjects_b = _student_exam_subject_ids(
            student_b,
            exam,
            placement_cache=placement_cache,
            offering_cache=offering_cache,
        )

        self.assertEqual(subjects_a, {english.id})
        self.assertEqual(subjects_b, {english.id, mathematics.id})

        analytics = build_analytics_data(
            Result.query.filter_by(exam_id=exam.id, is_published=True).all(),
            [student_a, student_b],
            exam,
            scoped_subjects=[english, mathematics],
            academic_year_level_id=self.level.id,
            year_scoped_subjects=[self.subject, math_offering],
        )
        self.assertEqual(analytics["completion_rate"]["expected"], 3)
        self.assertEqual(analytics["completion_rate"]["actual"], 3)
        self.assertEqual(analytics["overall_average"], 63.33)
        dashboard = build_dashboard_stats(exam)
        self.assertEqual(dashboard["expected_results"], 3)
        self.assertEqual(dashboard["actual_results"], 3)
        self.assertEqual(dashboard["total_subjects"], 2)


if __name__ == "__main__":
    unittest.main()
