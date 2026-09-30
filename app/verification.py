import base64
import secrets
from io import BytesIO

import qrcode
from flask import url_for

from . import db
from .models import ReportVerification


def get_or_create_verification(student, exam):
    record = ReportVerification.query.filter_by(student_id=student.id, exam_id=exam.id).first()
    if record:
        return record
    record = ReportVerification(student=student, exam=exam, token=secrets.token_urlsafe(32), is_valid=True)
    db.session.add(record)
    db.session.flush()
    return record


def qr_data_uri(url):
    image = qrcode.make(url)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verification_payload(student, exam):
    record = get_or_create_verification(student, exam)
    verify_url = url_for("public.verify_report", token=record.token, _external=True)
    return {"record": record, "url": verify_url, "qr_code": qr_data_uri(verify_url)}


def id_card_qr_payload(issue, exam_id=None):
    # The persisted issue is authoritative. The explicit argument is retained
    # for seating-print compatibility, but cannot override a bound card exam.
    effective_exam_id = getattr(issue, "exam_id", None) or exam_id
    query = {"exam_id": int(effective_exam_id)} if effective_exam_id else {}
    verify_url = url_for("public.qr_landing", token=issue.token, _external=True, **query)
    return {"url": verify_url, "qr_code": qr_data_uri(verify_url)}
