from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from flask import current_app, g
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from . import db
from .models import (
    AcademicYear,
    AcademicLevel,
    AcademicClass,
    AttendanceRecord,
    ExamType,
    Subject,
    Exam,
    GradeScale,
    Result,
    Student,
    Setting,
    LabelTranslation,
    AcademicYearSubject,
    AcademicYearLevel,
    ExamMarkingConfiguration,
    ExamHall,
    ExamHallEnrollment,
    ExamHallSubject,
    ExamSessionSubject,
    StudentEnrollment,
)
from .attendance_rules import NON_SAT_STATUSES, normalize_attendance_status
from .enrollment_service import (
    get_enrollment_for_student_year,
    resolve_student_academic_context,
    student_enrollment_scope_query,
)


DEFAULT_SETTINGS = {
    "school_name": "Taysir Schools",
    "school_address": "Mogadishu, Somalia",
    "school_phone": "+252",
    "school_email": "info@example.com",
    "school_website": "https://example.com",
    "school_motto": "Excellence through knowledge",
    "principal_name": "Principal",
    "school_footer": "Prepared by the Examination Office",
    "logo_path": "",
    "admin_logo_path": "",
    "dashboard_title": "School Result Management",
    "dashboard_subtitle": "Academic records, publishing, and student records in one secure workspace.",
    "dashboard_theme": "light",
    "primary_color": "#002060",
    "secondary_color": "#007bff",
    "sidebar_color": "#001a4d",
    "admin_password_composition": "letters_numbers",
    "admin_password_min_length": "8",
    "admin_session_timeout_minutes": "60",
    # Phase 3B: global switch for year/level-scoped promotion rules.
    # Keep it off by default so existing result and transition behavior is
    # unchanged until an administrator explicitly enables the feature.
    "promotion_rules_enabled": "false",
    "dashboard_background": "",
    "visible_cards": "students,classes,exams,published,subjects,locked",
    "homepage_widgets": "search,quick_links,social",
    "search_footer_text": "© SULTAN | 2026. All Rights Reserved.",
    "search_footer_font_size": "1.15rem",
    "search_footer_font_weight": "800",
    "search_footer_text_color": "#f8fbff",
    "search_footer_background_color": "rgba(0, 21, 58, .72)",
    "search_footer_border_color": "rgba(255,196,61,.86)",
    "search_footer_visibility": "on",
    "typography_page_title_size": "clamp(2.1rem, 5vw, 4rem)",
    "typography_subtitle_size": "clamp(.9rem, 2vw, 1.35rem)",
    "typography_input_label_size": ".78rem",
    "typography_input_placeholder_size": "1rem",
    "typography_button_size": "clamp(1rem, 1.8vw, 1.35rem)",
    "typography_footer_size": "1.15rem",
    "typography_copyright_size": "1.15rem",
    "typography_student_info_size": "1rem",
    "typography_dashboard_heading_size": "1.35rem",
    "typography_table_text_size": ".92rem",
    "default_language": "en",
    "whatsapp_url": "",
    "facebook_url": "",
    "instagram_url": "",
    "telegram_url": "",
    "twitter_url": "",
    "email_url": "mailto:info@example.com",
    "call_url": "tel:+252",
    "maps_url": "",
    "principal_signature_path": "",
    "vice_principal_signature_path": "",
    "exam_officer_signature_path": "",
    "enable_phone_verification": "off",
    "report_header_style": "classic",
    "report_footer_text": "",
    "report_primary_color": "#002060",
    "report_accent_color": "#007bff",
    "report_font_family": "Segoe UI",
    "report_border_style": "rounded",
    "report_background": "#f8fbff",
    "report_watermark": "",
    "report_logo_position": "left",
    "report_qr_position": "right",
    "report_photo_position": "left",
    "report_signature_position": "bottom",
    "report_comment_box": "highlighted",
    "report_table_style": "striped",
    "principal_comment": "",
    "school_stamp_path": "",
    "student_photo_shape": "circle",
    "student_photo_size": "medium",
    "student_photo_border": "on",
    "student_photo_shadow": "on",
    "attendance_color_present": "#16a34a",
    "attendance_color_absent": "#dc2626",
    "attendance_color_late": "#f59e0b",
    "attendance_color_excused": "#2563eb",
    "attendance_color_medical_leave": "#7c3aed",
    "attendance_color_blocked": "#111827",
    "attendance_icon_present": "fa-circle-check",
    "attendance_icon_absent": "fa-circle-xmark",
    "attendance_icon_late": "fa-clock",
    "attendance_icon_excused": "fa-file-circle-check",
    "attendance_icon_medical_leave": "fa-kit-medical",
    "attendance_icon_blocked": "fa-ban",
    "id_card_size": "cr80",
    "id_card_orientation": "portrait",
    "id_card_template": "classic",
    "id_card_background": "#ffffff",
    "id_card_primary_color": "#002060",
    "id_card_accent_color": "#007bff",
    "id_card_font_family": "Segoe UI",
    "id_card_border_style": "solid",
    "id_card_rounded_corners": "on",
    "id_card_logo_position": "left",
    "id_card_photo_position": "left",
    "id_card_qr_position": "right",
    "id_card_icon_style": "solid",
    "id_card_label_style": "uppercase",
    "id_card_spacing": "comfortable",
    "id_card_print_margin": "8",
    "id_card_show_barcode": "off",
    "id_card_header_text": "KAARKA OGOLAANSHAHA IMTIXAANKA",
    "id_card_footer": "Fadlan kaadhkan haddii aad hesho la xidhiidh:",
    "id_card_found_contact_text": "Fadlan kaadhkan haddii aad hesho la xidhiidh:",
    "id_card_exam_type": "Examination Office",
    "id_card_watermark": "",
    "id_card_signature_text": "",
    "id_card_issue_months": "12",
    "id_card_office_signature": "Office Examination Signature",
    "id_card_stamp_text": "School Stamp",
    "result_page_primary_color": "#002060",
    "result_page_accent_color": "#007bff",
    "result_dashboard_primary_color": "#08246a",
    "result_dashboard_secondary_color": "#087cff",
    "result_dashboard_accent_color": "#f0447b",
    "result_dashboard_background_color": "#f5f8ff",
    "result_dashboard_card_color": "#ffffff",
    "result_dashboard_button_color": "#087cff",
    "result_dashboard_header_color": "#08246a",
    "result_dashboard_footer_color": "#07142e",
    "result_dashboard_table_header_color": "#087cff",
    "result_dashboard_text_color": "#07142e",
    "result_dashboard_muted_text_color": "#64748b",
    "result_dashboard_font_family": "Inter, Segoe UI, Arial, sans-serif",
    "result_dashboard_base_font_size": "13px",
    "result_dashboard_font_weight": "700",
    "result_dashboard_line_height": "1.35",
    "result_dashboard_border_radius": "12px",
    "result_dashboard_card_spacing": "9px",
    "result_dashboard_shadow": "soft",
    "result_dashboard_padding": "9px",
    "result_dashboard_margin": "9px",
    "result_dashboard_show_student_photo": "on",
    "result_dashboard_show_school_logo": "on",
    "result_dashboard_show_qr": "on",
    "result_dashboard_show_sidebar": "on",
    "result_dashboard_show_grade_scale": "on",
    "result_dashboard_show_performance": "on",
    "result_dashboard_show_teacher_remarks": "on",
    "result_dashboard_show_summary": "on",
    "result_dashboard_show_footer": "on",
    "result_dashboard_show_social_icons": "on",
    "result_dashboard_show_download_button": "on",
    "result_dashboard_show_print_button": "on",
    "result_dashboard_show_share_button": "on",
    "result_dashboard_show_top10_button": "on",
    "top10_tunnel_music_path": "",
    "top10_tunnel_music_tracks": "[]",
    "result_dashboard_background_image": "",
    "result_dashboard_default_avatar": "",
    "result_dashboard_footer_logo": "",
    "result_sidebar_theme": "modern_blue",
    "result_sidebar_student_name_size": "1.42rem",
    "result_sidebar_student_name_weight": "950",
    "result_sidebar_label_size": ".68rem",
    "result_sidebar_value_size": ".92rem",
    "result_sidebar_title_size": ".92rem",
    "result_sidebar_school_name_size": ".88rem",
    "result_sidebar_school_motto_size": ".66rem",
    "result_sidebar_photo_border_width": "4px",
    "result_sidebar_photo_border_color": "#d9b95d",
    "result_sidebar_photo_border_style": "solid",
    "result_sidebar_photo_border_radius": "28px",
    "result_sidebar_photo_width": "162px",
    "result_sidebar_photo_height": "176px",
    "result_sidebar_photo_object_fit": "cover",
    "result_sidebar_photo_object_position": "center",
    "result_sidebar_photo_shadow": "0 18px 38px rgba(0,0,0,.28)",
    "result_sidebar_label_color": "rgba(255,255,255,.72)",
    "result_sidebar_label_font_weight": "900",
    "result_sidebar_label_letter_spacing": ".04em",
    "result_sidebar_label_text_transform": "uppercase",
    "result_sidebar_value_color": "#ffffff",
    "result_sidebar_value_weight": "900",
    "result_sidebar_show_student_photo": "on",
    "result_sidebar_show_school_logo": "on",
    "result_sidebar_show_overlay_logo": "on",
    "result_sidebar_show_student_name": "on",
    "result_sidebar_show_student_class": "on",
    "result_sidebar_show_parent_name": "on",
    "result_sidebar_show_student_id": "on",
    "result_sidebar_show_exam_name": "on",
    "result_sidebar_show_download_date": "on",
    "result_sidebar_show_percentage": "on",
    "result_sidebar_show_school_name": "on",
    "result_sidebar_show_school_motto": "on",
    "result_table_style": "striped",
    "result_card_style": "soft",
    "result_button_style": "filled",
    "result_icon_style": "solid",
    "result_online_title_primary": "REPORT",
    "result_online_title_accent": "CARD",
    "result_online_quote": "Your hard work today builds your success tomorrow.",
    "result_label_mother_name": "Mother's Name",
    "result_label_student_id": "Student ID",
    "result_label_student_class": "Student Class",
    "result_label_exam_type": "Exam Type",
    "result_label_date_issued": "Date Issued",
    "result_label_subject_percentage": "Percentage of Subjects",
    "result_academic_summary_title": "Academic Summary",
    "result_teacher_remarks_title": "Teacher's Remarks",
    "result_footer_owner": "SULTAN",
    "download_datetime_format": "month_day_year_12",
    "print_brand_code": "TIS",
    "print_report_title": "Kaarka Natiijada Imtixaanka",
    "print_exam_banner_text": "FINAL EXAMINATION RESULT",
    "print_subtitle": "Official Academic Report",
    "print_student_heading": "Xogta Ardeyga",
    "print_marks_heading": "Natiijada Imtixaanka",
    "print_qr_label": "Scan To Verify",
    "print_comments_heading": "Comments",
    "print_signature_title": "Maamulka Dugsiga",
    "print_signature_subtitle": "School Administration",
    "print_footer_owner": "SULTAN",
    "print_layout_header_color": "#08266e",
    "print_layout_banner_color": "#073986",
    "print_layout_banner_accent": "#22d3ee",
    "print_layout_table_header_color": "#f8fbff",
    "print_layout_border_color": "#cddcf1",
    "print_layout_background_color": "#f8fbff",
    "print_layout_text_color": "#07143b",
    "print_layout_font_family": "Segoe UI",
    "print_layout_font_size": "8pt",
    "print_layout_font_weight": "700",
    "print_layout_margin": "4mm",
    "print_layout_padding": "3mm",
    "print_layout_radius": "3mm",
    "print_layout_shadow": "soft",
    "print_layout_table_row_height": "6.4mm",
    "print_layout_table_font_size": "7.4pt",
    "print_layout_page_spacing": "1.8mm",
    "print_show_school_logo": "on",
    "print_show_school_name": "on",
    "print_show_academic_year_badge": "on",
    "print_show_exam_banner": "on",
    "print_show_student_photo": "on",
    "print_show_qr_code": "on",
    "print_show_download_date": "on",
    "print_show_teacher_signature": "off",
    "print_show_principal_signature": "on",
    "print_show_footer": "off",
    "print_show_watermark": "off",
    "print_background_image": "",
    "print_watermark_image": "",
    "print_footer_logo": "",
    "verify_page_enabled": "on",
    "verify_page_title": "VERIFIED",
    "verify_page_subtitle": "This academic result has been successfully verified.",
    "verify_page_footer_text": "Secure • Authentic • Verified",
    "verify_page_copyright_text": "Official Digital Verification",
    "verify_success_message": "Official Academic Result",
    "verify_badge_style": "premium",
    "verify_theme": "green",
    "verify_school_motto": "XARUNTA KORRIINKA MASKAXDA",
    "verify_primary_color": "#063fa8",
    "verify_secondary_color": "#0f5bd7",
    "verify_accent_color": "#0f8f3d",
    "verify_background_color": "#f5f8fc",
    "verify_card_color": "#ffffff",
    "verify_success_color": "#0f9f4a",
    "verify_text_color": "#07142e",
    "verify_muted_text_color": "#64748b",
    "verify_button_color": "#063fa8",
    "verify_border_color": "#d8e5f4",
    "verify_icon_color": "#0f5bd7",
    "verify_font_family": "Inter, Segoe UI, Arial, sans-serif",
    "verify_font_size": "15px",
    "verify_font_weight": "800",
    "verify_card_radius": "24px",
    "verify_card_shadow": "soft",
    "verify_spacing": "16px",
    "verify_page_width": "860px",
    "verify_section_order": "status,student,summary,details,footer",
    "verify_show_student_photo": "on",
    "verify_show_school_logo": "on",
    "verify_show_badge": "on",
    "verify_show_digital_seal": "on",
    "verify_show_result_summary": "on",
    "verify_show_details": "on",
    "verify_show_footer": "on",
    "verify_fields_student_name": "on",
    "verify_fields_student_id": "on",
    "verify_fields_class": "on",
    "verify_fields_exam": "on",
    "verify_fields_academic_year": "on",
    "verify_fields_total_marks": "on",
    "verify_fields_percentage": "on",
    "verify_fields_grade": "off",
    "verify_fields_rank": "off",
    "verify_fields_status": "on",
    "verify_message": "This academic result has been successfully verified.",
    "verify_badge_text": "VERIFIED",
    "verify_status_text": "VERIFIED",
    "verify_id_prefix": "TIS",
    "verify_footer_heading": "Official Digital Verification",
    "verify_animation_success": "on",
    "verify_animation_loading": "on",
    "verify_design_preset": "premium",
    "verify_animation_fade": "on",
    "verify_animation_zoom": "off",
    "verify_animation_slide": "on",
    "verify_animation_bounce": "off",
    "verify_animation_ripple": "on",
    "verify_animation_glow": "on",
    "verify_custom_css": "",
    "verify_custom_js": "",
    "verify_background_image": "",
    "verify_default_student_photo": "",
    "verify_id_header_primary": "#2563eb",
    "verify_id_header_secondary": "#1e40af",
    "verify_id_badge_primary": "#10b981",
    "verify_id_badge_secondary": "#059669",
    "verify_id_logo_size": "120",
    "verify_id_header_padding": "48",
    "verify_id_photo_size": "200",
    "verify_id_photo_border_width": "4",
    "verify_id_photo_border_color": "#10b981",
    "verify_id_photo_radius": "20",
    "verify_id_card_radius": "20",
    "verify_id_card_padding": "32",
    "verify_id_card_spacing": "24",
    "verify_id_stamp_size": "140",
    "verify_id_stamp_color": "#10b981",
    "verify_id_status_color": "#10b981",
    "verify_id_status_dark": "#059669",
    "verify_id_badge_radius": "16",
    "verify_id_badge_animation": "on",
    "verify_id_glass_effect": "on",
    "verify_id_show_watermark": "on",
    "verify_id_photo_shadow": "on",
    "verify_id_show_header": "on",
    "verify_id_show_badge": "on",
    "verify_id_show_status_card": "on",
    "verify_id_show_verification_area": "on",
    "verify_id_show_footer": "on",
    "verify_id_show_icons": "on",
    "verify_id_show_background_decorations": "on",
    "verify_id_show_logo": "on",
    "verify_id_show_photo": "on",
    "verify_id_show_student_name": "on",
    "verify_id_show_student_id": "on",
    "verify_id_show_mother_name": "on",
    "verify_id_show_class": "on",
    "verify_id_show_section": "on",
    "verify_id_show_academic_year": "on",
    "verify_id_show_exam_type": "on",
    "verify_id_show_issue_date": "on",
    "verify_id_show_expiry_date": "on",
    "verify_id_show_stamp": "on",
    "verify_id_show_verification_code": "on",
    "verify_id_show_date_time": "on",
    "verify_id_font_family": "Segoe UI, sans-serif",
    "verify_id_font_size": "16",
    "verify_id_font_weight": "400",
    "verify_id_text_color": "#1f2937",
    "verify_id_letter_spacing": "0",
    "verify_id_line_height": "1.5",
    "verify_id_text_align": "left",
    "verify_id_header_font_size": "36",
    "verify_id_name_font_size": "24",
    "verify_id_label_font_size": "14",
    "verify_id_value_font_size": "14",
    "verify_id_bg_color": "#f0f9ff",
    "verify_id_card_bg": "#ffffff",
    "verify_id_border_color": "#e5e7eb",
    "verify_id_footer_bg": "#f8fafc",
    "verify_id_footer_text_color": "#1f2937",
    "verify_id_badge_text_color": "#059669",
    "verify_id_status_text_color": "#ffffff",
    "verify_id_shadow_color": "#000000",
    "verify_id_template_style": "premium",
    "result_success_overlay_active_template": "m1",
    "result_success_overlay_duration_seconds": "8",
    "result_success_overlay_show_progress_bar": "on",
    "result_success_overlay_allow_manual_close": "on",
    "academic_decimal_precision": "2",
    # Premium shared footer settings (Student Result, Verification, ID Verification)
    # These are auto-seeded on first run so no manual configuration is needed.
    "show_footer": "on",
    "footer_logo_path": "",
    "footer_copyright_text": "© SULTAAN EMS",
    "footer_year": "2026",
    "footer_shimmer_enabled": "true",
    "footer_bg_start": "#071931",
    "footer_bg_end": "#0c3366",
    "footer_gold": "#C9A227",
    "footer_teal": "#1B998B",
}


