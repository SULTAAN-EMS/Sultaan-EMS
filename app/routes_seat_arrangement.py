import math
import random
from collections import Counter
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from . import db
from .audit import audit
from .models import (
    AcademicClass, AcademicLevel, AcademicSection, AcademicYear, Exam, ExamHall,
    SeatAssignment, Student
)
from .enrollment_service import EnrollmentValidationError, enrollment_placement_for_student, student_enrollment_legacy_scope_query
from .permissions import enforce_endpoint_permission

seat_arrangement_bp = Blueprint("seat_arrangement", __name__)


# Color palette for class visualization (deterministic by class ID)
CLASS_PALETTE = [
    '#60A5FA', '#F472B6', '#34D399', '#FBBF24', '#A78BFA', '#22D3EE',
    '#FB923C', '#818CF8', '#F87171', '#4ADE80', '#FB7185', '#38BDF8'
]


def get_class_color(class_id):
    """Deterministic color assignment based on class ID"""
    return CLASS_PALETTE[class_id % len(CLASS_PALETTE)]


def students_for_exam_classes(exam, class_ids):
    """Resolve seat candidates through the selected exam year's enrollments."""
    student_ids = set()
    for class_id in class_ids:
        try:
            student_ids.update(
                student.id
                for student in student_enrollment_legacy_scope_query(
                    exam.academic_year_id,
                    legacy_class_id=class_id,
                ).filter(Student.is_active.is_(True)).all()
            )
        except EnrollmentValidationError:
            continue
    students = (
        Student.query.filter(Student.id.in_(student_ids), Student.is_active.is_(True))
        .order_by(Student.full_name)
        .all()
        if student_ids else []
    )
    for student in students:
        student._seat_exam_placement = enrollment_placement_for_student(student, exam.academic_year_id) or {}
    return students


def seat_student_scope(student):
    placement = getattr(student, "_seat_exam_placement", {}) or {}
    return {
        "class_id": placement.get("academic_class_id") or student.academic_class_id,
        "class_name": placement.get("class_name") or (student.academic_class.name if student.academic_class else ""),
        "level": placement.get("level_name") or (student.academic_level.name if student.academic_level else ""),
    }


@seat_arrangement_bp.before_request
@login_required
def require_login():
    enforce_endpoint_permission()


@seat_arrangement_bp.route("/")
def index():
    """Main seat arrangement dashboard - select Exam Type and Hall"""
    academic_years = AcademicYear.query.order_by(AcademicYear.name.desc()).all()
    exams = Exam.query.filter_by(is_active=True).order_by(Exam.sort_order, Exam.name).all()
    halls = ExamHall.query.filter_by(is_active=True).order_by(ExamHall.sort_order, ExamHall.name).all()
    
    return render_template(
        "admin/seat_arrangement_index.html",
        academic_years=academic_years,
        exams=exams,
        halls=halls
    )


@seat_arrangement_bp.route("/halls")
def hall_list():
    """Manage exam halls"""
    halls = ExamHall.query.order_by(ExamHall.sort_order, ExamHall.name).all()
    return render_template("admin/seat_arrangement_halls.html", halls=halls)


@seat_arrangement_bp.route("/halls/new", methods=["GET", "POST"])
@seat_arrangement_bp.route("/halls/<int:hall_id>/edit", methods=["GET", "POST"])
def hall_form(hall_id=None):
    """Create or edit exam hall"""
    hall = db.session.get(ExamHall, hall_id) if hall_id else ExamHall()
    
    if request.method == "POST":
        hall.name = request.form["name"].strip()
        hall.code = request.form.get("code", "").strip().upper()
        hall.description = request.form.get("description", "").strip()
        hall.capacity = int(request.form.get("capacity", 0))
        hall.is_active = bool(request.form.get("is_active"))
        hall.sort_order = int(request.form.get("sort_order", 0))
        
        db.session.add(hall)
        audit("Seat Arrangement", f"Saved exam hall: {hall.name}")
        db.session.commit()
        flash("Exam hall saved successfully.", "success")
        return redirect(url_for("seat_arrangement.hall_list"))
    
    return render_template("admin/seat_arrangement_hall_form.html", hall=hall)


