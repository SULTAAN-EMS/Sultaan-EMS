from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app


R2_PREFIX = "r2:"
_windows_trust_installed = False


def r2_enabled():
    return str(current_app.config.get("BOOKS_STORAGE_BACKEND", "local")).lower() == "r2"


def is_r2_asset(name):
    return bool(name and name.startswith(R2_PREFIX))


def r2_key(name):
    if not is_r2_asset(name):
        raise ValueError("Not an R2 asset")
    return name[len(R2_PREFIX):]


def r2_client():
    global _windows_trust_installed
    account_id = current_app.config.get("R2_ACCOUNT_ID")
    access_key = current_app.config.get("R2_ACCESS_KEY_ID")
    secret_key = current_app.config.get("R2_SECRET_ACCESS_KEY")
    if not all((account_id, access_key, secret_key, current_app.config.get("R2_BUCKET_NAME"))):
        raise RuntimeError("R2 storage is enabled but its credentials or bucket configuration are incomplete")
    if os.name == "nt" and not _windows_trust_installed:
        import truststore

        truststore.inject_into_ssl()
        _windows_trust_installed = True
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=current_app.config.get("R2_ENDPOINT_URL") or f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def r2_bucket():
    return current_app.config["R2_BUCKET_NAME"]


def save_r2_small_upload(upload, extension, prefix, max_bytes):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in extension:
        raise ValueError("Nooca faylka la soo geliyey lama oggola.")
    body = upload.stream.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError("Cover-ku wuu ka weyn yahay xadka 5 MB.")
    signatures = {
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".webp": (b"RIFF",),
    }
    if not any(body.startswith(signature) for signature in signatures[suffix]):
        raise ValueError("Faylku ma waafaqsana nooca la sheegay.")
    if suffix == ".webp" and body[8:12] != b"WEBP":
        raise ValueError("Faylku ma waafaqsana nooca la sheegay.")
    name = f"{prefix}_{uuid.uuid4().hex}{suffix}"
    key = f"books/covers/{name}"
    client = r2_client()
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    client.put_object(Bucket=r2_bucket(), Key=key, Body=body, ContentType=mime_types[suffix])
    return f"{R2_PREFIX}{key}"


def delete_asset(name):
    if not name:
        return
    if is_r2_asset(name):
        r2_client().delete_object(Bucket=r2_bucket(), Key=r2_key(name))
        return
    from .routes_books import _storage_dir

    (_storage_dir() / Path(name).name).unlink(missing_ok=True)
