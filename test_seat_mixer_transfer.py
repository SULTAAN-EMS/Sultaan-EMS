import json
import unittest

from app import create_app, db
from app.models import (
    AcademicClass,
    AcademicLevel,
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    Exam,
    ExamHall,
    ExamHallVersion,
    SeatMixerAssignment,
    SeatMixerSaveSnapshot,
    Setting,
    Student,
    StudentEnrollment,
    User,
)
from app.routes_seat_mixer import (
    api_restore_history_snapshot,
    api_save,
    get_school_name,
    set_current_snapshot_id,
)


class SeatMixerTransferTests(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "seat-mixer-transfer-test"
        WTF_CSRF_ENABLED = False
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.year = AcademicYear(name="2026-2027", is_current=True)
        self.level = AcademicLevel(name="Dugsi Sare")
        db.session.add_all([self.year, self.level])
        db.session.flush()
        self.year_level = AcademicYearLevel(
            academic_year=self.year,
            legacy_level=self.level,
            name="Dugsi Sare",
        )
        self.academic_class = AcademicClass(
            academic_level=self.level,
            name="Form 1",
        )
        db.session.add_all([self.year_level, self.academic_class])
        db.session.flush()
        self.year_class = AcademicYearClass(
            academic_year_level=self.year_level,
            legacy_class=self.academic_class,
            name="Form 1",
        )
        self.exam = Exam(
            name="Imtixaan Bileedka 1aad",
            academic_year=self.year,
            is_active=True,
        )
        self.student = Student(
            student_code="SM-001",
            full_name="Arday Tijaabo",
            is_active=True,
        )
        db.session.add_all([self.year_class, self.exam, self.student])
        db.session.flush()
        db.session.add(StudentEnrollment(
            student=self.student,
            academic_year=self.year,
            academic_year_level=self.year_level,
            academic_year_class=self.year_class,
            status="active",
            academic_outcome="pending",
            enrollment_source="manual",
        ))

        self.source_hall = ExamHall(
            name="Hall Hore",
            code="SM-OLD",
            academic_year=self.year,
            exam=self.exam,
        )
        self.target_hall = ExamHall(
            name="Hall Cusub",
            code="SM-NEW",
            academic_year=self.year,
            exam=self.exam,
        )
        db.session.add_all([self.source_hall, self.target_hall])
        db.session.flush()
        self.source_version = ExamHallVersion(
            hall=self.source_hall,
            version_number=1,
            label="Nooca A",
        )
        self.target_version = ExamHallVersion(
            hall=self.target_hall,
            version_number=1,
            label="Nooca B",
        )
        db.session.add_all([self.source_version, self.target_version])
        db.session.flush()

        self.source_assignments = [{
            "student_id": self.student.id,
            "row": 0,
            "table": 0,
            "seat": 0,
        }]
        old_snapshot = SeatMixerSaveSnapshot(
            version_id=self.source_version.id,
            snapshot_json=json.dumps({
                "config": {"rows": 1, "tablesPerRow": 1, "seatsPerTable": 1},
                "assignments": self.source_assignments,
                "selected_students": {str(self.academic_class.id): [self.student.id]},
                "last_meta": "Initial plan",
                "academic_year_id": self.year.id,
                "exam_id": self.exam.id,
            }),
            integrity_score=100,
            near_adjacency_count=0,
            placed_count=1,
        )
        db.session.add_all([
            old_snapshot,
            SeatMixerAssignment(
                version=self.source_version,
                student=self.student,
                row_number=0,
                table_number=0,
                seat_number=0,
                rows_config=1,
                tables_per_row_config=1,
                seats_per_table_config=1,
            ),
        ])
        db.session.flush()
        self.old_snapshot = old_snapshot
        set_current_snapshot_id(self.source_version.id, old_snapshot.id)
        self.admin = User(
            username="seat-mixer-transfer-admin",
            full_name="Seat Mixer Admin",
            role="super_admin",
            is_active=True,
        )
        self.admin.set_password("SeatMixerTest123!")
        db.session.add(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _payload(self, transfer_ids=None):
        payload = {
            "version_id": self.target_version.id,
            "assignments": [{
                "student_id": self.student.id,
                "row": 0,
                "table": 0,
                "seat": 0,
            }],
            "config": {"rows": 1, "tablesPerRow": 1, "seatsPerTable": 1},
            "selected_students": {str(self.academic_class.id): [self.student.id]},
            "academic_year_id": self.year.id,
            "exam_id": self.exam.id,
        }
        if transfer_ids is not None:
            payload["transfer_student_ids"] = transfer_ids
        return payload

    def _call_save(self, payload):
        with self.app.test_request_context(
            "/admin/seat-mixer/api/save",
            method="POST",
            json=payload,
        ):
            result = api_save()
        return result if isinstance(result, tuple) else (result, 200)

    def test_requires_confirmation_then_moves_active_assignment_once(self):
        response, status = self._call_save(self._payload())
        self.assertEqual(status, 409)
        self.assertEqual(response.get_json()["conflicts"][0]["hall_name"], "Hall Hore")
        self.assertEqual(
            SeatMixerAssignment.query.filter_by(version_id=self.source_version.id).count(),
            1,
        )
        self.assertEqual(
            SeatMixerAssignment.query.filter_by(version_id=self.target_version.id).count(),
            0,
        )

        response, status = self._call_save(self._payload([self.student.id]))
        self.assertEqual(status, 200, response.get_json())
        self.assertEqual(
            SeatMixerAssignment.query.filter_by(version_id=self.source_version.id).count(),
            0,
        )
        self.assertEqual(
            SeatMixerAssignment.query.filter_by(version_id=self.target_version.id).count(),
            1,
        )
        self.assertEqual(
            SeatMixerSaveSnapshot.query.filter_by(version_id=self.source_version.id).count(),
            2,
        )

        with self.app.test_request_context(
            f"/admin/seat-mixer/api/version/{self.source_version.id}/history/{self.old_snapshot.id}/restore",
            method="POST",
        ):
            restored, restore_status = api_restore_history_snapshot(
                self.source_version.id,
                self.old_snapshot.id,
            )
        self.assertEqual(restore_status, 409)
        self.assertIn("hall kale", restored.get_json()["error"])

    def test_main_seat_mixer_page_contains_transfer_dialog(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin.id)
            session["_fresh"] = True

        response = client.get("/admin/seat-mixer/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="smTransferOverlay"', response.data)
        self.assertIn("FARIIN MUHIIM AH".encode(), response.data)
        self.assertIn("Halakan usoo wareeji".encode(), response.data)
        self.assertIn(b"student-transfer-design-20261015-1", response.data)
        self.assertIn(b"student-table-seats-inline-20261015-1", response.data)

    def test_school_name_prefers_somali_setting_over_dashboard_title(self):
        db.session.add_all([
            Setting(key="dashboard_title", value="School Result Management"),
            Setting(key="school_name", value="Taysir Schools"),
            Setting(key="school_name_so", value="Dugsiyada Caalamiga ah ee Taysiir"),
        ])
        db.session.flush()

        self.assertEqual(get_school_name(), "Dugsiyada Caalamiga ah ee Taysiir")
        self.assertEqual(get_school_name().upper(), "DUGSIYADA CAALAMIGA AH EE TAYSIIR")


if __name__ == "__main__":
    unittest.main()