RESULT_SUCCESS_TEMPLATE_ORDER = ("m1", "m2", "term", "m3", "m4", "final")

RESULT_SUCCESS_TEMPLATE_DEFAULTS = {
    "m1": {
        "name": "Monthly Exam 1",
        "title": "Excellent Start!",
        "subtitle": "Strong result in {exam name}",
    },
    "m2": {
        "name": "Monthly Exam 2",
        "title": "Great Progress!",
        "subtitle": "Solid performance in {exam name}",
    },
    "term": {
        "name": "Term Exam",
        "title": "Impressive Mid-Year Result!",
        "subtitle": "Well done in the {exam name}",
    },
    "m3": {
        "name": "Monthly Exam 3",
        "title": "Momentum Building!",
        "subtitle": "Great result in {exam name}",
    },
    "m4": {
        "name": "Monthly Exam 4",
        "title": "Almost at the Finish!",
        "subtitle": "Excellent effort in {exam name}",
    },
    "final": {
        "name": "Final Exam",
        "title": "Congratulations! Promoted!",
        "subtitle": "Final result: Promoted to Next Class",
    },
}

RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS = {
    "name_caption": "Student result",
    "average_suffix": "average",
    "pill_podium": "Podium Finish",
    "pill_top5": "Top 5 Result",
    "pill_top10": "Top 10 Result",
    "pill_result": "Result: Passed",
    "placement_1": "1st Place",
    "placement_2": "2nd Place",
    "placement_3": "3rd Place",
    "placement_4": "Top 5 - 4th",
    "placement_5": "Top 5 - 5th",
    "placement_6": "6th",
    "placement_7": "7th",
    "placement_8": "8th",
    "placement_9": "9th",
    "placement_10": "10th",
    "placement_result": "Class Result",
}

