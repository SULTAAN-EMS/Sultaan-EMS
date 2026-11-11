import unittest

from sqlalchemy import create_engine, inspect, text

from migrations.phase_33_id_card_validity_window import COLUMNS, ensure_columns


class IdCardValidityMigrationTests(unittest.TestCase):
    def test_adds_nullable_validity_window_columns_idempotently(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE id_card_issues (id INTEGER PRIMARY KEY, issue_date DATE, expiry_date DATE)"
            ))
            connection.execute(text(
                "INSERT INTO id_card_issues (id, issue_date, expiry_date) "
                "VALUES (1, '2026-11-10', '2027-11-10')"
            ))

            ensure_columns(connection)
            ensure_columns(connection)

            columns = inspect(connection).get_columns("id_card_issues")
            for name in COLUMNS:
                column = next(item for item in columns if item["name"] == name)
                self.assertTrue(column["nullable"])
            existing = connection.execute(text(
                "SELECT issue_date, expiry_date FROM id_card_issues WHERE id = 1"
            )).one()
            self.assertEqual(tuple(existing), ("2026-11-10", "2027-11-10"))


if __name__ == "__main__":
    unittest.main()
