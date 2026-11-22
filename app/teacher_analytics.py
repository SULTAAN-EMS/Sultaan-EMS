import statistics
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from flask import g, has_request_context

from . import db
from .models import (
    AcademicClass,
    AcademicLevel,
    AcademicSection,
    AcademicYear,
    AcademicYearClass,
    AttendanceRecord,
    Exam,
    ExamSession,
    Result,
    SchoolClass,
    Student,
    Subject,
    TeacherResultAssignment,
)
from .attendance_rules import NON_SAT_STATUSES, normalize_attendance_status
from .services import grade_for, load_grade_scale_cache, resolve_subject_max_score
from .enrollment_service import EnrollmentValidationError, enrollment_placement_for_student, student_enrollment_legacy_scope_query, student_enrollment_scope_query


def _request_cache(name):
    """Return a request-local cache without affecting CLI/background callers."""
    if not has_request_context():
        return None
    cache = getattr(g, name, None)
    if cache is None:
        cache = {}
        setattr(g, name, cache)
    return cache


def _filter_cache_key(filters):
    return tuple(sorted((str(key), repr(value)) for key, value in (filters or {}).items()))


def _pct(result):
    max_score = _result_max_score(result)
    if not max_score:
        return 0.0
    return round(float(Decimal(result.score) / max_score * 100), 2)


def _result_max_score(result):
    """Resolve the denominator in the result's exact exam and student scope."""
    exam = result.exam
    subject = result.subject
    if not exam or not subject:
        return Decimal(str(getattr(subject, "max_score", 0) or 0))

    placement = None
    placement_key = (result.student_id, exam.academic_year_id)
    placement_cache = _request_cache("_teacher_result_placements_cache")
    if placement_cache is not None and placement_key in placement_cache:
        placement = placement_cache[placement_key]
    else:
        student = result.student
        if student:
            placement = enrollment_placement_for_student(student, exam.academic_year_id)
        if placement_cache is not None:
            placement_cache[placement_key] = placement

    academic_level_id = (
        (placement or {}).get("academic_level_id")
        or exam.academic_level_id
    )
    academic_year_level_id = (placement or {}).get("academic_year_level_id")
    cache_key = (exam.id, academic_year_level_id, academic_level_id, subject.id)
    maxima_cache = _request_cache("_teacher_result_maxima_cache")
    if maxima_cache is not None and cache_key in maxima_cache:
        return maxima_cache[cache_key]

    maximum = resolve_subject_max_score(
        subject,
        exam=exam,
        academic_year_level_id=academic_year_level_id,
        academic_level_id=academic_level_id,
    )
    try:
        maximum = Decimal(str(maximum))
    except Exception:
        maximum = Decimal("0")
    if maxima_cache is not None:
        maxima_cache[cache_key] = maximum
    return maximum


def _safe_mean(values):
    return round(sum(values) / len(values), 2) if values else 0


def _safe_median(values):
    return round(statistics.median(values), 2) if values else 0


def _safe_mode(values):
    if not values:
        return 0
    rounded = [round(v, 1) for v in values]
    return statistics.mode(rounded) if rounded else 0


def _safe_stdev(values):
    return round(statistics.stdev(values), 2) if len(values) > 1 else 0


def teacher_assignments(teacher):
    """Get teacher's academic assignments using new hierarchy with legacy fallback"""
    cache = _request_cache("_teacher_assignments_cache")
    cache_key = teacher.id
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    # TeacherResultAssignment is the authoritative scope used by the teacher
    # result-entry workflow. The older many-to-many relations are retained as
    # a fallback for teachers who have not yet been migrated to assignments.
    result_assignments = (
        TeacherResultAssignment.query
        .filter(
            TeacherResultAssignment.teacher_id == teacher.id,
            TeacherResultAssignment.deleted_at.is_(None),
        )
        .all()
    )
    if result_assignments:
        academic_levels = []
        academic_classes = []
        sections = list(teacher.sections)
        subjects = []
        seen_levels = set()
        seen_classes = set()
        seen_subjects = set()
        for assignment in result_assignments:
            school_class = assignment.school_class
            subject = assignment.subject
            level = getattr(school_class, "academic_level", None) if school_class else None
            if level and level.id not in seen_levels:
                academic_levels.append(level)
                seen_levels.add(level.id)
            if school_class and school_class.id not in seen_classes:
                academic_classes.append(school_class)
                seen_classes.add(school_class.id)
            if subject and subject.id not in seen_subjects:
                subjects.append(subject)
                seen_subjects.add(subject.id)
    else:
        academic_levels = list(teacher.academic_levels)
        academic_classes = list(teacher.classes)
        sections = list(teacher.sections)
        subjects = list(teacher.subjects)
    
    # Legacy fallback for SchoolClass
    legacy_classes = [cls for cls in academic_classes if hasattr(cls, 'name')]
    
    academic_level_ids = {level.id for level in academic_levels}
    class_ids = {cls.id for cls in academic_classes}
    section_ids = {section.id for section in sections}
    subject_ids = {subject.id for subject in subjects}
    
    assignments = (academic_levels, academic_classes, sections, subjects, academic_level_ids, class_ids, section_ids, subject_ids)
    if cache is not None:
        cache[cache_key] = assignments
    return assignments


SHIFT_LABELS = {
    "morning": "Gelin Hore",
    "afternoon": "Gelin Dambe",
}


def _shift_key(value):
    value = str(value or "").strip().lower()
    return {
        "morning": "morning",
        "gelin hore": "morning",
        "afternoon": "afternoon",
        "gelin dambe": "afternoon",
    }.get(value, value)


def _year_class_for_assignment(assignment):
    """Resolve the year-aware class behind one legacy assignment class."""
    cache = _request_cache("_teacher_year_class_cache")
    key = (assignment.academic_year_id, assignment.class_id)
    if cache is not None and key in cache:
        return cache[key]

    legacy_level_id = getattr(assignment.school_class, "academic_level_id", None)
    query = (
        AcademicYearClass.query
        .join(AcademicYearClass.academic_year_level)
        .filter(
            AcademicYearClass.legacy_class_id == assignment.class_id,
            AcademicYearClass.academic_year_level.has(
                academic_year_id=assignment.academic_year_id,
            ),
        )
    )
    candidates = query.all()
    if legacy_level_id:
        candidates = [
            item for item in candidates
            if item.academic_year_level
            and item.academic_year_level.legacy_level_id == legacy_level_id
        ] or candidates
    value = candidates[0] if len(candidates) == 1 else None
    if cache is not None:
        cache[key] = value
    return value


def _student_shift(student, academic_year_id):
    placement = enrollment_placement_for_student(student, academic_year_id)
    year_class_id = (placement or {}).get("academic_year_class_id")
    if not year_class_id:
        return None
    cache = _request_cache("_teacher_student_shift_cache")
    if cache is not None and year_class_id in cache:
        return cache[year_class_id]
    year_class = db.session.get(AcademicYearClass, year_class_id)
    value = _shift_key(getattr(year_class, "school_shift", None)) if year_class else None
    if cache is not None:
        cache[year_class_id] = value
    return value


def scoped_teacher_result_assignments(teacher, filters=None):
    """Return the exact assignment pairs allowed by the selected analytics scope."""
    filters = filters or {}
    query = TeacherResultAssignment.query.filter(
        TeacherResultAssignment.teacher_id == teacher.id,
        TeacherResultAssignment.deleted_at.is_(None),
        TeacherResultAssignment.is_active.is_(True),
    )
    for field in ("academic_year_id", "exam_id", "class_id", "subject_id"):
        value = filters.get(field)
        if value:
            query = query.filter(getattr(TeacherResultAssignment, field) == value)

    assignments = []
    level_id = filters.get("academic_level_id")
    shift = _shift_key(filters.get("shift"))
    for assignment in query.all():
        if level_id and getattr(assignment.school_class, "academic_level_id", None) != level_id:
            continue
        year_class = _year_class_for_assignment(assignment)
        assignment._analytics_year_class = year_class
        if shift and _shift_key(getattr(year_class, "school_shift", None)) != shift:
            continue
        assignments.append(assignment)
    return assignments


def _attendance_snapshot(filters, student_ids, subject_ids):
    """Return the latest exam-attendance status per student and subject."""
    if not filters.get("academic_year_id") or not filters.get("exam_id") or not student_ids:
        return {}
    query = (
        AttendanceRecord.query
        .outerjoin(ExamSession, AttendanceRecord.exam_session_id == ExamSession.id)
        .filter(
            AttendanceRecord.academic_year_id == filters["academic_year_id"],
            AttendanceRecord.student_id.in_(student_ids),
            db.or_(
                AttendanceRecord.exam_id == filters["exam_id"],
                ExamSession.exam_id == filters["exam_id"],
            ),
        )
        .order_by(
            AttendanceRecord.attendance_date.desc(),
            AttendanceRecord.recorded_at.desc(),
            AttendanceRecord.id.desc(),
        )
    )
    snapshot = {}
    for record in query.all():
        if record.subject_id is not None and record.subject_id not in subject_ids:
            continue
        key = (record.student_id, record.subject_id)
        snapshot.setdefault(key, normalize_attendance_status(record.status))
    return snapshot