for _template_key, _template_copy in RESULT_SUCCESS_TEMPLATE_DEFAULTS.items():
    DEFAULT_SETTINGS.setdefault(
        f"result_success_overlay_{_template_key}_title",
        _template_copy["title"],
    )
    DEFAULT_SETTINGS.setdefault(
        f"result_success_overlay_{_template_key}_subtitle",
        _template_copy["subtitle"],
    )
for _label_key, _label_value in RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS.items():
    DEFAULT_SETTINGS.setdefault(
        f"result_success_overlay_label_{_label_key}",
        _label_value,
    )


PROMOTION_COPY_MARKERS = ("promot", "next class", "graduate", "graduat", "advance")


def result_success_template_key(exam):
    """Map the existing Results Hub exam record to one stable overlay template key."""
    if not exam:
        return "m1"

    raw_value = " ".join(
        value for value in (getattr(exam, "short_code", ""), getattr(exam, "name", "")) if value
    ).casefold()
    compact_value = re.sub(r"[^a-z0-9]+", " ", raw_value).strip()

    if any(marker in compact_value for marker in ("final", "dhammaad", "dhamaad", "gunaanaad", "gunaaanad")):
        return "final"
    if any(marker in compact_value for marker in ("term", "mid year", "midyear", "teeram")):
        return "term"

    monthly_patterns = {
        "m1": ("m1", "mt1", "monthly 1", "monthly exam 1", "bileedka 1", "bileed 1"),
        "m2": ("m2", "mt2", "monthly 2", "monthly exam 2", "bileedka 2", "bileed 2"),
        "m3": ("m3", "mt3", "monthly 3", "monthly exam 3", "bileedka 3", "bileed 3"),
        "m4": ("m4", "mt4", "monthly 4", "monthly exam 4", "bileedka 4", "bileed 4"),
    }
    for template_key, markers in monthly_patterns.items():
        if any(marker in compact_value for marker in markers):
            return template_key

    # Unknown legacy names must never receive promotion messaging.
    return "m1"


def result_success_template_copy(template_key, settings=None):
    """Resolve editable overlay copy without allowing non-final promotion language."""
    settings = settings or get_settings()
    template_key = template_key if template_key in RESULT_SUCCESS_TEMPLATE_DEFAULTS else "m1"
    defaults = RESULT_SUCCESS_TEMPLATE_DEFAULTS[template_key]

    title = str(settings.get(f"result_success_overlay_{template_key}_title") or "").strip() or defaults["title"]
    subtitle = str(settings.get(f"result_success_overlay_{template_key}_subtitle") or "").strip() or defaults["subtitle"]
    if template_key != "final":
        if any(marker in title.casefold() for marker in PROMOTION_COPY_MARKERS):
            title = defaults["title"]
        if any(marker in subtitle.casefold() for marker in PROMOTION_COPY_MARKERS):
            subtitle = defaults["subtitle"]
    return {"name": defaults["name"], "title": title, "subtitle": subtitle}


def result_success_overlay_settings(settings=None):
    settings = settings or get_settings()
    try:
        duration = int(settings.get("result_success_overlay_duration_seconds", 8))
    except (TypeError, ValueError, InvalidOperation):
        duration = 8
    duration = min(max(duration, 3), 60)
    active_template = str(settings.get("result_success_overlay_active_template") or "m1").strip().casefold()
    if active_template not in RESULT_SUCCESS_TEMPLATE_DEFAULTS:
        active_template = "m1"
    return {
        "active_template": active_template,
        "duration_seconds": duration,
        "show_progress_bar": str(settings.get("result_success_overlay_show_progress_bar", "on")).casefold() == "on",
        "allow_manual_close": str(settings.get("result_success_overlay_allow_manual_close", "on")).casefold() == "on",
    }


def result_success_overlay_labels(settings=None):
    """Resolve shared, student-visible overlay labels from persisted settings."""
    settings = settings or get_settings()
    return {
        key: str(settings.get(f"result_success_overlay_label_{key}") or "").strip() or default
        for key, default in RESULT_SUCCESS_OVERLAY_LABEL_DEFAULTS.items()
    }


def _blend_hex(foreground, background, ratio):
    def parse(value):
        value = str(value or "#000000").lstrip("#")
        if len(value) == 3:
            value = "".join(char * 2 for char in value)
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))

    fg = parse(foreground)
    bg = parse(background)
    mixed = tuple(round((channel * ratio) + (base * (1 - ratio))) for channel, base in zip(fg, bg))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def result_success_position_tier(position, settings=None):
    try:
        position = int(position)
    except (TypeError, ValueError, InvalidOperation):
        position = None

    labels = result_success_overlay_labels(settings)
    tier_map = {
        1: ("podium", "&#x1F947;", "placement_1", "pill_podium", "&#x1F3C6;", "#f5c451", "#f59e0b", 70, "sonar-triple"),
        2: ("podium", "&#x1F948;", "placement_2", "pill_podium", "&#x1F3C6;", "#cbd5e1", "#94a3b8", 70, "sonar-triple"),
        3: ("podium", "&#x1F949;", "placement_3", "pill_podium", "&#x1F3C6;", "#f0a875", "#c2703d", 70, "sonar-triple"),
        4: ("top5", "&#x1F3C5;", "placement_4", "pill_top5", "&#x1F3AF;", "#4ade80", "#22c55e", 45, "sonar-triple"),
        5: ("top5", "&#x1F396;&#xFE0F;", "placement_5", "pill_top5", "&#x1F3AF;", "#4ade80", "#16a34a", 45, "sonar-triple"),
        6: ("top10", "&#x2B50;", "placement_6", "pill_top10", "&#x1F4CC;", "#67e8f9", "#22d3ee", 30, "sonar-triple"),
        7: ("top10", "&#x1F31F;", "placement_7", "pill_top10", "&#x1F4CC;", "#67e8f9", "#0ea5e9", 28, "sonar-triple"),
        8: ("top10", "&#x2728;", "placement_8", "pill_top10", "&#x1F4CC;", "#93c5fd", "#3b82f6", 27, "sonar-triple"),
        9: ("top10", "&#x1F537;", "placement_9", "pill_top10", "&#x1F4CC;", "#93c5fd", "#6366f1", 26, "sonar-triple"),
        10: ("top10", "&#x1F539;", "placement_10", "pill_top10", "&#x1F4CC;", "#a5b4fc", "#818cf8", 25, "sonar-triple"),
        # Positions 11-20 use a fixed, static sequence. Each ring style is paired
        # with the icon meaning intentionally: target/diamond/celebration pulse,
        # compass/sun sweep, books/handshake breathe, and leaf orbits.
        11: ("slot_1", "&#x1F3AF;", "placement_result", "pill_result", "&#x2705;", "#4ade80", "#16a34a", 22, "sonar-triple"),
        12: ("slot_2", "&#x1F9ED;", "placement_result", "pill_result", "&#x2705;", "#60a5fa", "#2563eb", 22, "conic-spin"),
        13: ("slot_3", "&#x1F4DA;", "placement_result", "pill_result", "&#x2705;", "#a78bfa", "#7c3aed", 22, "halo-breathe"),
        14: ("slot_4", "&#x1F48E;", "placement_result", "pill_result", "&#x2705;", "#22d3ee", "#0891b2", 22, "sonar-triple"),
        15: ("slot_5", "&#x1F4C8;", "placement_result", "pill_result", "&#x2705;", "#4ade80", "#22c55e", 22, "dashed-rotate"),
        16: ("slot_6", "&#x1F389;", "placement_result", "pill_result", "&#x2705;", "#f472b6", "#db2777", 22, "sonar-triple"),
        17: ("slot_7", "&#x1F91D;", "placement_result", "pill_result", "&#x2705;", "#fb923c", "#ea580c", 22, "halo-breathe"),
        18: ("slot_8", "&#x2600;&#xFE0F;", "placement_result", "pill_result", "&#x2705;", "#fde047", "#f59e0b", 22, "conic-spin"),
        19: ("slot_9", "&#x1F343;", "placement_result", "pill_result", "&#x2705;", "#86efac", "#16a34a", 22, "orbit-dot"),
        20: ("slot_10", "&#x1F4D6;", "placement_result", "pill_result", "&#x2705;", "#5eead4", "#0d9488", 22, "dashed-rotate"),
    }
    tier, icon, placement_key, pill_key, pill_icon, accent_a, accent_b, confetti_count, ring_style = tier_map.get(
        position,
        ("fallback", "&#x1F451;", "placement_result", "pill_result", "&#x2705;", "#c4b5fd", "#7c3aed", 24, "sonar-triple"),
    )
    return {
        "position": position,
        "tier": tier,
        "icon": icon,
        "placement": labels[placement_key],
        "pill": labels[pill_key],
        "pill_icon": pill_icon,
        "accent_a": accent_a,
        "accent_b": accent_b,
        "confetti_count": confetti_count,
        "ring_style": ring_style,
        "show_position": position is not None and 1 <= position <= 10,
        "show_metric": position is not None and position >= 11,
        "tint1": _blend_hex(accent_a, "#071a37", 0.18),
        "bg_a": _blend_hex(accent_a, "#0b1730", 0.17),
        "bg_b": _blend_hex(accent_b, "#060d1d", 0.14),
        "border": _blend_hex(accent_a, "#334155", 0.52),
        "ring": accent_a,
        "icon_bg": _blend_hex(accent_a, "#0f172a", 0.22),
        "glow": accent_b,
        "title_color": "#f8fafc",
        "tier_bg": _blend_hex(accent_a, "#0f172a", 0.22),
        "tier_color": "#f8fafc",
        "tier_border": _blend_hex(accent_a, "#ffffff", 0.7),
        "name_a": accent_a,
        "name_b": accent_b,
    }


