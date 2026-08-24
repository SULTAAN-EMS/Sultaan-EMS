import json
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(
        db.Enum("super_admin", "admin", "staff", name="user_role_enum"),
        default="admin",
        nullable=False,
    )
    permissions = db.Column(db.Text)
    photo_path = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def can_manage_users(self):
        return self.role == "super_admin"

    def permission_set(self):
        if self.role == "super_admin":
            return {"*"}
        if not self.permissions:
            return set()
        try:
            value = json.loads(self.permissions)
        except (TypeError, ValueError):
            return set()
        return set(value if isinstance(value, list) else [])

    def has_permission(self, permission):
        return self.role == "super_admin" or permission in self.permission_set()

    def set_permissions(self, permissions):
        self.permissions = json.dumps(sorted(set(permissions or [])))


class AcademicYear(TimestampMixin, db.Model):
    __tablename__ = "academic_years"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    is_current = db.Column(db.Boolean, default=False, nullable=False)


class AcademicYearLevel(TimestampMixin, db.Model):
    """Year-scoped level definition; legacy levels remain the compatibility source."""

    __tablename__ = "academic_year_levels"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_levels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_year = db.relationship("AcademicYear", backref=db.backref("year_levels", lazy="dynamic"))
    legacy_level = db.relationship("AcademicLevel", backref=db.backref("year_scopes", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint("academic_year_id", "name", name="uq_academic_year_level_name"),
    )


class AcademicYearClass(TimestampMixin, db.Model):
    """Year-scoped class definition under an AcademicYearLevel."""

    __tablename__ = "academic_year_classes"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_levels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_class_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_classes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_year_level = db.relationship(
        "AcademicYearLevel",
        backref=db.backref("year_classes", lazy="dynamic"),
    )
    legacy_class = db.relationship("AcademicClass", backref=db.backref("year_scopes", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint("academic_year_level_id", "name", name="uq_academic_year_class_name"),
    )


class AcademicYearSubject(TimestampMixin, db.Model):
    """Year + level scoped subject offering with a legacy result bridge."""

    __tablename__ = "academic_year_subjects"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_year_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_levels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_subject_id = db.Column(
        db.Integer,
        db.ForeignKey("subjects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    max_score = db.Column(db.Numeric(8, 3), default=100, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_year = db.relationship("AcademicYear", backref=db.backref("year_subjects", lazy="dynamic"))
    academic_year_level = db.relationship(
        "AcademicYearLevel",
        backref=db.backref("year_subjects", lazy="dynamic"),
    )
    legacy_subject = db.relationship("Subject", backref=db.backref("year_scopes", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint(
            "academic_year_id",
            "academic_year_level_id",
            "name",
            name="uq_academic_year_subject_name",
        ),
    )


class PromotionRule(TimestampMixin, db.Model):
    """Year-aware promotion policy for one academic level.

    This is intentionally separate from ``StudentEnrollment.academic_outcome``.
    Enrollment outcomes describe lifecycle transitions; this model describes
    the policy used when an academic evaluation is performed.
    """

    __tablename__ = "promotion_rules"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_year_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_levels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    overall_pass_threshold = db.Column(db.Numeric(6, 3), default=50, nullable=False)
    critical_subject_pass_threshold = db.Column(db.Numeric(6, 3), default=50, nullable=False)

    academic_year = db.relationship("AcademicYear", backref=db.backref("promotion_rules", lazy="dynamic"))
    academic_year_level = db.relationship("AcademicYearLevel", backref=db.backref("promotion_rule", uselist=False))
    critical_subjects = db.relationship(
        "PromotionRuleCriticalSubject",
        back_populates="promotion_rule",
        cascade="all, delete-orphan",
        order_by="PromotionRuleCriticalSubject.id",
    )

    __table_args__ = (
        UniqueConstraint(
            "academic_year_id",
            "academic_year_level_id",
            name="uq_promotion_rule_year_level",
        ),
        db.CheckConstraint(
            "overall_pass_threshold >= 0 AND overall_pass_threshold <= 100",
            name="ck_promotion_rule_overall_threshold",
        ),
        db.CheckConstraint(
            "critical_subject_pass_threshold >= 0 AND critical_subject_pass_threshold <= 100",
            name="ck_promotion_rule_critical_threshold",
        ),
    )


class PromotionRuleCriticalSubject(db.Model):
    """One year-aware subject selected as critical for a promotion rule."""

    __tablename__ = "promotion_rule_critical_subjects"

    id = db.Column(db.Integer, primary_key=True)
    promotion_rule_id = db.Column(
        db.Integer,
        db.ForeignKey("promotion_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_year_subject_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_subjects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    promotion_rule = db.relationship("PromotionRule", back_populates="critical_subjects")
    academic_year_subject = db.relationship("AcademicYearSubject")

    __table_args__ = (
        UniqueConstraint(
            "promotion_rule_id",
            "academic_year_subject_id",
            name="uq_promotion_rule_critical_subject",
        ),
    )


class AcademicLevel(TimestampMixin, db.Model):
    __tablename__ = "academic_levels"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class AcademicClass(TimestampMixin, db.Model):
    __tablename__ = "academic_classes"

    id = db.Column(db.Integer, primary_key=True)
    academic_level_id = db.Column(db.Integer, db.ForeignKey("academic_levels.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_level = db.relationship("AcademicLevel", backref=db.backref("classes", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint("academic_level_id", "name", name="uq_level_class"),
    )


class AcademicSection(TimestampMixin, db.Model):
    __tablename__ = "academic_sections"

    id = db.Column(db.Integer, primary_key=True)
    academic_class_id = db.Column(db.Integer, db.ForeignKey("academic_classes.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_class = db.relationship("AcademicClass", backref=db.backref("sections", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint("academic_class_id", "name", name="uq_class_section"),
    )


class SchoolClass(TimestampMixin, db.Model):
    __tablename__ = "school_classes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))


class Subject(TimestampMixin, db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    academic_level_id = db.Column(db.Integer, db.ForeignKey("academic_levels.id"), nullable=True)
    max_score = db.Column(db.Numeric(8, 3), default=100, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_level = db.relationship("AcademicLevel", backref=db.backref("subjects", lazy="dynamic"))

    __table_args__ = (
        UniqueConstraint("name", "academic_level_id", name="uq_subject_level"),
    )


class Exam(TimestampMixin, db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    short_code = db.Column(db.String(20), nullable=True)  # e.g., "MT1", "MT2", "FIN"
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    
    # Exam type configuration
    weight_percentage = db.Column(db.Float, default=0.0)  # Weight in final grade calculation
    sort_order = db.Column(db.Integer, default=0)  # Order in which exams appear
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    
    # New academic hierarchy fields
    academic_level_id = db.Column(db.Integer, db.ForeignKey("academic_levels.id"), nullable=True)
    academic_class_id = db.Column(db.Integer, db.ForeignKey("academic_classes.id"), nullable=True)
    academic_section_id = db.Column(db.Integer, db.ForeignKey("academic_sections.id"), nullable=True)
    
    is_published = db.Column(db.Boolean, default=False, nullable=False)

    academic_year = db.relationship("AcademicYear")
    academic_level = db.relationship("AcademicLevel")
    academic_class = db.relationship("AcademicClass")
    academic_section = db.relationship("AcademicSection")

    __table_args__ = (
        UniqueConstraint("name", "academic_year_id", name="uq_exam_year"),
    )


class Student(TimestampMixin, db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)

    # ✅ THIS IS THE ID USERS TYPE (3007)
    student_code = db.Column(db.String(50), unique=True, nullable=False, index=True)

    full_name = db.Column(db.String(180), nullable=False)
    mother_name = db.Column(db.String(180))
    phone = db.Column(db.String(40))
    # Used exclusively by the per-level results analytics report.
    gender = db.Column(db.String(10), nullable=True)

    # Legacy fields for backward compatibility during migration
    class_id = db.Column(db.Integer, db.ForeignKey("school_classes.id"), nullable=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False)
    level = db.Column(db.String(80))
    section = db.Column(db.String(80))

    # New academic hierarchy fields
    academic_level_id = db.Column(db.Integer, db.ForeignKey("academic_levels.id"), nullable=True)
    academic_class_id = db.Column(db.Integer, db.ForeignKey("academic_classes.id"), nullable=True)
    academic_section_id = db.Column(db.Integer, db.ForeignKey("academic_sections.id"), nullable=True)

    photo_path = db.Column(db.String(255))
    note = db.Column(db.Text)
    # The student's reusable result-portal signature. Existing complaint
    # records keep their own historical signature_data unchanged.
    saved_signature_data = db.Column(db.Text, nullable=True)

    is_result_locked = db.Column(db.Boolean, default=False, nullable=False)
    lock_reason = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    school_class = db.relationship("SchoolClass")
    academic_year = db.relationship("AcademicYear")
    academic_level = db.relationship("AcademicLevel")
    academic_class = db.relationship("AcademicClass")
    academic_section = db.relationship("AcademicSection")

    # ✅ FIX: allow system to use "student_id" in queries safely
    @property
    def student_id(self):
        return self.student_code


class StudentEnrollment(TimestampMixin, db.Model):
    """Historical academic placement for a permanent Student identity.

    This is the authoritative placement history for a requested academic
    year.  The legacy placement fields on ``Student`` are intentionally kept
    as a compatibility snapshot for older imports, integrations, and display
    paths; they must not override a matching enrollment or describe a
    different historical year.
    """

    __tablename__ = "student_enrollments"

    STATUS_VALUES = ("active", "completed", "transferred", "withdrawn", "archived")
    OUTCOME_VALUES = ("pending", "passed", "failed", "promoted", "repeated", "graduated")
    SOURCE_VALUES = ("backfill", "manual", "import", "transfer", "promotion", "repeat")

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_levels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year_class_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_section_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_sections.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    academic_outcome = db.Column(db.String(20), nullable=False, default="pending", index=True)
    enrollment_source = db.Column(db.String(20), nullable=False, default="manual", index=True)
    previous_enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    enrolled_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    exited_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    student = db.relationship(
        "Student",
        backref=db.backref("enrollments", lazy="dynamic"),
        passive_deletes=True,
    )
    academic_year = db.relationship("AcademicYear", backref=db.backref("enrollments", lazy="dynamic"))
    academic_year_level = db.relationship("AcademicYearLevel")
    academic_year_class = db.relationship("AcademicYearClass")
    academic_section = db.relationship("AcademicSection")
    previous_enrollment = db.relationship(
        "StudentEnrollment",
        remote_side=[id],
        backref=db.backref("next_enrollments", lazy="dynamic"),
    )

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "academic_year_id",
            name="uq_student_enrollment_student_year",
        ),
        db.CheckConstraint(
            "status IN ('active', 'completed', 'transferred', 'withdrawn', 'archived')",
            name="ck_student_enrollment_status",
        ),
        db.CheckConstraint(
            "academic_outcome IN ('pending', 'passed', 'failed', 'promoted', 'repeated', 'graduated')",
            name="ck_student_enrollment_outcome",
        ),
        db.CheckConstraint(
            "enrollment_source IN ('backfill', 'manual', 'import', 'transfer', 'promotion', 'repeat')",
            name="ck_student_enrollment_source",
        ),
        db.Index(
            "idx_student_enrollment_year_level_class",
            "academic_year_id",
            "academic_year_level_id",
            "academic_year_class_id",
        ),
    )


class StudentEnrollmentMovement(TimestampMixin, db.Model):
    """Immutable audit trail for enrollment placement movements.

    A local transfer updates one ``StudentEnrollment`` in place, while a
    cross-year move creates a new enrollment. This ledger preserves both
    placements in either case without changing result or attendance history.
    """

    __tablename__ = "student_enrollment_movements"

    MOVEMENT_VALUES = (
        "local_transfer",
        "cross_year_transfer",
        "promotion",
        "repeat",
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    movement_type = db.Column(db.String(30), nullable=False, index=True)

    from_academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False)
    from_academic_year_level_id = db.Column(db.Integer, db.ForeignKey("academic_year_levels.id", ondelete="RESTRICT"), nullable=False)
    from_academic_year_class_id = db.Column(db.Integer, db.ForeignKey("academic_year_classes.id", ondelete="RESTRICT"), nullable=False)
    from_academic_section_id = db.Column(db.Integer, db.ForeignKey("academic_sections.id", ondelete="SET NULL"), nullable=True)

    to_academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False)
    to_academic_year_level_id = db.Column(db.Integer, db.ForeignKey("academic_year_levels.id", ondelete="RESTRICT"), nullable=False)
    to_academic_year_class_id = db.Column(db.Integer, db.ForeignKey("academic_year_classes.id", ondelete="RESTRICT"), nullable=False)
    to_academic_section_id = db.Column(db.Integer, db.ForeignKey("academic_sections.id", ondelete="SET NULL"), nullable=True)

    reason = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    moved_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    performed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    student = db.relationship("Student")
    enrollment = db.relationship("StudentEnrollment", backref=db.backref("movements", lazy="dynamic"))
    from_academic_year = db.relationship("AcademicYear", foreign_keys=[from_academic_year_id])
    from_academic_year_level = db.relationship("AcademicYearLevel", foreign_keys=[from_academic_year_level_id])
    from_academic_year_class = db.relationship("AcademicYearClass", foreign_keys=[from_academic_year_class_id])
    from_academic_section = db.relationship("AcademicSection", foreign_keys=[from_academic_section_id])
    to_academic_year = db.relationship("AcademicYear", foreign_keys=[to_academic_year_id])
    to_academic_year_level = db.relationship("AcademicYearLevel", foreign_keys=[to_academic_year_level_id])
    to_academic_year_class = db.relationship("AcademicYearClass", foreign_keys=[to_academic_year_class_id])
    to_academic_section = db.relationship("AcademicSection", foreign_keys=[to_academic_section_id])
    performer = db.relationship("User", foreign_keys=[performed_by])

    __table_args__ = (
        db.CheckConstraint(
            "movement_type IN ('local_transfer', 'cross_year_transfer', 'promotion', 'repeat')",
            name="ck_student_enrollment_movement_type",
        ),
        db.Index(
            "idx_student_enrollment_movement_student_time",
            "student_id",
            "moved_at",
        ),
    )


class PromotionEvaluation(TimestampMixin, db.Model):
    """Immutable evidence snapshot for one promotion evaluation attempt."""

    __tablename__ = "promotion_evaluations"

    OUTCOME_VALUES = ("PASS", "FAIL")
    STATUS_VALUES = ("EVALUATED", "INCOMPLETE", "INVALID", "NOT_EVALUATED")

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    student_enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_year_levels.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The selected exam is the explicit evaluation context.  It is nullable
    # only for legacy Phase 3B snapshots created before Phase 3C.
    exam_id = db.Column(
        db.Integer,
        db.ForeignKey("exams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    promotion_rule_id = db.Column(
        db.Integer,
        db.ForeignKey("promotion_rules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    promotion_rule_snapshot_json = db.Column(db.Text, nullable=False, default="{}")
    evaluation_context_json = db.Column(db.Text, nullable=False, default="{}")
    overall_percentage = db.Column(db.Numeric(8, 3), nullable=True)
    base_outcome = db.Column(db.String(4), nullable=True)
    final_outcome = db.Column(db.String(4), nullable=True)
    evaluation_status = db.Column(
        db.String(20),
        nullable=False,
        default="NOT_EVALUATED",
        index=True,
    )
    critical_subject_results_json = db.Column(db.Text, nullable=False, default="[]")
    override_reason = db.Column(db.String(80), nullable=True)
    evaluated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    student = db.relationship("Student", backref=db.backref("promotion_evaluations", lazy="dynamic"))
    student_enrollment = db.relationship(
        "StudentEnrollment",
        backref=db.backref("promotion_evaluations", lazy="dynamic"),
    )
    academic_year = db.relationship("AcademicYear")
    academic_year_level = db.relationship("AcademicYearLevel")
    exam = db.relationship("Exam")
    promotion_rule = db.relationship("PromotionRule")

    __table_args__ = (
        db.CheckConstraint(
            "base_outcome IS NULL OR base_outcome IN ('PASS', 'FAIL')",
            name="ck_promotion_evaluation_base_outcome",
        ),
        db.CheckConstraint(
            "final_outcome IS NULL OR final_outcome IN ('PASS', 'FAIL')",
            name="ck_promotion_evaluation_final_outcome",
        ),
        db.CheckConstraint(
            "evaluation_status IN ('EVALUATED', 'INCOMPLETE', 'INVALID', 'NOT_EVALUATED')",
            name="ck_promotion_evaluation_status",
        ),
        db.Index(
            "idx_promotion_evaluation_scope",
            "academic_year_id",
            "academic_year_level_id",
            "student_id",
            "evaluated_at",
        ),
    )


class PromotionOutcomeApplication(TimestampMixin, db.Model):
    """Explicit, auditable use of one immutable promotion evaluation.

    A snapshot is evidence only.  This ledger records the separate action an
    authorized administrator took after reviewing that evidence, so applying
    an outcome can never rewrite the snapshot or be silently repeated.
    """

    __tablename__ = "promotion_outcome_applications"

    OUTCOME_VALUES = ("passed", "failed")
    ACTION_VALUES = ("outcome", "promotion", "repeat", "graduation")
    STATUS_VALUES = ("APPLIED", "TRANSITIONED", "GRADUATED")

    id = db.Column(db.Integer, primary_key=True)
    promotion_evaluation_id = db.Column(
        db.Integer,
        db.ForeignKey("promotion_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    applied_outcome = db.Column(db.String(10), nullable=False)
    action = db.Column(db.String(20), nullable=False, default="outcome")
    application_status = db.Column(db.String(20), nullable=False, default="APPLIED", index=True)
    destination_enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    movement_id = db.Column(
        db.Integer,
        db.ForeignKey("student_enrollment_movements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    applied_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    applied_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    promotion_evaluation = db.relationship(
        "PromotionEvaluation",
        backref=db.backref("outcome_application", uselist=False),
    )
    student = db.relationship("Student")
    source_enrollment = db.relationship("StudentEnrollment", foreign_keys=[source_enrollment_id])
    destination_enrollment = db.relationship("StudentEnrollment", foreign_keys=[destination_enrollment_id])
    movement = db.relationship("StudentEnrollmentMovement")
    performer = db.relationship("User", foreign_keys=[applied_by])

    __table_args__ = (
        db.CheckConstraint(
            "applied_outcome IN ('passed', 'failed')",
            name="ck_promotion_outcome_application_outcome",
        ),
        db.CheckConstraint(
            "action IN ('outcome', 'promotion', 'repeat', 'graduation')",
            name="ck_promotion_outcome_application_action",
        ),
        db.CheckConstraint(
            "application_status IN ('APPLIED', 'TRANSITIONED', 'GRADUATED')",
            name="ck_promotion_outcome_application_status",
        ),
    )


class Result(TimestampMixin, db.Model):
    __tablename__ = "results"

    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)

    score = db.Column(db.Numeric(8, 3), nullable=False)
    grade_override = db.Column(db.String(20))
    comment = db.Column(db.String(255))
    is_published = db.Column(db.Boolean, default=True, nullable=False)

    student = db.relationship("Student", backref=db.backref("results", cascade="all, delete-orphan"))
    exam = db.relationship("Exam")
    subject = db.relationship("Subject")

    __table_args__ = (
        UniqueConstraint("student_id", "exam_id", "subject_id", name="uq_student_exam_subject"),
    )


class Setting(TimestampMixin, db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)


class StudentFeedback(TimestampMixin, db.Model):
    """A student's result-portal feedback submission."""

    __tablename__ = "student_feedback"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), nullable=True, index=True)
    ref_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    reaction = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    read_by_student = db.Column(db.Boolean, default=True, nullable=False, index=True)
    delivered_at = db.Column(db.DateTime, nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)

    student = db.relationship("Student", backref=db.backref("feedback_entries", cascade="all, delete-orphan"))
    exam = db.relationship("Exam")


class StudentComplaint(TimestampMixin, db.Model):
    """A signed result-portal complaint awaiting Education Office review."""

    __tablename__ = "student_complaints"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), nullable=True, index=True)
    ref_number = db.Column(db.String(40), unique=True, nullable=False, index=True)
    complaint_type = db.Column(db.String(20), nullable=False, index=True)
    subject_name = db.Column(db.String(120))
    details = db.Column(db.Text, nullable=False)
    signature_data = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    read_by_student = db.Column(db.Boolean, default=True, nullable=False, index=True)
    delivered_at = db.Column(db.DateTime, nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)

    student = db.relationship("Student", backref=db.backref("complaint_entries", cascade="all, delete-orphan"))
    exam = db.relationship("Exam")


class StudentFeedbackReply(TimestampMixin, db.Model):
    __tablename__ = "student_feedback_replies"

    id = db.Column(db.Integer, primary_key=True)
    feedback_id = db.Column(db.Integer, db.ForeignKey("student_feedback.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    office_name = db.Column(db.String(150), nullable=False, default="Xafiiska Waxbarashada")
    message = db.Column(db.Text, nullable=False)

    feedback = db.relationship("StudentFeedback", backref=db.backref("replies", cascade="all, delete-orphan", order_by="StudentFeedbackReply.created_at.asc()"))
    admin = db.relationship("User")


class StudentComplaintReply(TimestampMixin, db.Model):
    __tablename__ = "student_complaint_replies"

    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey("student_complaints.id", ondelete="CASCADE"), nullable=False, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    office_name = db.Column(db.String(150), nullable=False, default="Xafiiska Waxbarashada")
    message = db.Column(db.Text, nullable=False)

    complaint = db.relationship("StudentComplaint", backref=db.backref("replies", cascade="all, delete-orphan", order_by="StudentComplaintReply.created_at.asc()"))
    admin = db.relationship("User")


class GradeScale(TimestampMixin, db.Model):
    __tablename__ = "grade_scales"

    id = db.Column(db.Integer, primary_key=True)
    grade = db.Column(db.String(20), nullable=False)
    min_score = db.Column(db.Numeric(8, 3), nullable=False)
    max_score = db.Column(db.Numeric(8, 3), nullable=False)
    comment = db.Column(db.String(120), nullable=False)
    grade_point = db.Column(db.Numeric(6, 3), default=0, nullable=False)
    is_pass = db.Column(db.Boolean, default=True, nullable=False)
    badge_color = db.Column(db.String(20), default="#10b981", nullable=False)
    text_color = db.Column(db.String(20), default="#ffffff", nullable=False)
    background_color = db.Column(db.String(20), default="#ecfdf5", nullable=False)
    border_color = db.Column(db.String(20), default="#10b981", nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        db.Index('idx_grade_scale_exam_id', 'exam_id'),
        db.Index('idx_grade_scale_min_score', 'min_score'),
        db.Index('idx_grade_scale_max_score', 'max_score'),
        db.Index('idx_grade_scale_is_active', 'is_active'),
    )


class ReportVerification(TimestampMixin, db.Model):
    __tablename__ = "report_verifications"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(120), unique=True, nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    is_valid = db.Column(db.Boolean, default=True, nullable=False)

    student = db.relationship("Student")
    exam = db.relationship("Exam")

    __table_args__ = (
        UniqueConstraint("student_id", "exam_id", name="uq_report_student_exam"),
    )


class AttendanceRecord(TimestampMixin, db.Model):
    __tablename__ = "attendance_records"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False, index=True)
    
    # Hall Exam Attendance fields
    exam_hall_id = db.Column(db.Integer, db.ForeignKey("exam_halls.id", ondelete="CASCADE"), nullable=True, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True, index=True)
    # New attendance is scoped to the scheduled exam sitting.  This remains
    # nullable so historical records created before timetable support stay valid.
    exam_session_id = db.Column(db.Integer, db.ForeignKey("exam_sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    exam_type_id = db.Column(db.Integer, db.ForeignKey("exam_types.id", ondelete="SET NULL"), nullable=True, index=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Legacy fields for backward compatibility
    class_id = db.Column(db.Integer, db.ForeignKey("school_classes.id"), nullable=True, index=True)
    academic_level_id = db.Column(db.Integer, db.ForeignKey("academic_levels.id"), nullable=True)
    academic_class_id = db.Column(db.Integer, db.ForeignKey("academic_classes.id"), nullable=True)
    academic_section_id = db.Column(db.Integer, db.ForeignKey("academic_sections.id"), nullable=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), index=True)
    attendance_date = db.Column(db.Date, default=datetime.utcnow, nullable=False, index=True)
    
    status = db.Column(
        db.String(50),
        default="present",
        nullable=False,
        index=True,
    )
    note = db.Column(db.String(255))
    marked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    student = db.relationship("Student")
    academic_year = db.relationship("AcademicYear")
    school_class = db.relationship("SchoolClass")
    academic_level = db.relationship("AcademicLevel")
    academic_class = db.relationship("AcademicClass")
    academic_section = db.relationship("AcademicSection")
    exam = db.relationship("Exam")
    marked_by = db.relationship("User")
    
    exam_hall = db.relationship("ExamHall")
    subject = db.relationship("Subject")
    exam_session = db.relationship("ExamSession")
    exam_type = db.relationship("ExamType")

    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "exam_hall_id",
            "subject_id",
            "exam_session_id",
            name="uq_student_hall_subject_session_attendance",
        ),
    )


class IdCardIssue(TimestampMixin, db.Model):
    __tablename__ = "id_card_issues"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(120), unique=True, nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id"), nullable=False, index=True)
    issue_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date)
    status = db.Column(
        db.Enum("Active", "Inactive", "Expired", "Blocked", name="id_card_issue_status_enum"),
        default="Active",
        nullable=False,
        index=True,
    )
    template_name = db.Column(db.String(80), default="default", nullable=False)

    student = db.relationship("Student")
    academic_year = db.relationship("AcademicYear")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    ip_address = db.Column(db.String(80))
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)


teacher_subjects = db.Table(
    "teacher_subjects",
    db.Column("teacher_id", db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("subject_id", db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
)

teacher_classes = db.Table(
    "teacher_classes",
    db.Column("teacher_id", db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("class_id", db.Integer, db.ForeignKey("academic_classes.id", ondelete="CASCADE"), primary_key=True),
)

teacher_sections = db.Table(
    "teacher_sections",
    db.Column("teacher_id", db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("section_id", db.Integer, db.ForeignKey("academic_sections.id", ondelete="CASCADE"), primary_key=True),
)

teacher_academic_levels = db.Table(
    "teacher_academic_levels",
    db.Column("teacher_id", db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), primary_key=True),
    db.Column("academic_level_id", db.Integer, db.ForeignKey("academic_levels.id", ondelete="CASCADE"), primary_key=True),
)


class Teacher(TimestampMixin, db.Model):
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    teacher_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(180), nullable=False)
    gender = db.Column(db.Enum("Male", "Female", "Other", name="teacher_gender_enum"), nullable=True)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    emergency_contact = db.Column(db.String(180))
    employment_date = db.Column(db.Date)
    employment_type = db.Column(
        db.Enum("Full Time", "Part Time", "Contract", name="teacher_employment_type_enum"),
        default="Full Time",
        nullable=False,
    )
    qualification = db.Column(db.String(255))
    years_experience = db.Column(db.Integer, default=0, nullable=False)
    department = db.Column(db.String(120))
    employment_status = db.Column(
        db.Enum("Active", "Inactive", name="teacher_employment_status_enum"),
        default="Active",
        nullable=False,
    )
    school_level = db.Column(
        db.Enum("Primary", "Middle", "Secondary", "High School", name="teacher_school_level_enum"),
        nullable=True,
    )
    photo_path = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    force_password_change = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)
    last_logout_at = db.Column(db.DateTime)

    user = db.relationship("User", backref=db.backref("teacher_profile", uselist=False))
    subjects = db.relationship("Subject", secondary=teacher_subjects, backref=db.backref("assigned_teachers", lazy="dynamic"))
    classes = db.relationship("AcademicClass", secondary=teacher_classes, backref=db.backref("assigned_teachers", lazy="dynamic"))
    sections = db.relationship("AcademicSection", secondary=teacher_sections, backref=db.backref("assigned_teachers", lazy="dynamic"))
    academic_levels = db.relationship("AcademicLevel", secondary=teacher_academic_levels, backref=db.backref("assigned_teachers", lazy="dynamic"))


class TeacherPermission(db.Model):
    __tablename__ = "teacher_permissions"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True)
    permission = db.Column(db.String(80), nullable=False)

    teacher = db.relationship("Teacher", backref=db.backref("permission_rows", cascade="all, delete-orphan"))

    __table_args__ = (
        UniqueConstraint("teacher_id", "permission", name="uq_teacher_permission"),
    )


class TeacherActivity(db.Model):
    __tablename__ = "teacher_activities"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(80))

    teacher = db.relationship("Teacher", backref=db.backref("activities", cascade="all, delete-orphan", order_by="TeacherActivity.created_at.desc()"))


class TeacherCodeSequence(db.Model):
    __tablename__ = "teacher_code_sequences"

    id = db.Column(db.Integer, primary_key=True)
    prefix = db.Column(db.String(40), unique=True, nullable=False)
    last_number = db.Column(db.Integer, default=0, nullable=False)


class IncidentCategory(TimestampMixin, db.Model):
    __tablename__ = "incident_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class SeverityLevel(TimestampMixin, db.Model):
    __tablename__ = "severity_levels"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(20), default="#64748b", nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class IncidentAction(TimestampMixin, db.Model):
    __tablename__ = "incident_actions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class IncidentReport(TimestampMixin, db.Model):
    __tablename__ = "incident_reports"

    id = db.Column(db.Integer, primary_key=True)
    report_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invigilator_id = db.Column(db.Integer, db.ForeignKey("exam_invigilators.id", ondelete="SET NULL"), nullable=True, index=True)
    
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), nullable=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    
    category_id = db.Column(db.Integer, db.ForeignKey("incident_categories.id", ondelete="RESTRICT"), nullable=False)
    severity_id = db.Column(db.Integer, db.ForeignKey("severity_levels.id", ondelete="RESTRICT"), nullable=False)
    
    exam_room = db.Column(db.String(120))
    incident_date = db.Column(db.Date, nullable=False, index=True)
    incident_time = db.Column(db.Time, nullable=False)
    
    description = db.Column(db.Text, nullable=False)
    actions_taken = db.Column(db.Text)
    # Independent category and action description fields
    category_description = db.Column(db.String(500), nullable=True)
    action_description = db.Column(db.String(500), nullable=True)
    # Retains free-form context for legacy records backward compatibility.
    other_description = db.Column(db.String(500), nullable=True)
    signature_data = db.Column(db.Text)
    
    status = db.Column(
        db.Enum(
            "Pending Review",
            "Under Investigation",
            "Resolved",
            "Rejected",
            name="incident_report_status_enum",
        ),
        default="Pending Review",
        nullable=False,
        index=True
    )
    
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)
    
    student = db.relationship("Student", backref=db.backref("incident_reports", lazy="dynamic"))
    teacher = db.relationship("Teacher", backref=db.backref("incident_reports", lazy="dynamic"))
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("submitted_reports", lazy="dynamic"))
    invigilator = db.relationship("ExamInvigilator", backref=db.backref("submitted_reports", lazy="dynamic"))
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id], backref=db.backref("reviewed_reports", lazy="dynamic"))
    exam = db.relationship("Exam")
    subject = db.relationship("Subject")
    category = db.relationship("IncidentCategory")
    severity = db.relationship("SeverityLevel")
    category_links = db.relationship(
        "IncidentReportCategory",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="IncidentReportCategory.id",
    )

    @property
    def selected_categories(self):
        """Return every category for new multi-category reports and legacy reports."""
        linked_categories = [link.category for link in self.category_links if link.category]
        return linked_categories or ([self.category] if self.category else [])


class IncidentReportCategory(TimestampMixin, db.Model):
    """Additional categories selected for one incident report.

    ``IncidentReport.category_id`` remains the primary/legacy category so old
    records and integrations remain valid. This relation stores the complete
    selection for reports submitted with the multi-category selector.
    """
    __tablename__ = "incident_report_categories"
    __table_args__ = (
        db.UniqueConstraint("report_id", "category_id", name="uq_incident_report_category"),
    )

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("incident_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("incident_categories.id", ondelete="RESTRICT"), nullable=False, index=True)

    report = db.relationship("IncidentReport", back_populates="category_links")
    category = db.relationship("IncidentCategory")


class IncidentAttachment(TimestampMixin, db.Model):
    __tablename__ = "incident_attachments"

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("incident_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    report = db.relationship("IncidentReport", backref=db.backref("attachments", lazy="dynamic", cascade="all, delete-orphan"))


class ExamInvigilator(TimestampMixin, db.Model):
    __tablename__ = "exam_invigilators"

    id = db.Column(db.Integer, primary_key=True)
    invigilator_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    visible_password = db.Column(db.String(255))
    full_name = db.Column(db.String(180), nullable=False)
    photo_path = db.Column(db.String(255))
    mobile_number = db.Column(db.String(40))
    signature_data = db.Column(db.Text)
    role = db.Column(
        db.Enum(
            "Invigilator",
            "Supervisor",
            "Chief Invigilator",
            "Administrator",
            name="exam_invigilator_role_enum",
        ),
        default="Invigilator",
        nullable=False
    )
    school = db.Column(db.String(180))
    notes = db.Column(db.Text)
    status = db.Column(
        db.Enum("Active", "Inactive", "Locked", name="exam_invigilator_status_enum"),
        default="Active",
        nullable=False,
        index=True
    )
    active_from = db.Column(db.Date)
    active_until = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)
    last_logout_at = db.Column(db.DateTime)
    force_password_change = db.Column(db.Boolean, default=False, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_valid(self):
        """Check if invigilator account is currently valid based on status and date range"""
        from datetime import date
        if self.status != "Active" or not self.is_active:
            return False
        today = date.today()
        if self.active_from and today < self.active_from:
            return False
        if self.active_until and today > self.active_until:
            return False
        return True


class InvigilatorLoginHistory(TimestampMixin, db.Model):
    __tablename__ = "invigilator_login_history"

    id = db.Column(db.Integer, primary_key=True)
    invigilator_id = db.Column(db.Integer, db.ForeignKey("exam_invigilators.id", ondelete="CASCADE"), nullable=False, index=True)
    login_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    logout_time = db.Column(db.DateTime)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    login_status = db.Column(
        db.Enum("Success", "Failed", "Locked", "Expired", name="invigilator_login_status_enum"),
       default="Success",
        nullable=False
    )
    failure_reason = db.Column(db.String(255))

    invigilator = db.relationship("ExamInvigilator", backref=db.backref("login_history", cascade="all, delete-orphan"))


class IncidentReportSettings(TimestampMixin, db.Model):
    __tablename__ = "incident_report_settings"

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    setting_value = db.Column(db.Text)
    setting_type = db.Column(
        db.Enum("boolean", "string", "integer", "json", name="incident_setting_type_enum"),
        default="string",
        nullable=False
    )
    category = db.Column(db.String(50), default="general")
    description = db.Column(db.String(255))


class LabelTranslation(TimestampMixin, db.Model):
    """Model for storing UI label translations across languages"""
    __tablename__ = "label_translations"

    id = db.Column(db.Integer, primary_key=True)
    label_key = db.Column(db.String(100), nullable=False, index=True)
    language_code = db.Column(db.String(10), nullable=False, index=True)
    text_value = db.Column(db.Text, nullable=False)
    context = db.Column(db.String(100))  # Screen/area where label appears (e.g., "Dashboard", "Roster", "PDF")
    
    __table_args__ = (
        db.UniqueConstraint('label_key', 'language_code', name='uq_label_language'),
    )


class ExamType(TimestampMixin, db.Model):
    """Exam types (e.g. 1st Exam, 2nd Exam, Final Exam) per academic year"""
    __tablename__ = "exam_types"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(db.Integer, db.ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    academic_year = db.relationship("AcademicYear", backref=db.backref("exam_types", cascade="all, delete-orphan"))

    __table_args__ = (
        UniqueConstraint("academic_year_id", "name", name="uq_exam_type_year_name"),
    )


class ExamSession(TimestampMixin, db.Model):
    """One scheduled sitting within an academic-year examination."""
    __tablename__ = "exam_sessions"

    id = db.Column(db.Integer, primary_key=True)
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Results Hub's Exam is authoritative for new configurations.  The
    # legacy ExamType link keeps older halls and records compatible.
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=True, index=True)
    exam_type_id = db.Column(db.Integer, db.ForeignKey("exam_types.id", ondelete="CASCADE"), nullable=True, index=True)
    session_date = db.Column("date", db.Date, nullable=False, index=True)
    sitting_label = db.Column(db.String(120), nullable=False)
    session_time = db.Column("time", db.Time, nullable=True)

    academic_year = db.relationship("AcademicYear")
    exam = db.relationship("Exam")
    exam_type = db.relationship("ExamType")
    subject_assignments = db.relationship(
        "ExamSessionSubject",
        back_populates="exam_session",
        cascade="all, delete-orphan",
        order_by="ExamSessionSubject.id",
    )

    __table_args__ = (
        UniqueConstraint(
            "academic_year_id",
            "exam_id",
            "exam_type_id",
            "date",
            "sitting_label",
            "time",
            name="uq_exam_session_scope_sitting",
        ),
    )


class ExamSessionSubject(TimestampMixin, db.Model):
    """A level-specific subject scheduled inside one exam sitting."""
    __tablename__ = "exam_session_subjects"

    id = db.Column(db.Integer, primary_key=True)
    exam_session_id = db.Column(
        db.Integer,
        db.ForeignKey("exam_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_level_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_levels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id = db.Column(
        db.Integer,
        db.ForeignKey("subjects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stored scope rejects duplicate subject scheduling during concurrent saves.
    exam_scope_key = db.Column(db.String(100), nullable=False, index=True)

    exam_session = db.relationship("ExamSession", back_populates="subject_assignments")
    academic_level = db.relationship("AcademicLevel")
    subject = db.relationship("Subject")

    __table_args__ = (
        UniqueConstraint("exam_session_id", "subject_id", name="uq_exam_session_subject"),
        UniqueConstraint("exam_scope_key", "academic_level_id", "subject_id", name="uq_exam_scope_level_subject"),
    )


class ExamHall(TimestampMixin, db.Model):
    """Model for exam halls/rooms where seating arrangements and hall exams are held"""
    __tablename__ = "exam_halls"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255))
    capacity = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    # Seat Mixer halls are independent records, so keep their academic-year
    # scope on the hall itself instead of inferring it from legacy links.
    academic_year_id = db.Column(
        db.Integer,
        db.ForeignKey("academic_years.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Scoping for Hall Exams
    # Canonical Results Hub Setup exam relationship. ``exam_type_id`` remains
    # for backward compatibility with older hall records.
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="SET NULL"), nullable=True, index=True)
    exam_type_id = db.Column(db.Integer, db.ForeignKey("exam_types.id", ondelete="SET NULL"), nullable=True, index=True)
    academic_class_id = db.Column(db.Integer, db.ForeignKey("academic_classes.id", ondelete="SET NULL"), nullable=True, index=True)

    # Seat Mixer v2: time window for active/expired status
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

    exam = db.relationship("Exam", backref=db.backref("hall_exams", lazy="dynamic"))
    exam_type = db.relationship("ExamType", backref=db.backref("legacy_halls", lazy="dynamic"))
    academic_class = db.relationship("AcademicClass", backref=db.backref("halls", lazy="dynamic"))
    academic_year = db.relationship("AcademicYear", backref=db.backref("seat_mixer_halls", lazy="dynamic"))

    versions = db.relationship(
        "ExamHallVersion",
        backref="hall",
        cascade="all, delete-orphan",
        order_by="ExamHallVersion.version_number",
    )


class ExamHallSubject(TimestampMixin, db.Model):
    """Subjects belonging to a given exam hall's exam"""
    __tablename__ = "exam_hall_subjects"

    id = db.Column(db.Integer, primary_key=True)
    exam_hall_id = db.Column(db.Integer, db.ForeignKey("exam_halls.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)

    exam_hall = db.relationship("ExamHall", backref=db.backref("hall_subjects", cascade="all, delete-orphan"))
    subject = db.relationship("Subject")

    __table_args__ = (
        UniqueConstraint("exam_hall_id", "subject_id", name="uq_hall_subject"),
    )


class ExamHallEnrollment(TimestampMixin, db.Model):
    """Students assigned to a specific hall exam"""
    __tablename__ = "exam_hall_enrollments"

    id = db.Column(db.Integer, primary_key=True)
    exam_hall_id = db.Column(db.Integer, db.ForeignKey("exam_halls.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)

    exam_hall = db.relationship("ExamHall", backref=db.backref("enrollments", cascade="all, delete-orphan"))
    student = db.relationship("Student", backref=db.backref("hall_enrollments", cascade="all, delete-orphan"))

    __table_args__ = (
        UniqueConstraint("exam_hall_id", "student_id", name="uq_hall_student_enrollment"),
    )


class ExamHallVersion(TimestampMixin, db.Model):
    """Independent version of an Exam Hall — each has its own config, classes, and seat assignments."""
    __tablename__ = "exam_hall_versions"

    id = db.Column(db.Integer, primary_key=True)
    exam_hall_id = db.Column(db.Integer, db.ForeignKey("exam_halls.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(100), nullable=False)


class SeatMixerAssignment(TimestampMixin, db.Model):
    """Seat assignments scoped to an Exam Hall Version (Seat Mixer v2)."""
    __tablename__ = "seat_mixer_assignments"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, db.ForeignKey("exam_hall_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)

    row_number = db.Column(db.Integer, nullable=False)
    table_number = db.Column(db.Integer, nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)

    rows_config = db.Column(db.Integer, default=3)
    tables_per_row_config = db.Column(db.Integer, default=5)
    seats_per_table_config = db.Column(db.Integer, default=2)

    version = db.relationship("ExamHallVersion", backref=db.backref("assignments", cascade="all, delete-orphan"))
    student = db.relationship("Student")

    __table_args__ = (
        db.UniqueConstraint('version_id', 'student_id', name='uq_smixer_version_student'),
        db.UniqueConstraint('version_id', 'row_number', 'table_number', 'seat_number', name='uq_smixer_version_seat_position'),
    )


class SeatMixerSaveSnapshot(TimestampMixin, db.Model):
    """Immutable Seat Mixer save revision retained for one layout version."""
    __tablename__ = "seat_mixer_save_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(
        db.Integer,
        db.ForeignKey("exam_hall_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_json = db.Column(db.Text, nullable=False)
    integrity_score = db.Column(db.Integer, nullable=False, default=0)
    near_adjacency_count = db.Column(db.Integer, nullable=False, default=0)
    placed_count = db.Column(db.Integer, nullable=False, default=0)

    version = db.relationship(
        "ExamHallVersion",
        backref=db.backref(
            "save_snapshots",
            cascade="all, delete-orphan",
            order_by="SeatMixerSaveSnapshot.created_at.desc()",
        ),
    )

    __table_args__ = (
        db.Index("idx_smixer_snapshot_version_created", "version_id", "created_at"),
    )


class SeatAssignment(TimestampMixin, db.Model):
    """Model for seat assignments scoped to Exam + Hall combination"""
    __tablename__ = "seat_assignments"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_hall_id = db.Column(db.Integer, db.ForeignKey("exam_halls.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Seat position details
    row_number = db.Column(db.Integer, nullable=False)
    table_number = db.Column(db.Integer, nullable=False)
    seat_number = db.Column(db.Integer, nullable=False)  # Seat within table (1, 2, etc.)
    
    # Configuration snapshot (to preserve arrangement even if hall config changes)
    rows_config = db.Column(db.Integer, default=3)
    tables_per_row_config = db.Column(db.Integer, default=5)
    seats_per_table_config = db.Column(db.Integer, default=2)
    
    exam = db.relationship("Exam", backref=db.backref("seat_assignments", cascade="all, delete-orphan"))
    exam_hall = db.relationship("ExamHall", backref=db.backref("seat_assignments", cascade="all, delete-orphan"))
    student = db.relationship("Student", backref=db.backref("seat_assignments", cascade="all, delete-orphan"))

    __table_args__ = (
        db.UniqueConstraint('exam_id', 'exam_hall_id', 'student_id', name='uq_exam_hall_student'),
        db.UniqueConstraint('exam_id', 'exam_hall_id', 'row_number', 'table_number', 'seat_number', name='uq_exam_hall_seat_position'),
        db.Index('idx_exam_hall_combo', 'exam_id', 'exam_hall_id'),
    )
