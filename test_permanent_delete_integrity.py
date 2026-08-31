"""Database-level regression tests for Configuration Center permanent delete."""

import unittest

from sqlalchemy import func, select

from app import create_app, db
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    AcademicClass,
    AcademicLevel,
    Exam,
    Result,
    Student,
    Subject,
    User,
)


class TestPermanentDeleteIntegrity(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "permanent-delete-integrity"
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

        self.legacy_level = AcademicLevel(name="Secondary")
        self.year = AcademicYear(name="2025-2026", is_current=False)
        db.session.add_all([self.legacy_level, self.year])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year_id=self.year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
            is_active=False,
        )
        self.legacy_subject = Subject(
            name="Xisaab",
            academic_level_id=self.legacy_level.id,
        )
        db.session.add_all([self.year_level, self.legacy_subject])
        db.session.flush()
        self.year_subject = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.year_level.id,
            legacy_subject_id=self.legacy_subject.id,
            name="Xisaab",
            is_active=False,
        )
        admin = User(
            username="delete-admin",
            full_name="Delete Administrator",
            role="super_admin",
            is_active=True,
        )
        admin.set_password("correct-password")
        db.session.add_all([self.year_subject, admin])
        db.session.commit()
        self.year_id = self.year.id
        self.legacy_level_id = self.legacy_level.id
        self.year_level_id = self.year_level.id
        self.year_subject_id = self.year_subject.id
        self.legacy_subject_id = self.legacy_subject.id
        self.admin_id = admin.id

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _authenticated_client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_id)
            session["_fresh"] = True
            session["config_center_authenticated"] = True
        return client

    def test_subject_delete_removes_orphan_bridge_and_allows_recreation(self):
        client = self._authenticated_client()
        response = client.post(
            f"/admin/config-center/api/subjects/{self.year_subject_id}/delete"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(
            db.session.execute(
                select(func.count()).select_from(AcademicYearSubject.__table__).where(
                    AcademicYearSubject.id == self.year_subject_id
                )
            ).scalar_one(),
            0,
        )
        self.assertEqual(
            db.session.execute(
                select(func.count()).select_from(Subject.__table__).where(
                    Subject.id == self.legacy_subject_id
                )
            ).scalar_one(),
            0,
        )

        recreate_response = client.post(
            "/admin/config-center/api/subjects",
            json={
                "name": "Xisaab",
                "academic_year_id": self.year_id,
                "academic_year_level_id": self.year_level_id,
                "subject_kind": "exam",
            },
        )
        self.assertEqual(recreate_response.status_code, 200)
        self.assertTrue(recreate_response.get_json()["success"])
        self.assertIsNotNone(
            db.session.execute(
                select(Subject.id).where(Subject.name == "Xisaab")
            ).scalar_one()
        )

    def test_create_subject_replaces_unreferenced_same_name_on_other_level(self):
        stale_level = AcademicLevel(name="Legacy Only")
        stale_subject = Subject(name="Orphan Subject", academic_level=stale_level)
        db.session.add_all([stale_level, stale_subject])
        db.session.commit()
        stale_subject_id = stale_subject.id

        response = self._authenticated_client().post(
            "/admin/config-center/api/subjects",
            json={
                "name": "Orphan Subject",
                "academic_year_id": self.year_id,
                "academic_year_level_id": self.year_level_id,
                "subject_kind": "exam",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"], response.get_json())
        created = AcademicYearSubject.query.filter_by(name="Orphan Subject").one()
        self.assertEqual(created.academic_year_id, self.year_id)
        self.assertEqual(created.academic_year_level_id, self.year_level_id)
        self.assertEqual(created.legacy_subject.academic_level_id, self.legacy_level_id)
        # SQLite may reuse the deleted integer primary key. Verify the old
        # orphan was replaced by its correct level-scoped bridge, rather than
        # treating ID reuse as evidence that stale data survived.
        self.assertEqual(Subject.query.filter_by(name="Orphan Subject").count(), 1)
        self.assertEqual(created.legacy_subject.id, stale_subject_id)
        self.assertNotEqual(created.legacy_subject.academic_level_id, stale_level.id)

    def test_shared_bridge_is_retained_without_blocking_scope_delete(self):
        other_year = AcademicYear(name="2026-2027", is_current=False)
        db.session.add(other_year)
        db.session.flush()
        other_level = AcademicYearLevel(
            academic_year_id=other_year.id,
            legacy_level_id=self.legacy_level.id,
            name="Secondary",
            is_active=False,
        )
        db.session.add(other_level)
        db.session.flush()
        other_scope = AcademicYearSubject(
            academic_year_id=other_year.id,
            academic_year_level_id=other_level.id,
            legacy_subject_id=self.legacy_subject_id,
            name="Xisaab",
            is_active=False,
        )
        db.session.add(other_scope)
        db.session.commit()

        response = self._authenticated_client().post(
            f"/admin/config-center/api/subjects/{self.year_subject_id}/delete"
        )

        self.assertTrue(response.get_json()["success"])
        self.assertIn("shared legacy record was retained", response.get_json()["message"])
        self.assertIsNone(db.session.get(AcademicYearSubject, self.year_subject_id))
        self.assertIsNotNone(db.session.get(AcademicYearSubject, other_scope.id))
        self.assertIsNotNone(db.session.get(Subject, self.legacy_subject_id))

    def test_subject_with_same_year_results_is_rejected_without_deletion(self):
        exam = Exam(name="Monthly", academic_year_id=self.year.id)
        student = Student(
            student_code="DELETE-001",
            full_name="Delete Test Student",
            academic_year_id=self.year.id,
        )
        db.session.add_all([exam, student])
        db.session.flush()
        db.session.add(
            Result(
                student_id=student.id,
                exam_id=exam.id,
                subject_id=self.legacy_subject_id,
                score=75,
            )
        )
        db.session.commit()

        response = self._authenticated_client().post(
            f"/admin/config-center/api/subjects/{self.year_subject_id}/delete"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(response.get_json()["message"], "Cannot delete item with dependencies")
        self.assertIsNotNone(db.session.get(AcademicYearSubject, self.year_subject_id))
        self.assertIsNotNone(db.session.get(Subject, self.legacy_subject_id))
        self.assertEqual(Result.query.filter_by(subject_id=self.legacy_subject_id).count(), 1)

    def test_empty_level_class_and_exam_type_delete_their_owned_records(self):
        extra_legacy_level = AcademicLevel(name="Primary")
        empty_level = AcademicYearLevel(
            academic_year_id=self.year_id,
            legacy_level=extra_legacy_level,
            name="Primary",
            is_active=False,
        )
        class_legacy_level = AcademicLevel(name="Middle")
        legacy_class = AcademicClass(
            name="Middle A",
            academic_level=class_legacy_level,
        )
        class_level = AcademicYearLevel(
            academic_year_id=self.year_id,
            legacy_level=class_legacy_level,
            name="Middle",
            is_active=False,
        )
        db.session.add_all([empty_level, class_level, legacy_class])
        db.session.flush()
        scoped_class = AcademicYearClass(
            academic_year_level_id=class_level.id,
            legacy_class_id=legacy_class.id,
            name="Middle A",
            is_active=False,
        )
        empty_exam = Exam(
            name="Delete Me Exam",
            academic_year_id=self.year_id,
            is_active=False,
        )
        db.session.add_all([scoped_class, empty_exam])
        db.session.commit()
        empty_level_id = empty_level.id
        extra_legacy_level_id = extra_legacy_level.id
        scoped_class_id = scoped_class.id
        legacy_class_id = legacy_class.id
        empty_exam_id = empty_exam.id

        client = self._authenticated_client()
        level_response = client.post(
            f"/admin/config-center/api/levels/{empty_level_id}/delete"
        )
        class_response = client.post(
            f"/admin/config-center/api/classes/{scoped_class_id}/delete"
        )
        exam_response = client.post(
            f"/admin/config-center/api/exam-types/{empty_exam_id}/delete"
        )

        self.assertTrue(level_response.get_json()["success"])
        self.assertTrue(class_response.get_json()["success"])
        self.assertTrue(exam_response.get_json()["success"])
        self.assertIsNone(db.session.get(AcademicYearLevel, empty_level_id))
        self.assertIsNone(db.session.get(AcademicLevel, extra_legacy_level_id))
        self.assertIsNone(db.session.get(AcademicYearClass, scoped_class_id))
        self.assertIsNone(db.session.get(AcademicClass, legacy_class_id))
        self.assertIsNone(db.session.get(Exam, empty_exam_id))
        self.assertIsNotNone(db.session.get(AcademicYearLevel, class_level.id))
        self.assertIsNotNone(db.session.get(AcademicLevel, class_legacy_level.id))


if __name__ == "__main__":
    unittest.main()
