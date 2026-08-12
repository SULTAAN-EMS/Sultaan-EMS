from datetime import datetime, timedelta

from flask import Flask, flash, redirect, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

from config import Config

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    @app.teardown_appcontext
    def cleanup_database_session(exception=None):
        try:
            if exception is not None:
                db.session.rollback()
        except Exception:
            pass
        finally:
            db.session.remove()

    from .models import User, Setting
    from .permissions import can
    from .services import format_academic_number, seed_grade_scales, seed_missing_settings

    @app.template_filter("academic_number")
    def academic_number_filter(value, settings=None):
        return format_academic_number(value, settings=settings)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.errorhandler(Exception)
    def handle_unhandled_exception(error):
        from flask import jsonify, request
        app.logger.error("UNHANDLED SERVER EXCEPTION [%s %s]: %s", request.method, request.path, str(error), exc_info=True)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
           "application/json" in request.headers.get("Accept", "") or \
           request.is_json:
            return jsonify({
                "success": False,
                "error": "Internal Server Error",
                "details": str(error)
            }), 500
        return f"<h1>Internal Server Error</h1><p>{str(error)}</p>", 500

    @app.after_request
    def secure_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.before_request
    def enforce_admin_session_timeout():
        """Enforce the owner-configured idle timeout for authenticated admin sessions."""
        if request.endpoint in {"static", "auth.login", "auth.logout"} or not current_user.is_authenticated:
            return None
        timeout_setting = db.session.get(Setting, "admin_session_timeout_minutes")
        try:
            timeout_minutes = max(5, min(1440, int(timeout_setting.value if timeout_setting else 60)))
        except (TypeError, ValueError):
            timeout_minutes = 60
        now = datetime.utcnow()
        previous = session.get("admin_last_activity")
        try:
            last_activity = datetime.fromisoformat(previous) if previous else now
        except (TypeError, ValueError):
            last_activity = now
        if now - last_activity > timedelta(minutes=timeout_minutes):
            username = current_user.username
            logout_user()
            session.clear()
            flash("Your security session expired. Please sign in again.", "warning")
            app.logger.info("Admin session expired for %s", username)
            return redirect(url_for("auth.login", next=request.url))
        session["admin_last_activity"] = now.isoformat()
        return None

    with app.app_context():
        try:
            from .schema_compat import ensure_schema_compatibility
            ensure_schema_compatibility()
        except Exception as ex:
            app.logger.error("Automatic schema compatibility sync failed on startup: %s", str(ex), exc_info=True)

    @app.context_processor
    def inject_ui_settings():
        from .i18n import active_translations, current_language, translate
        from .services import get_settings

        settings = get_settings()
        lang = current_language(settings.get("default_language", "en"))

        def asset_url(path):
            if not path:
                return ""
            value = str(path)
            if value.startswith(("http://", "https://", "data:")):
                return value
            return url_for("static", filename=value)

        return {
            "ui_settings": settings,
            "settings": settings,
            "_": translate,
            "active_translations": active_translations(),
            "current_language": lang,
            "text_direction": "rtl" if lang == "ar" else "ltr",
            "can": can,
            "asset_url": asset_url,
        }

    from .routes_admin import admin_bp
    from .routes_advanced_results import advanced_results_bp
    from .routes_attendance import attendance_bp
    from .routes_auth import auth_bp
    from .routes_id_cards import id_cards_bp
    from .routes_public import public_bp
    from .routes_teacher_portal import teacher_portal_bp
    from .routes_teachers import teachers_bp
    from .routes_academic_structure import academic_bp
    from .routes_invigilator import invigilator_bp
    from .routes_seat_arrangement import seat_arrangement_bp
    from .routes_seat_mixer import seat_mixer_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(attendance_bp, url_prefix="/admin/attendance")
    app.register_blueprint(id_cards_bp, url_prefix="/admin/id-cards")
    app.register_blueprint(advanced_results_bp, url_prefix="/admin/advanced-results")
    app.register_blueprint(teachers_bp, url_prefix="/admin/teachers")
    app.register_blueprint(teacher_portal_bp, url_prefix="/teacher")
    app.register_blueprint(academic_bp, url_prefix="/admin/academic-structure")
    app.register_blueprint(invigilator_bp, url_prefix="/invigilator")
    app.register_blueprint(seat_arrangement_bp, url_prefix="/admin/seat-arrangement")
    app.register_blueprint(seat_mixer_bp, url_prefix="/admin/seat-mixer")

    register_cli(app)

    # =========================
    # FIX: AUTO CREATE EVERYTHING
    # =========================
    with app.app_context():
        db.create_all()
        from .schema_compat import ensure_schema_compatibility

        ensure_schema_compatibility()

        # seed missing settings only; never overwrite production values
        if seed_missing_settings:
            seed_missing_settings()
            seed_grade_scales()
            from .teacher_services import seed_teacher_settings

            seed_teacher_settings()
            db.session.commit()

        # 🔥 AUTO ADMIN FIX (IMPORTANT)
        from .models import User

        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(
                username="admin",
                full_name="System Administrator",
                role="super_admin",
                is_active=True
            )
            admin.set_password("Admin@12345")
            db.session.add(admin)
            db.session.commit()

        # 🔥 AUTO INCIDENT DATA SEEDING
        from .models import IncidentCategory, SeverityLevel, IncidentAction

        if not SeverityLevel.query.first():
            severities = [
                {"name": "Minor", "color": "#22c55e", "description": "Minor incident with minimal impact", "sort_order": 1},
                {"name": "Moderate", "color": "#eab308", "description": "Moderate incident requiring attention", "sort_order": 2},
                {"name": "Serious", "color": "#f97316", "description": "Serious incident with significant impact", "sort_order": 3},
                {"name": "Critical", "color": "#ef4444", "description": "Critical incident requiring immediate action", "sort_order": 4},
            ]
            for s in severities:
                db.session.add(SeverityLevel(**s))

        if not IncidentCategory.query.first():
            categories = [
                {"name": "Talking During Exam", "description": "Student was talking during examination", "sort_order": 1},
                {"name": "Cheating Attempt", "description": "Attempted to cheat during exam", "sort_order": 2},
                {"name": "Using Mobile Phone", "description": "Used mobile phone during examination", "sort_order": 3},
                {"name": "Possession of Unauthorized Materials", "description": "Had unauthorized materials during exam", "sort_order": 4},
                {"name": "Disturbing Other Students", "description": "Disturbed other students during exam", "sort_order": 5},
                {"name": "Disrespecting Invigilator", "description": "Showed disrespect to exam invigilator", "sort_order": 6},
                {"name": "Late Arrival", "description": "Arrived late to examination", "sort_order": 7},
                {"name": "Seat Switching", "description": "Switched seats without permission", "sort_order": 8},
                {"name": "Impersonation", "description": "Attempted to impersonate another student", "sort_order": 9},
                {"name": "Other", "description": "Other type of incident", "sort_order": 10},
            ]
            for c in categories:
                db.session.add(IncidentCategory(**c))

        if not IncidentAction.query.first():
            actions = [
                {"name": "Warning Given", "description": "Verbal or written warning issued", "sort_order": 1},
                {"name": "Seat Changed", "description": "Student moved to different seat", "sort_order": 2},
                {"name": "Answer Sheet Collected", "description": "Answer sheet was collected", "sort_order": 3},
                {"name": "Mobile Phone Confiscated", "description": "Mobile phone was confiscated", "sort_order": 4},
                {"name": "Student Removed From Exam", "description": "Student was removed from examination", "sort_order": 5},
                {"name": "Sent to Administration", "description": "Student sent to administration office", "sort_order": 6},
                {"name": "Exam Cancelled", "description": "Examination was cancelled", "sort_order": 7},
                {"name": "Other", "description": "Other action taken", "sort_order": 8},
            ]
            for a in actions:
                db.session.add(IncidentAction(**a))

        # 🔥 AUTO INCIDENT REPORT SETTINGS SEEDING
        from .models import IncidentReportSettings

        default_settings = [
            # Field visibility settings
            {"setting_key": "show_student_photo", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show student photo in incident form"},
            {"setting_key": "show_student_info", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show student information section"},
            {"setting_key": "show_exam_room", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show exam room field"},
            {"setting_key": "show_exam", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show exam selection"},
            {"setting_key": "show_subject", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show subject selection"},
            {"setting_key": "show_teacher_info", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show teacher information fields"},
            {"setting_key": "show_evidence_upload", "setting_value": "true", "setting_type": "boolean", "category": "fields", "description": "Show evidence upload section"},
            
            # Field requirement settings
            {"setting_key": "require_exam_room", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require exam room field"},
            {"setting_key": "require_exam", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require exam selection"},
            {"setting_key": "require_subject", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require subject selection"},
            {"setting_key": "require_teacher_name", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require teacher name"},
            {"setting_key": "require_teacher_id", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require teacher ID"},
            {"setting_key": "require_evidence", "setting_value": "false", "setting_type": "boolean", "category": "requirements", "description": "Require evidence upload"},
            
            # Custom labels
            {"setting_key": "label_exam_room", "setting_value": "Exam Room", "setting_type": "string", "category": "labels", "description": "Label for exam room field"},
            {"setting_key": "label_teacher_name", "setting_value": "Invigilator Name", "setting_type": "string", "category": "labels", "description": "Label for teacher name field"},
            {"setting_key": "label_teacher_id", "setting_value": "Invigilator ID", "setting_type": "string", "category": "labels", "description": "Label for teacher ID field"},
            {"setting_key": "label_description", "setting_value": "Incident Description", "setting_type": "string", "category": "labels", "description": "Label for description field"},
            {"setting_key": "label_actions_taken", "setting_value": "Actions Taken", "setting_type": "string", "category": "labels", "description": "Label for actions taken field"},
            
            # Styling settings
            {"setting_key": "template", "setting_value": "premium", "setting_type": "string", "category": "styling", "description": "Form template (classic, premium, modern, government, university)"},
            {"setting_key": "primary_color", "setting_value": "#3b82f6", "setting_type": "string", "category": "styling", "description": "Primary color"},
            {"setting_key": "secondary_color", "setting_value": "#1e40af", "setting_type": "string", "category": "styling", "description": "Secondary color"},
            {"setting_key": "background_color", "setting_value": "#f8fafc", "setting_type": "string", "category": "styling", "description": "Background color"},
            {"setting_key": "card_background", "setting_value": "#ffffff", "setting_type": "string", "category": "styling", "description": "Card background color"},
            {"setting_key": "font_family", "setting_value": "Segoe UI", "setting_type": "string", "category": "styling", "description": "Font family"},
            {"setting_key": "font_size", "setting_value": "16", "setting_type": "integer", "category": "styling", "description": "Base font size in pixels"},
            
            # Header settings
            {"setting_key": "show_header", "setting_value": "true", "setting_type": "boolean", "category": "header", "description": "Show form header"},
            {"setting_key": "header_title", "setting_value": "Incident Report", "setting_type": "string", "category": "header", "description": "Header title"},
            {"setting_key": "header_subtitle", "setting_value": "Submit examination incident report", "setting_type": "string", "category": "header", "description": "Header subtitle"},
            
            # Footer settings
            {"setting_key": "show_footer", "setting_value": "true", "setting_type": "boolean", "category": "footer", "description": "Show form footer"},
            {"setting_key": "footer_text", "setting_value": "© 2024 Examination System", "setting_type": "string", "category": "footer", "description": "Footer text"},
            {"setting_key": "footer_copyright_text", "setting_value": "© SULTAAN EMS", "setting_type": "string", "category": "footer", "description": "Copyright prefix"},
            {"setting_key": "footer_org_name", "setting_value": "Sultaan Administration", "setting_type": "string", "category": "footer", "description": "Organization name"},
            {"setting_key": "footer_year", "setting_value": "2026", "setting_type": "string", "category": "footer", "description": "Copyright year"},
            {"setting_key": "footer_bg_start", "setting_value": "#071931", "setting_type": "string", "category": "footer", "description": "Footer background start color"},
            {"setting_key": "footer_bg_end", "setting_value": "#0c3366", "setting_type": "string", "category": "footer", "description": "Footer background end color"},
            {"setting_key": "footer_gold", "setting_value": "#C9A227", "setting_type": "string", "category": "footer", "description": "Footer gold accent color"},
            {"setting_key": "footer_teal", "setting_value": "#1B998B", "setting_type": "string", "category": "footer", "description": "Footer teal accent color"},
            {"setting_key": "footer_logo_path", "setting_value": "", "setting_type": "string", "category": "footer", "description": "Footer logo path"},
            {"setting_key": "footer_shimmer_enabled", "setting_value": "true", "setting_type": "boolean", "category": "footer", "description": "Enable text shimmer animation"},
        ]
        
        for setting in default_settings:
            if not IncidentReportSettings.query.filter_by(setting_key=setting["setting_key"]).first():
                db.session.add(IncidentReportSettings(
                    setting_key=setting["setting_key"],
                    setting_value=setting["setting_value"],
                    setting_type=setting["setting_type"],
                    category=setting["category"],
                    description=setting["description"]
                ))

        db.session.commit()

    return app


def register_cli(app):
    import click

    from .models import AcademicYear, GradeScale, Setting, User
    from .services import seed_grade_scales, seed_missing_settings

    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        from .schema_compat import ensure_schema_compatibility

        ensure_schema_compatibility()

        seed_missing_settings()

        seed_grade_scales()
        from .teacher_services import seed_teacher_settings

        seed_teacher_settings()

        if not AcademicYear.query.first():
            db.session.add(AcademicYear(name="2024-2025", is_current=True))

        if not User.query.filter_by(username="admin").first():
            admin = User(
                username="admin",
                full_name="System Administrator",
                role="super_admin",
                is_active=True
            )
            admin.set_password("Admin@12345")
            db.session.add(admin)

        db.session.commit()
        click.echo("Database initialized. Default login: admin / Admin@12345")
