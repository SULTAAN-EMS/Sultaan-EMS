import json
import re
import secrets
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.utils import secure_filename
from sqlalchemy.orm import selectinload

from . import csrf, db
from .books_storage import delete_asset, delete_assets, is_r2_asset, r2_bucket, r2_client, r2_enabled, r2_key, save_r2_small_upload
from .models import BookBranch, BookLibraryItem, BookPrintCenter, BookSubjectCatalog
from .security import role_required


books_bp = Blueprint("books", __name__)


def _flash_center_popup(action, kind, name):
    flash(json.dumps({"type": action, "kind": kind, "name": name}, ensure_ascii=False), "center-popup")

LEVELS = {
    "Xanaano": {"classes": ["KG1", "KG2"], "color": "#EC4899"},
    "Hoose": {"classes": [f"Fasal {n}" for n in range(1, 5)], "color": "#F59E0B"},
    "Dhexe": {"classes": [f"Fasal {n}" for n in range(5, 9)], "color": "#3B82F6"},
    "Sare": {"classes": [f"Form {n}" for n in range(1, 5)], "color": "#8B5CF6"},
}
SUBJECTS = [
    "Math", "Physics", "Chemistry", "Biology", "Juqraafi", "Taariikh",
    "Technology", "Luuqadda Carabiga", "Af Soomaali", "English",
    "English Films", "Tarbiyadda Islaamka", "Business",
]
BOOK_SUBJECTS_BY_LEVEL = {
    "Xanaano": SUBJECTS,
    "Hoose": ["Xisaab", "Af Soomaali", "Tarbiyada Islaamka", "Luuqadda Carabiga", "Saynis", "Cilmi Bulsho", "English"],
    "Dhexe": ["Xisaab", "Af Soomaali", "Tarbiyada Islaamka", "Luuqadda Carabiga", "Saynis", "Cilmi Bulsho", "English", "English Films", "Teknooloji"],
    "Sare": SUBJECTS,
}
ALL = "Dhammaan"
R2_PART_BYTES = 16 * 1024 * 1024
R2_UPLOAD_TOKEN_SALT = "books-r2-multipart-v1"
ALLOWED_COVERS = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


def _subjects_for_class(level, class_name):
    configured = BookSubjectCatalog.query.filter_by(level=level).all()
    if not configured:
        return list(BOOK_SUBJECTS_BY_LEVEL.get(level, SUBJECTS))
    return sorted({
        item.name for item in configured
        if item.is_active and (not item.class_names or class_name in item.class_names)
    }, key=str.casefold)


def _subject_catalog_payload():
    catalog = {}
    configured = {}
    all_items = BookSubjectCatalog.query.all()
    levels_with_configuration = {item.level for item in all_items}
    for item in all_items:
        if item.is_active:
            configured.setdefault(item.level, []).append(item)
    for level, details in LEVELS.items():
        catalog[level] = {}
        for class_name in details["classes"]:
            if level not in levels_with_configuration:
                subjects = BOOK_SUBJECTS_BY_LEVEL.get(level, SUBJECTS)
            else:
                subjects = [item.name for item in configured.get(level, [])
                            if not item.class_names or class_name in item.class_names]
            catalog[level][class_name] = sorted(set(subjects), key=str.casefold)
    return catalog


def _subject_manager_payload():
    return [
        {
            "id": item.id,
            "level": item.level,
            "name": item.name,
            "classNames": list(item.class_names or []),
            "appliesToAllClasses": not bool(item.class_names),
        }
        for item in BookSubjectCatalog.query.filter_by(is_active=True)
        .order_by(BookSubjectCatalog.level, BookSubjectCatalog.name)
        .all()
    ]


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
        "bookKind": book.book_kind,
        "catalogType": book.catalog_type,
        "branchIds": [branch.id for branch in book.branches],
        "branchNames": [branch.name for branch in book.branches],
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


