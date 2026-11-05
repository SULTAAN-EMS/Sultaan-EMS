import unittest

from sqlalchemy import event

from app import create_app, db
from app.models import Student, StudentCodeAlias
from app.student_identity import find_student_by_code


class TestStudentIdentityLookup(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "student-identity-lookup-test"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        AUTO_INIT_DB = False

    def setUp(self):
        self.app = create_app(self.TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.student = Student(student_code="TIS-CASE-01", full_name="Lookup Test")
        db.session.add(self.student)
        db.session.flush()
        db.session.add(StudentCodeAlias(
            student_id=self.student.id,
            old_code="TIS-OLD-01",
            new_code=self.student.student_code,
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def test_canonical_id_uses_one_indexable_lookup(self):
        statements = []

        def record_select(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record_select)
        try:
            found = find_student_by_code("TIS-CASE-01")
        finally:
            event.remove(db.engine, "before_cursor_execute", record_select)

        self.assertEqual(found.id, self.student.id)
        self.assertEqual(len(statements), 1)
        self.assertIn("student_code", statements[0])

    def test_lookup_remains_case_and_whitespace_tolerant(self):
        self.assertEqual(find_student_by_code("  tis-case-01  ").id, self.student.id)
        self.assertEqual(find_student_by_code("  tis-old-01  ").id, self.student.id)


if __name__ == "__main__":
    unittest.main()