@seat_arrangement_bp.route("/halls/<int:hall_id>/delete", methods=["POST"])
def delete_hall(hall_id):
    """Delete exam hall"""
    hall = db.session.get(ExamHall, hall_id)
    if not hall:
        flash("Exam hall not found.", "danger")
        return redirect(url_for("seat_arrangement.hall_list"))
    
    # Check if hall has seat assignments
    if hall.seat_assignments.count() > 0:
        flash("Cannot delete exam hall with existing seat assignments.", "danger")
        return redirect(url_for("seat_arrangement.hall_list"))
    
    db.session.delete(hall)
    audit("Seat Arrangement", f"Deleted exam hall: {hall.name}")
    db.session.commit()
    flash("Exam hall deleted.", "success")
    return redirect(url_for("seat_arrangement.hall_list"))


@seat_arrangement_bp.route("/builder")
def builder():
    """Seat arrangement builder for specific Exam + Hall combination"""
    exam_id = request.args.get("exam_id", type=int)
    hall_id = request.args.get("hall_id", type=int)
    
    if not exam_id or not hall_id:
        flash("Please select an exam and hall.", "warning")
        return redirect(url_for("seat_arrangement.index"))
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    hall = db.session.get(ExamHall, hall_id) or abort(404)
    
    # Load existing assignments or start fresh
    existing_assignments = SeatAssignment.query.filter_by(
        exam_id=exam_id, exam_hall_id=hall_id
    ).all()
    
    # Get available classes/sections for this exam's academic year
    classes = AcademicClass.query.join(AcademicLevel).filter(
        AcademicClass.is_active == True
    ).order_by(AcademicLevel.sort_order, AcademicClass.sort_order).all()
    
    # Build class data with student counts
    class_data = []
    for cls in classes:
        student_count = len(students_for_exam_classes(exam, [cls.id]))
        if student_count > 0:
            class_data.append({
                'id': cls.id,
                'name': cls.name,
                'level': cls.academic_level.name if cls.academic_level else '',
                'count': student_count,
                'color': get_class_color(cls.id)
            })
    
    # Determine configuration from existing assignments or defaults
    if existing_assignments:
        config = {
            'rows': existing_assignments[0].rows_config,
            'tables_per_row': existing_assignments[0].tables_per_row_config,
            'seats_per_table': existing_assignments[0].seats_per_table_config
        }
    else:
        config = {'rows': 3, 'tables_per_row': 5, 'seats_per_table': 2}
    
    return render_template(
        "admin/seat_arrangement_builder.html",
        exam=exam,
        hall=hall,
        class_data=class_data,
        config=config,
        existing_assignments=existing_assignments
    )


@seat_arrangement_bp.route("/api/students")
def api_students():
    """API to get students for selected classes"""
    exam_id = request.args.get("exam_id", type=int)
    class_ids = request.args.getlist("class_ids", type=int)
    
    if not exam_id or not class_ids:
        return jsonify({'error': 'Missing parameters'}), 400
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    
    students = students_for_exam_classes(exam, class_ids)
    
    student_data = []
    for student in students:
        scope = seat_student_scope(student)
        student_data.append({
            'id': student.id,
            'student_code': student.student_code,
            'full_name': student.full_name,
            'class_id': scope['class_id'],
            'class_name': scope['class_name'],
            'level': scope['level'],
            'photo_path': student.photo_path
        })
    
    return jsonify({'students': student_data})


