import unittest

from sqlalchemy import create_engine, inspect, text

from migrations.phase_32_seat_mixer_attendance_sync import COLUMNS, ensure_columns


class SeatMixerAttendanceMigrationTests(unittest.TestCase):
    def test_adds_nullable_snapshot_columns_idempotently_without_changing_roster(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE exam_halls (id INTEGER PRIMARY KEY, name VARCHAR(100))"
            ))
            connection.execute(text(
                "CREATE TABLE exam_hall_enrollments ("
                "id INTEGER PRIMARY KEY, exam_hall_id INTEGER NOT NULL, student_id INTEGER NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO exam_halls (id, name) VALUES (1, 'Hall A')"
            ))
            connection.execute(text(
                "INSERT INTO exam_hall_enrollments (id, exam_hall_id, student_id) "
                "VALUES (1, 1, 42)"
            ))

            ensure_columns(connection)
            ensure_columns(connection)

            for table, names in COLUMNS.items():
                existing = {column["name"] for column in inspect(connection).get_columns(table)}
                self.assertTrue(set(names).issubset(existing))
                for name in names:
                    column = next(
                        item for item in inspect(connection).get_columns(table)
                        if item["name"] == name
                    )
                    self.assertTrue(column["nullable"])

            row = connection.execute(text(
                "SELECT exam_hall_id, student_id FROM exam_hall_enrollments WHERE id = 1"
            )).one()
            self.assertEqual(tuple(row), (1, 42))


if __name__ == "__main__":
    unittest.main()
