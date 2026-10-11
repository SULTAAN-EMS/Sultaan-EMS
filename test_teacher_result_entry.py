"""Teacher result-entry isolation, validation, review, and publishing tests."""

import unittest
from unittest.mock import patch

from app import create_app, db
from app.models import (
    AcademicClass, AcademicLevel, AcademicYear, AcademicYearClass, AcademicYearLevel,
    AcademicYearSubject, Exam, Result, Student, StudentEnrollment, Subject, Teacher,
    TeacherResultAssignment, TeacherResultSubmission, User,
)
from migrations import phase_14_teacher_result_entry


class TestTeacherResultEntry(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "teacher-result-entry-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.level = AcademicLevel(name="Secondary", is_active=True)
        db.session.add_all([self.year, self.level])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year=self.year, legacy_level=self.level, name="Secondary"
        )
        self.school_class = AcademicClass(academic_level=self.level, name="Form 1", is_active=True)
        db.session.add_all([self.year_level, self.school_class])
        db.session.flush()
        self.year_class = AcademicYearClass(
            academic_year_level=self.year_level, legacy_class=self.school_class, name="Form 1"
        )
        self.subject = Subject(name="Mathematics", academic_level=self.level, max_score=50, is_active=True)
        self.exam = Exam(name="Midterm", academic_year=self.year, is_active=True)
        db.session.add_all([self.year_class, self.subject, self.exam])
        db.session.flush()
        db.session.add(AcademicYearSubject(
            academic_year=self.year, academic_year_level=self.year_level,
            legacy_subject=self.subject, name=self.subject.name, subject_kind="exam", max_score=50,
        ))
        self.students = []
        for index in range(2):
            student = Student(student_code=f"ST-{index + 1}", full_name=f"Student {index + 1}", is_active=True)
            db.session.add(student)
            db.session.flush()
            db.session.add(StudentEnrollment(
                student=student, academic_year=self.year, academic_year_level=self.year_level,
                academic_year_class=self.year_class, status="active", academic_outcome="pending",
                enrollment_source="manual",
            ))
            self.students.append(student)

        self.admin = User(username="result-admin", full_name="Results Admin", role="admin", is_active=True)
        self.admin.set_password("AdminPass123")
        self.admin.set_permissions(["teachers"])
        self.teacher_user = User(username="result-teacher", full_name="Results Teacher", role="staff", is_active=True)
        self.teacher_user.set_password("TeacherPass123")
        db.session.add_all([self.admin, self.teacher_user])
        db.session.flush()
        self.teacher = Teacher(
            teacher_id="RT-1", teacher_code="RT-C1", full_name="Results Teacher",
            employment_status="Active", is_active=True, user_id=self.teacher_user.id,
            classes=[self.school_class], subjects=[self.subject],
        )
        db.session.add(self.teacher)
        db.session.flush()
        self.assignment = TeacherResultAssignment(
            teacher=self.teacher, academic_year=self.year, exam=self.exam,
            school_class=self.school_class, subject=self.subject, max_submissions=1,
        )
        db.session.add(self.assignment)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _teacher_login(self):
        return self.client.post("/teacher/login", data={
            "username": "result-teacher", "password": "TeacherPass123"
        }, follow_redirects=True)

    def test_teacher_portal_exposes_only_result_entry_and_limits_roster_to_assignment(self):
        response = self._teacher_login()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Result Entry", response.data)
        self.assertIn(b"Student 1", response.data)
        self.assertNotIn(b"Exam Analytics", response.data)
        self.assertNotIn(b"Naadigayga", response.data)

    def test_invalid_score_is_not_saved_and_submitted_marks_publish_only_after_admin_approval(self):
        self._teacher_login()
        invalid = self.client.post("/teacher/result-entry/save", data={
            "assignment_id": self.assignment.id,
            "action": "draft",
            f"score_{self.students[0].id}": "51",
            f"score_{self.students[1].id}": "40",
        }, follow_redirects=True)
        self.assertEqual(invalid.status_code, 200)
        self.assertIsNone(TeacherResultSubmission.query.first())

        response = self.client.post("/teacher/result-entry/save", data={
            "assignment_id": self.assignment.id,
            "action": "submit",
            f"score_{self.students[0].id}": "49.5",
            f"score_{self.students[1].id}": "40",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        submission = TeacherResultSubmission.query.one()
        self.assertEqual(submission.status, "submitted")
        self.assertEqual(Result.query.count(), 0)

        existing = Result(
            student_id=self.students[0].id, exam_id=self.exam.id,
            subject_id=self.subject.id, score=38, is_published=True,
        )
        db.session.add(existing)
        db.session.commit()

        self.client.post("/teacher/logout")
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})
        review = self.client.get("/admin/teachers/result-entry")
        self.assertEqual(review.status_code, 200)
        approved = self.client.post("/admin/teachers/result-entry", data={
            "action": "approve", "assignment_id": str(self.assignment.id),
            "reviewer_note": "Checked against source sheets",
        }, follow_redirects=True)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(submission.status, "submitted")
        self.assertEqual(float(Result.query.filter_by(student_id=self.students[0].id).one().score), 38)
        approved = self.client.post("/admin/teachers/result-entry", data={
            "action": "approve", "assignment_id": str(self.assignment.id),
            "reviewer_note": "Checked against source sheets", "confirm_replace": "yes",
        }, follow_redirects=True)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(submission.status, "approved")
        self.assertEqual(Result.query.count(), 2)
        self.assertTrue(all(result.is_published for result in Result.query.all()))
        self.assertEqual(float(Result.query.filter_by(student_id=self.students[0].id).one().score), 49.5)

    def test_teacher_cannot_submit_another_teachers_assignment(self):
        self._teacher_login()
        other_teacher = Teacher(
            teacher_id="RT-2", teacher_code="RT-C2", full_name="Other Teacher",
            employment_status="Active", is_active=True,
        )
        db.session.add(other_teacher)
        db.session.flush()
        other_assignment = TeacherResultAssignment(
            teacher=other_teacher, academic_year=self.year, exam=self.exam,
            school_class=self.school_class, subject=self.subject,
        )
        db.session.add(other_assignment)
        db.session.commit()
        response = self.client.post("/teacher/result-entry/save", data={
            "assignment_id": other_assignment.id, "action": "submit",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Result.query.count(), 0)

    def test_admin_can_create_assignment_only_for_existing_teacher_scope(self):
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})
        response = self.client.post("/admin/teachers/result-entry", data={
            "action": "create", "teacher_id": str(self.teacher.id),
            "academic_year_id": str(self.year.id), "exam_id": str(self.exam.id),
            "academic_year_level_id": str(self.year_level.id),
            "academic_year_class_id": str(self.year_class.id),
            "academic_year_subject_id": str(AcademicYearSubject.query.one().id),
            "max_submissions": "2",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(TeacherResultAssignment.query.count(), 1)
        self.assertEqual(TeacherResultAssignment.query.one().max_submissions, 2)

    def test_admin_can_assign_result_scope_without_legacy_teacher_class_subject_links(self):
        unscoped_teacher = Teacher(
            teacher_id="RT-3", teacher_code="RT-C3", full_name="Unscoped Teacher",
            employment_status="Active", is_active=True,
        )
        db.session.add(unscoped_teacher)
        db.session.commit()
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})

        response = self.client.post("/admin/teachers/result-entry", data={
            "action": "create", "teacher_id": str(unscoped_teacher.id),
            "academic_year_id": str(self.year.id), "exam_id": str(self.exam.id),
            "academic_year_level_id": str(self.year_level.id),
            "academic_year_class_id": str(self.year_class.id),
            "academic_year_subject_id": str(AcademicYearSubject.query.one().id),
            "max_submissions": "1",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        assignment = TeacherResultAssignment.query.filter_by(teacher_id=unscoped_teacher.id).one()
        self.assertEqual(assignment.class_id, self.school_class.id)
        self.assertEqual(assignment.subject_id, self.subject.id)

    def test_admin_assignment_form_uses_year_scoped_records(self):
        orphan_class = AcademicClass(academic_level=self.level, name="Unmapped legacy class", is_active=True)
        orphan_subject = Subject(name="Unoffered legacy subject", academic_level=self.level, is_active=True)
        db.session.add_all([orphan_class, orphan_subject])
        db.session.commit()
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})

        response = self.client.get("/admin/teachers/result-entry")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"name=\"academic_year_level_id\"", response.data)
        self.assertIn(b"name=\"academic_year_class_id\"", response.data)
        self.assertIn(b"name=\"academic_year_subject_id\"", response.data)
        self.assertIn(f'data-level-id="{self.year_level.id}"'.encode(), response.data)
        self.assertIn(b"Mathematics", response.data)
        self.assertNotIn(b"Unmapped legacy class", response.data)
        self.assertNotIn(b"Unoffered legacy subject", response.data)

    def test_admin_rejects_class_and_subject_scopes_from_another_level(self):
        other_level = AcademicLevel(name="Middle", is_active=True)
        db.session.add(other_level)
        db.session.flush()
        other_year_level = AcademicYearLevel(
            academic_year=self.year, legacy_level=other_level, name="Middle"
        )
        other_class = AcademicClass(academic_level=other_level, name="Grade 7", is_active=True)
        other_subject = Subject(name="Science", academic_level=other_level, is_active=True)
        db.session.add_all([other_year_level, other_class, other_subject])
        db.session.flush()
        other_year_class = AcademicYearClass(
            academic_year_level=other_year_level, legacy_class=other_class, name="Grade 7"
        )
        other_year_subject = AcademicYearSubject(
            academic_year=self.year, academic_year_level=other_year_level,
            legacy_subject=other_subject, name=other_subject.name, subject_kind="exam",
        )
        db.session.add_all([other_year_class, other_year_subject])
        db.session.flush()
        self.teacher.classes.append(other_class)
        self.teacher.subjects.append(other_subject)
        db.session.commit()
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})

        response = self.client.post("/admin/teachers/result-entry", data={
            "action": "create", "teacher_id": str(self.teacher.id),
            "academic_year_id": str(self.year.id), "exam_id": str(self.exam.id),
            "academic_year_level_id": str(self.year_level.id),
            "academic_year_class_id": str(other_year_class.id),
            "academic_year_subject_id": str(other_year_subject.id),
            "max_submissions": "1",
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Xilsaaristan isma waafaqsana".encode(), response.data)
        self.assertEqual(TeacherResultAssignment.query.count(), 1)

    def test_admin_teacher_home_opens_result_entry_control_without_dashboard_mockups(self):
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})
        response = self.client.get("/admin/teachers/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/teachers/result-entry", response.headers["Location"])

        response = self.client.get("/admin/teachers/result-entry")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Result Entry Control", response.data)
        self.assertIn(b"All assignments", response.data)
        self.assertIn(b"Form 1", response.data)
        self.assertNotIn(b"Top Performance Snapshot", response.data)

    def test_reopened_approved_sheet_requires_reason_and_explicit_result_replacement(self):
        self.client.post("/teacher/login", data={
            "username": "result-teacher", "password": "TeacherPass123"
        })
        first_submit = self.client.post("/teacher/result-entry/save", data={
            "assignment_id": self.assignment.id,
            "action": "submit",
            f"score_{self.students[0].id}": "44",
            f"score_{self.students[1].id}": "39",
        }, follow_redirects=True)
        self.assertEqual(first_submit.status_code, 200)

        self.client.post("/teacher/logout")
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})
        self.client.post("/admin/teachers/result-entry", data={
            "action": "approve", "assignment_id": str(self.assignment.id),
        }, follow_redirects=True)
        submission = TeacherResultSubmission.query.one()
        self.assertEqual(submission.status, "approved")

        self.client.post("/admin/teachers/result-entry", data={
            "action": "reopen", "assignment_id": str(self.assignment.id),
        }, follow_redirects=True)
        self.assertEqual(submission.status, "approved")
        reopened = self.client.post("/admin/teachers/result-entry", data={
            "action": "reopen", "assignment_id": str(self.assignment.id),
            "reviewer_note": "Correct one entry after source-sheet review",
        }, follow_redirects=True)
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(submission.status, "reopened")
        self.assertEqual(self.assignment.max_submissions, 2)

        self.client.post("/admin/logout")
        self.client.post("/teacher/login", data={
            "username": "result-teacher", "password": "TeacherPass123"
        })
        second_submit = self.client.post("/teacher/result-entry/save", data={
            "assignment_id": self.assignment.id,
            "action": "submit",
            f"score_{self.students[0].id}": "46",
            f"score_{self.students[1].id}": "39",
        }, follow_redirects=True)
        self.assertEqual(second_submit.status_code, 200)
        self.assertEqual(submission.submission_count, 2)

        self.client.post("/teacher/logout")
        self.client.post("/admin/login", data={"username": "result-admin", "password": "AdminPass123"})
        self.client.post("/admin/teachers/result-entry", data={
            "action": "approve", "assignment_id": str(self.assignment.id),
        }, follow_redirects=True)
        self.assertEqual(submission.status, "submitted")
        self.assertEqual(float(Result.query.filter_by(student_id=self.students[0].id).one().score), 44)
        self.client.post("/admin/teachers/result-entry", data={
            "action": "approve", "assignment_id": str(self.assignment.id), "confirm_replace": "yes",
        }, follow_redirects=True)
        self.assertEqual(submission.status, "approved")
        self.assertEqual(float(Result.query.filter_by(student_id=self.students[0].id).one().score), 46)

    def test_migration_is_repeatable(self):
        with patch.object(phase_14_teacher_result_entry, "create_app", return_value=self.app):
            self.assertEqual(phase_14_teacher_result_entry.upgrade(), {"status": "applied", "tables": 3})
            self.assertEqual(phase_14_teacher_result_entry.upgrade(), {"status": "applied", "tables": 3})


if __name__ == "__main__":
    unittest.main()