def result_success_overlay_config(exam, position, average, settings=None, letter_grade=None):
    settings = settings or get_settings()
    template_key = result_success_template_key(exam)
    labels = result_success_template_copy(template_key, settings)
    exam_name = getattr(exam, "name", None) or labels["name"]
    return {
        "exam_type": template_key,
        "exam_name": exam_name,
        "title": labels["title"].replace("{exam name}", exam_name),
        "subtitle": labels["subtitle"].replace("{exam name}", exam_name),
        "average": average,
        "letter_grade": letter_grade,
        "settings": result_success_overlay_settings(settings),
        "labels": result_success_overlay_labels(settings),
        "tier": result_success_position_tier(position, settings),
    }

DEFAULT_GRADE_SCALES = [
    {"grade": "A+", "min_score": 95, "max_score": 100, "grade_point": 4.0, "comment": "Outstanding", "is_pass": True, "badge_color": "#065f46", "text_color": "#ffffff", "background_color": "#d1fae5", "border_color": "#10b981", "sort_order": 1},
    {"grade": "A", "min_score": 90, "max_score": 94, "grade_point": 3.9, "comment": "Excellent", "is_pass": True, "badge_color": "#16a34a", "text_color": "#ffffff", "background_color": "#dcfce7", "border_color": "#22c55e", "sort_order": 2},
    {"grade": "A-", "min_score": 85, "max_score": 89, "grade_point": 3.7, "comment": "Very Good", "is_pass": True, "badge_color": "#22c55e", "text_color": "#052e16", "background_color": "#f0fdf4", "border_color": "#86efac", "sort_order": 3},
    {"grade": "B+", "min_score": 80, "max_score": 84, "grade_point": 3.5, "comment": "Good", "is_pass": True, "badge_color": "#2563eb", "text_color": "#ffffff", "background_color": "#dbeafe", "border_color": "#60a5fa", "sort_order": 4},
    {"grade": "B", "min_score": 75, "max_score": 79, "grade_point": 3.2, "comment": "Above Average", "is_pass": True, "badge_color": "#3b82f6", "text_color": "#ffffff", "background_color": "#eff6ff", "border_color": "#93c5fd", "sort_order": 5},
    {"grade": "B-", "min_score": 70, "max_score": 74, "grade_point": 3.0, "comment": "Average", "is_pass": True, "badge_color": "#0ea5e9", "text_color": "#ffffff", "background_color": "#e0f2fe", "border_color": "#7dd3fc", "sort_order": 6},
    {"grade": "C+", "min_score": 65, "max_score": 69, "grade_point": 2.7, "comment": "Fair", "is_pass": True, "badge_color": "#f97316", "text_color": "#ffffff", "background_color": "#ffedd5", "border_color": "#fdba74", "sort_order": 7},
    {"grade": "C", "min_score": 60, "max_score": 64, "grade_point": 2.4, "comment": "Satisfactory", "is_pass": True, "badge_color": "#fb923c", "text_color": "#431407", "background_color": "#fff7ed", "border_color": "#fed7aa", "sort_order": 8},
    {"grade": "C-", "min_score": 50, "max_score": 59, "grade_point": 2.0, "comment": "Needs Improvement", "is_pass": True, "badge_color": "#facc15", "text_color": "#422006", "background_color": "#fef9c3", "border_color": "#fde047", "sort_order": 9},
    {"grade": "D", "min_score": 40, "max_score": 49, "grade_point": 1.0, "comment": "Weak", "is_pass": True, "badge_color": "#eab308", "text_color": "#422006", "background_color": "#fef3c7", "border_color": "#facc15", "sort_order": 10},
    {"grade": "E", "min_score": 20, "max_score": 39, "grade_point": 0.5, "comment": "Very Weak", "is_pass": False, "badge_color": "#ef4444", "text_color": "#ffffff", "background_color": "#fee2e2", "border_color": "#f87171", "sort_order": 11},
    {"grade": "F", "min_score": 0, "max_score": 19, "grade_point": 0.0, "comment": "Fail", "is_pass": False, "badge_color": "#7f1d1d", "text_color": "#ffffff", "background_color": "#fee2e2", "border_color": "#991b1b", "sort_order": 12},
]


def get_settings():
    rows = Setting.query.all()
    settings = DEFAULT_SETTINGS.copy()
    settings.update({row.key: row.value for row in rows})
    return settings


def seed_missing_settings():
    """Insert absent defaults without overwriting configured values.

    App startup can overlap briefly while a development reloader or multiple
    production workers start.  A nested transaction makes a concurrent insert
    harmless instead of allowing a duplicate settings key to abort startup.
    """
    for key, value in DEFAULT_SETTINGS.items():
        if db.session.get(Setting, key):
            continue
        try:
            with db.session.begin_nested():
                db.session.add(Setting(key=key, value=value))
                db.session.flush()
        except IntegrityError:
            # Another worker created the same default after our lookup.
            continue


def validate_admin_password(password, settings=None):
    """Validate administrator passwords against the centrally managed policy."""
    settings = settings or get_settings()
    value = str(password or "")
    try:
        minimum = max(6, min(32, int(settings.get("admin_password_min_length", "8"))))
    except (TypeError, ValueError):
        minimum = 8
    policy = str(settings.get("admin_password_composition", "letters_numbers"))
    if len(value) < minimum:
        return False, f"Password must be at least {minimum} characters."
    if policy == "numbers" and not value.isdigit():
        return False, "Password must contain numbers only."
    if policy == "letters" and not value.isalpha():
        return False, "Password must contain letters only."
    if policy == "letters_numbers" and not (any(char.isalpha() for char in value) and any(char.isdigit() for char in value)):
        return False, "Password must contain both letters and numbers."
    return True, ""


def academic_decimal_precision(settings=None):
    """Return the global mark/grade-point display precision (0 through 3)."""
    try:
        if settings is None or not hasattr(settings, "get"):
            settings = get_settings()
        precision = int(settings.get("academic_decimal_precision", "2"))
    except Exception:
        precision = 2
    return min(max(precision, 0), 3)


def format_academic_number(value, settings=None, precision=None):
    """Format marks and grade points consistently without changing calculations."""
    if value in (None, ""):
        value = 0
    try:
        decimal_value = Decimal(str(value))
    except Exception:
        decimal_value = Decimal("0")
    places = academic_decimal_precision(settings) if precision is None else min(max(int(precision), 0), 3)
    quantum = Decimal("1").scaleb(-places)
    return f"{decimal_value.quantize(quantum, rounding=ROUND_HALF_UP):.{places}f}"


def academic_round(value, settings=None):
    """Return a numeric value rounded with the configured academic precision."""
    return float(format_academic_number(value, settings=settings))


def competition_rank_lookup(scores_by_student):
    """Return dense ranks for the supplied official scores.

    Equal scores share a rank and the following distinct score receives the
    immediately next rank: 98.6, 98.6, 97.8 becomes 1, 1, 2.
    """
    ordered_scores = sorted(
        ((student_id, Decimal(str(score))) for student_id, score in scores_by_student.items()),
        key=lambda item: (-item[1], item[0]),
    )
    ranks = {}
    previous_score = None
    current_rank = 0
    for student_id, score in ordered_scores:
        if previous_score is None or score != previous_score:
            current_rank += 1
            previous_score = score
        ranks[student_id] = current_rank
    return ranks


