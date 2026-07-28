import re
from collections import Counter
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from . import db
from .models import AcademicClass, AcademicLevel, AcademicSection, AcademicYear, Exam, Result, SchoolClass, Student, Subject


STUDENT_HEADERS = ["student_id", "full_name", "mother_name", "phone", "class", "academic_year"]
PHONE_REGEX = re.compile(r"^\+25261\d{7}$")
YEAR_REGEX = re.compile(r"^\d{4}-\d{4}$")


def student_template():
    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    ws.append(STUDENT_HEADERS)
    ws.append(["3001", "Amina Ali Omar", "Sahra Jama", "+252615551234", "Form One A", "2025-2026"])
    ws.append(["3002", "Hassan Farah Noor", "Fadumo Abdi", "+252615555678", "Form One B", "2025-2026"])
    return wb


def result_entry_import_template(year_id=None, exam_id=None, level_id=None, class_id=None, section_id=None):
    """Generate an Excel template for result import.

    When scope parameters are supplied the sheet is pre-filled with every
    enrolled student in the selected class and the subject columns that
    belong to that level.  The teacher only needs to enter marks.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Result Entry"

    # ── resolve scope objects ────────────────────────────────────────
    selected_year = db.session.get(AcademicYear, year_id) if year_id else None
    selected_exam = db.session.get(Exam, exam_id) if exam_id else None
    selected_level = db.session.get(AcademicLevel, level_id) if level_id else None
    selected_class = db.session.get(AcademicClass, class_id) if class_id else None
    selected_section = db.session.get(AcademicSection, section_id) if section_id else None

    # Derive level from class if not explicitly given
    if selected_class and not selected_level:
        selected_level = selected_class.academic_level

    # ── resolve subjects for scope ───────────────────────────────────
    effective_level_id = selected_level.id if selected_level else None
    if effective_level_id:
        scoped_subjects = Subject.query.filter_by(academic_level_id=effective_level_id).order_by(Subject.sort_order, Subject.name).all()
        if not scoped_subjects:
            scoped_subjects = Subject.query.filter(Subject.academic_level_id.is_(None)).order_by(Subject.sort_order, Subject.name).all()
    else:
        scoped_subjects = Subject.query.order_by(Subject.sort_order, Subject.name).all()
    subject_names = [s.name for s in scoped_subjects]
    if not subject_names:
        subject_names = ["Math", "English", "Somali", "Physics", "Chemistry",
                         "Biology", "History", "Geography", "Islamic", "Technology"]

    # ── header row ───────────────────────────────────────────────────
    headers = ["#", "student_id", "full_name", "mother_name", "class",
               "exam_type", "academic_year"] + subject_names
    ws.append(headers)

    # Style the header row
    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(name="Inter", bold=True, color="FFFFFF", size=11)
    thin_border = Border(
        bottom=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
    )
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # ── pre-fill student rows when scope is available ────────────────
    year_name = selected_year.name if selected_year else ""
    exam_type_name = selected_exam.name if selected_exam else ""
    class_name = selected_class.name if selected_class else ""

    if selected_year and selected_class:
        # Build student query identical to the result entry grid
        query = Student.query.filter_by(academic_year_id=selected_year.id)
        query = query.filter(
            (Student.academic_class_id == selected_class.id) |
            (Student.class_id == SchoolClass.query.filter_by(name=selected_class.name).first().id
             if SchoolClass.query.filter_by(name=selected_class.name).first() else False)
        )
        if selected_section:
            query = query.filter(
                (Student.academic_section_id == selected_section.id) |
                (Student.section == selected_section.name)
            )
        students = query.order_by(Student.full_name).all()

        locked_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        locked_font = Font(name="Inter", size=10, color="334155")
        mark_font = Font(name="Inter", size=11)

        for idx, student in enumerate(students, start=1):
            row_data = [
                idx,
                student.student_code,
                student.full_name,
                student.mother_name or "",
                class_name,
                exam_type_name,
                year_name,
            ] + ["" for _ in subject_names]  # blank mark cells
            ws.append(row_data)

            # Style the pre-filled metadata cells (lock-grey)
            row_num = ws.max_row
            for col_i in range(1, 8):  # columns A-G  (# through academic_year)
                cell = ws.cell(row=row_num, column=col_i)
                cell.fill = locked_fill
                cell.font = locked_font
                cell.alignment = Alignment(horizontal="center")
            # Style the mark cells with a subtle prompt
            for col_i in range(8, 8 + len(subject_names)):
                cell = ws.cell(row=row_num, column=col_i)
                cell.font = mark_font
                cell.alignment = Alignment(horizontal="center")
    else:
        # Fallback: generic sample row when no scope is provided
        sample_scores = [85, 90, 78, 88, 92, 85, 95, 80, 96, 91][:len(subject_names)]
        ws.append([1, "3001", "Amina Ali Omar", "Sahra Jama", "Form One A",
                   "Midterm", "2025-2026"] + sample_scores)

    # Auto-size columns for readability
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 4, 30)

    return wb


def clean_str(val):
    if val is None:
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def process_student_import(file):
    wb = load_workbook(file, data_only=True)
    ws = wb.active

    headers = [clean_str(cell.value).lower() for cell in ws[1]]
    required_headers = ["student_id", "full_name", "mother_name", "phone", "class", "academic_year"]
    missing = [h for h in required_headers if h not in headers]
    if missing:
        return {
            "success_count": 0,
            "failed_count": 0,
            "errors": [f"Missing required columns in header: {', '.join(missing)}"],
            "kind": "Students"
        }

    # Pre-fetch lookup data
    existing_students = {s.student_code: s for s in Student.query.all()}
    existing_years = {y.name: y for y in AcademicYear.query.all()}

    classes_by_name = {}
    for sc in SchoolClass.query.all():
        classes_by_name[sc.name.lower()] = sc
    for ac in AcademicClass.query.all():
        classes_by_name[ac.name.lower()] = ac

    seen_file_ids = set()
    valid_students_to_add = []
    failed_errors = []
    success_count = 0
    failed_count = 0

    for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row_cells or all(c is None or str(c).strip() == "" for c in row_cells):
            continue

        data = {headers[i]: clean_str(val) for i, val in enumerate(row_cells) if i < len(headers)}

        student_id = data.get("student_id", "")
        full_name = data.get("full_name", "")
        mother_name = data.get("mother_name", "")
        phone = data.get("phone", "")
        class_name = data.get("class", "")
        academic_year = data.get("academic_year", "")

        # Format normalization for phone if starts with 25261 without plus
        if phone.startswith("25261") and len(phone) == 12:
            phone = "+" + phone

        row_errors = []

        # 1. student_id validation
        if not student_id:
            row_errors.append(f"Row {row_idx}: student_id is required.")
        elif not student_id.isdigit():
            row_errors.append(f"Row {row_idx}: student_id must be numeric (got '{student_id}').")
        elif student_id in seen_file_ids:
            row_errors.append(f"Row {row_idx}: student_id '{student_id}' is duplicated within this file.")
        elif student_id in existing_students:
            row_errors.append(f"Row {row_idx}: student_id '{student_id}' already exists in database.")
        else:
            seen_file_ids.add(student_id)

        # 2. full_name validation
        if not full_name:
            row_errors.append(f"Row {row_idx}: full_name is required.")

        # 3. mother_name validation
        if not mother_name:
            row_errors.append(f"Row {row_idx}: mother_name is required.")

        # 4. phone validation
        if not phone:
            row_errors.append(f"Row {row_idx}: phone is required.")
        elif not PHONE_REGEX.match(phone):
            row_errors.append(f"Row {row_idx}: phone number invalid (must start with +25261 followed by 7 digits).")

        # 5. class validation
        if not class_name:
            row_errors.append(f"Row {row_idx}: class is required.")
        elif class_name.lower() not in classes_by_name:
            row_errors.append(f"Row {row_idx}: class '{class_name}' does not exist.")

        # 6. academic_year validation
        if not academic_year:
            row_errors.append(f"Row {row_idx}: academic_year is required.")
        elif not YEAR_REGEX.match(academic_year):
            row_errors.append(f"Row {row_idx}: academic_year format invalid (must be YYYY-YYYY).")
        elif academic_year not in existing_years:
            row_errors.append(f"Row {row_idx}: academic_year '{academic_year}' does not exist in database.")

        if row_errors:
            failed_count += 1
            failed_errors.extend(row_errors)
        else:
            year_obj = existing_years[academic_year]
            matched_class = classes_by_name[class_name.lower()]

            new_student = Student(
                student_code=student_id,
                full_name=full_name,
                mother_name=mother_name,
                phone=phone,
                academic_year=year_obj,
                is_active=True
            )

            if isinstance(matched_class, AcademicClass):
                new_student.academic_class = matched_class
                if matched_class.academic_level:
                    new_student.academic_level = matched_class.academic_level
                    new_student.level = matched_class.academic_level.name
                school_c = SchoolClass.query.filter_by(name=matched_class.name).first()
                if not school_c:
                    school_c = SchoolClass(name=matched_class.name)
                    db.session.add(school_c)
                    db.session.flush()
                new_student.school_class = school_c
            else:
                new_student.school_class = matched_class
                ac_c = AcademicClass.query.filter_by(name=matched_class.name).first()
                if ac_c:
                    new_student.academic_class = ac_c
                    if ac_c.academic_level:
                        new_student.academic_level = ac_c.academic_level
                        new_student.level = ac_c.academic_level.name

            valid_students_to_add.append(new_student)
            success_count += 1

    if valid_students_to_add:
        try:
            db.session.add_all(valid_students_to_add)
            db.session.commit()
        except Exception as ex:
            db.session.rollback()
            failed_errors.append(f"Database error while saving students: {str(ex)}")
            return {
                "success_count": 0,
                "failed_count": success_count + failed_count,
                "errors": failed_errors,
                "kind": "Students"
            }

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "errors": failed_errors,
        "kind": "Students"
    }


def process_result_import(file):
    wb = load_workbook(file, data_only=True)
    ws = wb.active

    raw_headers = [clean_str(cell.value) for cell in ws[1]]
    headers_lower = [h.lower() for h in raw_headers]

    required_fields = ["student_id", "class", "exam_type", "academic_year"]
    missing = [f for f in required_fields if f not in headers_lower]
    if missing:
        return {
            "success_count": 0,
            "failed_count": 0,
            "errors": [f"Missing required headers in file: {', '.join(missing)}"],
            "kind": "Results"
        }

    fixed_header_set = {"#", "student_id", "full_name", "mother_name", "class", "exam_type", "academic_year"}
    db_subjects = {s.name.lower(): s for s in Subject.query.all()}

    subject_cols = {}
    header_errors = []
    for col_idx, h_name in enumerate(raw_headers):
        if not h_name or h_name.lower() in fixed_header_set:
            continue
        subj_obj = db_subjects.get(h_name.lower())
        if subj_obj:
            subject_cols[col_idx] = subj_obj
        else:
            header_errors.append(f"Subject '{h_name}' in column {col_idx + 1} does not exist in system.")

    if header_errors:
        return {
            "success_count": 0,
            "failed_count": 0,
            "errors": header_errors,
            "kind": "Results"
        }

    existing_students = {s.student_code: s for s in Student.query.all()}
    existing_years = {y.name: y for y in AcademicYear.query.all()}
    existing_exams = {(e.name.lower(), e.academic_year_id): e for e in Exam.query.all()}

    success_count = 0
    failed_count = 0
    failed_errors = []
    valid_row_entries = []

    for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        try:
            if not row_cells or all(c is None or str(c).strip() == "" for c in row_cells):
                continue

            row_map = {}
            for col_idx, val in enumerate(row_cells):
                if col_idx < len(headers_lower) and headers_lower[col_idx]:
                    row_map[headers_lower[col_idx]] = clean_str(val)

            student_id = row_map.get("student_id", "")
            provided_class = row_map.get("class", "")
            exam_type = row_map.get("exam_type", "")
            academic_year = row_map.get("academic_year", "")

            row_errors = []

        # 1. student_id check
        student_obj = None
        if not student_id:
            row_errors.append(f"Row {row_idx}: student_id is required.")
        else:
            student_obj = existing_students.get(student_id)
            if not student_obj:
                row_errors.append(f"Row {row_idx}: student_id '{student_id}' not found in system.")

        # 2. class check (must match student's actual class)
        if student_obj:
            student_actual_class = ""
            if student_obj.academic_class:
                student_actual_class = student_obj.academic_class.name
            elif student_obj.school_class:
                student_actual_class = student_obj.school_class.name

            if not provided_class:
                row_errors.append(f"Row {row_idx}: class is required.")
            elif provided_class.lower() != student_actual_class.lower():
                row_errors.append(f"Row {row_idx}: class '{provided_class}' does not match student's class '{student_actual_class}'.")

        # 3. exam_type check
        if not exam_type:
            row_errors.append(f"Row {row_idx}: exam_type is required.")

        # 4. academic_year check
        year_obj = None
        if not academic_year:
            row_errors.append(f"Row {row_idx}: academic_year is required.")
        elif not YEAR_REGEX.match(academic_year):
            row_errors.append(f"Row {row_idx}: academic_year format invalid (must be YYYY-YYYY).")
        else:
            year_obj = existing_years.get(academic_year)
            if not year_obj:
                row_errors.append(f"Row {row_idx}: academic_year '{academic_year}' does not exist in system.")

        # 5. subject mark checks
        row_subject_marks = []
        for col_idx, subj_obj in subject_cols.items():
            if col_idx >= len(row_cells):
                continue
            cell_val = row_cells[col_idx]
            if cell_val is None or str(cell_val).strip() == "":
                continue

            try:
                score_num = float(cell_val)
                max_score = float(subj_obj.max_score or 100)
                if score_num < 0 or score_num > max_score:
                    row_errors.append(f"Row {row_idx}: mark for '{subj_obj.name}' ({score_num:g}) must be between 0 and {max_score:g}.")
                else:
                    row_subject_marks.append((subj_obj, score_num))
            except (TypeError, ValueError):
                row_errors.append(f"Row {row_idx}: mark for '{subj_obj.name}' must be numeric (got '{cell_val}').")

            if row_errors:
                failed_count += 1
                failed_errors.extend(row_errors)
            else:
                valid_row_entries.append({
                    "row_idx": row_idx,
                    "student": student_obj,
                    "exam_name": exam_type,
                    "year": year_obj,
                    "marks": row_subject_marks
                })
        except Exception as e:
            failed_count += 1
            failed_errors.append(f"Row {row_idx}: Unhandled error parsing row ({str(e)})")

    if valid_row_entries:
        for entry in valid_row_entries:
            row_idx = entry["row_idx"]
            st = entry["student"]
            ex_name = entry["exam_name"]
            yr = entry["year"]
            marks = entry["marks"]

            exam_key = (ex_name.lower(), yr.id)
            exam_obj = existing_exams.get(exam_key)

            try:
                with db.session.begin_nested():
                    if not exam_obj:
                        exam_obj = Exam(
                            name=ex_name,
                            academic_year_id=yr.id,
                            is_active=True,
                            is_published=True
                        )
                        # Omit binding the exam to a specific class/level if it's a global import
                        db.session.add(exam_obj)
                        db.session.flush()
                        existing_exams[exam_key] = exam_obj

                    for subj_obj, score_num in marks:
                        res = Result.query.filter_by(
                            student_id=st.id,
                            exam_id=exam_obj.id,
                            subject_id=subj_obj.id
                        ).first()
                        if not res:
                            res = Result(
                                student_id=st.id,
                                exam_id=exam_obj.id,
                                subject_id=subj_obj.id,
                                score=score_num,
                                is_published=True
                            )
                            db.session.add(res)
                        else:
                            res.score = score_num
                            res.is_published=True
                            
                success_count += 1
            except Exception as ex:
                failed_count += 1
                failed_errors.append(f"Row {row_idx}: Database error saving results - {str(ex)}")

        try:
            db.session.commit()
        except Exception as ex:
            db.session.rollback()
            return {
                "success_count": 0,
                "failed_count": success_count + failed_count,
                "errors": [f"Fatal database error during commit: {str(ex)}"],
                "kind": "Results"
            }

    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "errors": failed_errors,
        "kind": "Results"
    }
