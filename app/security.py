from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename


ALLOWED_PHOTOS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_SHEETS = {"xlsx"}
ALLOWED_AUDIO = {"mp3", "wav"}


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def allowed_file(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def safe_upload_name(filename):
    return secure_filename(filename or "").replace(" ", "_")


def flash_errors(errors):
    for error in errors:
        flash(error, "danger")
