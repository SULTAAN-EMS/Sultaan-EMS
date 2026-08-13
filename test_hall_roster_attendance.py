import os
import sys
import unittest
from datetime import date

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from config import Config
from app.models import (
    AcademicClass, AcademicLevel, AcademicYear, AttendanceRecord,
    Exam, ExamHall, ExamHallEnrollment, ExamHallSubject, ExamSession,
    ExamSessionSubject, ExamType, SchoolClass, Student, Subject, User
)
from app.attendance_rules import scheduled_subject_scope_key
from sqlalchemy.exc import IntegrityError


class AttendanceTestConfig(Config):
    """Isolated database configuration for attendance route tests."""

    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}


class TestHallRosterAndAttendance(unittest.TestCase):
    def setUp(self):
        # The database URI must be provided before create_app initializes SQLAlchemy.
        # Updating app.config afterwards leaves the live local database attached.
        self.app = create_app(AttendanceTestConfig)
        self.client = self.app.test_client()

        self.app_context = self.app.app_context()
        self.app_context.push()

        db.create_all()

        # Create or fetch test user
        self.user = User.query.filter_by(username='admin_test').first()
        if not self.user:
            self.user = User(username='admin_test', full_name='System Admin', role='super_admin')
            self.user.set_password('password')
            db.session.add(self.user)

        # Create academic year & exam type
        self.year = AcademicYear.query.filter_by(name='2025/2026').first()
        if not self.year:
            self.year = AcademicYear(name='2025/2026', is_current=True)
            db.session.add(self.year)
        db.session.commit()

        self.exam_type = ExamType.query.filter_by(academic_year_id=self.year.id, name='Final Exam').first()
        if not self.exam_type:
            self.exam_type = ExamType(academic_year_id=self.year.id, name='Final Exam')
            db.session.add(self.exam_type)

        self.exam = Exam.query.filter_by(academic_year_id=self.year.id, name='Results Setup Final').first()
        if not self.exam:
            self.exam = Exam(
                academic_year_id=self.year.id,
                name='Results Setup Final',
                sort_order=1,
                is_active=True,
            )
            db.session.add(self.exam)

        # Create academic levels & classes
        self.level_sec = AcademicLevel.query.filter_by(name='Secondary Test').first()
        if not self.level_sec:
            self.level_sec = AcademicLevel(name='Secondary Test', sort_order=1)
            db.session.add(self.level_sec)
            db.session.commit()

        self.class_form1 = AcademicClass.query.filter_by(academic_level_id=self.level_sec.id, name='Form One Test').first()
        if not self.class_form1:
            self.class_form1 = AcademicClass(academic_level_id=self.level_sec.id, name='Form One Test')
            db.session.add(self.class_form1)

        self.class_form2 = AcademicClass.query.filter_by(academic_level_id=self.level_sec.id, name='Form Two Test').first()
        if not self.class_form2:
            self.class_form2 = AcademicClass(academic_level_id=self.level_sec.id, name='Form Two Test')
            db.session.add(self.class_form2)
        db.session.commit()

        # Create subjects
        self.subject = Subject.query.filter_by(name='Af Soomaali Test').first()
        if not self.subject:
            self.subject = Subject(name='Af Soomaali Test', academic_level_id=self.level_sec.id)
            db.session.add(self.subject)

        # Create school class for legacy compatibility
        self.school_class = SchoolClass.query.first()
        if not self.school_class:
            self.school_class = SchoolClass(name='Class A Test')
            db.session.add(self.school_class)
            db.session.commit()

        # Create students in real classes
        import uuid
        uid = str(uuid.uuid4())[:6]
        self.student1 = Student(
            student_code=f'TIS-1001-{uid}',
            full_name='Amina Ali',
            academic_year_id=self.year.id,
            academic_level_id=self.level_sec.id,
            academic_class_id=self.class_form1.id,
            class_id=self.school_class.id,
            is_active=True
        )
        self.student2 = Student(
            student_code=f'TIS-1002-{uid}',
            full_name='Cabdi Xasan',
            academic_year_id=self.year.id,
            academic_level_id=self.level_sec.id,
            academic_class_id=self.class_form1.id,
            class_id=self.school_class.id,
            is_active=True
        )
        self.student3_form2 = Student(
            student_code=f'TIS-2001-{uid}',
            full_name='Farhiya Nuur',
            academic_year_id=self.year.id,
            academic_level_id=self.level_sec.id,
            academic_class_id=self.class_form2.id,
            class_id=self.school_class.id,
            is_active=True
        )
        db.session.add_all([self.student1, self.student2, self.student3_form2])

        # Create exam hall
        self.hall = ExamHall(
            name=f'Form One — Hall A {uid}',
            code=f'HALL_F1_A_{uid}',
            exam_type_id=self.exam_type.id,
            academic_class_id=self.class_form1.id,
            is_active=True
        )
        db.session.add(self.hall)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def login(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.user.id)
            sess['_fresh'] = True

    def test_a_hall_roster_filters_students_by_level_and_class(self):
        """(a) Hall roster only offers students matching the selected level/class filter, from real class records."""
        self.login()

        # Query pool with Form One filter
        res = self.client.get(f'/admin/attendance/api/hall-roster-data?exam_hall_id={self.hall.id}&academic_level_id={self.level_sec.id}&academic_class_id={self.class_form1.id}')
        data = res.get_json()

        self.assertTrue(data['success'])
        available_ids = [s['id'] for s in data['available_students']]
        
        # Should include Form One students (student1, student2) and EXCLUDE Form Two student (student3_form2)
        self.assertIn(self.student1.id, available_ids)
        self.assertIn(self.student2.id, available_ids)
        self.assertNotIn(self.student3_form2.id, available_ids)

        # Query pool with Form Two filter
        res2 = self.client.get(f'/admin/attendance/api/hall-roster-data?exam_hall_id={self.hall.id}&academic_level_id={self.level_sec.id}&academic_class_id={self.class_form2.id}')
        data2 = res2.get_json()
        available_ids2 = [s['id'] for s in data2['available_students']]

        # Should include Form Two student and exclude Form One students
        self.assertIn(self.student3_form2.id, available_ids2)
        self.assertNotIn(self.student1.id, available_ids2)
        print("[PASS] Test (a): Hall roster filtering by level/class verified.")

    def test_b_attendance_roster_matches_hall_assigned_students_exactly(self):
        """(b) Attendance roster matches the hall's assigned students exactly."""
        self.login()

        # Assign student1 and student2 to hall
        enroll1 = ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id)
        enroll2 = ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student2.id)
        db.session.add_all([enroll1, enroll2])
        db.session.commit()

        # Query attendance data for the hall
        res = self.client.get(f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}&subject_id={self.subject.id}')
        data = res.get_json()

        self.assertTrue(data['success'])
        roster_ids = [s['id'] for s in data['students']]

        # Attendance roster must match exactly the assigned students (student1 & student2), excluding non-enrolled student3_form2
        self.assertEqual(len(roster_ids), 2)
        self.assertIn(self.student1.id, roster_ids)
        self.assertIn(self.student2.id, roster_ids)
        self.assertNotIn(self.student3_form2.id, roster_ids)
        print("[PASS] Test (b): Attendance roster matches hall assigned students exactly.")

    def test_c_bulk_mark_present_and_individual_override(self):
        """(c) Bulk 'mark all present' then an individual override correctly leaves one student non-present while the rest stay present."""
        self.login()

        # Enroll student1 and student2 in hall
        db.session.add_all([
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id),
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student2.id)
        ])
        db.session.commit()

        # Step 1: Bulk mark all present
        bulk_res = self.client.post('/admin/attendance/api/mark-bulk', json={
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'status': 'present'
        })
        self.assertTrue(bulk_res.get_json()['success'])

        # Verify both are present
        rec1 = AttendanceRecord.query.filter_by(student_id=self.student1.id, exam_hall_id=self.hall.id, subject_id=self.subject.id).first()
        rec2 = AttendanceRecord.query.filter_by(student_id=self.student2.id, exam_hall_id=self.hall.id, subject_id=self.subject.id).first()
        self.assertEqual(rec1.status, 'present')
        self.assertEqual(rec2.status, 'present')

        # Step 2: Individual override on student2 -> absent
        override_res = self.client.post('/admin/attendance/api/mark-status', json={
            'student_id': self.student2.id,
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'status': 'absent'
        })
        self.assertTrue(override_res.get_json()['success'])

        # Verify student1 remains present, while student2 is now absent
        db.session.expire_all()
        rec1_after = AttendanceRecord.query.filter_by(student_id=self.student1.id, exam_hall_id=self.hall.id, subject_id=self.subject.id).first()
        rec2_after = AttendanceRecord.query.filter_by(student_id=self.student2.id, exam_hall_id=self.hall.id, subject_id=self.subject.id).first()

        self.assertEqual(rec1_after.status, 'present')
        self.assertEqual(rec2_after.status, 'absent')
        print("[PASS] Test (c): Bulk mark present + individual override verified.")

    def test_d_status_rendering_and_styling(self):
        """(d) Selected status renders with its bold fill + contrasting tick color, distinct per status."""
        self.login()

        # Enroll student1
        db.session.add(ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id))
        db.session.commit()

        # Test marking all 6 statuses
        statuses = ['present', 'absent', 'excused', 'sick', 'emergency', 'late']
        for st in statuses:
            res = self.client.post('/admin/attendance/api/mark-status', json={
                'student_id': self.student1.id,
                'exam_hall_id': self.hall.id,
                'subject_id': self.subject.id,
                'academic_year_id': self.year.id,
                'exam_type_id': self.exam_type.id,
                'status': st
            })
            self.assertTrue(res.get_json()['success'])

            rec = AttendanceRecord.query.filter_by(student_id=self.student1.id, exam_hall_id=self.hall.id, subject_id=self.subject.id).first()
            self.assertEqual(rec.status, st)

        # Verify template attendance.html contains CSS rules for bold fill + tick styling per status
        with self.app.open_resource('templates/admin/attendance.html') as f:
            tmpl_content = f.read().decode('utf-8')

        self.assertIn('.opt.sel.o-present', tmpl_content)
        self.assertIn('.opt.sel.o-absent', tmpl_content)
        self.assertIn('.opt.sel.o-excused', tmpl_content)
        self.assertIn('.opt.sel.o-sick', tmpl_content)
        self.assertIn('.opt.sel.o-emergency', tmpl_content)
        self.assertIn('.opt.sel.o-late', tmpl_content)
        self.assertIn('.opt.sel .tick', tmpl_content)
        print("[PASS] Test (d): Status rendering and styling definitions verified.")

    def test_e_hall_subject_scope_and_clear(self):
        """Attendance uses explicitly linked hall subjects and clear removes marks."""
        self.login()
        second_subject = Subject(name='Mathematics Test', academic_level_id=self.level_sec.id)
        db.session.add(second_subject)
        db.session.flush()
        db.session.add(ExamHallSubject(exam_hall_id=self.hall.id, subject_id=self.subject.id))
        db.session.add(ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id))
        db.session.commit()

        data = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}'
        ).get_json()
        self.assertEqual([subject['id'] for subject in data['subjects']], [self.subject.id])

        mark = self.client.post('/admin/attendance/api/mark-bulk', json={
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'status': 'present',
        })
        self.assertTrue(mark.get_json()['success'])
        clear = self.client.post('/admin/attendance/api/mark-bulk', json={
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'status': 'clear',
        })
        self.assertTrue(clear.get_json()['success'])
        refreshed = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}'
            f'&subject_id={self.subject.id}'
        ).get_json()
        self.assertIsNone(refreshed['students'][0]['status'])
        print("[PASS] Test (e): Explicit hall subject scope + clear action verified.")

    def test_f_results_setup_exam_is_the_attendance_source(self):
        """Attendance lists and creates halls against the official Exam records."""
        self.login()
        types = self.client.get(f'/admin/attendance/api/exam-types?academic_year_id={self.year.id}').get_json()
        self.assertIn(
            {'id': self.exam.id, 'name': self.exam.name, 'source': 'exam'},
            types['exam_types'],
        )

        created = self.client.post('/admin/attendance/api/halls/create', json={
            'name': 'Results Setup Hall',
            'academic_year_id': self.year.id,
            'exam_id': self.exam.id,
            'academic_class_id': self.class_form1.id,
        })
        payload = created.get_json()
        self.assertTrue(payload['success'])
        official_hall = db.session.get(ExamHall, payload['hall']['id'])
        self.assertEqual(official_hall.exam_id, self.exam.id)
        self.assertIsNone(official_hall.exam_type_id)

        halls = self.client.get(
            f'/admin/attendance/api/halls?academic_year_id={self.year.id}&exam_id={self.exam.id}'
        ).get_json()
        self.assertIn(official_hall.id, [hall['id'] for hall in halls['halls']])
        print("[PASS] Test (f): Results Hub Setup Exam source + hall creation verified.")

    def test_g_official_exam_status_survives_reload_and_override(self):
        """Official Results Hub exams persist a status and return the latest value on reload."""
        self.login()
        hall = ExamHall(
            name='Official Attendance Hall',
            code='OFFICIAL_ATTENDANCE_HALL',
            exam_id=self.exam.id,
            academic_class_id=self.class_form1.id,
            is_active=True,
        )
        db.session.add(hall)
        db.session.flush()
        db.session.add(ExamHallEnrollment(exam_hall_id=hall.id, student_id=self.student1.id))
        db.session.add(ExamHallSubject(exam_hall_id=hall.id, subject_id=self.subject.id))
        db.session.commit()

        payload = {
            'student_id': self.student1.id,
            'exam_hall_id': hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_id': self.exam.id,
            'status': 'present',
        }
        saved = self.client.post('/admin/attendance/api/mark-status', json=payload).get_json()
        self.assertTrue(saved['success'])
        self.assertEqual(saved['status'], 'present')
        reloaded = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_id={self.exam.id}&exam_hall_id={hall.id}&subject_id={self.subject.id}'
        ).get_json()
        self.assertEqual(reloaded['students'][0]['status'], 'present')

        payload['status'] = 'late'
        changed = self.client.post('/admin/attendance/api/mark-status', json=payload).get_json()
        self.assertTrue(changed['success'])
        self.assertEqual(changed['status'], 'late')
        changed_reload = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_id={self.exam.id}&exam_hall_id={hall.id}&subject_id={self.subject.id}'
        ).get_json()
        self.assertEqual(changed_reload['students'][0]['status'], 'late')
        print("[PASS] Test (g): Official exam attendance status survives reload and override.")

    def test_h_roster_rows_keep_badge_and_action_in_one_grid_row(self):
        """The roster template uses one compact grid row for both panels."""
        with self.app.open_resource('templates/admin/hall_roster.html') as f:
            tmpl_content = f.read().decode('utf-8')
        self.assertIn('grid-template-columns:30px 82px minmax(0,1fr) auto auto 42px', tmpl_content)
        self.assertIn('white-space:nowrap', tmpl_content)
        print("[PASS] Test (h): Roster row alignment verified.")

    def test_i_hall_delete_soft_removes_from_active_list(self):
        """Deleting a hall deactivates it so it no longer appears in selectors."""
        self.login()
        delete_res = self.client.post('/admin/attendance/api/halls/delete', json={
            'exam_hall_id': self.hall.id,
        })
        payload = delete_res.get_json()
        self.assertTrue(payload['success'])
        db.session.expire_all()
        self.assertFalse(db.session.get(ExamHall, self.hall.id).is_active)

        halls = self.client.get(
            f'/admin/attendance/api/halls?academic_year_id={self.year.id}&exam_type_id={self.exam_type.id}'
        ).get_json()
        self.assertNotIn(self.hall.id, [hall['id'] for hall in halls['halls']])
        print("[PASS] Test (i): Hall delete lifecycle verified.")

    def test_j_attendance_polish_assets_are_present(self):
        """Round 3 polish CSS/markup is present without changing data flow."""
        with self.app.open_resource('templates/admin/attendance.html') as f:
            attendance_tmpl = f.read().decode('utf-8')
        with self.app.open_resource('templates/admin/hall_roster.html') as f:
            roster_tmpl = f.read().decode('utf-8')

        self.assertIn('tally-present', attendance_tmpl)
        self.assertIn('fa-circle-check', attendance_tmpl)
        self.assertIn('sclass', attendance_tmpl)
        self.assertIn('pool-select level', roster_tmpl)
        self.assertIn('deleteHallModal', roster_tmpl)
        self.assertIn('btnDeleteHall', roster_tmpl)
        self.assertIn('sortMode', attendance_tmpl)
        self.assertIn('letterFilter', attendance_tmpl)
        self.assertIn('status-icon', attendance_tmpl)
        self.assertIn('--card-accent:#2563EB', attendance_tmpl)
        print("[PASS] Test (j): Round 3 UI polish markers verified.")

    def test_k_all_six_attendance_statuses_and_switching(self):
        """All six attendance statuses (Joogto, Maqan, La fasaxay, Cudur daar, Xaalad degdeg, Soo daahid) work and switch cleanly."""
        self.login()

        # Enroll student1 in hall
        enroll = ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id)
        db.session.add(enroll)
        db.session.commit()

        statuses_to_test = ["present", "absent", "excused", "sick", "emergency", "late"]

        for st in statuses_to_test:
            res = self.client.post('/admin/attendance/api/mark-status', json={
                'student_id': self.student1.id,
                'exam_hall_id': self.hall.id,
                'subject_id': self.subject.id,
                'academic_year_id': self.year.id,
                'exam_id': self.exam.id,
                'status': st
            })
            data = res.get_json()
            self.assertTrue(data['success'], f"Failed saving status '{st}'")
            self.assertEqual(data['status'], st)

            # Verify persistent retrieval
            fetch_res = self.client.get(
                f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
                f'&exam_id={self.exam.id}&exam_hall_id={self.hall.id}&subject_id={self.subject.id}'
            ).get_json()
            self.assertTrue(fetch_res['success'])
            self.assertEqual(fetch_res['students'][0]['status'], st)
            self.assertEqual(fetch_res['tallies'][st], 1)
            record = AttendanceRecord.query.filter_by(
                student_id=self.student1.id,
                exam_hall_id=self.hall.id,
                subject_id=self.subject.id,
            ).first()
            self.assertEqual(record.class_id, self.student1.class_id)

        print("[PASS] Test (k): All six attendance statuses and switching verified.")

    def test_l_legacy_record_null_hall_single_and_bulk_mark(self):
        """Test single-click and bulk-mark when students have pre-existing legacy attendance rows with exam_hall_id IS NULL."""
        self.login()

        # Enroll student1 and student2 in hall
        db.session.add_all([
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id),
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student2.id),
        ])
        db.session.commit()

        # Pre-seed legacy attendance records with exam_hall_id = NULL
        legacy_rec1 = AttendanceRecord(
            student_id=self.student1.id,
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
            subject_id=self.subject.id,
            exam_hall_id=None,
            status="absent",
        )
        legacy_rec2 = AttendanceRecord(
            student_id=self.student2.id,
            academic_year_id=self.year.id,
            exam_id=self.exam.id,
            subject_id=self.subject.id,
            exam_hall_id=None,
            status="sick",
        )
        db.session.add_all([legacy_rec1, legacy_rec2])
        db.session.commit()

        # Test single status click (Joogto / present) on student1
        res1 = self.client.post('/admin/attendance/api/mark-status', json={
            'student_id': self.student1.id,
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_id': self.exam.id,
            'status': 'present'
        })
        data1 = res1.get_json()
        self.assertTrue(data1['success'], f"Single status mark failed with error: {data1.get('error')}")
        self.assertEqual(data1['status'], 'present')

        # Test bulk action ("Calaamadi dhammaan: Joogto")
        res_bulk = self.client.post('/admin/attendance/api/mark-bulk', json={
            'exam_hall_id': self.hall.id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_id': self.exam.id,
            'status': 'present'
        })
        data_bulk = res_bulk.get_json()
        self.assertTrue(data_bulk['success'], f"Bulk mark action failed with error: {data_bulk.get('error')}")
        self.assertEqual(data_bulk['updated_count'], 2)

        # Verify persistent state after single and bulk mark
        fetch_res = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_id={self.exam.id}&exam_hall_id={self.hall.id}&subject_id={self.subject.id}'
        ).get_json()
        self.assertTrue(fetch_res['success'])
        for s in fetch_res['students']:
            self.assertEqual(s['status'], 'present')
        self.assertEqual(fetch_res['tallies']['present'], 2)

        print("[PASS] Test (l): Legacy records with exam_hall_id IS NULL single and bulk mark verified.")

    def _create_schedule_session(self, subject_ids, when=date(2026, 8, 12)):
        """Create a legacy-compatible timetable session through the live API."""
        created = self.client.post('/admin/attendance/api/sessions', json={
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'date': when.isoformat(),
            'time': '08:00',
            'sitting_label': 'Fadhi 1aad',
        }).get_json()
        self.assertTrue(created['success'], created)
        session_id = created['session']['id']
        saved = self.client.put(
            f'/admin/attendance/api/sessions/{session_id}/subjects',
            json={'assignments': subject_ids},
        ).get_json()
        self.assertTrue(saved['success'], saved)
        return session_id

    def _add_primary_student_and_subject(self):
        primary_level = AcademicLevel(name='Primary Schedule Test', sort_order=2)
        db.session.add(primary_level)
        db.session.flush()
        primary_class = AcademicClass(
            academic_level_id=primary_level.id,
            name='Grade Five Schedule Test',
        )
        db.session.add(primary_class)
        db.session.flush()
        primary_subject = Subject(
            name='Saynis Schedule Test',
            academic_level_id=primary_level.id,
        )
        primary_student = Student(
            student_code=f'TIS-PRIMARY-{self.student1.id}',
            full_name='Bilan Cabdi',
            academic_year_id=self.year.id,
            academic_level_id=primary_level.id,
            academic_class_id=primary_class.id,
            class_id=self.school_class.id,
            is_active=True,
        )
        db.session.add_all([primary_subject, primary_student])
        db.session.flush()
        return primary_level, primary_subject, primary_student

    def test_m_timetable_session_persists_and_filters_students_by_level(self):
        """Sessions persist and Attendance only exposes the matching level subjects."""
        self.login()
        primary_level, primary_subject, primary_student = self._add_primary_student_and_subject()
        db.session.add_all([
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id),
            ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=primary_student.id),
        ])
        db.session.commit()

        session_id = self._create_schedule_session([
            {'level_id': self.level_sec.id, 'subject_id': self.subject.id},
            {'level_id': primary_level.id, 'subject_id': primary_subject.id},
        ])

        sessions = self.client.get(
            f'/admin/attendance/api/sessions?academic_year_id={self.year.id}'
            f'&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}'
        ).get_json()
        self.assertTrue(sessions['success'])
        self.assertIn(session_id, [item['id'] for item in sessions['sessions']])

        roster = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}'
            f'&exam_session_id={session_id}'
        ).get_json()
        self.assertTrue(roster['success'], roster)
        student_slots = {
            student['id']: {slot['subject_id'] for slot in student['slots']}
            for group in roster['groups']
            for student in group['students']
        }
        self.assertEqual(student_slots[self.student1.id], {self.subject.id})
        self.assertEqual(student_slots[primary_student.id], {primary_subject.id})
        print("[PASS] Test (m): Timetable session persistence + strictly level-specific roster verified.")

    def test_n_delete_session_cascades_subject_assignments(self):
        """The confirmation-backed delete endpoint removes the session and its assignments."""
        self.login()
        session_id = self._create_schedule_session([
            {'level_id': self.level_sec.id, 'subject_id': self.subject.id},
        ])
        self.assertEqual(ExamSessionSubject.query.filter_by(exam_session_id=session_id).count(), 1)
        deleted = self.client.delete(f'/admin/attendance/api/sessions/{session_id}').get_json()
        self.assertTrue(deleted['success'], deleted)
        self.assertIsNone(db.session.get(ExamSession, session_id))
        self.assertEqual(ExamSessionSubject.query.filter_by(exam_session_id=session_id).count(), 0)
        with self.app.open_resource('templates/admin/exam_timetable.html') as handle:
            self.assertIn('deleteModal', handle.read().decode('utf-8'))
        print("[PASS] Test (n): Timetable delete cascade + confirmation UI marker verified.")

    def test_n1_schedule_rejects_subject_already_assigned_in_exam_scope(self):
        """A subject cannot be scheduled twice for one year/exam/level scope."""
        self.login()
        first_session_id = self._create_schedule_session([
            {'level_id': self.level_sec.id, 'subject_id': self.subject.id},
        ])
        self.assertTrue(first_session_id)
        second = self.client.post('/admin/attendance/api/sessions', json={
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'date': date(2026, 8, 13).isoformat(),
            'sitting_label': 'Fadhi 2aad',
        }).get_json()
        self.assertTrue(second['success'], second)
        saved = self.client.put(
            f"/admin/attendance/api/sessions/{second['session']['id']}/subjects",
            json={'assignments': [{'level_id': self.level_sec.id, 'subject_id': self.subject.id}]},
        )
        self.assertEqual(saved.status_code, 409)
        self.assertFalse(saved.get_json()['success'])

    def test_n2_database_scope_constraint_rejects_concurrent_duplicate_subject(self):
        """The database itself protects a schedule if an API pre-check is bypassed."""
        first = ExamSession(
            academic_year_id=self.year.id,
            exam_type_id=self.exam_type.id,
            session_date=date(2026, 8, 12),
            sitting_label='Concurrent Fadhi 1',
        )
        second = ExamSession(
            academic_year_id=self.year.id,
            exam_type_id=self.exam_type.id,
            session_date=date(2026, 8, 13),
            sitting_label='Concurrent Fadhi 2',
        )
        db.session.add_all([first, second])
        db.session.flush()
        scope_key = scheduled_subject_scope_key(self.year.id, None, self.exam_type.id)
        db.session.add(ExamSessionSubject(
            exam_session_id=first.id,
            academic_level_id=self.level_sec.id,
            subject_id=self.subject.id,
            exam_scope_key=scope_key,
        ))
        db.session.commit()
        db.session.add(ExamSessionSubject(
            exam_session_id=second.id,
            academic_level_id=self.level_sec.id,
            subject_id=self.subject.id,
            exam_scope_key=scope_key,
        ))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_o_attendance_status_is_isolated_per_subject_slot(self):
        """Changing one subject status cannot change another subject for the same student/session."""
        self.login()
        second_subject = Subject(
            name='Mathematics Schedule Test',
            academic_level_id=self.level_sec.id,
        )
        db.session.add(second_subject)
        db.session.flush()
        db.session.add(ExamHallEnrollment(exam_hall_id=self.hall.id, student_id=self.student1.id))
        db.session.commit()
        session_id = self._create_schedule_session([
            {'level_id': self.level_sec.id, 'subject_id': self.subject.id},
            {'level_id': self.level_sec.id, 'subject_id': second_subject.id},
        ])
        saved = self.client.post('/admin/attendance/api/mark-status', json={
            'student_id': self.student1.id,
            'exam_hall_id': self.hall.id,
            'exam_session_id': session_id,
            'subject_id': self.subject.id,
            'academic_year_id': self.year.id,
            'exam_type_id': self.exam_type.id,
            'status': 'absent',
        }).get_json()
        self.assertTrue(saved['success'], saved)
        roster = self.client.get(
            f'/admin/attendance/api/attendance-data?academic_year_id={self.year.id}'
            f'&exam_type_id={self.exam_type.id}&exam_hall_id={self.hall.id}'
            f'&exam_session_id={session_id}'
        ).get_json()
        slots = next(
            student['slots'] for group in roster['groups'] for student in group['students']
            if student['id'] == self.student1.id
        )
        statuses = {slot['subject_id']: slot['status'] for slot in slots}
        self.assertEqual(statuses[self.subject.id], 'absent')
        self.assertIsNone(statuses[second_subject.id])
        print("[PASS] Test (o): One student x subject slot cannot affect another subject slot.")


if __name__ == '__main__':
    unittest.main()