def _validate_scope(form, *, existing_book=None):
    title = (form.get("title") or "").strip()
    level = (form.get("level") or "").strip()
    scope = (form.get("scope") or "").strip()
    class_name = (form.get("class") or "").strip()
    subject = (form.get("subject") or "").strip()
    description = (form.get("description") or "").strip()
    book_kind = (form.get("book_kind") or "textbook").strip()
    catalog_type = (form.get("catalog_type") or "national").strip()
    if book_kind not in {"textbook", "general"}:
        raise ValueError("Dooro nooca saxda ah ee buugga.")
    if catalog_type not in {"national", "local", "unassigned"} or (catalog_type == "unassigned" and not existing_book):
        raise ValueError("Dooro nooca saxda ah ee buugga.")
    branch_ids = []
    for raw_id in form.getlist("branch_ids"):
        try:
            branch_id = int(raw_id)
        except (TypeError, ValueError):
            raise ValueError("Xarun dugsi oo sax ah dooro.")
        if branch_id not in branch_ids:
            branch_ids.append(branch_id)
    branches = BookBranch.query.filter(BookBranch.id.in_(branch_ids), BookBranch.is_active.is_(True)).all() if branch_ids else []
    if len(branches) != len(branch_ids):
        raise ValueError("Mid ka mid ah xarumaha la doortay ma jiro ama wuu xiran yahay.")
    if catalog_type == "local" and not branches:
        raise ValueError("Buugga maxalliga ah ugu yaraan hal xarun ku xidh.")
    if catalog_type == "unassigned" and branches:
        raise ValueError("Buug sugaya kala-saarid xarun laguma xiri karo.")
    if not title or len(title) > 200:
        raise ValueError("Geli cinwaan buug oo aan ka badnayn 200 xaraf.")
    if level not in LEVELS:
        raise ValueError("Dooro heer dugsi oo sax ah.")
    if scope not in {"level", "class", "subject"}:
        raise ValueError("Dooro baaxadda buugga.")
    if book_kind == "general" and scope == "subject":
        raise ValueError("Buug guud ma laha maaddo gaar ah; dooro heer dhan ama fasal gaar ah.")
    if scope == "level":
        class_name, subject = ALL, ALL
    else:
        if class_name not in LEVELS[level]["classes"]:
            raise ValueError("Fasalka la doortay kama tirsana heerkan.")
        if scope == "class" or book_kind == "general":
            subject = ALL
        elif subject not in _subjects_for_class(level, class_name):
            keep_existing = bool(
                existing_book and existing_book.level == level
                and existing_book.class_name == class_name and existing_book.subject == subject
            )
            if not keep_existing:
                raise ValueError("Maaddadan kama diiwaangashana fasalka la doortay. Marka hore ka maamul Maaddooyinka Buugaagta.")
    if len(description) > 3000:
        raise ValueError("Sharaxaadda buuggu waa inay ka yar tahay 3,000 xaraf.")
    return {
        "title": title,
        "level": level,
        "class_name": class_name,
        "subject": subject,
        "scope": scope,
        "book_kind": book_kind,
        "catalog_type": catalog_type,
        "description": description,
        "is_visible": request.form.get("visible") == "true" and catalog_type != "unassigned",
    }, branches


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


def _branch_public_dict(branch):
    return {
        "id": branch.id,
        "name": branch.name,
        "region": (branch.region or "").strip(),
        "printCenters": [
            {"id": center.id, "name": center.name, "phone": center.phone, "whatsapp": center.whatsapp or "", "address": center.address, "mapUrl": center.map_url or ""}
            for center in branch.print_centers if center.is_active
        ],
    }