def top_students_for_class(student, exam, limit=10):
    """Return the published Top 10 for one student's real class and exam.

    The ranking deliberately resolves results through the class level's subject
    records.  Identically named subjects from another level are separate
    academic records and cannot influence this public Top 10 view.
    """
    if not student or not exam:
        return []

    selected_placement = resolve_student_academic_context(student, exam.academic_year_id)
    selected_enrollment = selected_placement.get("enrollment") if selected_placement else None
    if not selected_placement:
        return []
    level_id = selected_placement.get("academic_level_id")
    if not level_id:
        return []

    if selected_placement.get("academic_year_level_id") or selected_placement.get("academic_year_class_id"):
        classmates_query = student_enrollment_scope_query(
            exam.academic_year_id,
            academic_year_level_id=selected_placement.get("academic_year_level_id"),
            academic_year_class_id=selected_placement.get("academic_year_class_id"),
            academic_section_id=selected_placement.get("academic_section_id"),
        ).filter(Student.is_active.is_(True))
    else:
        classmates_query = Student.query.filter_by(academic_year_id=exam.academic_year_id, is_active=True)
    class_id = selected_placement.get("academic_class_id")
    if not selected_enrollment:
        if class_id:
            class_filters = [Student.academic_class_id == class_id]
            if student.class_id:
                class_filters.append((Student.academic_class_id.is_(None)) & (Student.class_id == student.class_id))
            classmates_query = classmates_query.filter(or_(*class_filters))
        elif student.class_id:
            classmates_query = classmates_query.filter(Student.class_id == student.class_id)
        else:
            classmates_query = classmates_query.filter(Student.academic_level_id == level_id)

    classmates = classmates_query.order_by(Student.full_name, Student.id).all()
    if not classmates:
        return []

    subject_ids = []
    if selected_placement.get("academic_year_level_id"):
        subject_ids = [
            row.legacy_subject_id
            for row in AcademicYearSubject.query.filter_by(
                academic_year_id=exam.academic_year_id,
                academic_year_level_id=selected_placement.get("academic_year_level_id"),
                is_active=True,
            ).order_by(AcademicYearSubject.sort_order, AcademicYearSubject.name, AcademicYearSubject.id).all()
            if row.legacy_subject_id
        ]
        if not subject_ids:
            return []
    else:
        subject_ids = [
            subject.id
            for subject in Subject.query.filter_by(academic_level_id=level_id)
            .order_by(Subject.sort_order, Subject.name, Subject.id)
            .all()
        ]
    if not subject_ids:
        return []

    marks_by_student = {classmate.id: [] for classmate in classmates}
    rows = (
        Result.query.join(Result.subject)
        .filter(
            Result.exam_id == exam.id,
            Result.is_published.is_(True),
            Result.student_id.in_(marks_by_student),
            Result.subject_id.in_(subject_ids),
        )
        .all()
    )
    for row in rows:
        row_placement = resolve_student_academic_context(
            next((peer for peer in classmates if peer.id == row.student_id), None),
            exam.academic_year_id,
        )
        max_score = resolve_subject_max_score(
            row.subject,
            exam=exam,
            academic_year_level_id=(
                row_placement.get("academic_year_level_id") if row_placement else None
            ),
            academic_level_id=(
                row_placement.get("academic_level_id") if row_placement else level_id
            ),
        )
        if max_score > 0:
            marks_by_student[row.student_id].append((Decimal(str(row.score or 0)), max_score))

    averages = {}
    for student_id, marks in marks_by_student.items():
        if not marks:
            continue
        total = sum((score for score, _max_score in marks), Decimal("0"))
        maximum = sum((max_score for _score, max_score in marks), Decimal("0"))
        if maximum > 0:
            averages[student_id] = (total / maximum * Decimal("100")).quantize(Decimal("0.01"))

    ranks = competition_rank_lookup(averages)
    class_name = selected_placement.get("class_name") or "Class"
    ordered = sorted(
        (classmate for classmate in classmates if classmate.id in averages),
        key=lambda classmate: (-averages[classmate.id], classmate.full_name.casefold(), classmate.id),
    )[:max(1, min(int(limit or 10), 10))]
    return [
        {
            "student_id": classmate.student_code,
            "name": classmate.full_name,
            "photo_path": classmate.photo_path,
            "average": float(averages[classmate.id]),
            "rank": ranks[classmate.id],
            "class_name": class_name,
        }
        for classmate in ordered
    ]


SUBJECT_ICON_DEFAULTS = {
    "math": "fa-calculator",
    "mathematics": "fa-calculator",
    "physics": "fa-atom",
    "chemistry": "fa-flask-vial",
    "biology": "fa-dna",
    "business": "fa-briefcase",
    "technology": "fa-microchip",
    "history": "fa-landmark",
    "geography": "fa-earth-africa",
    "islamic": "fa-mosque",
    "islamic studies": "fa-mosque",
    "arabic": "fa-language",
    "somali": "fa-book-open-reader",
    "english": "fa-spell-check",
}


def subject_icon(subject_name, settings=None):
    settings = settings or get_settings()
    key = f"subject_icon_{slug(subject_name)}"
    uploaded = settings.get(key)
    if uploaded:
        return {"type": "image", "value": uploaded}
    normalized = (subject_name or "").strip().lower()
    for needle, icon in SUBJECT_ICON_DEFAULTS.items():
        if needle in normalized:
            return {"type": "fa", "value": icon}
    return {"type": "fa", "value": "fa-book"}


def subject_display_name(subject, settings=None):
    """Subjects use their configured full name everywhere in the system."""
    return subject.name if subject else ""


def slug(value):
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def grade_for(score, exam_id=None):
    """Get grade for a given score, optionally scoped to a specific exam"""
    try:
        score = Decimal(str(score or 0))
    except (TypeError, ValueError):
        score = Decimal("0")

    # Keep all callers on one request-scoped scale snapshot. This prevents
    # analytics and result pages from issuing one SQL query per percentage.
    try:
        cache = getattr(g, "_grade_scale_cache", None)
        if cache is None:
            cache = {}
            g._grade_scale_cache = cache
        cache_key = int(exam_id or 0)
        if cache_key not in cache:
            cache[cache_key] = load_grade_scale_cache(exam_id)
        return grade_for_from_cache(score, cache[cache_key])
    except RuntimeError:
        # Preserve utility behavior for callers outside an application context.
        pass

    # Regression fix: treat is_active=NULL as True for legacy production rows.
    from sqlalchemy import or_ as _or
    _active = _or(GradeScale.is_active.is_(True), GradeScale.is_active.is_(None))

    # First try to find exam-specific grade scale
    if exam_id:
        scale = (
            GradeScale.query.filter(
                _active,
                GradeScale.exam_id == exam_id,
                GradeScale.min_score <= score,
                GradeScale.max_score >= score,
            )
            .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
            .first()
        )
        if scale:
            return grade_scale_payload(scale)
    
    # Fall back to global grade scale (exam_id IS NULL)
    scale = (
        GradeScale.query.filter(
            _active,
            GradeScale.exam_id.is_(None),
            GradeScale.min_score <= score,
            GradeScale.max_score >= score,
        )
        .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
        .first()
    )
    if scale:
        return grade_scale_payload(scale)

    # Last resort: any scale regardless of exam_id
    scale = (
        GradeScale.query.filter(
            _active,
            GradeScale.min_score <= score,
            GradeScale.max_score >= score,
        )
        .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
        .first()
    )
    if scale:
        return grade_scale_payload(scale)
    
    return {"grade": "-", "comment": "", "grade_point": 0.0, "is_pass": False, "badge_color": "#64748b", "text_color": "#ffffff", "background_color": "#f1f5f9", "border_color": "#cbd5e1"}


def load_grade_scale_cache(exam_id=None):
    """Load active grade scales once for in-memory grade lookup.

    Regression fix: legacy production rows may have is_active=NULL when the
    column was added via ALTER TABLE after initial data was inserted. We treat
    NULL as True (active) so that existing grade scales are always honoured.
    """
    from sqlalchemy import or_ as _or

    # is_active IS TRUE  OR  is_active IS NULL (legacy rows without explicit value)
    active_condition = _or(
        GradeScale.is_active.is_(True),
        GradeScale.is_active.is_(None),
    )

    exam_scales = []
    if exam_id:
        exam_scales = (
            GradeScale.query.filter(
                active_condition,
                GradeScale.exam_id == exam_id,
            )
            .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
            .all()
        )

    global_scales = (
        GradeScale.query.filter(
            active_condition,
            GradeScale.exam_id.is_(None),
        )
        .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
        .all()
    )

    # Last-resort fallback: if no global scales found (e.g. every row has a
    # non-NULL exam_id), load *all* active scales regardless of exam scope.
    if not global_scales and not exam_scales:
        global_scales = (
            GradeScale.query.filter(active_condition)
            .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc())
            .all()
        )

    return {
        "exam": [grade_scale_cache_row(scale) for scale in exam_scales],
        "global": [grade_scale_cache_row(scale) for scale in global_scales],
        "fallback": [],
    }


def grade_for_from_cache(score, scale_cache):
    """Resolve a grade using preloaded scale rows without database access."""
    try:
        score_value = Decimal(str(score or 0))
    except (TypeError, ValueError):
        score_value = Decimal("0")

    for bucket in ("exam", "global", "fallback"):
        for row in scale_cache.get(bucket, []):
            if row["min_score"] <= score_value <= row["max_score"]:
                return row["payload"]

    return {"grade": "-", "comment": "", "grade_point": 0.0, "is_pass": False, "badge_color": "#64748b", "text_color": "#ffffff", "background_color": "#f1f5f9", "border_color": "#cbd5e1"}


