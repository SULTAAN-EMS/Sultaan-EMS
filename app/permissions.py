from functools import wraps

from flask import abort, request
from flask_login import current_user


PERMISSIONS = [
    ("dashboard", "Dashboard"),
    ("students", "Students"),
    ("results", "Results"),
    ("subjects", "Subjects"),
    ("classes", "Classes"),
    ("exams", "Exams"),
    ("academic_years", "Academic Years"),
    ("users", "Users"),
    ("settings", "Settings"),
    ("import", "Import"),
    ("export", "Export"),
    ("reports", "Reports"),
    ("print", "Print"),
    ("publish_results", "Publish Results"),
    ("lock_results", "Lock Results"),
    ("unlock_results", "Unlock Results"),
    ("attendance", "Attendance"),
    ("id_cards", "ID Cards"),
    ("advanced_results", "Advanced Results"),
    ("teachers", "Teacher Department"),
    ("qr_verification", "QR Verification"),
    ("system_settings", "System Settings"),
    ("backup", "Backup"),
    ("restore", "Restore"),
    ("manage_academic_structure", "System Setup"),
    ("seat_arrangement", "Seat Arrangement"),
]

ENDPOINT_PERMISSIONS = {
    "admin.dashboard": "dashboard",
    "admin.students": "students",
    "admin.student_form": "students",
    "admin.delete_student": "students",
    "admin.toggle_student_lock": "students",
    "admin.import_students": "import",
    "admin.confirm_student_import": "import",
    "admin.student_import_template": "import",
    "admin.export_students": "export",
    "admin.results": "results",
    "admin.result_success_overlay_settings": "results",
    "admin.update_result_success_overlay_template": "results",
    "admin.update_result_success_overlay_labels": "results",
    "admin.activate_result_success_overlay_template": "results",
    "admin.update_result_success_overlay_timing": "results",
    "admin.save_results": "results",
    "admin.edit_result_set": "results",
    "admin.delete_result": "results",
    "admin.import_results": "import",
    "admin.confirm_result_import": "import",
    "admin.result_import_template": "import",
    "admin.export_results": "export",
    "admin.admin_print_report": "print",
    "admin.classes": "classes",
    "admin.delete_class": "classes",
    "admin.subjects": "subjects",
    "admin.delete_subject": "subjects",
    "admin.exams": "exams",
    "admin.delete_exam": "exams",
    "admin.toggle_exam": "publish_results",
    "admin.academic_years": "academic_years",
    "admin.switch_year": "academic_years",
    "admin.delete_academic_year": "academic_years",
    "admin.users": "users",
    "admin.reset_user_password": "users",
    "admin.settings": ("settings", "system_settings"),
    "admin.delete_grade_scale": ("settings", "system_settings"),
    "admin.audit_logs": "system_settings",
    "admin.feedback_complaints": "settings",
    "admin.reply_feedback_complaint": "settings",
    "admin_attendance.dashboard": "attendance",
    "admin_attendance.save": "attendance",
    "admin_attendance.bulk": "attendance",
    "admin_attendance.delete": "attendance",
    "admin_attendance.export_excel": ("attendance", "export"),
    "admin_attendance.export_pdf": ("attendance", "export"),
    "admin_attendance.print_sheet": ("attendance", "print"),
    "admin_id_cards.dashboard": "id_cards",
    "admin_id_cards.generate": "id_cards",
    "admin_id_cards.bulk_generate": "id_cards",
    "admin_id_cards.print_cards": ("id_cards", "print"),
    "admin_id_cards.export_pdf": ("id_cards", "export"),
    "admin_advanced_results.dashboard": ("advanced_results", "results"),
    "admin_advanced_results.new_dashboard": ("advanced_results", "results"),
    "admin_advanced_results.result_entry": "results",
    "admin_advanced_results.save_result_entry": "results",
    "admin_advanced_results.autosave_result_entry": "results",
    "admin_advanced_results.student_view": "results",
    "admin_advanced_results.students_management": "students",
    "admin_advanced_results.student_form": "students",
    "admin_advanced_results.delete_student": "students",
    "admin_advanced_results.toggle_student_lock": "students",
    "admin_advanced_results.import_students": "import",
    "admin_advanced_results.confirm_student_import": "import",
    "admin_advanced_results.student_import_template": "import",
    "admin_advanced_results.export_students": "export",
    "admin_advanced_results.bulk": "results",
    "admin_advanced_results.export_excel": ("advanced_results", "export"),
    "admin_teachers.dashboard": "teachers",
    "admin_teachers.teacher_list": "teachers",
    "admin_teachers.teacher_form": "teachers",
    "admin_teachers.teacher_view": "teachers",
    "admin_teachers.delete_teacher": "teachers",
    "admin_teachers.teacher_permissions": "teachers",
    "admin_teachers.performance": "teachers",
    "admin_teachers.activity": "teachers",
    "admin_teachers.teacher_settings": "teachers",
    "admin_teachers.export_teachers": ("teachers", "export"),
    "admin_teachers.print_teachers": ("teachers", "print"),
    "academic_structure.index": "manage_academic_structure",
    "academic_structure.academic_levels": "manage_academic_structure",
    "academic_structure.academic_level_form": "manage_academic_structure",
    "academic_structure.delete_academic_level": "manage_academic_structure",
    "academic_structure.academic_classes": "manage_academic_structure",
    "academic_structure.academic_class_form": "manage_academic_structure",
    "academic_structure.delete_academic_class": "manage_academic_structure",
    "academic_structure.academic_sections": "manage_academic_structure",
    "academic_structure.academic_section_form": "manage_academic_structure",
    "academic_structure.delete_academic_section": "manage_academic_structure",
    "seat_arrangement.index": "seat_arrangement",
    "seat_arrangement.hall_list": "seat_arrangement",
    "seat_arrangement.hall_form": "seat_arrangement",
    "seat_arrangement.delete_hall": "seat_arrangement",
    "seat_arrangement.builder": "seat_arrangement",
    "seat_arrangement.generate": "seat_arrangement",
    "seat_arrangement.optimize": "seat_arrangement",
    "seat_arrangement.save": "seat_arrangement",
    "seat_arrangement.replace_seat": "seat_arrangement",
    "seat_arrangement.print": ("seat_arrangement", "print"),
    "seat_mixer.index": "seat_arrangement",
    "seat_mixer.api_create_hall": "seat_arrangement",
    "seat_mixer.api_add_version": "seat_arrangement",
    "seat_mixer.api_versions": "seat_arrangement",
    "seat_mixer.api_students": "seat_arrangement",
    "seat_mixer.api_version_data": "seat_arrangement",
    "seat_mixer.api_save": "seat_arrangement",
    "seat_mixer.api_version_history": "seat_arrangement",
    "seat_mixer.api_history_snapshot": "seat_arrangement",
    "seat_mixer.api_restore_history_snapshot": "seat_arrangement",
    "seat_mixer.api_delete_history_snapshot": "seat_arrangement",
    "seat_mixer.api_class_students": "seat_arrangement",
    "seat_mixer.print_arrangement": ("seat_arrangement", "print"),
}


def can(permission):
    return current_user.is_authenticated and current_user.has_permission(permission)


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not can(permission):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def enforce_endpoint_permission():
    permission = ENDPOINT_PERMISSIONS.get(request.endpoint)
    if isinstance(permission, (tuple, list, set)):
        if not any(can(item) for item in permission):
            abort(403)
        return
    if permission and not can(permission):
        abort(403)
