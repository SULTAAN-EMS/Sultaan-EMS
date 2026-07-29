from datetime import datetime, timedelta
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash
import functools

from . import db
from .models import ExamInvigilator, IncidentReportSettings, InvigilatorLoginHistory
from .services import get_settings

invigilator_bp = Blueprint("invigilator", __name__)

def incident_setting_value(key, default=None):
    row = IncidentReportSettings.query.filter_by(setting_key=key).first()
    return row.setting_value if row else default


def incident_bool_setting(key, default=False):
    return str(incident_setting_value(key, "true" if default else "false")).lower() == "true"


def incident_int_setting(key, default):
    try:
        return int(incident_setting_value(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def incident_settings_dict():
    return {row.setting_key: row.setting_value for row in IncidentReportSettings.query.all()}


def validate_invigilator_password(password):
    policy_type = incident_setting_value("invigilator_password_type", "letters_numbers")
    min_length = int(incident_setting_value("invigilator_password_min_length", "6") or 6)
    if len(password or "") < min_length:
        return False, f"Password must be at least {min_length} characters."
    if policy_type == "numbers" and not password.isdigit():
        return False, "Password must contain numbers only."
    if policy_type == "letters" and not password.isalpha():
        return False, "Password must contain letters only."
    if policy_type == "letters_numbers" and not password.isalnum():
        return False, "Password must contain letters and numbers only."
    return True, ""


def login_invigilator(invigilator, remember=False):
    """Log in an invigilator and create session"""
    session["invigilator_id"] = invigilator.id
    session["invigilator_invigilator_id"] = invigilator.invigilator_id
    session["invigilator_username"] = invigilator.username
    session["invigilator_full_name"] = invigilator.full_name
    session["invigilator_role"] = invigilator.role
    session["invigilator_photo_path"] = invigilator.photo_path
    session["invigilator_logged_in"] = True
    session["invigilator_login_time"] = datetime.utcnow().isoformat()
    session["invigilator_last_activity"] = datetime.utcnow().isoformat()
    session["invigilator_remember_login"] = bool(remember)
    
    # Update last login time
    invigilator.last_login_at = datetime.utcnow()
    db.session.commit()


def logout_invigilator():
    """Log out the current invigilator"""
    invigilator_id = session.get("invigilator_id")
    if invigilator_id:
        invigilator = ExamInvigilator.query.get(invigilator_id)
        if invigilator:
            invigilator.last_logout_at = datetime.utcnow()
            db.session.commit()
    
    session.pop("invigilator_id", None)
    session.pop("invigilator_invigilator_id", None)
    session.pop("invigilator_username", None)
    session.pop("invigilator_full_name", None)
    session.pop("invigilator_role", None)
    session.pop("invigilator_photo_path", None)
    session.pop("invigilator_logged_in", None)
    session.pop("invigilator_login_time", None)
    session.pop("invigilator_last_activity", None)
    session.pop("invigilator_remember_login", None)


def current_invigilator():
    """Get the currently logged-in invigilator"""
    invigilator_id = session.get("invigilator_id")
    if invigilator_id:
        return ExamInvigilator.query.get(invigilator_id)
    return None


def check_invigilator_session():
    """Check if invigilator session is valid and not expired"""
    if not session.get("invigilator_logged_in"):
        return False
    
    login_time_str = session.get("invigilator_login_time")
    if not login_time_str:
        return False
    
    try:
        login_time = datetime.fromisoformat(login_time_str)
        session_age = datetime.utcnow() - login_time
        
        if session.get("invigilator_remember_login"):
            session_timeout = incident_int_setting("invigilator_remember_duration", 1440)
        else:
            session_timeout = incident_int_setting("invigilator_session_timeout", 30)
        if session_age > timedelta(minutes=session_timeout):
            return False
        last_activity_str = session.get("invigilator_last_activity") or login_time_str
        last_activity = datetime.fromisoformat(last_activity_str)
        idle_timeout = incident_int_setting("invigilator_idle_timeout", 30)
        if datetime.utcnow() - last_activity > timedelta(minutes=idle_timeout):
            return False
        session["invigilator_last_activity"] = datetime.utcnow().isoformat()
    except (ValueError, TypeError):
        return False
    
    return True


def invigilator_login_required(f):
    """Decorator to require invigilator login"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if session is valid and not expired
        if not check_invigilator_session():
            logout_invigilator()
            # Store the intended URL for redirect after login
            session["invigilator_next"] = request.url
            flash("Your session has expired. Please sign in again to continue.", "warning")
            return redirect(url_for("invigilator.login"))
        
        # Check if invigilator account is still valid
        invigilator = current_invigilator()
        if not invigilator or not invigilator.is_valid():
            logout_invigilator()
            flash("Your invigilator account is no longer valid.", "danger")
            return redirect(url_for("invigilator.login"))
        
        return f(*args, **kwargs)
    return decorated_function


def invigilator_role_required(*allowed_roles):
    """Decorator to require specific invigilator roles"""
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            invigilator = current_invigilator()
            if not invigilator:
                flash("Please log in as an invigilator to continue.", "warning")
                return redirect(url_for("invigilator.login"))
            
            if invigilator.role not in allowed_roles:
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for("public.qr_landing"))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def record_login_history(invigilator, status, ip_address=None, failure_reason=None):
    """Record invigilator login attempt in history"""
    if not invigilator:
        return
    history = InvigilatorLoginHistory(
        invigilator_id=invigilator.id,
        login_time=datetime.utcnow(),
        ip_address=ip_address or request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None,
        login_status=status,
        failure_reason=failure_reason
    )
    db.session.add(history)
    db.session.commit()


@invigilator_bp.route("/login", methods=["GET", "POST"])
def login():
    """Invigilator login page"""
    if session.get("invigilator_logged_in"):
        # If already logged in, redirect to next page or dashboard
        next_url = session.pop("invigilator_next", None)
        if next_url:
            return redirect(next_url)
        return redirect(url_for("public.qr_landing"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username or not password:
            flash("Please provide both username and password.", "danger")
            return render_template("invigilator/login.html", settings=get_settings())
        
        invigilator = ExamInvigilator.query.filter_by(username=username).first()
        
        if not invigilator:
            flash("Invalid username or password.", "danger")
            record_login_history(
                invigilator=ExamInvigilator.query.filter_by(username=username).first() or ExamInvigilator.query.first(),
                status="Failed",
                failure_reason="Invalid username"
            )
            return render_template("invigilator/login.html", settings=get_settings())
        
        # Check if account is locked
        if invigilator.status == "Locked":
            flash("Your account has been locked. Please contact an administrator.", "danger")
            record_login_history(invigilator, "Locked", failure_reason="Account locked")
            return render_template("invigilator/login.html", settings=get_settings())
        
        # Check if account is inactive
        if invigilator.status == "Inactive" or not invigilator.is_active:
            flash("Your account is inactive. Please contact an administrator.", "danger")
            record_login_history(invigilator, "Failed", failure_reason="Account inactive")
            return render_template("invigilator/login.html", settings=get_settings())
        
        # Check if account is within validity period
        if not invigilator.is_valid():
            flash("Your account is not within the valid examination period.", "danger")
            record_login_history(invigilator, "Expired", failure_reason="Account expired")
            return render_template("invigilator/login.html", settings=get_settings())
        
        # Check password
        if invigilator.check_password(password):
            # Check if password change is forced
            if invigilator.force_password_change:
                login_invigilator(invigilator, remember=bool(request.form.get("remember_login")))
                session["invigilator_requires_password_change"] = True
                flash("You must change your password before continuing.", "warning")
                return redirect(url_for("invigilator.change_password"))
            
            # Successful login
            login_invigilator(invigilator, remember=bool(request.form.get("remember_login")))
            record_login_history(invigilator, "Success")
            flash(f"Welcome, {invigilator.full_name}!", "success")
            
            # Redirect to intended page or QR landing
            next_url = session.pop("invigilator_next", None)
            if next_url:
                return redirect(next_url)
            return redirect(url_for("public.qr_landing"))
        else:
            flash("Invalid username or password.", "danger")
            record_login_history(invigilator, "Failed", failure_reason="Invalid password")
            return render_template("invigilator/login.html", settings=get_settings())
    
    return render_template("invigilator/login.html", settings=get_settings())


@invigilator_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Verify invigilator identity before allowing a password reset."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        invigilator_code = request.form.get("invigilator_id", "").strip()
        mobile_number = request.form.get("mobile_number", "").strip()

        if incident_bool_setting("reset_require_username", True) and not username:
            flash("Username is required to verify your account.", "danger")
            return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())
        if incident_bool_setting("reset_require_invigilator_id", True) and not invigilator_code:
            flash("Invigilator ID is required to verify your account.", "danger")
            return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())

        query = ExamInvigilator.query
        if username:
            query = query.filter_by(username=username)
        if invigilator_code:
            query = query.filter_by(invigilator_id=invigilator_code)
        invigilator = query.first()
        if not invigilator:
            flash("We could not verify those invigilator details.", "danger")
            return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())

        if incident_bool_setting("reset_require_phone", False) and invigilator.mobile_number and not mobile_number:
            flash("Mobile number is required to verify this account.", "danger")
            return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())
        if invigilator.mobile_number and mobile_number and invigilator.mobile_number.strip() != mobile_number:
            flash("The mobile number does not match this invigilator account.", "danger")
            return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())

        session["invigilator_reset_id"] = invigilator.id
        flash("Identity verified. Please create a new password.", "success")
        return redirect(url_for("invigilator.reset_password"))

    return render_template("invigilator/forgot_password.html", settings=get_settings(), incident_settings=incident_settings_dict())


@invigilator_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Reset password after successful forgot-password identity verification."""
    invigilator_id = session.get("invigilator_reset_id")
    invigilator = db.session.get(ExamInvigilator, invigilator_id) if invigilator_id else None
    if not invigilator:
        flash("Please verify your invigilator identity first.", "warning")
        return redirect(url_for("invigilator.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not new_password or not confirm_password:
            flash("Please enter and confirm your new password.", "danger")
            return render_template("invigilator/reset_password.html", settings=get_settings())
        if new_password != confirm_password:
            flash("New passwords do not match.", "danger")
            return render_template("invigilator/reset_password.html", settings=get_settings())

        is_valid_password, password_error = validate_invigilator_password(new_password)
        if not is_valid_password:
            flash(password_error, "danger")
            return render_template("invigilator/reset_password.html", settings=get_settings())

        invigilator.set_password(new_password)
        invigilator.visible_password = new_password
        invigilator.force_password_change = False
        db.session.commit()
        session.pop("invigilator_reset_id", None)
        flash("Password reset successfully. You can now log in.", "success")
        return redirect(url_for("invigilator.login"))

    return render_template("invigilator/reset_password.html", settings=get_settings())


@invigilator_bp.route("/logout")
def logout():
    """Invigilator logout"""
    invigilator = current_invigilator()
    if invigilator:
        flash(f"Goodbye, {invigilator.full_name}!", "info")
    logout_invigilator()
    return redirect(url_for("invigilator.login"))


@invigilator_bp.route("/change-password", methods=["GET", "POST"])
@invigilator_login_required
def change_password():
    """Change invigilator password"""
    invigilator = current_invigilator()
    
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        if not current_password or not new_password or not confirm_password:
            flash("Please fill in all fields.", "danger")
            return render_template("invigilator/change_password.html", settings=get_settings())
        
        if not invigilator.check_password(current_password):
            flash("Current password is incorrect.", "danger")
            return render_template("invigilator/change_password.html", settings=get_settings())
        
        if new_password != confirm_password:
            flash("New passwords do not match.", "danger")
            return render_template("invigilator/change_password.html", settings=get_settings())
        
        is_valid_password, password_error = validate_invigilator_password(new_password)
        if not is_valid_password:
            flash(password_error, "danger")
            return render_template("invigilator/change_password.html", settings=get_settings())
        
        invigilator.set_password(new_password)
        invigilator.visible_password = new_password
        invigilator.force_password_change = False
        db.session.commit()
        
        session.pop("invigilator_requires_password_change", None)
        flash("Password changed successfully!", "success")
        
        # Redirect to intended page or QR landing
        next_url = session.pop("invigilator_next", None)
        if next_url:
            return redirect(next_url)
        flash("Password changed successfully. Please sign in again, then scan a student QR code to continue.", "success")
        logout_invigilator()
        return redirect(url_for("invigilator.login"))
    
    return render_template("invigilator/change_password.html", settings=get_settings())


@invigilator_bp.route("/profile")
@invigilator_login_required
def profile():
    """Compatibility route for older profile redirects."""
    flash("Please scan a student QR code to continue to an incident workflow.", "info")
    logout_invigilator()
    return redirect(url_for("invigilator.login"))