def _approval_counts(teacher, filters, assignments, students, results):
    """Count approval states for the exact published results in this scope."""
    counts = {"draft": 0, "review": 0, "approved": 0}
    student_ids = {student.id for student in students}
    state_by_pair = {}
    for assignment in assignments:
        submissions = sorted(assignment.submissions, key=lambda item: item.id, reverse=True)
        submission = submissions[0] if submissions else None
        if not submission:
            continue
        marks = [mark for mark in submission.marks if mark.student_id in student_ids and mark.score is not None]
        for mark in marks:
            state = "approved" if mark.status == "approved" or submission.status == "approved" else {
                "submitted": "review",
                "reopened": "review",
                "draft": "draft",
            }.get(submission.status, "draft")
            state_by_pair[(mark.student_id, assignment.subject_id)] = state

    for result in results:
        state = state_by_pair.get((result.student_id, result.subject_id), "approved" if result.is_published else "draft")
        counts[state] += 1
    return counts


def _grade_metadata(exam_id, common_max):
    """Expose configured grade bands and their configured colors to the UI."""
    cache = load_grade_scale_cache(exam_id, full_marks=common_max)
    metadata = {}
    for bucket in ("exam", "global", "fallback"):
        for row in cache.get(bucket, []):
            payload = row["payload"]
            metadata.setdefault(
                payload["grade"],
                {
                    "grade": payload["grade"],
                    "min_pct": round(float(row["min_score"]), 2),
                    "max_pct": round(float(row["max_score"]), 2),
                    "badge_color": payload.get("badge_color"),
                    "background_color": payload.get("background_color"),
                    "border_color": payload.get("border_color"),
                    "is_pass": payload.get("is_pass", False),
                },
            )
    return metadata


def validate_filter_ids(teacher, filters):
    academic_levels, _, _, _, academic_level_ids, class_ids, _, subject_ids = teacher_assignments(teacher)
    validated = dict(filters)
    if validated.get("academic_level_id") and validated["academic_level_id"] not in academic_level_ids:
        validated["academic_level_id"] = None
    if validated.get("class_id") and validated["class_id"] not in class_ids:
        validated["class_id"] = None
    if validated.get("subject_id") and validated["subject_id"] not in subject_ids:
        validated["subject_id"] = None
    if validated.get("compare_class_id") and validated["compare_class_id"] not in class_ids:
        validated["compare_class_id"] = None
    if validated.get("compare_subject_id") and validated["compare_subject_id"] not in subject_ids:
        validated["compare_subject_id"] = None
    return validated


def scoped_students(teacher, filters):
    """Get students scoped to teacher's assignments using new academic hierarchy with legacy fallback"""
    cache = _request_cache("_teacher_students_cache")
    cache_key = (teacher.id, _filter_cache_key(filters))
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    _, academic_classes, sections, _, _, class_ids, section_ids, _ = teacher_assignments(teacher)
    scoped_class_ids = set(class_ids)
    exact_assignments = []
    if filters.get("academic_year_id") and filters.get("exam_id"):
        exact_assignments = scoped_teacher_result_assignments(teacher, filters)
        if not exact_assignments:
            if cache is not None:
                cache[cache_key] = []
            return []
        if exact_assignments:
            scoped_class_ids = {assignment.class_id for assignment in exact_assignments}
    if filters.get("academic_level_id"):
        scoped_class_ids = {
            school_class.id
            for school_class in academic_classes
            if getattr(school_class, "academic_level_id", None) == filters["academic_level_id"]
        }
    
    # Build the selected-year scope from StudentEnrollment first. Legacy
    # placement fields remain a compatibility fallback inside the service.
    selected_year_id = filters.get("academic_year_id")
    invalid_class_level_scope = bool(
        filters.get("class_id")
        and filters.get("academic_level_id")
        and filters["class_id"] not in scoped_class_ids
    )
    if invalid_class_level_scope:
        query = Student.query.filter(Student.id == -1)
    elif selected_year_id:
        scoped_ids = set()
        requested_class_ids = [filters["class_id"]] if filters.get("class_id") else list(scoped_class_ids)
        if requested_class_ids:
            for class_id in requested_class_ids:
                try:
                    scoped_ids.update(
                        student.id
                        for student in student_enrollment_legacy_scope_query(
                            selected_year_id,
                            legacy_class_id=class_id,
                        ).filter(Student.is_active.is_(True)).all()
                    )
                except EnrollmentValidationError:
                    continue
            query = Student.query.filter(Student.id.in_(scoped_ids)) if scoped_ids else Student.query.filter(Student.id == -1)
        else:
            query = student_enrollment_scope_query(selected_year_id).filter(Student.is_active.is_(True))
    else:
        query = Student.query.filter(Student.is_active.is_(True))
    
    # Filter by new academic hierarchy
    if scoped_class_ids and not selected_year_id:
        query = query.filter(Student.academic_class_id.in_(scoped_class_ids))
    if section_ids and not selected_year_id:
        query = query.filter(Student.academic_section_id.in_(section_ids))
    
    # Legacy fallback - if no new hierarchy assignments, use legacy class_id
    if not selected_year_id and not class_ids and not section_ids:
        # Try to get legacy class IDs from teacher's classes
        legacy_class_ids = {cls.id for cls in academic_classes if hasattr(cls, 'name')}
        if legacy_class_ids:
            query = query.filter(Student.class_id.in_(legacy_class_ids))
    
    # Apply additional filters
    if filters.get("class_id") and not selected_year_id:
        query = query.filter(Student.academic_class_id == filters["class_id"])
    if filters.get("section") and not selected_year_id:
        query = query.filter(Student.section == filters["section"])
    if filters.get("academic_year_id") and not class_ids:
        # The selected-year query above already applies the year-aware scope.
        pass
    if filters.get("q"):
        q = f"%{filters['q']}%"
        query = query.filter(db.or_(Student.full_name.like(q), Student.student_code.like(q)))
    
    students = query.order_by(Student.full_name.asc()).all()
    if exact_assignments:
        exact_class_ids = {assignment.class_id for assignment in exact_assignments}
        exact_level_ids = {
            getattr(assignment.school_class, "academic_level_id", None)
            for assignment in exact_assignments
        }
        exact_level_ids.discard(None)
        shift = _shift_key(filters.get("shift"))
        filtered_students = []
        for student in students:
            placement = enrollment_placement_for_student(student, filters["academic_year_id"])
            if not placement or placement.get("academic_class_id") not in exact_class_ids:
                continue
            if exact_level_ids and placement.get("academic_level_id") not in exact_level_ids:
                continue
            if shift and _student_shift(student, filters["academic_year_id"]) != shift:
                continue
            filtered_students.append(student)
        students = filtered_students
    elif filters.get("shift"):
        shift = _shift_key(filters.get("shift"))
        students = [
            student for student in students
            if _student_shift(student, filters.get("academic_year_id")) == shift
        ]
    if cache is not None:
        cache[cache_key] = students
    return students


def scoped_results(teacher, filters, students=None):
    """Get results scoped to teacher's assignments using new academic hierarchy with legacy fallback"""
    cache = _request_cache("_teacher_results_cache")
    student_key = None if students is None else tuple(sorted(student.id for student in students))
    cache_key = (teacher.id, _filter_cache_key(filters), student_key)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    exact_assignments = []
    if filters.get("academic_year_id") and filters.get("exam_id"):
        exact_assignments = scoped_teacher_result_assignments(teacher, filters)
        if not exact_assignments:
            if cache is not None:
                cache[cache_key] = []
            return []
    if exact_assignments:
        subject_ids = {assignment.subject_id for assignment in exact_assignments}
        class_subjects = defaultdict(set)
        for assignment in exact_assignments:
            class_subjects[assignment.class_id].add(assignment.subject_id)
    else:
        _, _, _, _, _, class_ids, _, subject_ids = teacher_assignments(teacher)
        class_subjects = {}
    if not subject_ids:
        if cache is not None:
            cache[cache_key] = []
        return []
    students = students if students is not None else scoped_students(teacher, filters)
    student_ids = [student.id for student in students]
    if not student_ids:
        if cache is not None:
            cache[cache_key] = []
        return []
    query = (
        Result.query.filter(
            Result.student_id.in_(student_ids),
            Result.subject_id.in_(subject_ids),
            Result.is_published.is_(True),
        )
        .join(Result.exam)
        .join(Result.subject)
    )
    if filters.get("subject_id"):
        query = query.filter(Result.subject_id == filters["subject_id"])
    if filters.get("exam_id"):
        query = query.filter(Result.exam_id == filters["exam_id"])
    if filters.get("academic_year_id"):
        query = query.filter(Exam.academic_year_id == filters["academic_year_id"])
    results = query.all()
    if exact_assignments:
        allowed_results = []
        for result in results:
            placement = enrollment_placement_for_student(result.student, filters["academic_year_id"])
            allowed_subjects = class_subjects.get((placement or {}).get("academic_class_id"), set())
            if result.subject_id in allowed_subjects:
                allowed_results.append(result)
        results = allowed_results
    if cache is not None:
        cache[cache_key] = results
    return results