def performance_tier_for(score, weak_config=None, fail_config=None):
    """Classify a percentage using the configured weak/fail report tiers.

    Grade-letter metadata (including ``GradeScale.is_pass``) is deliberately
    not used here.  Grade letters and report color tiers are separate
    Grade Management settings, so a configured tier is the only authority for
    weak/fail score-cell styling.
    """
    try:
        score_value = Decimal(str(score or 0))
    except (TypeError, ValueError):
        score_value = Decimal("0")

    def matches(config):
        if not config:
            return False
        try:
            minimum = Decimal(str(config.get("min")))
            maximum = Decimal(str(config.get("max")))
        except (AttributeError, TypeError, ValueError):
            return False
        return minimum <= score_value <= maximum

    is_fail = matches(fail_config)
    return {
        "is_fail": is_fail,
        "is_weak": not is_fail and matches(weak_config),
    }


def grade_scale_cache_row(scale):
    return {
        "min_score": Decimal(str(scale.min_score or 0)),
        "max_score": Decimal(str(scale.max_score or 0)),
        "payload": grade_scale_payload(scale),
    }


def grade_scale_payload(scale):
    return {
        "id": scale.id,
        "grade": scale.grade,
        "comment": scale.comment,
        "grade_point": float(scale.grade_point or 0),
        "is_pass": bool(scale.is_pass),
        "badge_color": scale.badge_color,
        "text_color": scale.text_color,
        "background_color": scale.background_color,
        "border_color": scale.border_color,
    }


def active_exam_for_student(student, preferred_year_id=None):
    """Return the generated active exam that best matches a student's academic scope."""
    if not student:
        return None

    active_filter = or_(Exam.is_published.is_(True), Exam.is_active.is_(True))
    year_ids = []
    if preferred_year_id:
        year_ids.append(preferred_year_id)
    enrollment_year_ids = [
        year_id
        for year_id, in (
            StudentEnrollment.query
            .filter_by(student_id=student.id)
            .with_entities(StudentEnrollment.academic_year_id)
            .order_by(StudentEnrollment.academic_year_id.desc(), StudentEnrollment.id.desc())
            .all()
        )
        if year_id
    ]
    for year_id in enrollment_year_ids:
        if year_id not in year_ids:
            year_ids.append(year_id)
    if student.academic_year_id:
        if student.academic_year_id not in year_ids:
            year_ids.append(student.academic_year_id)
    current_year = AcademicYear.query.filter_by(is_current=True).order_by(AcademicYear.id.desc()).first()
    if current_year and current_year.id not in year_ids:
        year_ids.append(current_year.id)

    def collect(rows, bucket, seen):
        for row in rows:
            if row.id not in seen:
                bucket.append(row)
                seen.add(row.id)

    candidates = []
    seen = set()
    for year_id in year_ids:
        collect(
            Exam.query.filter(Exam.academic_year_id == year_id, active_filter)
            .order_by(Exam.id.desc())
            .all(),
            candidates,
            seen,
        )

    if not candidates:
        for year_id in year_ids:
            collect(
                Exam.query.filter(Exam.academic_year_id == year_id)
                .order_by(Exam.id.desc())
                .all(),
                candidates,
                seen,
            )

    if not candidates:
        candidates = Exam.query.filter(active_filter).order_by(Exam.id.desc()).all()
    if not candidates:
        candidates = Exam.query.order_by(Exam.id.desc()).all()
    if not candidates:
        return None

    def score(exam):
        value = 0
        enrollment = get_enrollment_for_student_year(student.id, exam.academic_year_id)
        enrollment_level_id = enrollment.academic_year_level.legacy_level_id if enrollment and enrollment.academic_year_level else None
        enrollment_class_id = enrollment.academic_year_class.legacy_class_id if enrollment and enrollment.academic_year_class else None
        enrollment_section_id = enrollment.academic_section_id if enrollment else None
        if preferred_year_id and exam.academic_year_id == preferred_year_id:
            value += 20
        if enrollment:
            value += 16
        elif student.academic_year_id and exam.academic_year_id == student.academic_year_id:
            value += 16
        elif current_year and exam.academic_year_id == current_year.id:
            value += 12
        if exam.academic_section_id and exam.academic_section_id == enrollment_section_id:
            value += 8
        elif exam.academic_class_id and exam.academic_class_id == enrollment_class_id:
            value += 4
        if exam.academic_level_id and exam.academic_level_id == enrollment_level_id:
            value += 2
        if not exam.academic_section_id and not exam.academic_class_id and not exam.academic_level_id:
            value += 1
        return value

    return max(candidates, key=lambda exam: (score(exam), exam.id))


def seed_grade_scales():
    """Create default grade rows ONLY when the table is completely empty.

    If rows already exist, only backfill columns that are genuinely absent
    (NULL / falsy).  Never overwrite colours, score ranges, is_pass, or any
    other field the admin may have intentionally changed.
    """
    from . import db

    # Only seed defaults when the table is completely empty.
    if not GradeScale.query.first():
        for item in DEFAULT_GRADE_SCALES:
            db.session.add(GradeScale(**item))
        return

    # Backfill: touch only columns that are genuinely NULL / missing.
    # Do NOT overwrite colours, score ranges, is_pass, or grade_point —
    # those are admin-managed fields and must survive every deployment.
    defaults = {item["grade"]: item for item in DEFAULT_GRADE_SCALES}
    for scale in GradeScale.query.all():
        # is_active NULL → default True so grade lookups never silently break.
        if scale.is_active is None:
            scale.is_active = True

        item = defaults.get(scale.grade)
        if not item:
            continue

        # sort_order NULL → fill from defaults so ordering works.
        if scale.sort_order is None:
            scale.sort_order = item["sort_order"]

        # grade_point NULL on non-F grades → fill from defaults.
        if scale.grade_point is None and scale.grade != "F":
            scale.grade_point = item["grade_point"]

        # Intentionally NOT touching:
        #   badge_color, text_color, background_color, border_color
        #   → admin-saved; must never be reset by deployment.
        #   is_pass
        #   → admin-saved; must never be reset by deployment.
        #   min_score, max_score
        #   → admin-saved score ranges; must never be reset.


def _attendance_record_is_in_current_exam_scope(record, exam, *, year_aware):
    """Reject stale/unattached attendance rows before deriving MG.

    In the year-aware attendance flow, a non-sitting status is meaningful only
    when the row belongs to this exact exam and is still connected to the
    student's current hall roster and scheduled subject. Legacy-only years
    retain the historical exam-level behavior below for compatibility.
    """
    if not record or not exam or record.exam_id != exam.id:
        return False
    if record.academic_year_id != exam.academic_year_id:
        return False
    if not year_aware:
        return True
    if not record.exam_hall_id:
        return False
    hall = record.exam_hall
    if not hall:
        return False
    if hall.exam_id not in (None, exam.id):
        return False
    if hall.academic_year_id not in (None, exam.academic_year_id):
        return False
    if not ExamHallEnrollment.query.filter_by(
        exam_hall_id=record.exam_hall_id,
        student_id=record.student_id,
    ).first():
        return False
    if record.exam_session_id:
        session = record.exam_session
        if not session or session.exam_id not in (None, exam.id):
            return False
        return bool(
            ExamSessionSubject.query.filter_by(
                exam_session_id=record.exam_session_id,
                subject_id=record.subject_id,
            ).first()
        )
    return bool(
        ExamHallSubject.query.filter_by(
            exam_hall_id=record.exam_hall_id,
            subject_id=record.subject_id,
        ).first()
    )


def _exam_uses_year_aware_attendance(exam):
    return bool(
        AcademicYearLevel.query.filter_by(
            academic_year_id=exam.academic_year_id,
            is_active=True,
        ).first()
    )


def attendance_uf_subject_keys(exam, student_ids, subject_ids=None):
    """Return exact student/subject pairs that did not sit one examination.

    Attendance is the only source of truth.  The lookup is deliberately
    constrained by the Results Hub exam, academic year, student, and subject;
    an attendance row from another day, subject, or exam can never produce MG.
    Historical rows that predate ``exam_id`` remain readable only through the
    matching legacy ExamType in the same academic year.
    """
    if not exam or not student_ids:
        return set()

    year_aware = _exam_uses_year_aware_attendance(exam)
    scope_filters = [AttendanceRecord.exam_id == exam.id]
    # A year-aware exam must never inherit an old exam-type-only row. That
    # fallback is retained only for legacy years without year-aware scopes.
    if not year_aware:
        legacy_exam_type = ExamType.query.filter_by(
            academic_year_id=exam.academic_year_id,
            name=exam.name,
        ).first()
        if legacy_exam_type:
            scope_filters.append(
                (AttendanceRecord.exam_id.is_(None))
                & (AttendanceRecord.exam_type_id == legacy_exam_type.id)
            )

    query = AttendanceRecord.query.filter(
        AttendanceRecord.academic_year_id == exam.academic_year_id,
        AttendanceRecord.student_id.in_(list(student_ids)),
        or_(*scope_filters),
    )
    if subject_ids is not None:
        subject_ids = list(subject_ids)
        if not subject_ids:
            return set()
        query = query.filter(AttendanceRecord.subject_id.in_(subject_ids))

    # Several historic hall records can exist for the same subject.  The most
    # recently saved record is the authoritative correction for that exact
    # student + subject + exam scope.
    latest_by_pair = {}
    for record in query.order_by(
        AttendanceRecord.recorded_at.desc(), AttendanceRecord.id.desc()
    ).all():
        if record.subject_id is None:
            continue
        if not _attendance_record_is_in_current_exam_scope(
            record, exam, year_aware=year_aware
        ):
            continue
        latest_by_pair.setdefault((record.student_id, record.subject_id), record)

    return {
        pair
        for pair, record in latest_by_pair.items()
        if normalize_attendance_status(record.status) in NON_SAT_STATUSES
    }