@books_bp.route("/admin/books/centers", methods=["GET", "POST"])
@_admin_required
def admin_centers():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create-branch":
                name = (request.form.get("name") or "").strip()
                if not name or len(name) > 140:
                    raise ValueError("Geli magaca xarunta dugsiga.")
                slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:130] or "branch"
                branch = BookBranch(
                    name=name,
                    slug=f"{slug_base}-{secrets.token_hex(3)}",
                    region=(request.form.get("region") or "").strip()[:120] or None,
                    address=(request.form.get("address") or "").strip()[:300] or None,
                    phone=(request.form.get("phone") or "").strip()[:40] or None,
                )
                db.session.add(branch)
                db.session.commit()
                _flash_center_popup("add", "c", branch.name)
            elif action == "create-center":
                name = (request.form.get("name") or "").strip()
                phone = re.sub(r"[\s()-]", "", request.form.get("phone") or "")
                address = (request.form.get("address") or "").strip()
                if not name or len(name) > 160 or not address or len(address) > 300:
                    raise ValueError("Geli magaca iyo cinwaanka madbacadda.")
                if len(re.sub(r"\D", "", phone)) < 7 or len(phone) > 40:
                    raise ValueError("Geli telefoon sax ah oo madbacadda lagala xidhiidhi karo.")
                map_url = (request.form.get("map_url") or "").strip()
                if map_url and not re.match(r"^https?://", map_url, re.I):
                    raise ValueError("Link-ga khariidaddu waa inuu ku bilaabmaa https:// ama http://.")
                ids = []
                for raw in request.form.getlist("branch_ids"):
                    try:
                        ids.append(int(raw))
                    except ValueError:
                        raise ValueError("Xarunta dugsiga ee la doortay sax ma aha.")
                branches = BookBranch.query.filter(BookBranch.id.in_(set(ids)), BookBranch.is_active.is_(True)).all() if ids else []
                if len(branches) != len(set(ids)) or not branches:
                    raise ValueError("Madbacadda ugu yaraan hal xarun oo dugsi ku xidh.")
                center = BookPrintCenter(
                    name=name,
                    phone=phone,
                    whatsapp=(request.form.get("whatsapp") or "").strip()[:40] or None,
                    address=address,
                    map_url=map_url[:500] or None,
                    branches=branches,
                )
                db.session.add(center)
                db.session.commit()
                _flash_center_popup("add", "p", center.name)
            elif action == "update-branch":
                branch = db.session.get(BookBranch, request.form.get("item_id", type=int))
                name = (request.form.get("name") or "").strip()
                if not branch or not name or len(name) > 140:
                    raise ValueError("Xarunta dugsiga ama magaceeda lama aqoonsan.")
                branch.name = name
                branch.region = (request.form.get("region") or "").strip()[:120] or None
                branch.address = (request.form.get("address") or "").strip()[:300] or None
                branch.phone = (request.form.get("phone") or "").strip()[:40] or None
                db.session.commit()
                _flash_center_popup("edit", "c", branch.name)
            elif action == "update-center":
                center = db.session.get(BookPrintCenter, request.form.get("item_id", type=int))
                name = (request.form.get("name") or "").strip()
                phone = re.sub(r"[\s()-]", "", request.form.get("phone") or "")
                address = (request.form.get("address") or "").strip()
                map_url = (request.form.get("map_url") or "").strip()
                ids = {int(value) for value in request.form.getlist("branch_ids") if value.isdigit()}
                branches = BookBranch.query.filter(BookBranch.id.in_(ids), BookBranch.is_active.is_(True)).all() if ids else []
                if not center or not name or len(name) > 160 or not address or len(address) > 300:
                    raise ValueError("Geli magaca iyo cinwaanka madbacadda.")
                if len(re.sub(r"\D", "", phone)) < 7 or len(phone) > 40:
                    raise ValueError("Geli telefoon sax ah oo madbacadda lagala xidhiidhi karo.")
                if map_url and not re.match(r"^https?://", map_url, re.I):
                    raise ValueError("Link-ga khariidaddu waa inuu ku bilaabmaa https:// ama http://.")
                if not ids or len(branches) != len(ids):
                    raise ValueError("Madbacadda ugu yaraan hal xarun oo firfircoon ku xidh.")
                center.name = name
                center.phone = phone
                center.whatsapp = (request.form.get("whatsapp") or "").strip()[:40] or None
                center.address = address
                center.map_url = map_url[:500] or None
                center.branches = branches
                db.session.commit()
                _flash_center_popup("edit", "p", center.name)
            elif action in {"toggle-branch", "toggle-center"}:
                model = BookBranch if action == "toggle-branch" else BookPrintCenter
                item = db.session.get(model, request.form.get("item_id", type=int))
                if not item:
                    raise ValueError("Xogta la doortay lama helin.")
                item.is_active = not item.is_active
                kind = "c" if action == "toggle-branch" else "p"
                name = item.name
                popup_type = "on" if item.is_active else "off"
                db.session.commit()
                _flash_center_popup(popup_type, kind, name)
            elif action in {"delete-branch", "delete-center"}:
                model = BookBranch if action == "delete-branch" else BookPrintCenter
                item = db.session.get(model, request.form.get("item_id", type=int))
                if not item:
                    raise ValueError("Xogta la doortay lama helin ama horay ayaa loo tirtiray.")
                kind = "c" if action == "delete-branch" else "p"
                name = item.name
                db.session.delete(item)
                db.session.commit()
                _flash_center_popup("del", kind, name)
            else:
                raise ValueError("Ficilka lama aqoonsan.")
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Books branch/print-center update failed")
            flash("Xogta lama kaydin. Hubi in magaca xaruntu aanu hore u jirin.", "error")
        return redirect(url_for("books.admin_centers"))

    branches = BookBranch.query.order_by(BookBranch.name).all()
    centers = BookPrintCenter.query.options(selectinload(BookPrintCenter.branches)).order_by(BookPrintCenter.name).all()
    local_book_count = BookLibraryItem.query.filter_by(catalog_type="local").count()
    return render_template(
        "admin/books_centers.html",
        branches=branches,
        centers=centers,
        local_book_count=local_book_count,
    )