def student_exam_averages(results):
    grouped = defaultdict(list)
    for result in results:
        grouped[(result.student_id, result.exam_id)].append(_pct(result))
    averages = {}
    for key, values in grouped.items():
        averages[key] = _safe_mean(values)
    return averages


def student_overall_averages(results):
    grouped = defaultdict(list)
    for result in results:
        grouped[result.student_id].append(_pct(result))
    return {student_id: _safe_mean(values) for student_id, values in grouped.items()}


def filter_options(teacher, filters):
    """Get filter options for teacher portal using new academic hierarchy with legacy fallback"""
    academic_levels, academic_classes, sections, subjects, _, class_ids, _, subject_ids = teacher_assignments(teacher)
    result_assignments = (
        TeacherResultAssignment.query
        .filter(
            TeacherResultAssignment.teacher_id == teacher.id,
            TeacherResultAssignment.deleted_at.is_(None),
        )
        .all()
    )
    students = scoped_students(teacher, {})
    year_ids = sorted({assignment.academic_year_id for assignment in result_assignments})
    if not year_ids:
        year_ids = sorted(
            {
                student.academic_year_id
                for student in students
                if student.academic_year_id is not None
            }
        )
    years = AcademicYear.query.filter(AcademicYear.id.in_(year_ids)).order_by(AcademicYear.name.desc()).all() if year_ids else []
    assignment_exam_ids = {assignment.exam_id for assignment in result_assignments}
    exam_query = Exam.query.filter(Exam.id.in_(assignment_exam_ids)) if assignment_exam_ids else Exam.query
    if year_ids and not assignment_exam_ids:
        exam_query = exam_query.filter(Exam.academic_year_id.in_(year_ids))
    exams = exam_query.order_by(Exam.created_at.desc()).all()
    sections_list = sorted({student.section for student in students if student.section})
    scoped = scoped_students(teacher, filters)
    return {
        "academic_years": years,
        "exams": exams,
        "levels": academic_levels,
        "subjects": subjects,
        "classes": academic_classes,
        "sections": sections_list,
        "students": scoped,
    }


def attendance_rate_for_students(students, filters):
    if not students:
        return 0
    student_ids = [student.id for student in students]
    query = AttendanceRecord.query.filter(AttendanceRecord.student_id.in_(student_ids))
    if filters.get("academic_year_id"):
        query = query.filter(AttendanceRecord.academic_year_id == filters["academic_year_id"])
    if filters.get("class_id"):
        query = query.filter(AttendanceRecord.class_id == filters["class_id"])
    if filters.get("date_from"):
        try:
            query = query.filter(AttendanceRecord.attendance_date >= datetime.strptime(filters["date_from"], "%Y-%m-%d").date())
        except ValueError:
            pass
    if filters.get("date_to"):
        try:
            query = query.filter(AttendanceRecord.attendance_date <= datetime.strptime(filters["date_to"], "%Y-%m-%d").date())
        except ValueError:
            pass
    records = query.all()
    if not records:
        return 0
    present = sum(1 for record in records if record.status in ("Present", "Late", "Excused"))
    return round(present / len(records) * 100, 2)


def dashboard_summary(teacher, filters):
    """Examination-focused dashboard summary for new design"""
    filters = validate_filter_ids(teacher, filters)
    _, academic_classes, sections, subjects, _, class_ids, _, subject_ids = teacher_assignments(teacher)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    percentages = [_pct(result) for result in results]
    overall_averages = student_overall_averages(results)
    pass_count = sum(1 for avg in overall_averages.values() if grade_for(avg).get("is_pass"))
    fail_count = len(overall_averages) - pass_count
    exam_ids = {result.exam_id for result in results}
    published_results = len(results)
    prev_trend = _trend_delta(results, teacher, filters)
    
    # Get latest exam statistics
    latest_exam = _get_latest_exam(results)
    latest_exam_stats = _get_latest_exam_stats(latest_exam, students, results) if latest_exam else {}
    
    # Class performance grouping
    class_performance = _get_class_performance(results, academic_classes)
    
    # Top students
    top_students = _get_top_students(students, overall_averages, limit=5)
    
    # Grade distribution for analytics
    grade_distribution = _get_grade_distribution(percentages)
    
    # Class averages for progress bars
    class_averages = _get_class_averages(results, academic_classes)
    
    # Important overview data
    important_overview = _get_important_overview(results, students, academic_classes, overall_averages)
    
    return {
        "total_exams": len(exam_ids),
        "latest_exam": latest_exam_stats,
        "passed_students": pass_count,
        "failed_students": fail_count,
        "absent_students": len(students) - len(overall_averages),
        "total_students": len(students),
        "pass_rate": round(pass_count / len(overall_averages) * 100, 2) if overall_averages else 0,
        "fail_rate": round(fail_count / len(overall_averages) * 100, 2) if overall_averages else 0,
        "absent_rate": round((len(students) - len(overall_averages)) / len(students) * 100, 2) if students else 0,
        "class_performance": class_performance,
        "top_students": top_students,
        "grade_distribution": grade_distribution,
        "class_averages": class_averages,
        "important_overview": important_overview,
        "recent_exams": _get_recent_exams(exam_ids, results),
        "overall_average": _safe_mean(percentages),
        "trends": prev_trend,
        "chart_labels": [exam.name for exam in _ordered_exams(results)],
        "chart_values": _exam_average_series(results),
    }


def _ordered_exams(results):
    seen = {}
    for result in results:
        seen[result.exam_id] = result.exam
    return sorted(seen.values(), key=lambda exam: exam.created_at)


def _exam_average_series(results):
    buckets = defaultdict(list)
    for result in results:
        buckets[result.exam_id].append(_pct(result))
    ordered = _ordered_exams(results)
    return [_safe_mean(buckets.get(exam.id, [])) for exam in ordered]


def _trend_delta(results, teacher, filters):
    if not filters.get("exam_id"):
        return {"average": 0, "pass_rate": 0}
    current_exam = Exam.query.get(filters["exam_id"])
    if not current_exam:
        return {"average": 0, "pass_rate": 0}
    prior = (
        Exam.query.filter(
            Exam.academic_year_id == current_exam.academic_year_id,
            Exam.created_at < current_exam.created_at,
        )
        .order_by(Exam.created_at.desc())
        .first()
    )
    if not prior:
        return {"average": 0, "pass_rate": 0}
    current_results = results
    prior_filters = dict(filters)
    prior_filters["exam_id"] = prior.id
    prior_results = scoped_results(teacher, prior_filters)
    current_avg = _safe_mean([_pct(result) for result in current_results])
    prior_avg = _safe_mean([_pct(result) for result in prior_results])
    current_pass = _pass_rate_from_results(current_results)
    prior_pass = _pass_rate_from_results(prior_results)
    return {
        "average": round(current_avg - prior_avg, 2),
        "pass_rate": round(current_pass - prior_pass, 2),
    }


def _pass_rate_from_results(results):
    averages = student_overall_averages(results)
    if not averages:
        return 0
    passed = sum(1 for avg in averages.values() if grade_for(avg).get("is_pass"))
    return round(passed / len(averages) * 100, 2)


def _get_latest_exam(results):
    """Get the most recent exam from results"""
    seen = {}
    for result in results:
        seen[result.exam_id] = result.exam
    if not seen:
        return None
    return max(seen.values(), key=lambda exam: exam.created_at)


def _get_latest_exam_stats(latest_exam, students, results):
    """Get statistics for the latest exam"""
    exam_results = [r for r in results if r.exam_id == latest_exam.id]
    exam_student_ids = {r.student_id for r in exam_results}
    students_took_exam = len(exam_student_ids)
    total_students = len(students)
    percentage = round(students_took_exam / total_students * 100, 2) if total_students else 0
    return {
        "exam_name": latest_exam.name,
        "students_took": students_took_exam,
        "total_students": total_students,
        "percentage": percentage,
        "date": latest_exam.created_at.strftime("%Y-%m-%d") if latest_exam.created_at else "N/A"
    }


