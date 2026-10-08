import re
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from . import csrf, db
from .models import BookLibraryItem
from .security import role_required


books_bp = Blueprint("books", __name__)

LEVELS = {
    "Hoose": {"classes": [f"Fasal {n}" for n in range(1, 5)], "color": "#F59E0B"},
    "Dhexe": {"classes": [f"Fasal {n}" for n in range(5, 9)], "color": "#3B82F6"},
    "Sare": {"classes": [f"Form {n}" for n in range(1, 5)], "color": "#8B5CF6"},
}
SUBJECTS = [
    "Math", "Physics", "Chemistry", "Biology", "Juqraafi", "Taariikh",
    "Technology", "Luuqadda Carabiga", "Af Soomaali", "English",
    "English Films", "Tarbiyadda Islaamka", "Business",
]
ALL = "Dhammaan"
ALLOWED_COVERS = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


def _storage_dir():
    configured = current_app.config.get("BOOKS_STORAGE_FOLDER")
    root = Path(configured) if configured else Path(current_app.instance_path) / "books_private"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _book_dict(book):
    return {
        "id": book.id,
        "title": book.title,
        "level": book.level,
        "class": book.class_name,
        "subject": book.subject,
        "scope": book.scope,
        "description": book.description or "",
        "coverUrl": url_for("books.cover", book_id=book.id),
        "hasCover": bool(book.cover_storage_name),
        "pdfUrl": url_for("books.read_pdf", book_id=book.id),
        "downloadUrl": url_for("books.download_pdf", book_id=book.id),
        "filename": book.original_filename,
        "pages": book.page_count,
        "sizeBytes": book.size_bytes,
        "sizeMB": round(book.size_bytes / 1048576, 1),
        "visible": bool(book.is_visible),
        "readCount": book.read_count,
        "downloadCount": book.download_count,
        "createdAt": book.created_at.isoformat() if book.created_at else None,
        "updatedAt": book.updated_at.isoformat() if book.updated_at else None,
    }


def _admin_required(view):
    return login_required(role_required("admin", "super_admin")(view))


def _validate_scope(form):
    title = (form.get("title") or "").strip()
    level = (form.get("level") or "").strip()
    scope = (form.get("scope") or "").strip()
    class_name = (form.get("class") or "").strip()
    subject = (form.get("subject") or "").strip()
    description = (form.get("description") or "").strip()
    if not title or len(title) > 200:
        raise ValueError("Geli cinwaan buug oo aan ka badnayn 200 xaraf.")
    if level not in LEVELS:
        raise ValueError("Dooro heer dugsi oo sax ah.")
    if scope not in {"level", "class", "subject"}:
        raise ValueError("Dooro baaxadda buugga.")
    if scope == "level":
        class_name, subject = ALL, ALL
    else:
        if class_name not in LEVELS[level]["classes"]:
            raise ValueError("Fasalka la doortay kama tirsana heerkan.")
        if scope == "class":
            subject = ALL
        elif subject not in SUBJECTS:
            raise ValueError("Dooro maaddo sax ah.")
    if len(description) > 3000:
        raise ValueError("Sharaxaadda buuggu waa inay ka yar tahay 3,000 xaraf.")
    return {
        "title": title,
        "level": level,
        "class_name": class_name,
        "subject": subject,
        "scope": scope,
        "description": description,
        "is_visible": request.form.get("visible") == "true",
    }


def _save_upload(upload, extension, prefix):
    suffix = Path(secure_filename(upload.filename or "")).suffix.lower()
    if suffix not in extension:
        raise ValueError("Nooca faylka la soo geliyey lama oggola.")
    head = upload.stream.read(16)
    upload.stream.seek(0)
    if isinstance(extension, dict):
        valid = any(head.startswith(magic) for magic in ALLOWED_COVERS[suffix])
        if suffix == ".webp":
            upload.stream.seek(8)
            valid = valid and upload.stream.read(4) == b"WEBP"
            upload.stream.seek(0)
    else:
        valid = head.startswith(b"%PDF-")
    if not valid:
        raise ValueError("Faylku ma waafaqsana nooca la sheegay.")
    name = f"{prefix}_{uuid.uuid4().hex}{suffix}"
    destination = _storage_dir() / name
    upload.save(destination)
    if prefix == "cover" and destination.stat().st_size > int(current_app.config.get("BOOKS_MAX_COVER_BYTES", 5 * 1024 * 1024)):
        destination.unlink(missing_ok=True)
        raise ValueError("Cover-ku wuu ka weyn yahay xadka 5 MB.")
    return name, destination


