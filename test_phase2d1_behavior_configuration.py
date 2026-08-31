"""Phase 2D.1 coverage for authoritative Behavior configuration rules."""

import unittest
from decimal import Decimal

from app import db
from app.behavior_service import (
    BehaviorValidationError,
    calculate_session_score,
    validate_configuration_ready,
)
from app.models import (
    AcademicYearSubject,
    BehaviorAction,
    BehaviorCategory,
    BehaviorConfiguration,
    BehaviorSession,
    Exam,
    ExamType,
)
from test_phase2d_behavior_ux import TestPhase2DBehaviorUX


class TestPhase2D1BehaviorConfiguration(TestPhase2DBehaviorUX):
    def _new_configuration(self, suffix, status="draft"):
        subject = AcademicYearSubject(
            name=f"Dabeecad {suffix}",
            subject_kind="behavior",
            max_score=0,
            sort_order=10,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
        )
        db.session.add(subject)
        db.session.flush()
        config = BehaviorConfiguration(
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
            academic_year_subject_id=subject.id,
            frequency="monthly",
            status=status,
        )
        db.session.add(config)
        db.session.flush()
        return config

    def _add_sessions(self, config, maxima, prefix):
        sessions = []
        for index, maximum in enumerate(maxima, start=1):
            exam = ExamType(
                academic_year_id=self.year_one.id,
                name=f"{prefix} Exam {index}",
                sort_order=index,
                is_active=True,
            )
            session = BehaviorSession(
                configuration=config,
                exam_type=exam,
                session_label=f"{prefix} Session {index}",
                maximum_score=maximum,
                sort_order=index,
                is_active=True,
            )
            db.session.add_all([exam, session])
            sessions.append(session)
        db.session.flush()
        return sessions

    def _add_activation_dependencies(self, config, prefix):
        category = BehaviorCategory(
            configuration=config,
            name=f"Positive {prefix}",
            polarity="positive",
            is_active=True,
        )
        action = BehaviorAction(
            category=category,
            name=f"Action {prefix}",
            level_number=1,
            points=1,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([category, action])
        db.session.flush()

    def test_configuration_page_has_a_dedicated_behavior_subject_creator(self):
        client = self._client_as_admin()
        response = client.get(
            f"/admin/behavior/configuration?year_id={self.year_one.id}&level_id={self.level_one.id}"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Behavior Subject Setup", body)
        self.assertIn("Create Behavior Subject", body)
        self.assertIn("/admin/behavior/subjects", body)
        self.assertNotIn("Dedicated subject setup", body)

    def test_configuration_page_lists_only_same_year_exam_types_for_sessions(self):
        config = self._new_configuration("Exam Type Scope")
        same_year_exam = ExamType(
            academic_year_id=self.year_one.id,
            name="Same Year Behavior Exam",
            sort_order=1,
            is_active=True,
        )
        other_year_exam = ExamType(
            academic_year_id=self.year_two.id,
            name="Other Year Behavior Exam",
            sort_order=1,
            is_active=True,
        )
        db.session.add_all([same_year_exam, other_year_exam])
        db.session.commit()

        response = self._client_as_admin().get(
            f"/admin/behavior/configuration?config_id={config.id}"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Exam Types for this configuration", body)
        self.assertIn("Same Year Behavior Exam", body)
        self.assertIn('id="configExam"', body)
        self.assertNotIn("Other Year Behavior Exam", body)

    def test_configuration_exam_type_filter_is_year_scoped(self):
        config = self._new_configuration("Exam Filter")
        same_year_exam = Exam(
            academic_year_id=self.year_one.id,
            name="Same Year Canonical Exam",
            sort_order=1,
            is_active=True,
        )
        other_year_exam = Exam(
            academic_year_id=self.year_two.id,
            name="Other Year Canonical Exam",
            sort_order=1,
            is_active=True,
        )
        db.session.add_all([same_year_exam, other_year_exam])
        db.session.commit()

        body = self._client_as_admin().get(
            f"/admin/behavior/configuration?config_id={config.id}&exam_ref=exam:{same_year_exam.id}"
        ).get_data(as_text=True)
        self.assertIn("Same Year Canonical Exam", body)
        self.assertIn(f'value="exam:{same_year_exam.id}" selected', body)
        self.assertNotIn("Other Year Canonical Exam", body)

    def test_sessions_show_unreferenced_legacy_exam_types_when_canonical_rows_exist(self):
        config = self._new_configuration("Mixed Exam Registries")
        canonical_exam = Exam(
            academic_year_id=self.year_one.id,
            name="Canonical Results Exam",
            sort_order=1,
            is_active=True,
        )
        legacy_exam = ExamType(
            academic_year_id=self.year_one.id,
            name="Legacy Monthly Exam",
            sort_order=2,
            is_active=True,
        )
        other_year_legacy_exam = ExamType(
            academic_year_id=self.year_two.id,
            name="Other Year Monthly Exam",
            sort_order=1,
            is_active=True,
        )
        db.session.add_all([canonical_exam, legacy_exam, other_year_legacy_exam])
        db.session.commit()

        body = self._client_as_admin().get(
            f"/admin/behavior/sessions?config_id={config.id}"
        ).get_data(as_text=True)
        self.assertIn("Canonical Results Exam", body)
        self.assertIn("Legacy Monthly Exam", body)
        self.assertNotIn("Other Year Monthly Exam", body)

        response = self._client_as_admin().post(
            "/admin/behavior/sessions",
            data={
                "csrf_token": "",
                "config_id": str(config.id),
                "exam_ref": f"legacy:{legacy_exam.id}",
                "session_label": "Legacy Monthly Session",
                "maximum_score": "25",
                "sort_order": "2",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        session = BehaviorSession.query.filter_by(
            behavior_configuration_id=config.id
        ).one()
        self.assertEqual(session.exam_type_id, legacy_exam.id)
        self.assertEqual(session.session_label, "Legacy Monthly Session")
        self.assertEqual(session.maximum_score, Decimal("25.000"))

    def test_configuration_link_uses_the_configuration_year(self):
        subject = AcademicYearSubject(
            name="Other Year Conduct",
            subject_kind="behavior",
            max_score=0,
            academic_year_id=self.year_two.id,
            academic_year_level_id=self.level_two.id,
        )
        config = BehaviorConfiguration(
            academic_year_id=self.year_two.id,
            academic_year_level_id=self.level_two.id,
            behavior_subject=subject,
            frequency="monthly",
            status="draft",
        )
        exam = ExamType(
            academic_year_id=self.year_two.id,
            name="Other Year Exam Only",
            sort_order=1,
            is_active=True,
        )
        db.session.add_all([subject, config, exam])
        db.session.commit()

        response = self._client_as_admin().get(
            f"/admin/behavior/configuration?config_id={config.id}"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.year_two.name, body)
        self.assertIn("Other Year Exam Only", body)

    def test_behavior_subject_creator_is_year_level_scoped_and_unbridged(self):
        response = self._client_as_admin().post(
            "/admin/behavior/subjects",
            data={
                "csrf_token": "",
                "academic_year_id": self.year_one.id,
                "academic_year_level_id": self.level_one.id,
                "name": "Conduct created in Behavior Setup",
            },
        )
        self.assertEqual(response.status_code, 302)
        subject = AcademicYearSubject.query.filter_by(
            name="Conduct created in Behavior Setup"
        ).one()
        self.assertEqual(subject.subject_kind, "behavior")
        self.assertEqual(subject.academic_year_id, self.year_one.id)
        self.assertEqual(subject.academic_year_level_id, self.level_one.id)
        self.assertIsNone(subject.legacy_subject_id)
        self.assertEqual(subject.max_score, Decimal("0.000"))

    def test_sessions_remain_editable_after_configuration_is_complete(self):
        config = self._new_configuration("Visible Exam Types")
        # Legacy databases may still store the old lifecycle value; it must not
        # hide the ordinary session form or prevent normal edits.
        config.status = "active"
        session = self._add_sessions(config, [100], "Visible Exam Types")[0]
        db.session.commit()

        response = self._client_as_admin().get(
            f"/admin/behavior/sessions?config_id={config.id}"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="newSessionExam"', body)
        self.assertIn(session.exam_type.name, body)
        self.assertIn("Add session", body)

    def test_sessions_use_the_canonical_results_hub_exam_registry(self):
        config = self._new_configuration("Canonical Exam")
        exam = Exam(
            academic_year_id=self.year_one.id,
            name="Canonical Results Hub Exam",
            sort_order=1,
            is_active=True,
        )
        db.session.add(exam)
        db.session.commit()

        response = self._client_as_admin().post(
            "/admin/behavior/sessions",
            data={
                "csrf_token": "",
                "config_id": str(config.id),
                "exam_ref": f"exam:{exam.id}",
                "session_label": "Canonical Session",
                "maximum_score": "100",
                "sort_order": "1",
                "is_active": "on",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        session = BehaviorSession.query.filter_by(
            behavior_configuration_id=config.id
        ).one()
        self.assertEqual(session.exam_id, exam.id)
        self.assertIsNone(session.exam_type_id)
        self.assertEqual(session.session_label, "Canonical Session")
        self.assertEqual(session.maximum_score, Decimal("100.000"))

        page_response = self._client_as_admin().get(
            f"/admin/behavior/sessions?config_id={config.id}"
        )
        self.assertEqual(page_response.status_code, 200, page_response.location)
        body = page_response.get_data(as_text=True)
        self.assertIn("Canonical Results Hub Exam", body)

    def test_add_session_hides_assigned_exam_and_rejects_duplicate_post(self):
        config = self._new_configuration("Duplicate Guard")
        session = self._add_sessions(config, [40], "Duplicate Guard")[0]
        db.session.commit()

        page_response = self._client_as_admin().get(
            f"/admin/behavior/sessions?config_id={config.id}"
        )
        self.assertEqual(page_response.status_code, 200, page_response.location)
        body = page_response.get_data(as_text=True)
        add_select = body.split('id="newSessionExam"', 1)[1].split("</select>", 1)[0]
        self.assertNotIn(f'value="legacy:{session.exam_type_id}"', add_select)
        self.assertIn(session.exam_type.name, body)

        response = self._client_as_admin().post(
            "/admin/behavior/sessions",
            data={
                "csrf_token": "",
                "config_id": str(config.id),
                "exam_ref": f"legacy:{session.exam_type_id}",
                "session_label": "Duplicate attempt",
                "maximum_score": "60",
                "sort_order": "2",
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("already assigned to this Behavior configuration", response.get_data(as_text=True))
        self.assertEqual(BehaviorSession.query.filter_by(behavior_configuration_id=config.id).count(), 1)

    def test_configuration_rejects_an_examination_subject(self):
        exam_subject = AcademicYearSubject(
            name="Ordinary Examination Subject",
            subject_kind="exam",
            max_score=100,
            academic_year_id=self.year_one.id,
            academic_year_level_id=self.level_one.id,
        )
        db.session.add(exam_subject)
        db.session.commit()

        client = self._client_as_admin()
        response = client.post(
            "/admin/behavior/configuration",
            data={
                "csrf_token": "",
                "academic_year_id": self.year_one.id,
                "academic_year_level_id": self.level_one.id,
                "academic_year_subject_id": exam_subject.id,
                "frequency": "monthly",
                "status": "draft",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("not classified as a Behavior subject", response.get_data(as_text=True))
        self.assertIsNone(
            BehaviorConfiguration.query.filter_by(academic_year_subject_id=exam_subject.id).first()
        )

    def test_subject_setup_persists_behavior_subject_without_subject_maximum(self):
        client = self._client_as_admin()
        with client.session_transaction() as session:
            session["config_center_authenticated"] = True
        response = client.post(
            "/admin/config-center/api/subjects",
            json={
                "name": "Conduct from Subject Setup",
                "academic_year_id": self.year_one.id,
                "academic_year_level_id": self.level_one.id,
                "subject_kind": "behavior",
                "max_score": 999,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        subject = AcademicYearSubject.query.filter_by(name="Conduct from Subject Setup").one()
        self.assertEqual(subject.subject_kind, "behavior")
        self.assertEqual(subject.max_score, Decimal("0.000"))

    def test_route_supports_four_five_six_seven_and_more_sessions(self):
        client = self._client_as_admin()
        for count in (4, 5, 6, 7, 8):
            config = self._new_configuration(f"{count} Sessions")
            db.session.commit()
            maxima = [10] * (count - 1) + [100 - (10 * (count - 1))]
            for index, maximum in enumerate(maxima, start=1):
                exam = ExamType(
                    academic_year_id=self.year_one.id,
                    name=f"Dynamic {count} Exam {index}",
                    sort_order=index,
                    is_active=True,
                )
                db.session.add(exam)
                db.session.commit()
                response = client.post(
                    "/admin/behavior/sessions",
                    data={
                        "csrf_token": "",
                        "config_id": config.id,
                        "exam_type_id": exam.id,
                        "session_label": f"Dynamic {count} Session {index}",
                        "maximum_score": maximum,
                        "sort_order": index,
                        "is_active": "on",
                    },
                )
                self.assertEqual(response.status_code, 302)
            self.assertEqual(
                BehaviorSession.query.filter_by(behavior_configuration_id=config.id).count(),
                count,
            )

    def test_configuration_completion_requires_exactly_100_and_valid_year_exam_types(self):
        valid = self._new_configuration("Exactly 100")
        self._add_sessions(valid, [40, 60], "Valid")
        self._add_activation_dependencies(valid, "Exactly 100")
        self.assertIs(validate_configuration_ready(valid), valid)

        for total in (99, 101):
            config = self._new_configuration(f"Invalid {total}")
            self._add_sessions(config, [total], f"Invalid {total}")
            with self.assertRaises(BehaviorValidationError):
                validate_configuration_ready(config)

        wrong_year = self._new_configuration("Wrong Year")
        other_year_exam = ExamType(
            academic_year_id=self.year_two.id,
            name="Other Academic Year Exam",
            sort_order=1,
            is_active=True,
        )
        db.session.add(other_year_exam)
        db.session.flush()
        db.session.add(BehaviorSession(
            configuration=wrong_year,
            exam_type=other_year_exam,
            session_label="Wrong Year Session",
            maximum_score=100,
            sort_order=1,
            is_active=True,
        ))
        db.session.flush()
        with self.assertRaises(BehaviorValidationError):
            validate_configuration_ready(wrong_year)

    def test_sessions_and_categories_are_editable_until_history_exists(self):
        client = self._client_as_admin()
        locked = self._new_configuration("Locked")
        session = self._add_sessions(locked, [100], "Locked")[0]
        db.session.commit()

        response = client.post(
            "/admin/behavior/sessions",
            data={
                "csrf_token": "",
                "config_id": locked.id,
                "session_id": session.id,
                "exam_type_id": session.exam_type_id,
                "session_label": "Changed label",
                "maximum_score": 90,
                "sort_order": 1,
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(session)
        self.assertEqual(session.maximum_score, Decimal("90.000"))

        response = client.post(
            "/admin/behavior/categories",
            data={
                "csrf_token": "",
                "config_id": locked.id,
                "name": "Late category",
                "polarity": "negative",
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BehaviorCategory.query.filter_by(behavior_configuration_id=locked.id).count(), 1)

        historical = self._new_configuration("Historical")
        historical_session = self._add_sessions(historical, [100], "Historical")[0]
        category = BehaviorCategory(
            configuration=historical,
            name="Positive Historical",
            polarity="positive",
            is_active=True,
        )
        action = BehaviorAction(
            category=category,
            name="Helpful Historical",
            level_number=1,
            points=2,
            frequency="ad_hoc",
            is_active=True,
        )
        db.session.add_all([category, action])
        db.session.flush()
        db.session.commit()
        from app.behavior_service import record_event

        record_event(historical, self.enrollment_one, historical_session, category, action)
        db.session.commit()
        db.session.commit()

        response = client.post(
            "/admin/behavior/sessions",
            data={
                "csrf_token": "",
                "config_id": historical.id,
                "session_id": historical_session.id,
                "exam_type_id": historical_session.exam_type_id,
                "session_label": "Changed historical label",
                "maximum_score": 90,
                "sort_order": 1,
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertIn("recorded events", response.get_data(as_text=True))
        db.session.refresh(historical_session)
        self.assertEqual(historical_session.maximum_score, Decimal("100.000"))

    def test_base_score_is_half_of_each_session_maximum_without_events(self):
        config = self._new_configuration("Formula")
        session_17, session_15 = self._add_sessions(config, [17, 15], "Formula")
        empty_17 = calculate_session_score(config, session_17, self.enrollment_one)
        empty_15 = calculate_session_score(config, session_15, self.enrollment_one)
        self.assertEqual(empty_17["base_score"], Decimal("8.500"))
        self.assertEqual(empty_17["final_score"], Decimal("8.500"))
        self.assertEqual(empty_15["base_score"], Decimal("7.500"))
        self.assertEqual(empty_15["final_score"], Decimal("7.500"))


if __name__ == "__main__":
    unittest.main()
