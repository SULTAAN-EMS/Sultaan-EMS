import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app import create_app, db
from app.models import BookLibraryItem, User
from config import Config


def _pdf_bytes():
    def stream(text):
        content = f"BT /F1 18 Tf 24 350 Td ({text}) Tj ET".encode()
        return f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 6 0 R >> >> /Contents 4 0 R >>",
        stream("Math Form 2 Algebra"),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 6 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream("Second searchable page"),
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _upload(client, visibility="true"):
    payload = {
        "title": "Math Form 2 Algebra",
        "level": "Sare",
        "scope": "subject",
        "class": "Form 2",
        "subject": "Math",
        "description": "A searchable algebra book.",
        "visible": visibility,
        "pdf_file": (io.BytesIO(_pdf_bytes()), "math-form-2.pdf"),
    }
    return client.post("/admin/books/api", data=payload, content_type="multipart/form-data")


class BooksFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sultaan-books-test-")
        root = Path(self.temp_dir.name)

        class TestConfig(Config):
            TESTING = True
            WTF_CSRF_ENABLED = False
            AUTO_INIT_DB = False
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{root / 'books.sqlite'}"
            SQLALCHEMY_ENGINE_OPTIONS = {}
            BOOKS_STORAGE_FOLDER = str(root / "private-books")
            BOOKS_STORAGE_BACKEND = "local"
            BOOKS_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

        self.app = create_app(TestConfig)
        with self.app.app_context():
            db.create_all()
            user = User(username="books-admin", full_name="Books Admin", role="admin")
            user.set_password("temporary-test-password")
            db.session.add(user)
            db.session.commit()
            user_id = user.id
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.temp_dir.cleanup()

    def test_public_books_stream_range_download_and_hide(self):
        created = _upload(self.client)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        book = created.json["book"]
        self.assertEqual(book["scope"], "subject")
        self.assertEqual(book["class"], "Form 2")
        self.assertEqual(book["subject"], "Math")
        self.assertEqual(book["pages"], 2)
        self.assertFalse(book["hasCover"])

        admin_page = self.client.get("/admin/books")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Buug cusub", admin_page.data)

        public = self.client.get("/books")
        self.assertEqual(public.status_code, 200)
        self.assertIn("Math Form 2 Algebra", public.get_data(as_text=True))

        read = self.client.get(book["pdfUrl"], headers={"Range": "bytes=0-7"})
        self.assertEqual(read.status_code, 206)
        self.assertTrue(read.data.startswith(b"%PDF-1.4"))
        self.assertEqual(read.headers.get("Accept-Ranges"), "bytes")
        read.close()
        self.assertTrue(self.client.post(f"/books/api/{book['id']}/read").json["success"])

        download = self.client.get(book["downloadUrl"])
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()
        self.assertTrue(self.client.post(f"/books/api/{book['id']}/download-complete").json["success"])
        self.assertEqual(self.client.get("/admin/books/api").json["stats"]["downloads"], 1)

        hidden = self.client.post(f"/admin/books/api/{book['id']}/visibility")
        self.assertFalse(hidden.json["visible"])
        self.assertNotIn("Math Form 2 Algebra", self.client.get("/books").get_data(as_text=True))
        self.assertEqual(self.client.get(book["pdfUrl"]).status_code, 404)
        self.assertEqual(self.client.get(book["downloadUrl"]).status_code, 404)

    def test_update_and_delete(self):
        created = _upload(self.client)
        book = created.json["book"]
        updated = self.client.put(f"/admin/books/api/{book['id']}", data={
            "title": "Math for Form 2", "level": "Sare", "scope": "class",
            "class": "Form 2", "subject": "Dhammaan", "description": "Updated",
            "visible": "false",
        }, content_type="multipart/form-data")
        self.assertEqual(updated.status_code, 200, updated.get_data(as_text=True))
        self.assertEqual(updated.json["book"]["title"], "Math for Form 2")
        self.assertEqual(updated.json["book"]["subject"], "Dhammaan")
        self.assertFalse(updated.json["book"]["visible"])
        deleted = self.client.delete(f"/admin/books/api/{book['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(book["pdfUrl"]).status_code, 404)

    def test_scope_validation_and_admin_authentication(self):
        invalid = self.client.post("/admin/books/api", data={
            "title": "Wrong level class", "level": "Hoose", "scope": "class",
            "class": "Form 2", "subject": "Math", "visible": "true",
        })
        self.assertEqual(invalid.status_code, 400)

        with self.client.session_transaction() as session:
            session.clear()
        self.assertIn(self.client.get("/admin/books/api").status_code, {302, 401})

    def test_books_pdf_upload_limit_is_one_gibibyte(self):
        self.assertEqual(self.app.config["BOOKS_MAX_UPLOAD_BYTES"], 1024 * 1024 * 1024)

    def test_r2_direct_multipart_upload_stays_private_and_uses_signed_reads(self):
        pdf = _pdf_bytes()

        class FakeR2:
            def __init__(self):
                self.objects = {}

            def create_multipart_upload(self, **kwargs):
                self.key = kwargs["Key"]
                return {"UploadId": "upload-test"}

            def generate_presigned_url(self, operation, Params, ExpiresIn):
                if operation == "upload_part":
                    return "https://r2.test/upload-part"
                return f"https://r2.test/{operation}/{Params['Key']}?expires={ExpiresIn}"

            def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
                self.objects[Key] = pdf

            def put_object(self, Bucket, Key, Body, ContentType):
                self.objects[Key] = Body

            def head_object(self, Bucket, Key):
                return {"ContentLength": len(self.objects[Key])}

            def get_object(self, Bucket, Key, Range):
                start, end = (int(value) for value in Range.removeprefix("bytes=").split("-"))
                return {"Body": io.BytesIO(self.objects[Key][start:end + 1])}

            def delete_object(self, Bucket, Key):
                self.objects.pop(Key, None)

        fake_r2 = FakeR2()
        self.app.config.update(
            BOOKS_STORAGE_BACKEND="r2",
            R2_BUCKET_NAME="sultaan-media-prod",
            R2_ACCOUNT_ID="test-account",
            R2_ACCESS_KEY_ID="test-access",
            R2_SECRET_ACCESS_KEY="test-secret",
        )
        with patch("app.routes_books.r2_client", return_value=fake_r2), patch("app.books_storage.r2_client", return_value=fake_r2):
            initiated = self.client.post("/admin/books/uploads/init", json={"filename":"math.pdf","size":len(pdf)})
            self.assertEqual(initiated.status_code, 200, initiated.get_data(as_text=True))
            token = initiated.json["token"]
            signed_part = self.client.post("/admin/books/uploads/part-url", json={"token":token,"partNumber":1})
            self.assertEqual(signed_part.json["url"], "https://r2.test/upload-part")

            created = self.client.post("/admin/books/api", data={
                "title":"R2 Math", "level":"Sare", "scope":"subject", "class":"Form 2",
                "subject":"Math", "description":"R2 test", "visible":"true",
                "pdf_upload":json.dumps({"token":token,"parts":[{"PartNumber":1,"ETag":"etag-test"}]}),
                "cover_file":(io.BytesIO(b"\x89PNG\r\n\x1a\ncover"),"cover.png"),
            })
            self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
            book = created.json["book"]
            self.assertTrue(book["filename"].endswith(".pdf"))
            self.assertTrue(book["visible"])
            with self.app.app_context():
                item = db.session.get(BookLibraryItem, book["id"])
                self.assertTrue(item.pdf_storage_name.startswith("r2:books/pdfs/"))
            read = self.client.get(book["pdfUrl"])
            self.assertEqual(read.status_code, 302)
            self.assertIn("https://r2.test/get_object/", read.location)
            cover = self.client.get(book["coverUrl"])
            self.assertEqual(cover.status_code, 302)
            self.assertIn("https://r2.test/get_object/", cover.location)
            deleted = self.client.delete(f"/admin/books/api/{book['id']}")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(fake_r2.objects, {})
