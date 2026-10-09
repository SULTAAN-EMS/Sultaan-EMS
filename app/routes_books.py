import json
import re
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from . import csrf, db
from .books_storage import delete_asset, is_r2_asset, r2_bucket, r2_client, r2_enabled, r2_key, save_r2_small_upload
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
R2_PART_BYTES = 16 * 1024 * 1024
R2_UPLOAD_TOKEN_SALT = "books-r2-multipart-v1"
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
    page_pattern = re.compile(rb"/Type\s*/Page\b")
    pages_pattern = re.compile(rb"/Type\s*/Pages\b.{0,512}?/Count\s+(\d+)", re.S)
    pages = 0
    page_tree_counts = []
    overlap = 64 * 1024
    offset = 0
    carry = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            content = carry + chunk
            content_start = offset - len(carry)
            for match in page_pattern.finditer(content):
                if content_start + match.end() > offset:
                    pages += 1
            for match in pages_pattern.finditer(content):
                if content_start + match.end() > offset:
                    page_tree_counts.append(int(match.group(1)))
            offset += len(chunk)
            carry = content[-overlap:]
    if pages == 0:
        pages = max(page_tree_counts, default=0)
    return size, pages


def _remove_private(name):
    if not name:
        return
    try:
        delete_asset(name)
    except Exception:
        current_app.logger.exception("Unable to remove a private book asset")


def _upload_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=R2_UPLOAD_TOKEN_SALT)


def _load_upload_token(token):
    try:
        value = _upload_serializer().loads(token, max_age=4 * 60 * 60)
    except BadSignature as error:
        raise ValueError("Xogta upload-ku way dhacday. Fadlan mar kale isku day.") from error
    if str(value.get("user_id")) != str(current_user.get_id()):
        raise ValueError("Upload-kan user kale ayaa bilaabay.")
    return value


def _complete_r2_upload(payload):
    token = payload.get("token")
    parts = payload.get("parts")
    if not token or not isinstance(parts, list) or not parts:
        raise ValueError("Xogta qaybaha PDF-ga ma dhammaystirna.")
    upload = _load_upload_token(token)
    total_size = int(upload["size"])
    max_parts = (total_size + R2_PART_BYTES - 1) // R2_PART_BYTES
    normalized = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Qayb PDF ah lama aqoonsan.")
        number = int(part.get("PartNumber", 0))
        etag = str(part.get("ETag", "")).strip().strip('"')
        if number < 1 or number > max_parts or not etag:
            raise ValueError("Qayb PDF ah lama aqoonsan.")
        normalized.append({"PartNumber": number, "ETag": f'"{etag}"'})
    normalized.sort(key=lambda part: part["PartNumber"])
    if [part["PartNumber"] for part in normalized] != list(range(1, max_parts + 1)):
        raise ValueError("Qaar ka mid ah qaybaha PDF-ga ma soo gelin.")

    client = r2_client()
    bucket = r2_bucket()
    key = upload["key"]
    try:
        client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload["upload_id"],
            MultipartUpload={"Parts": normalized},
        )
    except Exception:
        try:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload["upload_id"])
        except Exception:
            current_app.logger.exception("Unable to abort failed R2 multipart upload")
        raise
    try:
        info = client.head_object(Bucket=bucket, Key=key)
        if int(info.get("ContentLength", -1)) != total_size or total_size > int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024)):
            raise ValueError("Cabbirka PDF-ga la soo geliyey ma waafaqsana.")
        head = client.get_object(Bucket=bucket, Key=key, Range="bytes=0-1023")["Body"].read(1024)
        tail_start = max(0, total_size - 4096)
        tail = client.get_object(Bucket=bucket, Key=key, Range=f"bytes={tail_start}-{total_size - 1}")["Body"].read(4096)
        if b"%PDF-" not in head or b"%%EOF" not in tail:
            raise ValueError("PDF-ga ma dhammaystirna ama si sax ah looma aqoonsan.")
    except Exception:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            current_app.logger.exception("Unable to remove invalid completed R2 upload")
        raise
    return f"r2:{key}", total_size, secure_filename(upload.get("filename", ""))[:255] or "buug.pdf", 0


def _r2_upload_field():
    raw = request.form.get("pdf_upload")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Xogta PDF upload-ku sax ma aha.") from error


def _cover_upload(upload):
    if r2_enabled():
        return save_r2_small_upload(
            upload,
            ALLOWED_COVERS,
            "cover",
            int(current_app.config.get("BOOKS_MAX_COVER_BYTES", 5 * 1024 * 1024)),
        )
    name, _ = _save_upload(upload, ALLOWED_COVERS, "cover")
    return name


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


@books_bp.post("/admin/books/uploads/init")
@_admin_required
def admin_upload_init():
    if not r2_enabled():
        return jsonify({"success": False, "message": "R2 weli lama hawlgelin server-ka."}), 503
    payload = request.get_json(silent=True) or {}
    filename = secure_filename(payload.get("filename", ""))
    try:
        size = int(payload.get("size", 0))
        maximum = int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024))
        if Path(filename).suffix.lower() != ".pdf":
            raise ValueError("Soo geli fayl PDF ah.")
        if size < 8 or size > maximum:
            raise ValueError("PDF-gu waa inuu ka weynaadaa 0, kana yaraan ama la mid noqdaa 1 GB.")
        key = f"books/pdfs/{uuid.uuid4().hex}.pdf"
        response = r2_client().create_multipart_upload(
            Bucket=r2_bucket(),
            Key=key,
            ContentType="application/pdf",
        )
        token = _upload_serializer().dumps({
            "key": key,
            "upload_id": response["UploadId"],
            "size": size,
            "filename": filename,
            "user_id": str(current_user.get_id()),
        })
        return jsonify({"success": True, "token": token, "partBytes": R2_PART_BYTES})
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception:
        error_id = uuid.uuid4().hex[:10]
        current_app.logger.exception("Unable to start R2 book upload (reference %s)", error_id)
        return jsonify({
            "success": False,
            "message": "R2 upload lama bilaabi karin. Hubi dejinta R2.",
            "reference": error_id,
        }), 503


