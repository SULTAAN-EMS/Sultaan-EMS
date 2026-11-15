"""Student portal ID + private access-code rules.

The public portal deliberately keeps the code out of URLs and only stores a
short-lived verification marker in the signed Flask session after a successful
check.  Administrators can print or share the code from the protected settings
page; public requests never receive the stored code value.
"""

from datetime import datetime, timedelta
import hashlib
import secrets

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from .models import (
    AcademicYear,
    AcademicYearClass,
    AcademicYearLevel,
    StudentEnrollment,
    StudentPortalAccessCode,
    StudentPortalAccessPolicy,
)


ACCESS_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ACCESS_CODE_LENGTH = 4
VERIFICATION_TTL = timedelta(minutes=30)
SESSION_KEY = "student_portal_verified_codes"


def normalize_access_code(value):
    return "".join(str(value or "").upper().split())


def valid_access_code(value):
    code = normalize_access_code(value)
    return len(code) == ACCESS_CODE_LENGTH and all(character in ACCESS_CODE_ALPHABET for character in code)


def generate_access_code(used_values=None):
    used = {normalize_access_code(value) for value in (used_values or ())}
    while True:
        code = "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH))
        if code not in used:
            return code


def set_code_value(record, value):
    code = normalize_access_code(value)
    if not valid_access_code(code):
        raise ValueError("Lambarka sireedku waa inuu ka koobnaadaa 4 xaraf ama lambar oo ka mid ah A-Z iyo 2-9.")
    record.code_value = code
    record.code_hash = generate_password_hash(code)
    return record


def verify_code(record, value):
    code = normalize_access_code(value)
    if not record or not record.is_active or record.status != "approved" or not valid_access_code(code):
        return False
    return check_password_hash(record.code_hash or "", code)


def _enrollment(student, academic_year_id=None):
    query = StudentEnrollment.query.filter(
        StudentEnrollment.student_id == student.id,
        StudentEnrollment.status.in_(("active", "completed")),
    )
    if academic_year_id:
        query = query.filter(StudentEnrollment.academic_year_id == academic_year_id)
    return query.order_by(StudentEnrollment.id.desc()).first()


def student_scope(student, academic_year_id=None):
    enrollment = _enrollment(student, academic_year_id)
    if enrollment:
        year_class = enrollment.academic_year_class
        year_level = enrollment.academic_year_level
        return {
            "academic_year_id": enrollment.academic_year_id,
            "academic_year_level_id": enrollment.academic_year_level_id,
            "academic_year_class_id": enrollment.academic_year_class_id,
            "school_shift": (year_class.school_shift if year_class else None) or None,
            "year_name": enrollment.academic_year.name if enrollment.academic_year else "-",
            "level_name": year_level.name if year_level else "-",
            "class_name": year_class.name if year_class else "-",
        }
    return {
        "academic_year_id": academic_year_id or getattr(student, "academic_year_id", None),
        "academic_year_level_id": None,
        "academic_year_class_id": None,
        "school_shift": None,
        "year_name": "-",
        "level_name": getattr(student, "level", None) or "-",
        "class_name": "-",
    }


def _policy_matches(policy, scope):
    if policy.academic_year_id != scope.get("academic_year_id"):
        return False
    if policy.academic_year_level_id and policy.academic_year_level_id != scope.get("academic_year_level_id"):
        return False
    if policy.academic_year_class_id and policy.academic_year_class_id != scope.get("academic_year_class_id"):
        return False
    if policy.school_shift and policy.school_shift != scope.get("school_shift"):
        return False
    return True


def applicable_policy(student, academic_year_id=None):
    scope = student_scope(student, academic_year_id)
    if not scope.get("academic_year_id"):
        return None, scope
    policies = StudentPortalAccessPolicy.query.filter_by(academic_year_id=scope["academic_year_id"]).all()
    matches = [policy for policy in policies if _policy_matches(policy, scope)]
    matches.sort(
        key=lambda policy: sum(
            bool(value)
            for value in (
                policy.academic_year_level_id,
                policy.academic_year_class_id,
                policy.school_shift,
            )
        ),
        reverse=True,
    )
    return (matches[0] if matches else None), scope


def access_code_required(student, settings, academic_year_id=None):
    enabled = str(settings.get("student_portal_access_codes_enabled", "off") or "off").lower()
    if enabled not in {"on", "true", "1", "yes"}:
        return False
    policy, _scope = applicable_policy(student, academic_year_id)
    return policy.is_enabled if policy is not None else True


def access_code_record(student):
    return StudentPortalAccessCode.query.filter_by(student_id=student.id).first()


def _verification_fingerprint(record):
    if not record or not record.is_active or record.status != "approved" or not record.code_hash:
        return None
    # Keep the password hash itself out of the signed session cookie while
    # still invalidating every old verification when the code is replaced.
    return hashlib.sha256(record.code_hash.encode("utf-8")).hexdigest()


def is_verified(student):
    values = session.get(SESSION_KEY) or {}
    marker = values.get(str(student.id))
    if not isinstance(marker, dict):
        # Older sessions stored only a timestamp. Do not trust those markers
        # after this security upgrade; the student must verify once again.
        if marker is not None:
            values.pop(str(student.id), None)
            session[SESSION_KEY] = values
        return False
    raw_timestamp = marker.get("verified_at")
    stored_fingerprint = marker.get("fingerprint")
    try:
        verified_at = datetime.fromisoformat(raw_timestamp)
    except (TypeError, ValueError):
        return False
    record = access_code_record(student)
    current_fingerprint = _verification_fingerprint(record)
    if not stored_fingerprint or stored_fingerprint != current_fingerprint:
        values.pop(str(student.id), None)
        session[SESSION_KEY] = values
        return False
    if datetime.utcnow() - verified_at > VERIFICATION_TTL:
        values.pop(str(student.id), None)
        session[SESSION_KEY] = values
        return False
    return True


def mark_verified(student):
    values = session.get(SESSION_KEY) or {}
    record = access_code_record(student)
    values[str(student.id)] = {
        "verified_at": datetime.utcnow().isoformat(),
        "fingerprint": _verification_fingerprint(record),
    }
    # Keep the signed cookie small and prevent stale verifications from growing.
    session[SESSION_KEY] = {
        key: value
        for key, value in values.items()
        if key == str(student.id)
    }


def code_status_label(record):
    if not record:
        return "Lama samayn"
    return {
        "draft": "Qabyo",
        "pending": "Sugaya ansixin",
        "approved": "La ansixiyey",
        "revoked": "La joojiyey",
    }.get(record.status, record.status)


def shift_label(value):
    return {"morning": "Gelin Hore", "afternoon": "Gelin Dambe"}.get(value, value or "Dhammaan gelinnada")
