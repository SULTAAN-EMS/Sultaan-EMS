"""Tests for administrator-owned club teams and leader roster isolation."""

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app import create_app, db
from app.models import (
    AcademicYear, AcademicYearClass, AcademicYearLevel, ClubTeam,
    ClubTeamLeaderAssignment, ClubTeamMembership, Student, StudentClub,
    StudentEnrollment, Teacher, User,
)
from migrations import phase_12_club_leader_teams


class TestClubLeaderPortal(unittest.TestCase):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "club-portal-test"
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

        self.year = AcademicYear(name="Club Test Year", is_current=True)
        self.level_a = AcademicYearLevel(name="Primary", academic_year=self.year)
        self.level_b = AcademicYearLevel(name="Secondary", academic_year=self.year)
        self.class_a = AcademicYearClass(name="P1A", academic_year_level=self.level_a)
        self.class_b = AcademicYearClass(name="S1A", academic_year_level=self.level_b)
        db.session.add_all([self.year, self.level_a, self.level_b, self.class_a, self.class_b])
        db.session.flush()

        self.enrollments = []
        for idx, (level, school_class, name) in enumerate((
            (self.level_a, self.class_a, "Primary Club Student"),
            (self.level_b, self.class_b, "Secondary Club Student"),
            (self.level_a, self.class_a, "Unassigned Student"),
        ), start=1):
            student = Student(student_code=f"CLUB-{idx}", full_name=name, is_active=True)
            db.session.add(student)
            db.session.flush()
            enrollment = StudentEnrollment(
                student=student, academic_year=self.year, academic_year_level=level,
                academic_year_class=school_class, status="active", academic_outcome="pending",
                enrollment_source="manual",
            )
            db.session.add(enrollment)
            self.enrollments.append(enrollment)

        self.admin = User(username="club-admin", full_name="Club Admin", role="admin", is_active=True)
        self.admin.set_password("ClubAdmin123")
        self.admin.set_permissions(["teachers"])
        self.leader_user = User(username="club-leader", full_name="Club Leader", role="staff", is_active=True)
        self.leader_user.set_password("ClubLeader123")
        self.second_leader_user = User(username="club-leader-2", full_name="Second Leader", role="staff", is_active=True)
        self.second_leader_user.set_password("ClubLeader223")
        db.session.add_all([self.admin, self.leader_user, self.second_leader_user])
        db.session.flush()
        self.leader = Teacher(
            teacher_id="CLUB-T1", teacher_code="CLUB-C1", full_name="Club Leader",
            employment_status="Active", is_active=True, user_id=self.leader_user.id,
        )
        self.second_leader = Teacher(
            teacher_id="CLUB-T2", teacher_code="CLUB-C2", full_name="Second Leader",
            employment_status="Active", is_active=True, user_id=self.second_leader_user.id,
        )
        self.club = StudentClub(name="Science Club", is_active=True)
        self.team = ClubTeam(name="Mixed Team", academic_year=self.year)
        db.session.add_all([self.leader, self.second_leader, self.club, self.team])
        db.session.flush()
        db.session.add_all([
            ClubTeamMembership(team=self.team, enrollment=self.enrollments[0], start_date=date.today(), assigned_by=self.admin),
            ClubTeamMembership(team=self.team, enrollment=self.enrollments[1], start_date=date.today(), assigned_by=self.admin),
            ClubTeamLeaderAssignment(team=self.team, club=self.club, teacher=self.leader, start_date=date.today(), assigned_by=self.admin),
        ])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _login_teacher(self, username, password):
        return self.client.post("/teacher/login", data={"username": username, "password": password}, follow_redirects=True)

    def test_teacher_workspace_navigation_is_result_entry_only(self):
        response = self._login_teacher("club-leader", "ClubLeader123")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Result Entry", response.data)
        self.assertNotIn(b"Naadigayga", response.data)
        self.assertNotIn(b"<span>Dashboard</span>", response.data)
        hidden_page = self.client.get("/teacher/clubs", follow_redirects=True)
        self.assertEqual(hidden_page.status_code, 200)
        self.assertIn(b"Science Club", hidden_page.data)
        self.assertNotIn(b'href="/teacher/clubs"', hidden_page.data)

    def test_admin_team_page_supports_roster_and_dated_leader_transfer(self):
        self.client.post("/admin/login", data={"username": "club-admin", "password": "ClubAdmin123"})
        response = self.client.get("/admin/teachers/club-teams?team_id=%s" % self.team.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Primary Club Student", response.data)
        self.assertIn(b"Secondary Club Student", response.data)

        transfer_date = date.today() + timedelta(days=1)
        response = self.client.post("/admin/teachers/club-teams", data={
            "operation": "assign_leader", "team_id": str(self.team.id),
            "club_id": str(self.club.id), "teacher_id": str(self.second_leader.id),
            "start_date": transfer_date.isoformat(), "transfer_note": "Scheduled rotation",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClubTeamLeaderAssignment.query.count(), 2)
        old_assignment = ClubTeamLeaderAssignment.query.filter_by(teacher_id=self.leader.id).one()
        self.assertEqual(old_assignment.end_date, transfer_date)
        new_assignment = ClubTeamLeaderAssignment.query.filter_by(teacher_id=self.second_leader.id).one()
        self.assertEqual(new_assignment.start_date, transfer_date)

    def test_student_transfer_preserves_previous_team_membership(self):
        self.client.post("/admin/login", data={"username": "club-admin", "password": "ClubAdmin123"})
        next_team = ClubTeam(name="Next Team", academic_year=self.year)
        db.session.add(next_team)
        db.session.commit()
        transfer_date = date.today() + timedelta(days=1)

        response = self.client.post("/admin/teachers/club-teams", data={
            "operation": "add_member", "team_id": str(next_team.id),
            "enrollment_id": str(self.enrollments[0].id), "start_date": transfer_date.isoformat(),
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        memberships = ClubTeamMembership.query.filter_by(enrollment_id=self.enrollments[0].id).order_by(ClubTeamMembership.start_date).all()
        self.assertEqual(len(memberships), 2)
        self.assertEqual(memberships[0].end_date, transfer_date)
        self.assertEqual(memberships[1].team_id, next_team.id)
        self.assertIsNone(memberships[1].end_date)

    def test_migration_is_repeatable_and_seeds_nine_default_clubs(self):
        before = StudentClub.query.count()
        with patch.object(phase_12_club_leader_teams, "create_app", return_value=self.app):
            self.assertEqual(phase_12_club_leader_teams.upgrade()["status"], "applied")
            phase_12_club_leader_teams.upgrade()
        self.assertEqual(StudentClub.query.count(), before + 9)


if __name__ == "__main__":
    unittest.main()