@books_bp.post("/admin/books/uploads/part-url")
@_admin_required
def admin_upload_part_url():
    if not r2_enabled():
        abort(503)
    payload = request.get_json(silent=True) or {}
    try:
        upload = _load_upload_token(payload.get("token", ""))
        part_number = int(payload.get("partNumber", 0))
        total_parts = (int(upload["size"]) + R2_PART_BYTES - 1) // R2_PART_BYTES
        if not 1 <= part_number <= total_parts:
            raise ValueError("Lambarka qaybta upload-ku sax ma aha.")
        url = r2_client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": r2_bucket(),
                "Key": upload["key"],
                "UploadId": upload["upload_id"],
                "PartNumber": part_number,
            },
            ExpiresIn=3600,
        )
        return jsonify({"success": True, "url": url})
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400
    except Exception:
        current_app.logger.exception("Unable to sign R2 upload part")
        return jsonify({"success": False, "message": "URL-ka qaybta PDF-ga lama diyaarin."}), 503


@books_bp.post("/admin/books/uploads/abort")
@_admin_required
def admin_upload_abort():
    if not r2_enabled():
        return jsonify({"success": True})
    payload = request.get_json(silent=True) or {}
    try:
        upload = _load_upload_token(payload.get("token", ""))
        r2_client().abort_multipart_upload(Bucket=r2_bucket(), Key=upload["key"], UploadId=upload["upload_id"])
    except Exception:
        current_app.logger.info("R2 multipart upload abort was skipped")
    return jsonify({"success": True})


@books_bp.post("/admin/books/api")
@_admin_required
def admin_create():
    pdf_name = cover_name = None
    try:
        values = _validate_scope(request.form)
        upload_data = _r2_upload_field()
        pdf = request.files.get("pdf_file")
        if upload_data:
            pdf_name, size, original_filename, pages = _complete_r2_upload(upload_data)
        else:
            if r2_enabled():
                raise ValueError("PDF-ga R2 si toos ah looma soo gelin. Cusboonaysii bogga oo mar kale isku day.")
            if not pdf or not pdf.filename:
                raise ValueError("Soo geli faylka PDF-ka buugga.")
            pdf_name, pdf_path = _save_upload(pdf, {".pdf"}, "book")
            size, pages = _pdf_metadata(pdf_path)
            original_filename = secure_filename(pdf.filename)[:255] or "buug.pdf"
        max_size = int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024))
        if size > max_size:
            raise ValueError("PDF-gu wuu ka weyn yahay xadka 1 GB.")
        cover = request.files.get("cover_file")
        if cover and cover.filename:
            cover_name = _cover_upload(cover)
        item = BookLibraryItem(
            **values,
            pdf_storage_name=pdf_name,
            original_filename=original_filename,
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
        upload_data = _r2_upload_field()
        pdf = request.files.get("pdf_file")
        cover = request.files.get("cover_file")
        if upload_data:
            new_pdf, size, original_filename, pages = _complete_r2_upload(upload_data)
            item.pdf_storage_name = new_pdf
            item.original_filename = original_filename
            item.size_bytes = size
            item.page_count = pages
        elif pdf and pdf.filename:
            if r2_enabled():
                raise ValueError("PDF-ga R2 si toos ah looma soo gelin. Cusboonaysii bogga oo mar kale isku day.")
            new_pdf, pdf_path = _save_upload(pdf, {".pdf"}, "book")
            size, pages = _pdf_metadata(pdf_path)
            if size > int(current_app.config.get("BOOKS_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024)):
                raise ValueError("PDF-gu wuu ka weyn yahay xadka 1 GB.")
            item.pdf_storage_name = new_pdf
            item.original_filename = secure_filename(pdf.filename)[:255] or "buug.pdf"
            item.size_bytes = size
            item.page_count = pages
        if cover and cover.filename:
            new_cover = _cover_upload(cover)
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
    if is_r2_asset(item.cover_storage_name):
        url = r2_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": r2_bucket(), "Key": r2_key(item.cover_storage_name)},
            ExpiresIn=600,
        )
        response = redirect(url)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    path = _storage_dir() / Path(item.cover_storage_name).name
    if not path.is_file():
        abort(404)
    return send_file(path, conditional=True, max_age=0)


@books_bp.get("/books/<int:book_id>/read")
def read_pdf(book_id):
    item = _visible_book_or_404(book_id)
    if is_r2_asset(item.pdf_storage_name):
        filename = secure_filename(item.original_filename) or "buug.pdf"
        url = r2_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": r2_bucket(),
                "Key": r2_key(item.pdf_storage_name),
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'inline; filename="{filename}"',
            },
            ExpiresIn=3600,
        )
        response = redirect(url)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    path = _storage_dir() / Path(item.pdf_storage_name).name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=item.original_filename, conditional=True, max_age=0)


@books_bp.get("/books/<int:book_id>/download")
def download_pdf(book_id):
    item = _visible_book_or_404(book_id)
    if is_r2_asset(item.pdf_storage_name):
        filename = secure_filename(item.original_filename) or "buug.pdf"
        url = r2_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": r2_bucket(),
                "Key": r2_key(item.pdf_storage_name),
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=3600,
        )
        response = redirect(url)
        response.headers["Cache-Control"] = "private, no-store"
        return response
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