def attendance_uf_record(exam, student_id, subject_id):
    """Return the authoritative non-sitting attendance row for one MG subject."""
    if not exam or not student_id or not subject_id:
        return None

    year_aware = _exam_uses_year_aware_attendance(exam)
    scope_filters = [AttendanceRecord.exam_id == exam.id]
    if not year_aware:
        legacy_exam_type = ExamType.query.filter_by(
            academic_year_id=exam.academic_year_id,
            name=exam.name,
        ).first()
        if legacy_exam_type:
            scope_filters.append(
                (AttendanceRecord.exam_id.is_(None))
                & (AttendanceRecord.exam_type_id == legacy_exam_type.id)
            )

    record = (
        AttendanceRecord.query
        .filter(
            AttendanceRecord.academic_year_id == exam.academic_year_id,
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.subject_id == subject_id,
            or_(*scope_filters),
        )
        .order_by(AttendanceRecord.recorded_at.desc(), AttendanceRecord.id.desc())
        .first()
    )
    if not record or not _attendance_record_is_in_current_exam_scope(
        record, exam, year_aware=year_aware
    ):
        return None
    return record if normalize_attendance_status(record.status) in NON_SAT_STATUSES else None


def calculate_score_totals(score_max_pairs):
    """Return totals using each subject's stored maximum mark.

    Keeping this calculation in one place prevents report surfaces from
    silently assuming that every subject is worth 100 marks.
    """
    total = Decimal("0")
    max_total = Decimal("0")
    for score, max_score in score_max_pairs:
        total += Decimal(str(score or 0))
        max_total += Decimal(str(max_score or 0))
    percentage = round(float(total / max_total * Decimal("100")), 2) if max_total else 0
    return total, max_total, percentage


def get_exam_marking_configuration(exam, academic_year_level_id=None, academic_level_id=None):
    """Return the default mark only for an exact year + level + exam scope."""
    if not exam:
        return None
    year_level_id = academic_year_level_id
    if not year_level_id and academic_level_id:
        year_level_id = (
            AcademicYearLevel.query
            .filter_by(
                academic_year_id=exam.academic_year_id,
                legacy_level_id=academic_level_id,
            )
            .with_entities(AcademicYearLevel.id)
            .scalar()
        )
    if not year_level_id:
        return None
    return ExamMarkingConfiguration.query.filter_by(
        academic_year_id=exam.academic_year_id,
        academic_year_level_id=year_level_id,
        exam_id=exam.id,
    ).first()


def resolve_subject_max_score(subject, exam=None, academic_year_level_id=None, academic_level_id=None):
    """Resolve a subject maximum from the scoped exam default, then legacy data."""
    configuration = get_exam_marking_configuration(
        exam,
        academic_year_level_id=academic_year_level_id,
        academic_level_id=academic_level_id,
    )
    if configuration:
        return Decimal(str(configuration.default_full_marks))
    return Decimal(str(getattr(subject, "max_score", 0) or 0))


def resolved_subject_maxima(subjects, exam=None, academic_year_level_id=None, academic_level_id=None):
    """Return ``{subject_id: maximum}`` using one exact exam context."""
    return {
        subject.id: resolve_subject_max_score(
            subject,
            exam=exam,
            academic_year_level_id=academic_year_level_id,
            academic_level_id=academic_level_id,
        )
        for subject in subjects
    }


CRITICAL_STAR_DESIGNS = ("emerald", "gold", "royalblue", "magenta")


def critical_subject_badges(exam, academic_year_level_id=None):
    """Return deterministic critical-subject badge metadata for one exam scope."""
    if not exam or not academic_year_level_id:
        return {}

    year_level = db.session.get(AcademicYearLevel, academic_year_level_id)
    if not year_level or year_level.academic_year_id != exam.academic_year_id:
        return {}

    # Keep the badge source identical to the exam-aware Promotion Rules scope.
    from .promotion_service import get_promotion_rule

    rule = get_promotion_rule(
        exam.academic_year_id,
        academic_year_level_id,
        exam_id=exam.id,
        active_only=True,
    )
    if not rule or not rule.critical_subjects:
        return {}

    critical_year_subject_ids = {
        item.academic_year_subject_id for item in rule.critical_subjects
    }
    scoped_subjects = AcademicYearSubject.query.filter_by(
        academic_year_id=exam.academic_year_id,
        academic_year_level_id=academic_year_level_id,
        is_active=True,
    ).all()
    legacy_subject_ids = {
        item.legacy_subject_id
        for item in scoped_subjects
        if item.id in critical_year_subject_ids and item.legacy_subject_id
    }
    if not legacy_subject_ids:
        return {}

    levels = (
        AcademicYearLevel.query
        .filter_by(academic_year_id=exam.academic_year_id, is_active=True)
        .order_by(AcademicYearLevel.sort_order, AcademicYearLevel.name, AcademicYearLevel.id)
        .all()
    )
    level_order = next(
        (index for index, item in enumerate(levels, start=1) if item.id == academic_year_level_id),
        None,
    )
    if not level_order:
        return {}

    design = CRITICAL_STAR_DESIGNS[(level_order - 1) % len(CRITICAL_STAR_DESIGNS)]
    threshold = float(rule.critical_subject_pass_threshold or 0)
    return {
        subject_id: {
            "design": design,
            "level_order": level_order,
            "minimum_percentage": threshold,
            "reason": "Maaddadani waxa ay ka mid tahay maadooyinka ay qasab tahay in uu ardeygu ku gudbo.",
        }
        for subject_id in legacy_subject_ids
    }