@seat_arrangement_bp.route("/api/generate", methods=["POST"])
def api_generate():
    """Quick generate - greedy fill algorithm"""
    data = request.get_json()
    exam_id = data.get("exam_id")
    hall_id = data.get("hall_id")
    config = data.get("config", {})
    class_ids = data.get("class_ids", [])
    
    if not exam_id or not hall_id or not class_ids:
        return jsonify({'error': 'Missing parameters'}), 400
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    
    # Get students for selected classes
    students = students_for_exam_classes(exam, class_ids)
    
    # Group students by class
    class_pools = {}
    for student in students:
        class_id = seat_student_scope(student)['class_id'] or student.id
        if class_id not in class_pools:
            class_pools[class_id] = []
        class_pools[class_id].append(student)
    
    # Shuffle each class pool
    for class_id in class_pools:
        random.shuffle(class_pools[class_id])
    
    # Build seat structure
    rows = config.get("rows", 3)
    tables_per_row = config.get("tables_per_row", 5)
    seats_per_table = config.get("seats_per_table", 2)
    
    seats = []
    for row in range(rows):
        for table in range(tables_per_row):
            for seat in range(seats_per_table):
                seats.append({
                    'row': row,
                    'table': table,
                    'seat': seat,
                    'assigned': None
                })
    
    # Greedy fill - no same class at same table
    table_classes = {}  # table_id -> set of class_ids
    
    for seat in seats:
        table_id = f"{seat['row']}-{seat['table']}"
        if table_id not in table_classes:
            table_classes[table_id] = set()
        
        # Find a class not at this table
        available_classes = [
            cid for cid in class_pools.keys()
            if class_pools[cid] and cid not in table_classes[table_id]
        ]
        
        if available_classes:
            # Pick from class with most remaining students
            chosen_class = max(available_classes, key=lambda cid: len(class_pools[cid]))
        else:
            # All classes at this table, pick any with remaining students
            available_classes = [cid for cid in class_pools.keys() if class_pools[cid]]
            if available_classes:
                chosen_class = max(available_classes, key=lambda cid: len(class_pools[cid]))
            else:
                continue  # No more students
        
        # Assign student
        student = class_pools[chosen_class].pop()
        seat['assigned'] = student
        table_classes[table_id].add(chosen_class)
    
    # Build assignments for response
    assignments = []
    for seat in seats:
        if seat['assigned']:
            scope = seat_student_scope(seat['assigned'])
            assignments.append({
                'student_id': seat['assigned'].id,
                'student_code': seat['assigned'].student_code,
                'full_name': seat['assigned'].full_name,
                'class_id': scope['class_id'],
                'class_name': scope['class_name'],
                'level': scope['level'],
                'photo_path': seat['assigned'].photo_path,
                'row': seat['row'],
                'table': seat['table'],
                'seat': seat['seat']
            })
    
    # Calculate metrics
    metrics = compute_metrics(assignments, rows, tables_per_row)
    
    return jsonify({
        'assignments': assignments,
        'metrics': metrics,
        'config': config
    })