def _visible_book_or_404(book_id):
    book = db.session.get(BookLibraryItem, book_id)
    if not book or not book.is_visible or book.catalog_type == "unassigned":
        abort(404)
    return book


@books_bp.get("/admin/books")
@_admin_required
def admin_page():
    branches = BookBranch.query.order_by(BookBranch.name).all()
    return render_template(
        "admin/books.html",
        books=[_book_dict(item) for item in BookLibraryItem.query.order_by(BookLibraryItem.updated_at.desc(), BookLibraryItem.id.desc()).all()],
        levels=LEVELS,
        subjects=SUBJECTS,
        branches=branches,
        branch_options=[{"id": branch.id, "name": branch.name} for branch in branches if branch.is_active],
        subject_catalog=_subject_catalog_payload(),
        csrf_value=__import__("flask_wtf.csrf", fromlist=["generate_csrf"]).generate_csrf(),
    )


@books_bp.get("/admin/books/subjects")
@_admin_required
def admin_subjects():
    from .services import get_settings

    return render_template(
        "admin/books_subjects.html",
        levels=LEVELS,
        subjects=_subject_manager_payload(),
        csrf_value=__import__("flask_wtf.csrf", fromlist=["generate_csrf"]).generate_csrf(),
        ui_settings=get_settings(),
    )