def result_payload(student, exam=None, public_only=True):
    query = Result.query.filter_by(student_id=student.id)
    if exam:
        query = query.filter_by(exam_id=exam.id)
    if public_only:
        query = query.join(Result.exam).filter(Result.is_published.is_(True))

    # A result subject is valid only for the student's actual level.  This
    # prevents same-name records from another level (for example English) from
    # leaking into the student portal or its printable counterpart.
    selected_placement = resolve_student_academic_context(
        student,
        exam.academic_year_id if exam else student.academic_year_id,
    )
    student_level_id = selected_placement.get("academic_level_id") if selected_placement else None
    selected_year_level_id = selected_placement.get("academic_year_level_id") if selected_placement else None
    query = query.join(Result.subject)
    selected_subject_ids = []
    if selected_year_level_id and exam:
        selected_subject_ids = [
            row.legacy_subject_id
            for row in AcademicYearSubject.query.filter_by(
                academic_year_id=exam.academic_year_id,
                academic_year_level_id=selected_year_level_id,
                is_active=True,
            ).all()
            if row.legacy_subject_id
        ]
    if selected_year_level_id:
        # A configured year-level with no subject mappings is incomplete setup,
        # not permission to expose every legacy subject. This applies to both
        # enrolled and legacy-only students.
        subject_scope_filter = Subject.id.in_(selected_subject_ids) if selected_subject_ids else Subject.id == -1
    elif selected_placement:
        # Legacy-only records remain readable for their own stored year and
        # level.  They are never used for an unrelated historical year.
        subject_scope_filter = Subject.academic_level_id == student_level_id if student_level_id else Subject.id == -1
    else:
        subject_scope_filter = Subject.id == -1
    rows = (
        query.filter(subject_scope_filter)
        .order_by(Result.subject_id.asc())
        .all()
        if student_level_id
        else []
    )
    maxima = resolved_subject_maxima(
        [row.subject for row in rows],
        exam=exam,
        academic_year_level_id=selected_year_level_id,
        academic_level_id=student_level_id,
    )
    total, max_total, average = calculate_score_totals(
        (row.score, maxima.get(row.subject_id, Decimal(str(row.subject.max_score or 0))))
        for row in rows
    )
    settings = dict(get_settings())
    active_exam = exam or (rows[0].exam if rows else None)
    critical_badges = critical_subject_badges(active_exam, selected_year_level_id)
    portal_outcome = {"code": "NOT_EVALUATED", "label": "LAMA QIIMEYN", "tone": "muted"}
    if active_exam and selected_placement and selected_placement.get("enrollment"):
        # Local import avoids the promotion_service -> services import cycle.
        from .promotion_service import portal_academic_outcome

        portal_outcome = portal_academic_outcome(
            selected_placement["enrollment"],
            exam_id=active_exam.id,
        )
    if active_exam:
        ex_title = active_exam.name.strip().upper()
        if "RESULT" not in ex_title:
            ex_title += " RESULT"
        settings["print_exam_banner_text"] = ex_title

    # Resolve every grade in this payload from one in-memory scale snapshot.
    # This keeps the public result, print report, and verification view aligned
    # with the selected exam's Grade Management configuration.
    grade_cache = load_grade_scale_cache(active_exam.id if active_exam else None)
    overall = grade_for_from_cache(average, grade_cache)
    status = "Gudbay" if overall.get("is_pass") else "Haray"

    uf_subject_keys = attendance_uf_subject_keys(
        active_exam,
        [student.id],
    )
    subject_rows = []
    for row in rows:
        max_score = maxima.get(row.subject_id, Decimal(str(row.subject.max_score or 0)))
        percentage_raw = Decimal(row.score) / max_score * 100 if max_score else 0
        percentage = round(float(percentage_raw), 2)
        automatic_grade = grade_for_from_cache(percentage_raw, grade_cache)
        displayed_grade = dict(automatic_grade)
        displayed_grade["grade"] = row.grade_override or automatic_grade["grade"]
        displayed_grade["comment"] = row.comment or automatic_grade["comment"]
        subject_rows.append(
            {
                "id": row.id,
                "subject_id": row.subject_id,
                "subject": subject_display_name(row.subject, settings).strip(),
                "score": float(row.score),
                "max_score": float(max_score),
                "grade": displayed_grade,
                "automatic_grade": automatic_grade,
                "status": "Pass" if displayed_grade.get("is_pass", automatic_grade.get("is_pass")) else "Needs Support",
                "percentage": percentage,
                "icon": subject_icon(row.subject.name, settings),
                "is_uf": (student.id, row.subject_id) in uf_subject_keys,
                "is_critical": row.subject_id in critical_badges,
                "critical_badge": critical_badges.get(row.subject_id),
            }
        )

    # An absent student can legitimately have no Result row at all.  Surface
    # that subject as MG without manufacturing a result, changing totals, or
    # affecting grades/ranks.  Existing numerical rows remain authoritative.
    missing_uf_subject_ids = {
        subject_id
        for student_id, subject_id in uf_subject_keys
        if student_id == student.id and subject_id not in {row.subject_id for row in rows}
    }
    if missing_uf_subject_ids and student_level_id:
        missing_subject_query = Subject.query.filter(Subject.id.in_(missing_uf_subject_ids))
        if selected_subject_ids:
            missing_subject_query = missing_subject_query.filter(Subject.id.in_(selected_subject_ids))
        elif selected_placement and student_level_id:
            missing_subject_query = missing_subject_query.filter(Subject.academic_level_id == student_level_id)
        else:
            missing_subject_query = missing_subject_query.filter(Subject.id == -1)
        for subject in (
            missing_subject_query
            .order_by(Subject.id.asc())
            .all()
        ):
            automatic_grade = grade_for_from_cache(Decimal("0"), grade_cache)
            subject_rows.append(
                {
                    "id": None,
                    "subject_id": subject.id,
                    "subject": subject_display_name(subject, settings).strip(),
                    "score": 0.0,
                    "max_score": float(resolve_subject_max_score(
                        subject,
                        exam=active_exam,
                        academic_year_level_id=selected_year_level_id,
                        academic_level_id=student_level_id,
                    )),
                    "grade": automatic_grade,
                    "automatic_grade": automatic_grade,
                    "status": "Needs Support",
                    "percentage": 0.0,
                    "icon": subject_icon(subject.name, settings),
                    "is_uf": True,
                    "is_critical": subject.id in critical_badges,
                    "critical_badge": critical_badges.get(subject.id),
                }
            )

    rank = None
    if rows and active_exam:
        # Rank against the student's own class first.  Older student records that
        # only have the legacy class/level fields remain in the same scope.
        peer_year_id = active_exam.academic_year_id or student.academic_year_id
        peer_placement = resolve_student_academic_context(student, peer_year_id) if peer_year_id else None
        peer_enrollment = peer_placement.get("enrollment") if peer_placement else None
        if peer_placement and (
            peer_placement.get("academic_year_level_id")
            or peer_placement.get("academic_year_class_id")
        ):
            peer_query = student_enrollment_scope_query(
                peer_year_id,
                academic_year_level_id=peer_placement.get("academic_year_level_id"),
                academic_year_class_id=peer_placement.get("academic_year_class_id"),
                academic_section_id=peer_placement.get("academic_section_id"),
            ).filter(Student.is_active.is_(True))
        elif peer_placement:
            peer_query = Student.query.filter_by(academic_year_id=peer_year_id, is_active=True)
        else:
            peer_query = Student.query.filter(Student.id == -1)
        if peer_placement and not peer_enrollment:
            peer_class_id = peer_placement.get("academic_class_id")
            if peer_class_id:
                class_filters = [Student.academic_class_id == peer_class_id]
                if student.class_id:
                    class_filters.append(
                        (Student.academic_class_id.is_(None)) & (Student.class_id == student.class_id)
                    )
                peer_query = peer_query.filter(or_(*class_filters))
            elif student.class_id:
                peer_query = peer_query.filter(Student.class_id == student.class_id)
            elif peer_placement.get("academic_level_id"):
                level_filters = [Student.academic_level_id == peer_placement.get("academic_level_id")]
                if student.level:
                    level_filters.append(
                        (Student.academic_level_id.is_(None)) & (Student.level == student.level)
                    )
                peer_query = peer_query.filter(or_(*level_filters))
            elif peer_placement.get("level_name"):
                peer_query = peer_query.filter(Student.level == student.level)

        peers = peer_query.all()
        subject_maxima = resolved_subject_maxima(
            [row.subject for row in rows],
            exam=active_exam,
            academic_year_level_id=peer_placement.get("academic_year_level_id") if peer_placement else None,
            academic_level_id=peer_placement.get("academic_level_id") if peer_placement else None,
        )
        if peers and subject_maxima:
            peer_rows_query = Result.query.filter(
                Result.exam_id == active_exam.id,
                Result.student_id.in_([peer.id for peer in peers]),
                Result.subject_id.in_(subject_maxima),
            )
            if public_only:
                peer_rows_query = peer_rows_query.filter(Result.is_published.is_(True))

            scores_by_student = {peer.id: Decimal("0") for peer in peers}
            for peer_row in peer_rows_query.all():
                scores_by_student[peer_row.student_id] += Decimal(peer_row.score)

            maximum_score = sum(subject_maxima.values(), Decimal("0"))
            official_scores = {
                peer_id: round(float(score / maximum_score * 100), 2) if maximum_score else 0
                for peer_id, score in scores_by_student.items()
            }
            rank = competition_rank_lookup(official_scores).get(student.id)

    # Regression fix: treat is_active=NULL as True for legacy production rows.
    from sqlalchemy import or_ as _or_rs
    _active_rs = _or_rs(GradeScale.is_active.is_(True), GradeScale.is_active.is_(None))

    exam_grade_scales = (
        GradeScale.query.filter(_active_rs, GradeScale.exam_id == exam.id)
        .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc()).all()
        if exam else []
    )
    result_grade_scales = exam_grade_scales or (
        GradeScale.query.filter(_active_rs, GradeScale.exam_id.is_(None))
        .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc()).all()
    )
    # Final fallback: any active scale if the above queries return nothing
    if not result_grade_scales:
        result_grade_scales = (
            GradeScale.query.filter(_active_rs)
            .order_by(GradeScale.sort_order.asc(), GradeScale.min_score.desc()).all()
        )

    return {
        "student": student,
        "exam": exam or (rows[0].exam if rows else None),
        "subjects": subject_rows,
        "total": float(total),
        "max_total": float(max_total),
        "average": average,
        "status": status,
        "overall_grade": overall,
        "grade_scales": result_grade_scales,
        "rank": rank,
        "comment": overall.get("comment") or "",
        "settings": settings,
        "portal_outcome": portal_outcome,
    }


def automatic_comment(average, exam_id=None):
    return grade_for(average, exam_id=exam_id).get("comment") or ""


def get_label(label_key, language_code=None, default=None):
    """
    Get translated label text for a given label_key and language_code.
    Falls back to Somali (so) if the requested language is not available,
    then to the default value if provided.
    """
    if not language_code:
        # Try to get from settings, default to Somali
        settings = get_settings()
        language_code = settings.get("default_language", "so")
    
    # Try requested language first
    label = LabelTranslation.query.filter_by(
        label_key=label_key,
        language_code=language_code
    ).first()
    
    if label:
        return label.text_value
    
    # Fall back to Somali if requested language not found
    if language_code != "so":
        label = LabelTranslation.query.filter_by(
            label_key=label_key,
            language_code="so"
        ).first()
        if label:
            return label.text_value
    
    # Fall back to default if provided
    if default is not None:
        return default
    
    # Final fallback: return the label_key itself
    return label_key


def get_all_labels(language_code=None):
    """
    Get all labels for a given language as a dictionary.
    Useful for bulk loading labels for a template.
    """
    if not language_code:
        settings = get_settings()
        language_code = settings.get("default_language", "so")
    
    labels = LabelTranslation.query.filter_by(language_code=language_code).all()
    return {label.label_key: label.text_value for label in labels}


def is_setup_complete():
    """
    Check if the basic Setup configuration is complete.
    Returns a tuple: (is_complete, missing_items)
    """
    missing = []
    
    # Check for active academic year
    if not AcademicYear.query.filter_by(is_current=True).first():
        missing.append("Academic Year")
    
    # Check for at least one exam
    if not Exam.query.filter_by(is_active=True).first():
        missing.append("Exam Type")
    
    # Check for at least one level
    if not AcademicLevel.query.filter_by(is_active=True).first():
        missing.append("Academic Level")
    
    # Check for at least one class
    if not AcademicClass.query.first():
        missing.append("Class")
    
    # Check for at least one subject
    if not Subject.query.first():
        missing.append("Subject")
    
    return (len(missing) == 0, missing)


def require_setup_complete():
    """
    Helper to redirect to Setup if configuration is incomplete.
    Returns True if setup is complete, False otherwise.
    """
    from flask import redirect, url_for, flash
    
    is_complete, missing = is_setup_complete()
    if not is_complete:
        flash(f"Setup incomplete. Please configure: {', '.join(missing)}", "warning")
        return False
    return True
