"""Apply migrations required by the teacher result workflow before serving."""


def apply_teacher_result_report_migrations(database_url):
    url = str(database_url or "").strip().lower()
    if not url.startswith(("postgres://", "postgresql://", "postgresql+")):
        return False

    from migrations.phase_27_teacher_missing_mark_reports import upgrade as create_reports
    from migrations.phase_28_teacher_missing_mark_unreadable_reason import upgrade as extend_reasons

    create_reports()
    extend_reasons()
    return True