def _pdf_metadata(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(1024)
        stream.seek(max(0, size - 4096))
        tail = stream.read()
    if b"%PDF-" not in header or b"%%EOF" not in tail:
        raise ValueError("PDF-ga ma dhammaystirna ama si sax ah looma aqoonsan.")
    content = path.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page\b", content))
    if pages == 0:
        counts = [int(value) for value in re.findall(rb"/Type\s*/Pages\b.{0,512}?/Count\s+(\d+)", content, re.S)]
        pages = max(counts, default=0)
    return size, pages


def _remove_private(name):
    if not name:
        return
    try:
        (_storage_dir() / Path(name).name).unlink(missing_ok=True)
    except OSError:
        current_app.logger.exception("Unable to remove a private book asset")


def _visible_book_or_404(book_id):
    book = db.session.get(BookLibraryItem, book_id)
    if not book or not book.is_visible:
        abort(404)
    return book


@books_bp.get("/admin/books")
@_admin_required
def admin_page():
    return render_template(
        "admin/books.html",
        books=[_book_dict(item) for item in BookLibraryItem.query.order_by(BookLibraryItem.updated_at.desc(), BookLibraryItem.id.desc()).all()],
        levels=LEVELS,
        subjects=SUBJECTS,
        csrf_value=__import__("flask_wtf.csrf", fromlist=["generate_csrf"]).generate_csrf(),
    )


@books_bp.get("/admin/books/api")
@_admin_required
def admin_list():
    books = BookLibraryItem.query.order_by(BookLibraryItem.updated_at.desc(), BookLibraryItem.id.desc()).all()
    return jsonify({
        "books": [_book_dict(book) for book in books],
        "stats": {
            "total": len(books),
            "visible": sum(book.is_visible for book in books),
            "hidden": sum(not book.is_visible for book in books),
            "downloads": sum(book.download_count for book in books),
        },
    })


@books_bp.post("/admin/books/api")
@_admin_required
def admin_create():
    pdf_name = cover_name = None
    try:
        values = _validate_scope(request.form)
        pdf = request.files.get("pdf_file")
        if not pdf or not pdf.filename:
            raise ValueError("Soo geli faylka PDF-ka buugga.")
        pdf_name, pdf_path = _save_upload(pdf, {".pdf"}, "book")
        size, pages = _pdf_metadata(pdf_path)
        max_size = int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
        if size > max_size:
            raise ValueError("PDF-gu wuu ka weyn yahay xadka 50 MB.")
        cover = request.files.get("cover_file")
        if cover and cover.filename:
            cover_name, _ = _save_upload(cover, ALLOWED_COVERS, "cover")
        item = BookLibraryItem(
            **values,
            pdf_storage_name=pdf_name,
            original_filename=secure_filename(pdf.filename)[:255] or "buug.pdf",
            size_bytes=size,
            page_count=pages,
            cover_storage_name=cover_name,
        )
        db.session.add(item)
        db.session.commit()
        return jsonify({"success": True, "book": _book_dict(item)}), 201
    except ValueError as error:
        db.session.rollback()
        _remove_private(pdf_name)
        _remove_private(cover_name)
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception:
        db.session.rollback()
        _remove_private(pdf_name)
        _remove_private(cover_name)
        current_app.logger.exception("Book creation failed")
        return jsonify({"success": False, "message": "Buugga lama kaydin. Fadlan isku day mar kale."}), 500


@books_bp.put("/admin/books/api/<int:book_id>")
@_admin_required
def admin_update(book_id):
    item = db.session.get(BookLibraryItem, book_id)
    if not item:
        abort(404)
    old_pdf = item.pdf_storage_name
    old_cover = item.cover_storage_name
    new_pdf = new_cover = None
    try:
        values = _validate_scope(request.form)
        pdf = request.files.get("pdf_file")
        cover = request.files.get("cover_file")
        if pdf and pdf.filename:
            new_pdf, pdf_path = _save_upload(pdf, {".pdf"}, "book")
            size, pages = _pdf_metadata(pdf_path)
            if size > int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)):
                raise ValueError("PDF-gu wuu ka weyn yahay xadka 50 MB.")
            item.pdf_storage_name = new_pdf
            item.original_filename = secure_filename(pdf.filename)[:255] or "buug.pdf"
            item.size_bytes = size
            item.page_count = pages
        if cover and cover.filename:
            new_cover, _ = _save_upload(cover, ALLOWED_COVERS, "cover")
            item.cover_storage_name = new_cover
        elif request.form.get("remove_cover") == "true":
            item.cover_storage_name = None
        for key, value in values.items():
            setattr(item, key, value)
        db.session.commit()
        if new_pdf:
            _remove_private(old_pdf)
        if new_cover or request.form.get("remove_cover") == "true":
            _remove_private(old_cover)
        return jsonify({"success": True, "book": _book_dict(item)})
    except ValueError as error:
        db.session.rollback()
        _remove_private(new_pdf)
        _remove_private(new_cover)
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception:
        db.session.rollback()
        _remove_private(new_pdf)
        _remove_private(new_cover)
        current_app.logger.exception("Book update failed")
        return jsonify({"success": False, "message": "Isbeddelka buugga lama kaydin."}), 500