@seat_arrangement_bp.route("/api/optimize", methods=["POST"])
def api_optimize():
    """Strict optimizer - simulated annealing"""
    data = request.get_json()
    exam_id = data.get("exam_id")
    hall_id = data.get("hall_id")
    config = data.get("config", {})
    class_ids = data.get("class_ids", [])
    iterations = data.get("iterations", 6000)
    
    if not exam_id or not hall_id or not class_ids:
        return jsonify({'error': 'Missing parameters'}), 400
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    
    # Get students for selected classes
    students = students_for_exam_classes(exam, class_ids)
    
    # Group students by class
    class_pools = {}
    for student in students:
        class_id = seat_student_scope(student)['class_id'] or student.id
        if class_id not in class_pools:
            class_pools[class_id] = []
        class_pools[class_id].append(student)
    
    # Shuffle each class pool
    for class_id in class_pools:
        random.shuffle(class_pools[class_id])
    
    # Build seat structure
    rows = config.get("rows", 3)
    tables_per_row = config.get("tables_per_row", 5)
    seats_per_table = config.get("seats_per_table", 2)
    
    seats = []
    for row in range(rows):
        for table in range(tables_per_row):
            for seat in range(seats_per_table):
                seats.append({
                    'row': row,
                    'table': table,
                    'seat': seat,
                    'assigned': None
                })
    
    # Initial greedy fill
    table_classes = {}
    
    for seat in seats:
        table_id = f"{seat['row']}-{seat['table']}"
        if table_id not in table_classes:
            table_classes[table_id] = set()
        
        available_classes = [
            cid for cid in class_pools.keys()
            if class_pools[cid] and cid not in table_classes[table_id]
        ]
        
        if available_classes:
            chosen_class = max(available_classes, key=lambda cid: len(class_pools[cid]))
        else:
            available_classes = [cid for cid in class_pools.keys() if class_pools[cid]]
            if available_classes:
                chosen_class = max(available_classes, key=lambda cid: len(class_pools[cid]))
            else:
                continue
        
        student = class_pools[chosen_class].pop()
        seat['assigned'] = student
        table_classes[table_id].add(chosen_class)
    
    # Simulated annealing optimization
    assignments = []
    for seat in seats:
        if seat['assigned']:
            scope = seat_student_scope(seat['assigned'])
            assignments.append({
                'student_id': seat['assigned'].id,
                'student_code': seat['assigned'].student_code,
                'full_name': seat['assigned'].full_name,
                'class_id': scope['class_id'],
                'class_name': scope['class_name'],
                'level': scope['level'],
                'photo_path': seat['assigned'].photo_path,
                'row': seat['row'],
                'table': seat['table'],
                'seat': seat['seat']
            })
    
    # Simulated annealing
    initial_metrics = compute_metrics(assignments, rows, tables_per_row)
    current_score = initial_metrics['hard_count'] * 10 + initial_metrics['soft_count']
    
    for iteration in range(iterations):
        # Temperature schedule: starts high, decreases
        temperature = 1.0 - (iteration / iterations)
        
        # Select two random seats to swap
        if len(assignments) < 2:
            break
        
        idx1, idx2 = random.sample(range(len(assignments)), 2)
        a1, a2 = assignments[idx1], assignments[idx2]
        
        # Skip if same class (no benefit)
        if a1['class_id'] == a2['class_id']:
            continue
        
        # Calculate score before swap
        before_score = compute_metrics(assignments, rows, tables_per_row)
        before_val = before_score['hard_count'] * 10 + before_score['soft_count']
        
        # Perform swap
        temp_row, temp_table, temp_seat = a1['row'], a1['table'], a1['seat']
        a1['row'], a1['table'], a1['seat'] = a2['row'], a2['table'], a2['seat']
        a2['row'], a2['table'], a2['seat'] = temp_row, temp_table, temp_seat
        
        # Calculate score after swap
        after_score = compute_metrics(assignments, rows, tables_per_row)
        after_val = after_score['hard_count'] * 10 + after_score['soft_count']
        
        # Accept or reject based on simulated annealing
        delta = after_val - before_val
        
        if delta < 0:
            # Improvement: accept
            pass
        elif random.random() < math.exp(-delta / (temperature + 0.01)):
            # Worse but accept based on temperature
            pass
        else:
            # Reject: revert swap
            temp_row, temp_table, temp_seat = a1['row'], a1['table'], a1['seat']
            a1['row'], a1['table'], a1['seat'] = a2['row'], a2['table'], a2['seat']
            a2['row'], a2['table'], a2['seat'] = temp_row, temp_table, temp_seat
    
    final_metrics = compute_metrics(assignments, rows, tables_per_row)
    
    return jsonify({
        'assignments': assignments,
        'metrics': final_metrics,
        'config': config,
        'iterations': iterations
    })


def compute_metrics(assignments, rows, tables_per_row):
    """Compute hard and soft violations"""
    table_classes = {}
    table_counts = {}
    
    for a in assignments:
        table_id = f"{a['row']}-{a['table']}"
        if table_id not in table_classes:
            table_classes[table_id] = set()
            table_counts[table_id] = {}
        
        table_classes[table_id].add(a['class_id'])
        table_counts[table_id][a['class_id']] = table_counts[table_id].get(a['class_id'], 0) + 1
    
    # Hard violations: same class at same table
    hard_count = 0
    for counts in table_counts.values():
        for count in counts.values():
            if count > 1:
                hard_count += (count - 1)
    
    # Soft violations: adjacent tables with same class
    soft_count = 0
    for row in range(rows):
        for table in range(tables_per_row - 1):
            table_a = f"{row}-{table}"
            table_b = f"{row}-{table + 1}"
            if table_a in table_classes and table_b in table_classes:
                shared = table_classes[table_a] & table_classes[table_b]
                soft_count += len(shared)
    
    return {
        'hard_count': hard_count,
        'soft_count': soft_count,
        'total_seats': len(assignments),
        'total_students': len(assignments)
    }


