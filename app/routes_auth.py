from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .models import Setting, User
from .audit import audit
from . import db
from .services import get_settings, validate_admin_password

auth_bp = Blueprint("auth", __name__)


def _is_linked_teacher_user(user):
    teacher = getattr(user, "teacher_profile", None)
    return bool(teacher and teacher.user_id == user.id and teacher.is_active and teacher.employment_status == "Active")


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if _is_linked_teacher_user(current_user):
            return redirect(url_for("teacher_portal.dashboard"))
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username, is_active=True).first()
        if user and user.check_password(password):
            if _is_linked_teacher_user(user):
                audit("Failed Login", f"Teacher account {username} attempted admin login")
                db.session.commit()
                flash("Teachers must use the dedicated Teacher Portal login.", "warning")
                return redirect(url_for("teacher_portal.login"))
            login_user(user)
            session_timeout = get_settings().get("admin_session_timeout_minutes", "60")
            try:
                session_timeout = max(5, min(1440, int(session_timeout)))
            except (TypeError, ValueError):
                session_timeout = 60
            session["admin_last_activity"] = datetime.utcnow().isoformat()
            session["admin_session_timeout_minutes"] = session_timeout
            audit("Login", f"User {username} logged in")
            db.session.commit()
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        audit("Failed Login", f"Failed login for username {username}")
        db.session.commit()
        flash("Invalid username or password.", "danger")
    return render_template("admin/login.html")


@auth_bp.route("/admin/logout", methods=["POST"])
@login_required
def logout():
    from .teacher_portal import is_teacher_account

    was_teacher = is_teacher_account()
    audit("Logout", f"User {current_user.username} logged out")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    if was_teacher:
        return redirect(url_for("teacher_portal.login"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/admin/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if _is_linked_teacher_user(current_user):
        return redirect(url_for("teacher_portal.change_password"))
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        else:
            valid_password, password_error = validate_admin_password(new_password)
            if not valid_password:
                flash(password_error, "danger")
            elif new_password != confirm:
                flash("Passwords do not match.", "danger")
            else:
                current_user.set_password(new_password)
                audit("Security", f"Changed password for {current_user.username}")
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("auth.change_password"))
    return render_template(
        "admin/security.html",
        security_settings=get_settings(),
        can_manage_security=current_user.can_manage_users(),
    )


@auth_bp.route("/admin/security/settings", methods=["POST"])
@login_required
def save_security_settings():
    if not current_user.can_manage_users():
        abort(403)
    payload = request.get_json(silent=True) or request.form
    composition = str(payload.get("admin_password_composition", "letters_numbers"))
    if composition not in {"letters", "numbers", "letters_numbers"}:
        return jsonify({"success": False, "message": "Choose a valid password composition rule."}), 400
    try:
        minimum = max(6, min(32, int(payload.get("admin_password_min_length", 8))))
        timeout = max(5, min(1440, int(payload.get("admin_session_timeout_minutes", 60))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Enter valid security limits."}), 400
    values = {
        "admin_password_composition": composition,
        "admin_password_min_length": str(minimum),
        "admin_session_timeout_minutes": str(timeout),
    }
    for key, value in values.items():
        setting = db.session.get(Setting, key) or Setting(key=key)
        setting.value = value
        db.session.add(setting)
    audit("Security Settings", "Updated administrator password policy and session timeout")
    db.session.commit()
    return jsonify({"success": True, "message": "Security settings saved.", "settings": values})