@books_bp.post("/admin/books/api/<int:book_id>/visibility")
@_admin_required
def admin_visibility(book_id):
    item = db.session.get(BookLibraryItem, book_id)
    if not item:
        abort(404)
    item.is_visible = not item.is_visible
    db.session.commit()
    return jsonify({"success": True, "visible": item.is_visible})


@books_bp.delete("/admin/books/api/<int:book_id>")
@_admin_required
def admin_delete(book_id):
    item = db.session.get(BookLibraryItem, book_id)
    if not item:
        abort(404)
    assets = (item.pdf_storage_name, item.cover_storage_name)
    db.session.delete(item)
    db.session.commit()
    for asset in assets:
        _remove_private(asset)
    return jsonify({"success": True})


@books_bp.get("/books")
def public_page():
    books = BookLibraryItem.query.filter_by(is_visible=True).order_by(BookLibraryItem.title).all()
    return render_template("books/public.html", books=[_book_dict(item) for item in books], levels=LEVELS, subjects=SUBJECTS)


@books_bp.get("/books/<int:book_id>/cover")
def cover(book_id):
    item = db.session.get(BookLibraryItem, book_id)
    if not item:
        abort(404)
    if not item.is_visible:
        if not current_user.is_authenticated or current_user.role not in {"admin", "super_admin"}:
            abort(404)
    if not item.cover_storage_name:
        abort(404)
    path = _storage_dir() / Path(item.cover_storage_name).name
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True, max_age=0)


@books_bp.get("/books/<int:book_id>/read")
def read_pdf(book_id):
    item = _visible_book_or_404(book_id)
    path = _storage_dir() / Path(item.pdf_storage_name).name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=item.original_filename, conditional=True, max_age=0)


@books_bp.get("/books/<int:book_id>/download")
def download_pdf(book_id):
    item = _visible_book_or_404(book_id)
    path = _storage_dir() / Path(item.pdf_storage_name).name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=item.original_filename, conditional=True, max_age=0)


@books_bp.post("/books/api/<int:book_id>/read")
@csrf.exempt
def count_read(book_id):
    item = _visible_book_or_404(book_id)
    item.read_count = BookLibraryItem.read_count + 1
    db.session.commit()
    return jsonify({"success": True})


@books_bp.post("/books/api/<int:book_id>/download-complete")
@csrf.exempt
def count_download(book_id):
    item = _visible_book_or_404(book_id)
    item.download_count = BookLibraryItem.download_count + 1
    db.session.commit()
    return jsonify({"success": True})