@books_bp.post("/admin/books/subjects/api")
@_admin_required
def admin_subject_save():
    payload = request.get_json(silent=True) or {}
    level = str(payload.get("level") or "").strip()
    name = str(payload.get("name") or "").strip()
    class_names = payload.get("classNames")
    applies_to_all = payload.get("appliesToAllClasses") is True
    if level not in LEVELS or not name or len(name) > 120:
        return jsonify({"success": False, "message": "Dooro heer sax ah oo geli magac maaddo oo 1 ilaa 120 xaraf ah."}), 400
    if applies_to_all:
        class_names = []
    elif not isinstance(class_names, list) or not class_names:
        return jsonify({"success": False, "message": "Dooro ugu yaraan hal fasal, ama calaamadee heerka oo dhan."}), 400
    else:
        class_names = list(dict.fromkeys(str(value) for value in class_names))
        if any(value not in LEVELS[level]["classes"] for value in class_names):
            return jsonify({"success": False, "message": "Fasal aan ka tirsanayn heerkan ayaa la doortay."}), 400
    item = BookSubjectCatalog.query.filter_by(level=level, name=name).first()
    if item is None:
        item = BookSubjectCatalog(level=level, name=name)
        db.session.add(item)
    item.class_names = class_names
    item.is_active = True
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to save book subject")
        return jsonify({"success": False, "message": "Maaddada buugga lama kaydin."}), 500
    return jsonify({"success": True, "subjects": _subject_manager_payload(), "subjectCatalog": _subject_catalog_payload()})


@books_bp.post("/admin/books/subjects/api/remove")
@_admin_required
def admin_subject_remove():
    payload = request.get_json(silent=True) or {}
    level = str(payload.get("level") or "").strip()
    name = str(payload.get("name") or "").strip()
    class_name = str(payload.get("className") or "").strip()
    if level not in LEVELS or not name:
        return jsonify({"success": False, "message": "Maaddada ama heerka sax ma aha."}), 400
    item = BookSubjectCatalog.query.filter_by(level=level, name=name, is_active=True).first()
    if not item:
        return jsonify({"success": False, "message": "Maaddadan lama helin."}), 404
    if class_name == ALL:
        item.is_active = False
    elif class_name not in LEVELS[level]["classes"]:
        return jsonify({"success": False, "message": "Fasalka la doortay kama tirsana heerkan."}), 400
    elif not item.class_names:
        item.class_names = [value for value in LEVELS[level]["classes"] if value != class_name]
        if not item.class_names:
            item.is_active = False
    elif class_name in item.class_names:
        item.class_names = [value for value in item.class_names if value != class_name]
        if not item.class_names:
            item.is_active = False
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Unable to remove book subject")
        return jsonify({"success": False, "message": "Maaddada lagama saari karin fasalka."}), 500
    return jsonify({"success": True, "subjects": _subject_manager_payload(), "subjectCatalog": _subject_catalog_payload()})


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
            "unassigned": sum(book.catalog_type == "unassigned" for book in books),
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
        values, branches = _validate_scope(request.form)
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
            branches=branches,
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
        values, branches = _validate_scope(request.form, existing_book=item)
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
        item.branches = branches
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
    if item.catalog_type == "unassigned":
        return jsonify({"success": False, "message": "Marka hore u kala saar buuggan: manhaj qaran ama xarun maxalli ah."}), 400
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
    try:
        delete_assets(assets)
    except Exception:
        current_app.logger.exception("Unable to remove deleted book assets")
    return jsonify({"success": True})


@books_bp.get("/books")
def public_page():
    books = BookLibraryItem.query.filter(
        BookLibraryItem.is_visible.is_(True), BookLibraryItem.catalog_type.in_(("national", "local"))
    ).options(selectinload(BookLibraryItem.branches)).order_by(BookLibraryItem.title).all()
    branches = BookBranch.query.filter_by(is_active=True).options(selectinload(BookBranch.print_centers)).order_by(BookBranch.name).all()
    from .services import get_settings
    ui_settings = get_settings()
    return render_template(
        "books/public.html",
        books=[_book_dict(item) for item in books],
        levels=LEVELS,
        subjects=SUBJECTS,
        subject_catalog=_subject_catalog_payload(),
        branches=[_branch_public_dict(branch) for branch in branches],
        ui_settings=ui_settings,
    )


@books_bp.get("/books/<int:book_id>/cover")
def cover(book_id):
    item = db.session.get(BookLibraryItem, book_id)
    if not item:
        abort(404)
    if not item.is_visible or item.catalog_type == "unassigned":
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
