"""Coverage for Academic-Year-Level multi-selection on Behavior configurations."""

import unittest

from app import db
from app.behavior_service import (
    BehaviorValidationError,
    configuration_applies_to_level,
    validate_enrollment_scope,
)
from app.models import (
    AcademicYearClass,
    AcademicYearLevel,
    AcademicYearSubject,
    BehaviorConfigurationLevel,
    Student,
    StudentEnrollment,
)
from test_phase2d_behavior_ux import TestPhase2DBehaviorUX


class TestPhase3BehaviorConfigurationLevels(TestPhase2DBehaviorUX):
    def setUp(self):
        super().setUp()
        self.admin.set_permissions([
            "behavior.view", "behavior.record", "behavior.edit", "behavior.void",
            "behavior.configure", "behavior.audit",
        ])
        db.session.commit()

        self.secondary = AcademicYearLevel(
            academic_year_id=self.year_one.id,
            name="Form Two",
            sort_order=2,
            is_active=True,
        )
        self.secondary_class = AcademicYearClass(
            academic_year_level=self.secondary,
            name="2A",
            is_active=True,
        )
        db.session.add_all([self.secondary, self.secondary_class])
        db.session.flush()
        self.secondary_student = Student(
            student_code="BHV-C-003",
            full_name="Behavior Secondary",
            is_active=True,
        )
        db.session.add(self.secondary_student)
        db.session.flush()
        self.secondary_enrollment = StudentEnrollment(
            student_id=self.secondary_student.id,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.secondary.id,
            academic_year_class_id=self.secondary_class.id,
            status="active",
            academic_outcome="pending",
            enrollment_source="manual",
        )
        db.session.add(self.secondary_enrollment)
        db.session.commit()

    def test_selected_levels_are_saved_on_the_existing_configuration(self):
        response = self._client_as_admin().post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": [str(self.level_one.id), str(self.secondary.id)],
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 302)
        ids = {
            item.academic_year_level_id
            for item in BehaviorConfigurationLevel.query.filter_by(
                behavior_configuration_id=self.config_one.id,
            ).all()
        }
        self.assertEqual(ids, {self.level_one.id, self.secondary.id})
        self.assertTrue(configuration_applies_to_level(self.config_one, self.secondary.id))
        self.assertEqual(validate_enrollment_scope(self.config_one, self.secondary_enrollment.id).id, self.secondary_enrollment.id)

    def test_all_levels_uses_only_the_selected_academic_year(self):
        response = self._client_as_admin().post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_level_mode": "all",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 302)
        ids = {
            item.academic_year_level_id
            for item in BehaviorConfigurationLevel.query.filter_by(
                behavior_configuration_id=self.config_one.id,
            ).all()
        }
        self.assertEqual(ids, {self.level_one.id, self.secondary.id})
        self.assertNotIn(self.level_two.id, ids)

    def test_other_year_level_is_rejected_server_side(self):
        response = self._client_as_admin().post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": [str(self.level_one.id), str(self.level_two.id)],
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("must belong to the selected Academic Year", response.get_data(as_text=True))
        self.assertFalse(configuration_applies_to_level(self.config_one, self.level_two.id))

    def test_existing_single_level_fallback_remains_valid(self):
        self.assertTrue(configuration_applies_to_level(self.config_one, self.level_one.id))
        with self.assertRaises(BehaviorValidationError):
            validate_enrollment_scope(self.config_one, self.secondary_enrollment.id)

    def test_selected_levels_can_replace_the_legacy_anchor(self):
        response = self._client_as_admin().post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": [str(self.secondary.id)],
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(configuration_applies_to_level(self.config_one, self.level_one.id))
        self.assertTrue(configuration_applies_to_level(self.config_one, self.secondary.id))
        self.assertEqual(
            validate_enrollment_scope(self.config_one, self.secondary_enrollment.id).id,
            self.secondary_enrollment.id,
        )

    def test_explicit_secondary_scope_is_not_reset_to_primary(self):
        client = self._client_as_admin()
        response = client.post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": [str(self.level_one.id), str(self.secondary.id)],
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 302)

        response = client.get(
            "/admin/behavior/students"
            f"?year_id={self.year_one.id}&level_id={self.secondary.id}"
            f"&session_id={self.session_a.id}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(self.secondary_student.full_name, body)
        self.assertNotIn(self.student_one.full_name, body)

    def test_configuration_persists_three_academic_year_levels(self):
        third = AcademicYearLevel(
            academic_year_id=self.year_one.id,
            name="Form Three",
            sort_order=3,
            is_active=True,
        )
        db.session.add(third)
        db.session.flush()
        client = self._client_as_admin()
        response = client.post(
            "/admin/behavior/configuration",
            data={
                "config_id": str(self.config_one.id),
                "academic_year_id": str(self.year_one.id),
                "academic_year_level_ids": [
                    str(self.level_one.id),
                    str(self.secondary.id),
                    str(third.id),
                ],
                "academic_level_mode": "selected",
                "academic_year_subject_id": str(self.subject_one.id),
                "frequency": "monthly",
            },
        )
        self.assertEqual(response.status_code, 302)
        ids = {
            item.academic_year_level_id
            for item in BehaviorConfigurationLevel.query.filter_by(
                behavior_configuration_id=self.config_one.id,
            ).all()
        }
        self.assertEqual(ids, {self.level_one.id, self.secondary.id, third.id})
        page = client.get(f"/admin/behavior/configuration?config_id={self.config_one.id}")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn(
            f"{self.level_one.name}, {self.secondary.name}, {third.name}",
            body,
        )


if __name__ == "__main__":
    unittest.main()