@seat_arrangement_bp.route("/api/save", methods=["POST"])
def api_save():
    """Save seat arrangement to database"""
    data = request.get_json()
    exam_id = data.get("exam_id")
    hall_id = data.get("hall_id")
    assignments = data.get("assignments", [])
    config = data.get("config", {})
    
    if not exam_id or not hall_id:
        return jsonify({'error': 'Missing parameters'}), 400
    
    try:
        # Delete existing assignments for this exam + hall
        SeatAssignment.query.filter_by(
            exam_id=exam_id, exam_hall_id=hall_id
        ).delete()
        
        # Create new assignments
        for a in assignments:
            assignment = SeatAssignment(
                exam_id=exam_id,
                exam_hall_id=hall_id,
                student_id=a['student_id'],
                row_number=a['row'],
                table_number=a['table'],
                seat_number=a['seat'],
                rows_config=config.get("rows", 3),
                tables_per_row_config=config.get("tables_per_row", 5),
                seats_per_table_config=config.get("seats_per_table", 2)
            )
            db.session.add(assignment)
        
        db.session.commit()
        audit("Seat Arrangement", f"Saved arrangement for exam {exam_id} in hall {hall_id}")
        
        return jsonify({'success': True, 'count': len(assignments)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@seat_arrangement_bp.route("/api/class-students")
def api_class_students():
    """Get students from same class/section for replacement modal"""
    class_id = request.args.get("class_id", type=int)
    exam_id = request.args.get("exam_id", type=int)
    hall_id = request.args.get("hall_id", type=int)
    current_student_id = request.args.get("current_student_id", type=int)
    
    if not class_id or not exam_id:
        return jsonify({'error': 'Missing parameters'}), 400
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    
    # Get all students from this class
    students = students_for_exam_classes(exam, [class_id])
    
    # Get existing assignments for this exam + hall
    existing_assignments = SeatAssignment.query.filter_by(
        exam_id=exam_id, exam_hall_id=hall_id
    ).all()
    
    # Map student_id to their current seat position
    seat_positions = {}
    for sa in existing_assignments:
        seat_positions[sa.student_id] = {
            'row': sa.row_number,
            'table': sa.table_number,
            'seat': sa.seat_number
        }
    
    # Build response
    student_data = []
    for student in students:
        position = {
            'row': None,
            'table': None,
            'seat': None,
            'label': 'Not Assigned'
        }
        if student.id in seat_positions:
            pos = seat_positions[student.id]
            position = {
                'row': pos['row'],
                'table': pos['table'],
                'seat': pos['seat'],
                'label': f"R{pos['row']+1}T{pos['table']+1}S{pos['seat']+1}"
            }
        
        student_data.append({
            **seat_student_scope(student),
            'id': student.id,
            'student_code': student.student_code,
            'full_name': student.full_name,
            'photo_path': student.photo_path,
            'position': position,
            'is_current': student.id == current_student_id
        })
    
    return jsonify({'students': student_data})


@seat_arrangement_bp.route("/api/validate", methods=["POST"])
def api_validate():
    """Validate seating rules after manual replacement"""
    data = request.get_json()
    exam_id = data.get("exam_id")
    hall_id = data.get("hall_id")
    assignments = data.get("assignments", [])
    config = data.get("config", {})
    
    if not exam_id or not hall_id:
        return jsonify({'error': 'Missing parameters'}), 400
    
    rows = config.get("rows", 3)
    tables_per_row = config.get("tables_per_row", 5)
    
    metrics = compute_metrics(assignments, rows, tables_per_row)
    
    return jsonify({
        'valid': metrics['hard_count'] == 0,
        'metrics': metrics
    })


@seat_arrangement_bp.route("/print")
def print_arrangement():
    """Print/export view for seat arrangement"""
    exam_id = request.args.get("exam_id", type=int)
    hall_id = request.args.get("hall_id", type=int)
    
    if not exam_id or not hall_id:
        flash("Missing exam or hall ID.", "danger")
        return redirect(url_for("seat_arrangement.index"))
    
    exam = db.session.get(Exam, exam_id) or abort(404)
    hall = db.session.get(ExamHall, hall_id) or abort(404)
    
    # Get saved assignments
    assignments = SeatAssignment.query.filter_by(
        exam_id=exam_id, exam_hall_id=hall_id
    ).join(SeatAssignment.student).order_by(
        SeatAssignment.row_number,
        SeatAssignment.table_number,
        SeatAssignment.seat_number
    ).all()
    
    if not assignments:
        flash("No saved arrangement found for this exam and hall.", "warning")
        return redirect(url_for("seat_arrangement.builder", exam_id=exam_id, hall_id=hall_id))
    
    return render_template(
        "admin/seat_arrangement_print.html",
        exam=exam,
        hall=hall,
        assignments=assignments
    )
