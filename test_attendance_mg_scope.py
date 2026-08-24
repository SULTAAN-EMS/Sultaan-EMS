import unittest

from app import create_app, db
from app.models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    AttendanceRecord,
    Exam,
    ExamHall,
    ExamHallEnrollment,
    ExamHallSubject,
    Subject,
    Student,
)
from app.services import attendance_uf_record, attendance_uf_subject_keys


class TestAttendanceMgScope(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "attendance-mg-scope-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.remove()
        db.drop_all()
        db.create_all()

        self.year = AcademicYear(name="2027-2028", is_current=True)
        db.session.add(self.year)
        db.session.flush()
        self.level = AcademicYearLevel(
            academic_year_id=self.year.id, name="Secondary", is_active=True
        )
        self.year_class = AcademicYearClass(
            academic_year_level=self.level, name="Form Four"
        )
        db.session.add_all([self.level, self.year_class])
        db.session.flush()
        self.legacy_subject = Subject(name="TARBIYA", max_score=100)
        db.session.add(self.legacy_subject)
        db.session.flush()
        self.subject = AcademicYearSubject(
            academic_year_id=self.year.id,
            academic_year_level_id=self.level.id,
            legacy_subject_id=self.legacy_subject.id,
            name="TARBIYA",
        )
        self.exam = Exam(name="Midterm", academic_year_id=self.year.id)
        self.student = Student(
            student_code="MG001",
            full_name="MG Scope Student",
            academic_year_id=self.year.id,
        )
        db.session.add_all([self.subject, self.exam, self.student])
        db.session.flush()
        self.hall = ExamHall(
            name="Hall A",
            code="MG-HALL-A",
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
        )
        db.session.add(self.hall)
        db.session.flush()
        db.session.add_all([
            ExamHallSubject(exam_hall_id=self.hall.id, subject_id=self.legacy_subject.id),
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student.id),
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_no_attendance_record_means_no_mg(self):
        self.assertEqual(
            attendance_uf_subject_keys(self.exam, [self.student.id], [self.subject.id]),
            set(),
        )

    def test_current_scoped_absence_produces_mg(self):
        record = AttendanceRecord(
            student_id=self.student.id,
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
            exam_hall_id=self.hall.id,
            subject_id=self.subject.legacy_subject_id or self.subject.id,
            status="absent",
        )
        db.session.add(record)
        db.session.commit()
        subject_id = self.subject.legacy_subject_id or self.subject.id
        self.assertEqual(
            attendance_uf_subject_keys(self.exam, [self.student.id], [subject_id]),
            {(self.student.id, subject_id)},
        )
        self.assertIsNotNone(attendance_uf_record(self.exam, self.student.id, subject_id))

    def test_detached_old_hall_record_does_not_produce_mg(self):
        subject_id = self.subject.legacy_subject_id or self.subject.id
        db.session.add(AttendanceRecord(
            student_id=self.student.id,
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
            exam_hall_id=self.hall.id,
            subject_id=subject_id,
            status="sick",
        ))
        db.session.flush()
        ExamHallEnrollment.query.filter_by(
            exam_hall_id=self.hall.id,
            student_id=self.student.id,
        ).delete(synchronize_session=False)
        db.session.commit()
        self.assertEqual(
            attendance_uf_subject_keys(self.exam, [self.student.id], [subject_id]),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