def _get_class_performance(results, academic_classes):
    """Group classes by performance level"""
    class_averages = {}
    for result in results:
        placement = enrollment_placement_for_student(result.student, result.exam.academic_year_id) or {}
        class_id = placement.get("academic_class_id") or result.student.academic_class_id
        if class_id:
            if class_id not in class_averages:
                class_averages[class_id] = []
            class_averages[class_id].append(_pct(result))
    
    class_stats = []
    for cls in academic_classes:
        if cls.id in class_averages:
            avg = _safe_mean(class_averages[cls.id])
            class_stats.append({
                "name": cls.name,
                "average": avg,
                "id": cls.id
            })
    
    class_stats.sort(key=lambda x: x["average"], reverse=True)
    
    # Split into top, middle, and low performing
    n = len(class_stats)
    if n == 0:
        return {"top": [], "middle": [], "low": []}
    
    top_count = max(1, n // 3)
    low_count = max(1, n // 3)
    middle_count = n - top_count - low_count
    
    return {
        "top": class_stats[:top_count],
        "middle": class_stats[top_count:top_count + middle_count],
        "low": class_stats[-low_count:] if low_count > 0 else []
    }


def _get_top_students(students, overall_averages, limit=5):
    """Get top performing students"""
    student_list = []
    for student in students:
        avg = overall_averages.get(student.id, 0)
        student_list.append({
            "student": student,
            "average": avg
        })
    
    student_list.sort(key=lambda x: x["average"], reverse=True)
    return student_list[:limit]


def _get_grade_distribution(percentages):
    """Get distribution of grades for analytics"""
    if not percentages:
        return {
            "excellent": {"count": 0, "percentage": 0},
            "good": {"count": 0, "percentage": 0},
            "average": {"count": 0, "percentage": 0},
            "poor": {"count": 0, "percentage": 0},
            "fail": {"count": 0, "percentage": 0}
        }
    
    excellent = sum(1 for p in percentages if p >= 80)
    good = sum(1 for p in percentages if 70 <= p < 80)
    average = sum(1 for p in percentages if 60 <= p < 70)
    poor = sum(1 for p in percentages if 50 <= p < 60)
    fail = sum(1 for p in percentages if p < 50)
    
    total = len(percentages)
    
    return {
        "excellent": {"count": excellent, "percentage": round(excellent / total * 100, 1) if total else 0},
        "good": {"count": good, "percentage": round(good / total * 100, 1) if total else 0},
        "average": {"count": average, "percentage": round(average / total * 100, 1) if total else 0},
        "poor": {"count": poor, "percentage": round(poor / total * 100, 1) if total else 0},
        "fail": {"count": fail, "percentage": round(fail / total * 100, 1) if total else 0}
    }


def _get_class_averages(results, academic_classes):
    """Get average scores for each class for progress bars"""
    class_averages = {}
    for result in results:
        placement = enrollment_placement_for_student(result.student, result.exam.academic_year_id) or {}
        class_id = placement.get("academic_class_id") or result.student.academic_class_id
        if class_id:
            if class_id not in class_averages:
                class_averages[class_id] = []
            class_averages[class_id].append(_pct(result))
    
    averages = []
    for cls in academic_classes:
        if cls.id in class_averages:
            avg = _safe_mean(class_averages[cls.id])
            averages.append({
                "name": cls.name,
                "average": avg,
                "percentage": round(avg)
            })
    
    averages.sort(key=lambda x: x["average"], reverse=True)
    return averages


def _get_important_overview(results, students, academic_classes, overall_averages):
    """Get important overview information"""
    # Get class averages for best/worst class
    class_averages = _get_class_averages(results, academic_classes)
    best_class = class_averages[0] if class_averages else None
    worst_class = class_averages[-1] if class_averages else None
    
    # Get highest/lowest scoring students
    top_students = _get_top_students(students, overall_averages, limit=1)
    highest_student = top_students[0] if top_students else None
    
    # Students requiring support (below 50%)
    weak_students = [s for s in students if overall_averages.get(s.id, 0) < 50]
    
    # Exam status
    exam_ids = {result.exam_id for result in results}
    published_exams = [exam for exam in Exam.query.filter(Exam.id.in_(exam_ids)).all() if exam.is_published]
    unpublished_exams = [exam for exam in Exam.query.filter(Exam.id.in_(exam_ids)).all() if not exam.is_published]
    
    return {
        "pending_mark_entry": len(unpublished_exams),
        "awaiting_publication": len(unpublished_exams),
        "recently_published": len(published_exams),
        "students_requiring_support": len(weak_students),
        "best_performing_class": best_class["name"] if best_class else "N/A",
        "lowest_performing_class": worst_class["name"] if worst_class else "N/A",
        "highest_scoring_student": highest_student["student"].full_name if highest_student else "N/A",
        "lowest_class_average": worst_class["average"] if worst_class else 0
    }


def _get_recent_exams(exam_ids, results):
    """Get recent exams with details"""
    if not exam_ids:
        return []
    
    exams = Exam.query.filter(Exam.id.in_(exam_ids)).order_by(Exam.created_at.desc()).limit(5).all()
    recent_exams = []
    
    for exam in exams:
        exam_results = [r for r in results if r.exam_id == exam.id]
        avg = _safe_mean([_pct(r) for r in exam_results]) if exam_results else 0
        
        recent_exams.append({
            "name": exam.name,
            "class": exam_results[0].student.academic_class.name if exam_results and exam_results[0].student.academic_class else "N/A",
            "subject": exam_results[0].subject.name if exam_results else "N/A",
            "average": round(avg, 1),
            "date": exam.created_at.strftime("%Y-%m-%d") if exam.created_at else "N/A",
            "status": "Published" if exam.is_published else "Draft"
        })
    
    return recent_exams


def subjects_cards(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    cards = []
    for subject in teacher.subjects:
        subject_filters = dict(filters)
        subject_filters["subject_id"] = subject.id
        students = scoped_students(teacher, subject_filters)
        results = scoped_results(teacher, subject_filters, students)
        percentages = [_pct(result) for result in results]
        overall = student_overall_averages(results)
        passed = sum(1 for avg in overall.values() if grade_for(avg).get("is_pass"))
        total = len(overall)
        class_names = sorted({student.school_class.name for student in students})
        cards.append(
            {
                "subject": subject,
                "teacher_name": teacher.full_name,
                "classes": class_names,
                "student_count": len(students),
                "average": _safe_mean(percentages),
                "highest": round(max(percentages), 2) if percentages else 0,
                "lowest": round(min(percentages), 2) if percentages else 0,
                "pass_rate": round(passed / total * 100, 2) if total else 0,
                "fail_rate": round((total - passed) / total * 100, 2) if total else 0,
            }
        )
    return cards


def classes_cards(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    cards = []
    for school_class in teacher.classes:
        class_filters = dict(filters)
        class_filters["class_id"] = school_class.id
        students = scoped_students(teacher, class_filters)
        results = scoped_results(teacher, class_filters, students)
        percentages = [_pct(result) for result in results]
        overall = student_overall_averages(results)
        passed = sum(1 for avg in overall.values() if grade_for(avg).get("is_pass"))
        total = len(overall)
        sections = sorted({student.section for student in students if student.section})
        exam_count = len({result.exam_id for result in results})
        cards.append(
            {
                "class": school_class,
                "sections": sections,
                "student_count": len(students),
                "average": _safe_mean(percentages),
                "highest": round(max(percentages), 2) if percentages else 0,
                "lowest": round(min(percentages), 2) if percentages else 0,
                "pass_rate": round(passed / total * 100, 2) if total else 0,
                "attendance": attendance_rate_for_students(students, class_filters),
                "exams_taken": exam_count,
            }
        )
    return cards


def exam_analysis_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    percentages = [_pct(result) for result in results]
    overall = student_overall_averages(results)
    passed = sum(1 for avg in overall.values() if grade_for(avg).get("is_pass"))
    total_students = len(overall)
    return {
        "average": _safe_mean(percentages),
        "median": _safe_median(percentages),
        "mode": _safe_mode(percentages),
        "highest": round(max(percentages), 2) if percentages else 0,
        "lowest": round(min(percentages), 2) if percentages else 0,
        "range": round(max(percentages) - min(percentages), 2) if percentages else 0,
        "std_dev": _safe_stdev(percentages),
        "pass_rate": round(passed / total_students * 100, 2) if total_students else 0,
        "fail_rate": round((total_students - passed) / total_students * 100, 2) if total_students else 0,
        "student_count": total_students,
        "distribution": _score_distribution(percentages),
        "subject_breakdown": _subject_breakdown(results),
        "class_breakdown": _class_breakdown(results),
    }


def _score_distribution(percentages):
    bands = [(90, 100), (80, 89), (70, 79), (60, 69), (50, 59), (0, 49)]
    labels = ["90-100", "80-89", "70-79", "60-69", "50-59", "Below 50"]
    counts = []
    for low, high in bands:
        counts.append(sum(1 for value in percentages if low <= value <= high))
    return {"labels": labels, "values": counts}


def _subject_breakdown(results):
    buckets = defaultdict(list)
    for result in results:
        buckets[result.subject.name].append(_pct(result))
    return {name: _safe_mean(values) for name, values in sorted(buckets.items())}


def _class_breakdown(results):
    buckets = defaultdict(list)
    for result in results:
        buckets[result.student.school_class.name].append(_pct(result))
    return {name: _safe_mean(values) for name, values in sorted(buckets.items())}


def subject_analysis_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    by_class = _class_breakdown(results)
    by_section = defaultdict(list)
    by_exam = defaultdict(list)
    for result in results:
        by_section[result.student.section or "Unassigned"].append(_pct(result))
        by_exam[result.exam.name].append(_pct(result))
    grade_counts = Counter()
    for result in results:
        grade_counts[grade_for(_pct(result)).get("grade", "-")] += 1
    subject_name = None
    if filters.get("subject_id"):
        subject = Subject.query.get(filters["subject_id"])
        subject_name = subject.name if subject else None
    return {
        "subject_name": subject_name,
        "by_chapter": [{"name": f"Unit {index + 1}", "average": avg, "future_ready": True} for index, avg in enumerate(list(_subject_breakdown(results).values())[:6])],
        "by_class": by_class,
        "by_section": {name: _safe_mean(values) for name, values in sorted(by_section.items())},
        "by_exam": {name: _safe_mean(values) for name, values in sorted(by_exam.items())},
        "grade_comparison": dict(sorted(grade_counts.items())),
        "difficulty_analysis": _difficulty_analysis(results),
        "question_analysis_ready": True,
    }


def _difficulty_analysis(results):
    buckets = defaultdict(list)
    for result in results:
        buckets[result.subject.name].append(_pct(result))
    ranked = sorted(((name, _safe_mean(values)) for name, values in buckets.items()), key=lambda item: item[1])
    return {
        "easiest": ranked[-3:][::-1] if ranked else [],
        "hardest": ranked[:3] if ranked else [],
    }


def student_analysis_data(teacher, student_id, filters):
    from .models import Student as StudentModel

    student = StudentModel.query.get_or_404(student_id)
    _, _, class_ids, _ = teacher_assignments(teacher)
    if student.class_id not in class_ids:
        return None
    student_filters = dict(filters)
    student_filters["class_id"] = student.class_id
    students = [student]
    results = scoped_results(teacher, student_filters, students)
    history = []
    exam_groups = defaultdict(list)
    for result in results:
        exam_groups[result.exam.name].append(_pct(result))
    for exam_name, values in sorted(exam_groups.items()):
        avg = _safe_mean(values)
        history.append({"exam": exam_name, "average": avg, "grade": grade_for(avg).get("grade", "-")})
    overall_avg = _safe_mean([item["average"] for item in history])
    class_students = scoped_students(teacher, {"class_id": student.class_id, **{k: v for k, v in filters.items() if k != "student_id"}})
    class_results = scoped_results(teacher, filters, class_students)
    class_avg = _safe_mean([_pct(result) for result in class_results])
    rank_data = _student_rankings(teacher, filters)
    rank = next((item["rank"] for item in rank_data if item["student_id"] == student.id), None)
    strengths, weaknesses = _strengths_weaknesses(results)
    attendance = attendance_rate_for_students([student], filters)
    return {
        "student": student,
        "history": history,
        "average": overall_avg,
        "rank": rank,
        "grade": grade_for(overall_avg).get("grade", "-"),
        "attendance": attendance,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "trend": [item["average"] for item in history],
        "trend_labels": [item["exam"] for item in history],
        "class_average": class_avg,
        "comparison_delta": round(overall_avg - class_avg, 2),
    }


def _strengths_weaknesses(results):
    subject_scores = defaultdict(list)
    for result in results:
        subject_scores[result.subject.name].append(_pct(result))
    ranked = sorted(((name, _safe_mean(values)) for name, values in subject_scores.items()), key=lambda item: item[1], reverse=True)
    return [name for name, _ in ranked[:3]], [name for name, _ in ranked[-3:][::-1]]


def performance_trends_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    exams = _ordered_exams(results)
    exam_avgs = _exam_average_series(results)
    monthly = defaultdict(list)
    for result in results:
        key = result.created_at.strftime("%Y-%m")
        monthly[key].append(_pct(result))
    monthly_labels = sorted(monthly.keys())
    monthly_values = [_safe_mean(monthly[label]) for label in monthly_labels]
    year_buckets = defaultdict(list)
    for result in results:
        year_buckets[result.exam.academic_year.name].append(_pct(result))
    return {
        "exam_labels": [exam.name for exam in exams],
        "exam_values": exam_avgs,
        "monthly_labels": monthly_labels,
        "monthly_values": monthly_values,
        "year_labels": list(year_buckets.keys()),
        "year_values": [_safe_mean(year_buckets[label]) for label in year_buckets.keys()],
    }


def grade_distribution_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    grade_counts = Counter()
    student_grade = {}
    for result in results:
        grade = grade_for(_pct(result)).get("grade", "-")
        grade_counts[grade] += 1
        student_grade.setdefault(result.student_id, []).append(grade)
    ordered_grades = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "E", "F"]
    labels = [grade for grade in ordered_grades if grade in grade_counts] + [g for g in grade_counts if g not in ordered_grades]
    values = [grade_counts[label] for label in labels]
    total = sum(values) or 1
    percentages = [round(value / total * 100, 2) for value in values]
    return {
        "labels": labels,
        "values": values,
        "percentages": percentages,
        "student_count": len({result.student_id for result in results}),
    }


def pass_fail_analysis_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    overall = student_overall_averages(results)
    passed = sum(1 for avg in overall.values() if grade_for(avg).get("is_pass"))
    failed = len(overall) - passed
    class_compare = {}
    section_compare = {}
    for student_id, avg in overall.items():
        student = next(item for item in students if item.id == student_id)
        class_compare.setdefault(student.school_class.name, {"pass": 0, "fail": 0})
        section_compare.setdefault(student.section or "Unassigned", {"pass": 0, "fail": 0})
        bucket = "pass" if grade_for(avg).get("is_pass") else "fail"
        class_compare[student.school_class.name][bucket] += 1
        section_compare[student.section or "Unassigned"][bucket] += 1
    return {
        "total_passed": passed,
        "total_failed": failed,
        "pass_rate": round(passed / len(overall) * 100, 2) if overall else 0,
        "fail_rate": round(failed / len(overall) * 100, 2) if overall else 0,
        "gender_comparison": {"note": "Student gender is not stored in the current database schema."},
        "class_comparison": class_compare,
        "section_comparison": section_compare,
    }


def _student_rankings(teacher, filters):
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    averages = student_overall_averages(results)
    ranked = sorted(averages.items(), key=lambda item: item[1], reverse=True)
    payload = []
    for index, (student_id, average) in enumerate(ranked, start=1):
        student = next((item for item in students if item.id == student_id), None)
        if not student:
            continue
        payload.append(
            {
                "rank": index,
                "student_id": student_id,
                "student": student,
                "average": average,
                "grade": grade_for(average).get("grade", "-"),
            }
        )
    return payload


def top_performers_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    rankings = _student_rankings(teacher, filters)
    limit = filters.get("limit") or 10
    top_10 = rankings[:10]
    top_20 = rankings[:20]
    perfect = [item for item in rankings if item["average"] >= 99.9]
    most_improved = _most_improved(teacher, filters, rankings)
    return {
        "top_10": top_10,
        "top_20": top_20,
        "top_boys": [],
        "top_girls": [],
        "gender_note": "Gender-specific rankings require a student gender field.",
        "most_improved": most_improved,
        "highest_average": rankings[:5],
        "perfect_scores": perfect,
        "limit": limit,
    }


def _most_improved(teacher, filters, rankings):
    if not filters.get("exam_id"):
        return rankings[:3]
    current_exam = Exam.query.get(filters["exam_id"])
    if not current_exam:
        return []
    prior = (
        Exam.query.filter(Exam.academic_year_id == current_exam.academic_year_id, Exam.created_at < current_exam.created_at)
        .order_by(Exam.created_at.desc())
        .first()
    )
    if not prior:
        return []
    current = {item["student_id"]: item["average"] for item in rankings}
    prior_filters = dict(filters)
    prior_filters["exam_id"] = prior.id
    prior_results = scoped_results(teacher, prior_filters)
    prior_avgs = student_overall_averages(prior_results)
    deltas = []
    for student_id, avg in current.items():
        if student_id in prior_avgs:
            deltas.append((student_id, round(avg - prior_avgs[student_id], 2)))
    deltas.sort(key=lambda item: item[1], reverse=True)
    improved = []
    for student_id, delta in deltas[:5]:
        student = Student.query.get(student_id)
        if student:
            improved.append({"student": student, "improvement": delta})
    return improved


def weak_students_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    rankings = _student_rankings(teacher, filters)
    bottom = list(reversed(rankings))[:10]
    lowest_avg = rankings[-5:][::-1] if rankings else []
    repeated_failures = []
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    exam_failures = defaultdict(int)
    exam_groups = defaultdict(lambda: defaultdict(list))
    for result in results:
        exam_groups[result.student_id][result.exam_id].append(_pct(result))
    for student_id, exams in exam_groups.items():
        fails = sum(1 for values in exams.values() if not grade_for(_safe_mean(values)).get("is_pass"))
        if fails >= 2:
            student = next((item for item in students if item.id == student_id), None)
            if student:
                repeated_failures.append({"student": student, "fail_count": fails})
    needs_intervention = [item for item in rankings if item["average"] < 50][-10:][::-1]
    revision_subjects = _difficulty_analysis(results).get("hardest", [])
    return {
        "bottom_students": bottom,
        "lowest_average": lowest_avg,
        "repeated_failures": repeated_failures,
        "needs_intervention": needs_intervention,
        "recommended_revision": [name for name, _ in revision_subjects],
        "teacher_notes": [student.note for student in students if student.note],
    }


def comparison_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    compare_type = filters.get("compare_type") or "exam"
    left_label = right_label = "Set A"
    left_value = right_value = 0
    if compare_type == "exam" and filters.get("exam_id") and filters.get("compare_exam_id"):
        left_results = scoped_results(teacher, {**filters, "exam_id": filters["exam_id"]})
        right_results = scoped_results(teacher, {**filters, "exam_id": filters["compare_exam_id"]})
        left_value = _safe_mean([_pct(result) for result in left_results])
        right_value = _safe_mean([_pct(result) for result in right_results])
        left_label = Exam.query.get(filters["exam_id"]).name
        right_label = Exam.query.get(filters["compare_exam_id"]).name
    elif compare_type == "class" and filters.get("class_id") and filters.get("compare_class_id"):
        left_results = scoped_results(teacher, {**filters, "class_id": filters["class_id"]})
        right_results = scoped_results(teacher, {**filters, "class_id": filters["compare_class_id"]})
        left_value = _safe_mean([_pct(result) for result in left_results])
        right_value = _safe_mean([_pct(result) for result in right_results])
        left_label = SchoolClass.query.get(filters["class_id"]).name
        right_label = SchoolClass.query.get(filters["compare_class_id"]).name
    elif compare_type == "section" and filters.get("section") and filters.get("compare_section"):
        left_results = scoped_results(teacher, {**filters, "section": filters["section"]})
        right_results = scoped_results(teacher, {**filters, "section": filters["compare_section"]})
        left_value = _safe_mean([_pct(result) for result in left_results])
        right_value = _safe_mean([_pct(result) for result in right_results])
        left_label = filters["section"]
        right_label = filters["compare_section"]
    elif compare_type == "year" and filters.get("academic_year_id") and filters.get("compare_year_id"):
        left_results = scoped_results(teacher, {**filters, "academic_year_id": filters["academic_year_id"]})
        right_results = scoped_results(teacher, {**filters, "academic_year_id": filters["compare_year_id"]})
        left_value = _safe_mean([_pct(result) for result in left_results])
        right_value = _safe_mean([_pct(result) for result in right_results])
        left_label = AcademicYear.query.get(filters["academic_year_id"]).name
        right_label = AcademicYear.query.get(filters["compare_year_id"]).name
    elif compare_type == "subject" and filters.get("subject_id") and filters.get("compare_subject_id"):
        left_results = scoped_results(teacher, {**filters, "subject_id": filters["subject_id"]})
        right_results = scoped_results(teacher, {**filters, "subject_id": filters["compare_subject_id"]})
        left_value = _safe_mean([_pct(result) for result in left_results])
        right_value = _safe_mean([_pct(result) for result in right_results])
        left_label = Subject.query.get(filters["subject_id"]).name
        right_label = Subject.query.get(filters["compare_subject_id"]).name
    return {
        "compare_type": compare_type,
        "left_label": left_label,
        "right_label": right_label,
        "left_value": left_value,
        "right_value": right_value,
        "delta": round(left_value - right_value, 2),
    }


def generate_ai_insights(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    insights = []
    summary = dashboard_summary(teacher, filters)
    if summary["trends"]["average"]:
        direction = "dropped" if summary["trends"]["average"] < 0 else "improved"
        insights.append(f"The average score {direction} by {abs(summary['trends']['average'])}% compared with the previous exam.")
    class_cards = classes_cards(teacher, filters)
    if class_cards:
        best = max(class_cards, key=lambda item: item["average"])
        insights.append(f"{best['class'].name} performed best with an average of {best['average']}%.")
    weak = weak_students_data(teacher, filters)
    if weak["needs_intervention"]:
        insights.append(f"{len(weak['needs_intervention'])} students need additional support.")
    subject_cards = subjects_cards(teacher, filters)
    if len(subject_cards) >= 2:
        ranked = sorted(subject_cards, key=lambda item: item["average"])
        insights.append(f"{ranked[0]['subject'].name} has the lowest average score ({ranked[0]['average']}%).")
        insights.append(f"Revision should focus on {ranked[0]['subject'].name}.")
    trends = performance_trends_data(teacher, filters)
    if len(trends["exam_values"]) >= 3 and trends["exam_values"][-1] > trends["exam_values"][0]:
        insights.append("Performance has improved steadily over the last three examinations.")
    if not insights:
        insights.append("Not enough data yet to generate insights. Add more examination results for your assigned classes.")
    return insights


def report_summary(teacher, filters):
    return {
        "dashboard": dashboard_summary(teacher, filters),
        "subjects": subjects_cards(teacher, filters),
        "classes": classes_cards(teacher, filters),
        "grades": grade_distribution_data(teacher, filters),
        "pass_fail": pass_fail_analysis_data(teacher, filters),
        "insights": generate_ai_insights(teacher, filters),
    }


def fallanqeyn_data(teacher, filters):
    """Build the real, assignment-scoped payload for the teacher analytics view.

    The payload deliberately starts from ``scoped_results`` so unpublished or
    out-of-assignment records cannot leak into the teacher portal. Missing
    subjects are calculated against the teacher's assigned subjects and the
    selected student scope, rather than being mistaken for zero scores.
    """
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    assignments = scoped_teacher_result_assignments(teacher, filters)
    assigned_subjects_by_class = defaultdict(set)
    assigned_subjects = {}
    for assignment in assignments:
        assigned_subjects_by_class[assignment.class_id].add(assignment.subject_id)
        assigned_subjects[assignment.subject_id] = assignment.subject

    # The assignment matrix is the authority for this page. It prevents a
    # subject assigned to one class from leaking into another class selected
    # in the same year/exam.
    subject_pool = [
        subject for subject in assigned_subjects.values()
        if subject and (not filters.get("subject_id") or subject.id == filters["subject_id"])
    ]
    subject_pool.sort(key=lambda subject: (subject.sort_order or 0, subject.name or ""))

    student_subject_ids = {}
    for student in students:
        placement = enrollment_placement_for_student(student, filters.get("academic_year_id"))
        class_id = (placement or {}).get("academic_class_id")
        allowed = set(assigned_subjects_by_class.get(class_id, set()))
        if filters.get("subject_id"):
            allowed &= {filters["subject_id"]}
        student_subject_ids[student.id] = allowed

    raw_results = scoped_results(teacher, filters, students)
    attendance = _attendance_snapshot(
        filters,
        {student.id for student in students},
        {subject.id for subject in subject_pool},
    )
    results = []
    for result in raw_results:
        if result.subject_id not in student_subject_ids.get(result.student_id, set()):
            continue
        if attendance.get((result.student_id, result.subject_id)) in NON_SAT_STATUSES:
            # A non-sitting attendance status is authoritative. Keep the
            # historical Result row untouched, but do not count its score in
            # teacher analytics.
            continue
        results.append(result)
    student_map = {student.id: student for student in students}
    expected = sum(len(subject_ids) for subject_ids in student_subject_ids.values())
    expected = max(expected, len(results))
    absent_pairs = {
        key for key, status in attendance.items()
        if status in NON_SAT_STATUSES
        and key[0] in student_subject_ids
        and key[1] in student_subject_ids.get(key[0], set())
    }
    scored_pairs = {(result.student_id, result.subject_id) for result in results}
    missing_pairs = max(expected - len(scored_pairs) - len(absent_pairs), 0)
    scores = [_pct(result) for result in results]
    result_by_student = defaultdict(list)
    result_by_subject = defaultdict(list)
    result_by_exam = defaultdict(list)
    for result in results:
        result_by_student[result.student_id].append(result)
        result_by_subject[result.subject_id].append(result)

    # The trend card must compare the selected exam with the other exams in
    # the same year and scope.  ``results`` is intentionally limited to the
    # selected exam for the KPIs, so load a second, scoped set for the trend.
    trend_filters = dict(filters)
    trend_filters["exam_id"] = None
    trend_results = scoped_results(teacher, trend_filters, students)
    for result in trend_results:
        if result.subject_id in student_subject_ids.get(result.student_id, set()):
            result_by_exam[result.exam_id].append(result)

    exam_for_scale = filters.get("exam_id") or (results[0].exam_id if results else None)
    result_maxima = [_result_max_score(result) for result in results]
    distinct_maxima = {Decimal(str(value)) for value in result_maxima if value}
    common_max = next(iter(distinct_maxima)) if len(distinct_maxima) == 1 else None
    grade_metadata = _grade_metadata(exam_for_scale, common_max)
    pass_marks = [
        metadata["min_pct"]
        for metadata in grade_metadata.values()
        if metadata.get("is_pass") and metadata.get("min_pct") is not None
    ]
    pass_mark = min(pass_marks) if pass_marks else 50

    def grade_info_for_percentage(value, exam_id=None):
        """Use the already-normalized selected-exam scale before any fallback."""
        if exam_id in (None, exam_for_scale):
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = 0
            for metadata in grade_metadata.values():
                if metadata["min_pct"] <= value <= metadata["max_pct"]:
                    return metadata
        return grade_for(value, exam_id=exam_id)

    def is_pass_percentage(value, exam_id=None):
        return bool(grade_info_for_percentage(value, exam_id or exam_for_scale).get("is_pass"))

    placement_cache = {}

    def selected_year_placement(student):
        year_id = filters.get("academic_year_id")
        if not year_id:
            return None
        if student.id not in placement_cache:
            placement_cache[student.id] = enrollment_placement_for_student(student, year_id)
        return placement_cache[student.id]

    def class_name(student):
        placement = selected_year_placement(student)
        if placement and placement.get("class_name"):
            return placement["class_name"]
        return (
            getattr(getattr(student, "academic_class", None), "name", None)
            or getattr(getattr(student, "school_class", None), "name", None)
            or "Fasal aan la cayimin"
        )

    def detail(result):
        pct = _pct(result)
        return {
            "subject": result.subject.name if result.subject else "Maaddo aan la cayimin",
            "status": "scored",
            "state": "approved" if result.is_published else "draft",
            "pct": pct,
            "grade": grade_info_for_percentage(pct, result.exam_id).get("grade", "-"),
        }

    # Student movement is compared with the immediately previous exam in the
    # same academic year and the same teacher-selected student scope. If no
    # exam is selected, we leave movement empty rather than inventing a trend
    # from an unbounded collection of exams.
    previous_exam = None
    previous_averages = {}
    current_exam = Exam.query.get(filters["exam_id"]) if filters.get("exam_id") else None
    if current_exam and current_exam.created_at is not None:
        previous_exam = (
            Exam.query.filter(
                Exam.academic_year_id == current_exam.academic_year_id,
                Exam.created_at < current_exam.created_at,
            )
            .order_by(Exam.created_at.desc(), Exam.id.desc())
            .first()
        )
        if previous_exam:
            previous_filters = dict(filters)
            previous_filters["exam_id"] = previous_exam.id
            previous_results = scoped_results(teacher, previous_filters, students)
            previous_averages = student_overall_averages(previous_results)

    current_scores_by_student = {
        student.id: [_pct(result) for result in result_by_student.get(student.id, [])]
        for student in students
    }

    # These thresholds mirror the visual language in the V3 sample: a visible
    # movement badge starts at five percentage points, while a low spread over
    # at least two subjects earns the consistency badge.
    improvement_threshold = 5
    decline_threshold = 5
    consistency_limit = 7

    students_payload = []
    for student in students:
        student_results = result_by_student.get(student.id, [])
        subject_ids = {result.subject_id for result in student_results}
        allowed_subject_ids = student_subject_ids.get(student.id, set())
        missing_subjects = [
            assigned_subjects[subject_id].name
            for subject_id in sorted(allowed_subject_ids - subject_ids)
            if subject_id in assigned_subjects
        ]
        absent_subjects = [
            assigned_subjects[subject_id].name
            for subject_id in sorted(allowed_subject_ids)
            if attendance.get((student.id, subject_id)) in NON_SAT_STATUSES
            and subject_id in assigned_subjects
        ]
        student_scores = [_pct(result) for result in student_results]
        average = _safe_mean(student_scores) if student_scores else None
        reasons = []
        weight = 0
        low_scores = [
            _pct(result)
            for result in student_results
            if not is_pass_percentage(_pct(result), result.exam_id)
        ]
        if low_scores:
            reasons.append({"code": "low_score", "v": {"pct": round(min(low_scores), 1)}})
            weight += 2
        if missing_subjects:
            reasons.append({"code": "missing", "v": {"subjects": missing_subjects}})
            weight += 1
        if absent_subjects:
            reasons.append({"code": "absent", "v": {"subjects": absent_subjects}})
            weight += 1
        if average is not None and not is_pass_percentage(average):
            reasons.append({"code": "low_avg", "v": {"avg": average}})
            weight += 2
        previous_average = previous_averages.get(student.id)
        delta = round(average - previous_average, 2) if average is not None and previous_average is not None else None
        if delta is not None and delta <= -decline_threshold:
            reasons.append({"code": "declined", "v": {"pts": abs(delta)}})
            weight += 2
        priority = "high" if weight >= 4 else "medium" if weight >= 2 else "low"
        students_payload.append(
            {
                "id": str(student.id),
                "name": student.full_name,
                "cls": class_name(student),
                "gender": student.gender or "Aan la cayimin",
                "avg": average,
                "complete": bool(allowed_subject_ids) and not (allowed_subject_ids - subject_ids),
                "delta": delta,
                "priority": priority,
                "badges": [],
                "reasons": reasons,
                "results": [detail(result) for result in student_results],
            }
        )

    scored_students = [
        row for row in students_payload
        if row["avg"] is not None and row.get("complete")
    ]
    scored_students.sort(key=lambda row: row["avg"], reverse=True)
    for index, row in enumerate(scored_students[:5]):
        badges = ["top"] if index < 3 else []
        if row["delta"] is not None and row["delta"] >= improvement_threshold:
            badges.append("improved")
        student_scores = current_scores_by_student.get(int(row["id"]), [])
        if len(student_scores) >= 2 and _safe_stdev(student_scores) <= consistency_limit:
            badges.append("consistent")
        row["badges"] = badges

    improving = sorted(
        (row for row in scored_students if row["delta"] is not None and row["delta"] >= improvement_threshold),
        key=lambda row: row["delta"],
        reverse=True,
    )[:5]
    declining = sorted(
        (row for row in scored_students if row["delta"] is not None and row["delta"] <= -decline_threshold),
        key=lambda row: row["delta"],
    )[:5]

    support = [row for row in students_payload if row["reasons"]]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    support.sort(key=lambda row: (priority_order[row["priority"]], row["avg"] is None, row["avg"] or 0))

    def risk_payload(name, items, student_count=None, expected_count=None):
        item_scores = [_pct(result) for result in items]
        item_expected = (
            expected_count
            if expected_count is not None
            else (student_count or len(students)) * len(subject_pool)
            if student_count is not None
            else len(items)
        )
        missing_count = max(item_expected - len(items), 0)
        avg = _safe_mean(item_scores)
        passed = sum(
            1
            for result in items
            if is_pass_percentage(_pct(result), result.exam_id)
        )
        pass_rate = round(passed / len(item_scores) * 100, 2) if item_scores else None
        missing_pct = round(missing_count / item_expected * 100, 2) if item_expected else 0
        reasons = []
        if item_scores and avg < 55:
            reasons.append({"code": "low_avg", "v": {"avg": avg}})
        if item_scores and pass_rate < 65:
            reasons.append({"code": "low_pass", "v": {"pass": pass_rate}})
        if missing_pct > 8:
            reasons.append({"code": "missing_many", "v": {"pct": missing_pct}})
        return {
            "name": name,
            "n": len(items),
            "expected": item_expected,
            "students": student_count,
            "avg": avg if item_scores else None,
            "pass": pass_rate,
            "missing": missing_count,
            "missing_pct": missing_pct,
            "risk": {"level": "high" if len(reasons) >= 2 else "medium" if reasons else "none", "reasons": reasons},
        }

    subjects_payload = []
    for subject in subject_pool:
        subject_expected = sum(
            subject.id in allowed_subject_ids
            for allowed_subject_ids in student_subject_ids.values()
        )
        subjects_payload.append(
            risk_payload(
                subject.name,
                result_by_subject.get(subject.id, []),
                expected_count=subject_expected,
            )
        )

    class_students = defaultdict(list)
    for student in students:
        class_students[class_name(student)].append(student)
    result_by_student_id = defaultdict(list)
    for result in results:
        result_by_student_id[result.student_id].append(result)
    class_payload = []
    for name, class_roster in sorted(class_students.items()):
        roster_ids = {student.id for student in class_roster}
        class_items = [
            result
            for student_id in roster_ids
            for result in result_by_student_id.get(student_id, [])
        ]
        class_expected = sum(
            len(student_subject_ids.get(student.id, set()))
            for student in class_roster
        )
        class_payload.append(
            risk_payload(
                name,
                class_items,
                len(class_roster),
                expected_count=class_expected,
            )
        )

    grade_counts = Counter()
    grade_scores = defaultdict(list)
    for result, score in zip(results, scores):
        grade_name = grade_info_for_percentage(score, result.exam_id).get("grade", "-")
        grade_counts[grade_name] += 1
        grade_scores[grade_name].append(float(result.score))
    grade_total = sum(grade_counts.values())
    grades_payload = []
    ordered_grade_names = list(grade_metadata)
    for grade_name in grade_counts:
        if grade_name not in ordered_grade_names:
            ordered_grade_names.append(grade_name)
    for grade_name in ordered_grade_names:
        metadata = grade_metadata.get(grade_name, {})
        number = grade_counts.get(grade_name, 0)
        observed_points = grade_scores.get(grade_name, [])
        min_points = (
            round(float(metadata["min_pct"] * float(common_max) / 100), 2)
            if common_max is not None and metadata.get("min_pct") is not None
            else round(min(observed_points), 2) if observed_points else None
        )
        max_points = (
            round(float(metadata["max_pct"] * float(common_max) / 100), 2)
            if common_max is not None and metadata.get("max_pct") is not None
            else round(max(observed_points), 2) if observed_points else None
        )
        grades_payload.append(
            {
                "grade": grade_name,
                "min": min_points,
                "max": max_points,
                "min_pct": metadata.get("min_pct"),
                "max_pct": metadata.get("max_pct"),
                "n": number,
                "pct": round(number / grade_total * 100, 2) if grade_total else 0,
                "color": metadata.get("badge_color"),
                "background": metadata.get("background_color"),
                "border": metadata.get("border_color"),
            }
        )

    histogram = []
    if common_max is not None and common_max > 0:
        common_max_float = float(common_max)
        if common_max_float <= 20 and common_max_float.is_integer():
            edges = [(float(index), float(index)) for index in range(int(common_max_float) + 1)]
        else:
            width = common_max_float / 10
            edges = [
                (round(index * width, 2), round(common_max_float if index == 9 else (index + 1) * width, 2))
                for index in range(10)
            ]
        for low, high in edges:
            number = sum(
                1 for result in results
                if low <= float(result.score)
                and (float(result.score) <= high if high == common_max_float else float(result.score) < high)
            )
            histogram.append(
                {
                    "label": str(int(low)) if low == high and low.is_integer() else f"{low:g}-{high:g}",
                    "n": number,
                    "low": low,
                    "high": high,
                    "tone": "ok" if is_pass_percentage(high / common_max_float * 100) else "bad",
                }
            )

    trend = []
    for exam_id, items in sorted(result_by_exam.items(), key=lambda item: item[1][0].exam.created_at):
        exam_scores = [_pct(result) for result in items]
        trend.append(
            {
                "label": items[0].exam.name,
                "avg": _safe_mean(exam_scores),
                "pass": round(
                    sum(
                        1
                        for result in items
                        if is_pass_percentage(_pct(result), result.exam_id)
                    )
                    / len(exam_scores)
                    * 100,
                    2,
                ) if exam_scores else 0,
                "n": len(exam_scores),
            }
        )

    passed_students = sum(
        1 for row in scored_students
        if row["avg"] is not None and is_pass_percentage(row["avg"])
    )
    assessed_students = len(scored_students)
    entered_students = sum(1 for row in students_payload if row["avg"] is not None)
    complete_students = assessed_students
    partial_students = sum(
        1 for row in students_payload
        if row["avg"] is not None and not row.get("complete")
    )
    no_result_students = sum(1 for row in students_payload if row["avg"] is None)
    missing = missing_pairs
    approval_states = _approval_counts(teacher, filters, assignments, students, results)
    average_points = (
        round(sum(float(result.score) for result in results) / len(results), 2)
        if results else 0
    )
    insights = []
    risky_subjects = [item for item in subjects_payload if item["risk"]["level"] != "none"]
    if risky_subjects:
        weakest = min(risky_subjects, key=lambda item: item["avg"] if item["avg"] is not None else 101)
        insights.append({"code": "weak_subject", "tone": "bad", "importance": "high", "v": {"name": weakest["name"], "avg": weakest["avg"], "pass": weakest["pass"]}})
    if support:
        insights.append({"code": "support_students", "tone": "warn", "importance": "high" if any(row["priority"] == "high" for row in support) else "medium", "v": {"n": len(support), "high": sum(row["priority"] == "high" for row in support)}})
    if improving:
        insights.append({"code": "improving", "tone": "good", "importance": "medium", "v": {"n": len(improving), "pts": improving[0]["delta"]}})
    if declining:
        insights.append({"code": "declining", "tone": "bad", "importance": "high", "v": {"n": len(declining), "pts": abs(declining[0]["delta"])}})
    if missing:
        insights.append({"code": "missing_results", "tone": "warn", "importance": "high", "v": {"n": missing, "pct": round(missing / expected * 100, 2) if expected else 0}})
    if not insights:
        insights.append({"code": "best_subject", "tone": "good", "importance": "low", "v": {"name": "Xogtu way dhammaystiran tahay", "avg": _safe_mean(scores)}})

    gender_buckets = defaultdict(list)
    for student in students:
        gender = (student.gender or "").strip().lower()
        gender_label = {
            "male": "Wiilal",
            "female": "Gabdho",
        }.get(gender, "Kale / lama cayimin")
        gender_buckets[gender_label].append(student)
    gender_payload = []
    payload_by_student = {row["id"]: row for row in students_payload}
    for name, gender_students in sorted(gender_buckets.items()):
        if not gender_students:
            continue
        gender_rows = [payload_by_student.get(str(student.id)) for student in gender_students]
        gender_rows = [row for row in gender_rows if row]
        gender_scores = [
            _pct(result)
            for row in gender_rows
            for result in result_by_student.get(int(row["id"]), [])
        ]
        gender_assessed = [row for row in gender_rows if row.get("complete") and row.get("avg") is not None]
        gender_passed = sum(
            1
            for row in gender_assessed
            if is_pass_percentage(row["avg"])
        )
        gender_failed = len(gender_assessed) - gender_passed
        gender_maxima = {
            Decimal(str(_result_max_score(result)))
            for row in gender_rows
            for result in result_by_student.get(int(row["id"]), [])
            if _result_max_score(result)
        }
        gender_max = float(next(iter(gender_maxima))) if len(gender_maxima) == 1 else None
        gender_payload.append(
            {
                "name": name,
                "n": len(gender_students),
                "students": len(gender_students),
                "avg": _safe_mean(gender_scores),
                "avg_points": round(
                    sum(float(result.score) for row in gender_rows for result in result_by_student.get(int(row["id"]), []))
                    / len(gender_scores),
                    2,
                ) if gender_scores else None,
                "max_points": gender_max,
                "passed": gender_passed,
                "failed": gender_failed,
                "pass": round(gender_passed / len(gender_assessed) * 100, 2) if gender_assessed else 0,
                "fail": round(gender_failed / len(gender_assessed) * 100, 2) if gender_assessed else 0,
            }
        )

    return {
        "config": {"pass_mark": pass_mark, "prev_exam": previous_exam.name if previous_exam else "imtixaankii hore"},
        "counts": {
            "students": len(students),
            "expected": expected,
            "entered": len(results),
            "entered_students": entered_students,
            "complete_students": complete_students,
            "partial_students": partial_students,
            "no_result_students": no_result_students,
            "missing": missing,
            "absent": len(absent_pairs),
            "absent_students": len({student_id for student_id, _ in absent_pairs}),
            "zero": sum(float(result.score) == 0 for result in results),
            "passed": passed_students, "failed": max(assessed_students - passed_students, 0),
            "not_assessed": len(students) - assessed_students,
            "states": approval_states,
        },
        "kpi": {
            "avg": _safe_mean(scores),
            "avg_points": average_points,
            "max_points": float(common_max) if common_max is not None else None,
            "median": _safe_median(scores),
            "high": max(scores) if scores else None,
            "low": min(scores) if scores else None,
            "pass_rate": round(passed_students / assessed_students * 100, 2) if assessed_students else None,
        },
        "grades": grades_payload,
        "hist": histogram,
        "subjects": subjects_payload,
        "classes": class_payload,
        "gender": gender_payload,
        "trend": trend,
        "support": support[:20],
        "support_total": len(support),
        "top": scored_students[:5],
        "up": improving,
        "down": declining,
        "insights": insights,
    }


def students_list_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    overall_averages = student_overall_averages(results)
    rows = []
    for student in students:
        average = overall_averages.get(student.id, 0)
        grade = grade_for(average).get("grade", "-")
        rows.append(
            {
                "student": student,
                "class_name": student.school_class.name if student.school_class else "-",
                "section": student.section or "-",
                "average": round(average, 2),
                "grade": grade,
            }
        )
    rows.sort(key=lambda item: item["student"].full_name.lower())
    return rows


def examinations_list_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    results = scoped_results(teacher, filters, students)
    exam_map = {}
    for result in results:
        exam = result.exam
        bucket = exam_map.setdefault(
            exam.id,
            {
                "exam": exam,
                "result_count": 0,
                "student_ids": set(),
                "average_values": [],
            },
        )
        bucket["result_count"] += 1
        bucket["student_ids"].add(result.student_id)
        bucket["average_values"].append(_pct(result))
    rows = []
    for payload in exam_map.values():
        rows.append(
            {
                "exam": payload["exam"],
                "result_count": payload["result_count"],
                "student_count": len(payload["student_ids"]),
                "average": _safe_mean(payload["average_values"]),
                "academic_year": payload["exam"].academic_year.name if payload["exam"].academic_year else "-",
            }
        )
    rows.sort(key=lambda item: item["exam"].created_at, reverse=True)
    return rows


def results_list_data(teacher, filters):
    filters = validate_filter_ids(teacher, filters)
    students = scoped_students(teacher, filters)
    student_map = {student.id: student for student in students}
    results = scoped_results(teacher, filters, students)
    rows = []
    for result in results:
        student = student_map.get(result.student_id)
        if not student:
            continue
        percentage = _pct(result)
        rows.append(
            {
                "student": student,
                "exam": result.exam,
                "subject": result.subject,
                "score": float(result.score),
                "max_score": float(_result_max_score(result)),
                "percentage": round(percentage, 2),
                "grade": grade_for(percentage, exam_id=result.exam_id).get("grade", "-"),
                "is_published": result.is_published,
            }
        )
    rows.sort(key=lambda item: (item["exam"].created_at, item["student"].full_name), reverse=True)
    return rows
