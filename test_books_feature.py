import html
import io
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app import create_app, db
from app.models import BookBranch, BookLibraryItem, BookPrintCenter, User
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


def _upload(client, visibility="true", catalog_type="national", branch_ids=(), book_kind="textbook", level="Sare", scope="subject", class_name="Form 2", subject="Math"):
    payload = {
        "title": "Math Form 2 Algebra",
        "level": level,
        "scope": scope,
        "class": class_name,
        "subject": subject,
        "book_kind": book_kind,
        "description": "A searchable algebra book.",
        "visible": visibility,
        "catalog_type": catalog_type,
        "pdf_file": (io.BytesIO(_pdf_bytes()), "math-form-2.pdf"),
    }
    payload["branch_ids"] = [str(branch_id) for branch_id in branch_ids]
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

    def test_national_book_can_be_scoped_to_selected_branches(self):
        with self.app.app_context():
            branch = BookBranch(name="National Catalog Branch", slug="national-catalog-branch")
            db.session.add(branch)
            db.session.commit()
            branch_id = branch.id

        created = _upload(self.client, catalog_type="national", branch_ids=[branch_id])
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.assertEqual(created.json["book"]["catalogType"], "national")
        self.assertEqual(created.json["book"]["branchIds"], [branch_id])

    def test_general_nursery_book_has_no_subject(self):
        created = _upload(
            self.client, book_kind="general", level="Xanaano", scope="class",
            class_name="KG1", subject="Dhammaan",
        )
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        book = created.json["book"]
        self.assertEqual(book["bookKind"], "general")
        self.assertEqual(book["level"], "Xanaano")
        self.assertEqual(book["class"], "KG1")
        self.assertEqual(book["subject"], "Dhammaan")

    def test_books_admin_manages_subjects_by_level_and_class(self):
        page = self.client.get("/admin/books/subjects")
        self.assertEqual(page.status_code, 200)
        self.assertIn("MAADDOOYINKA BUUGAAGTA".encode(), page.data)

        saved = self.client.post("/admin/books/subjects/api", json={
            "level": "Hoose", "name": "Barashada Deegaanka",
            "classNames": ["Fasal 1"], "appliesToAllClasses": False,
        })
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertIn("Barashada Deegaanka", saved.json["subjectCatalog"]["Hoose"]["Fasal 1"])
        self.assertNotIn("Barashada Deegaanka", saved.json["subjectCatalog"]["Hoose"]["Fasal 2"])

        valid_book = _upload(
            self.client, level="Hoose", class_name="Fasal 1",
            scope="subject", subject="Barashada Deegaanka",
        )
        self.assertEqual(valid_book.status_code, 201, valid_book.get_data(as_text=True))
        invalid_class = _upload(
            self.client, level="Hoose", class_name="Fasal 2",
            scope="subject", subject="Barashada Deegaanka",
        )
        self.assertEqual(invalid_class.status_code, 400)

        saved_all = self.client.post("/admin/books/subjects/api", json={
            "level": "Hoose", "name": "Barashada Deegaanka",
            "classNames": [], "appliesToAllClasses": True,
        })
        self.assertEqual(saved_all.status_code, 200, saved_all.get_data(as_text=True))
        self.assertIn("Barashada Deegaanka", saved_all.json["subjectCatalog"]["Hoose"]["Fasal 2"])

    def test_scope_validation_and_admin_authentication(self):
        invalid = self.client.post("/admin/books/api", data={
            "title": "Wrong level class", "level": "Hoose", "scope": "class",
            "class": "Form 2", "subject": "Math", "visible": "true",
        })
        self.assertEqual(invalid.status_code, 400)

        with self.client.session_transaction() as session:
            session.clear()
        self.assertIn(self.client.get("/admin/books/api").status_code, {302, 401})

    def test_admin_can_delete_branch_and_print_center_without_deleting_books(self):
        with self.app.app_context():
            branch = BookBranch(name="Delete Test Branch", slug="delete-test-branch")
            center = BookPrintCenter(
                name="Delete Test Printer", phone="252611234567", address="Hodan", branches=[branch],
            )
            db.session.add_all([branch, center])
            db.session.commit()
            branch_id, center_id = branch.id, center.id

        uploaded = _upload(self.client, catalog_type="local", branch_ids=[branch_id])
        self.assertEqual(uploaded.status_code, 201, uploaded.get_data(as_text=True))
        book_id = uploaded.json["book"]["id"]
        centers_page = self.client.get("/admin/books/centers")
        self.assertIn(b'<dialog class="books-delete-dialog"', centers_page.data)
        self.assertIn(b"Dib-u-noqo", centers_page.data)
        self.assertIn(b"fa-arrow-left", centers_page.data)
        self.assertIn(b"Tirtir xarunta dugsiga?", centers_page.data)
        self.assertIn(b"Haa, tirtir madbacadda", centers_page.data)
        self.assertNotIn(b"onsubmit=\"return confirm(", centers_page.data)

        delete_branch = self.client.post("/admin/books/centers", data={
            "action": "delete-branch", "item_id": str(branch_id),
        })
        self.assertEqual(delete_branch.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(BookBranch, branch_id))
            self.assertIsNotNone(db.session.get(BookPrintCenter, center_id))
            book = db.session.get(BookLibraryItem, book_id)
            self.assertIsNotNone(book)
            self.assertEqual(book.branches, [])

        delete_center = self.client.post("/admin/books/centers", data={
            "action": "delete-center", "item_id": str(center_id),
        })
        self.assertEqual(delete_center.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(BookPrintCenter, center_id))
            self.assertIsNotNone(db.session.get(BookLibraryItem, book_id))
        admin_page = self.client.get("/admin/books/centers")
        self.assertNotIn(b"<h3>Delete Test Branch</h3>", admin_page.data)
        self.assertNotIn(b"<h3>Delete Test Printer</h3>", admin_page.data)
        self.assertNotIn("Maamul xarumaha, buugaagta maxalliga ah iyo xiriirka madbacadaha.".encode(), admin_page.data)

    def test_center_create_edit_toggle_and_delete_use_reference_popup(self):
        def popup(response):
            match = re.search(rb'data-center-notify="([^"]+)"', response.data)
            self.assertIsNotNone(match, response.get_data(as_text=True))
            return json.loads(html.unescape(match.group(1).decode()))

        created = self.client.post("/admin/books/centers", data={
            "action": "create-branch", "name": "Popup Test Branch",
        }, follow_redirects=True)
        self.assertEqual(popup(created), {"type": "add", "kind": "c", "name": "Popup Test Branch"})
        self.assertNotIn(b'class="toast center-popup"', created.data)

        with self.app.app_context():
            branch_id = BookBranch.query.filter_by(name="Popup Test Branch").one().id

        updated = self.client.post("/admin/books/centers", data={
            "action": "update-branch", "item_id": str(branch_id), "name": "Updated Popup Branch",
        }, follow_redirects=True)
        self.assertEqual(popup(updated), {"type": "edit", "kind": "c", "name": "Updated Popup Branch"})

        closed = self.client.post("/admin/books/centers", data={
            "action": "toggle-branch", "item_id": str(branch_id),
        }, follow_redirects=True)
        self.assertEqual(popup(closed), {"type": "off", "kind": "c", "name": "Updated Popup Branch"})

        reopened = self.client.post("/admin/books/centers", data={
            "action": "toggle-branch", "item_id": str(branch_id),
        }, follow_redirects=True)
        self.assertEqual(popup(reopened), {"type": "on", "kind": "c", "name": "Updated Popup Branch"})

        deleted = self.client.post("/admin/books/centers", data={
            "action": "delete-branch", "item_id": str(branch_id),
        }, follow_redirects=True)
        self.assertEqual(popup(deleted), {"type": "del", "kind": "c", "name": "Updated Popup Branch"})

        branch_page = self.client.post("/admin/books/centers", data={
            "action": "create-branch", "name": "Printer Popup Branch",
        }, follow_redirects=True)
        self.assertEqual(popup(branch_page), {"type": "add", "kind": "c", "name": "Printer Popup Branch"})
        with self.app.app_context():
            print_branch_id = BookBranch.query.filter_by(name="Printer Popup Branch").one().id

        printer_added = self.client.post("/admin/books/centers", data={
            "action": "create-center", "name": "Popup Test Printer", "phone": "252611234567",
            "address": "Hodan", "branch_ids": [str(print_branch_id)],
        }, follow_redirects=True)
        self.assertEqual(popup(printer_added), {"type": "add", "kind": "p", "name": "Popup Test Printer"})
        with self.app.app_context():
            printer_id = BookPrintCenter.query.filter_by(name="Popup Test Printer").one().id

        printer_edited = self.client.post("/admin/books/centers", data={
            "action": "update-center", "item_id": str(printer_id), "name": "Updated Popup Printer",
            "phone": "252611234567", "address": "Hodan", "branch_ids": [str(print_branch_id)],
        }, follow_redirects=True)
        self.assertEqual(popup(printer_edited), {"type": "edit", "kind": "p", "name": "Updated Popup Printer"})

        printer_closed = self.client.post("/admin/books/centers", data={
            "action": "toggle-center", "item_id": str(printer_id),
        }, follow_redirects=True)
        self.assertEqual(popup(printer_closed), {"type": "off", "kind": "p", "name": "Updated Popup Printer"})

        printer_opened = self.client.post("/admin/books/centers", data={
            "action": "toggle-center", "item_id": str(printer_id),
        }, follow_redirects=True)
        self.assertEqual(popup(printer_opened), {"type": "on", "kind": "p", "name": "Updated Popup Printer"})

        printer_deleted = self.client.post("/admin/books/centers", data={
            "action": "delete-center", "item_id": str(printer_id),
        }, follow_redirects=True)
        self.assertEqual(popup(printer_deleted), {"type": "del", "kind": "p", "name": "Updated Popup Printer"})

    @unittest.skip("The Books order and printer portal workflow has been removed.")
    def test_guest_print_order_quote_acceptance_and_printer_scope(self):
        with self.app.app_context():
            branch = BookBranch(name="Branch Test", slug="branch-test", region="Hodan", address="Main road")
            other_branch = BookBranch(name="Other Branch", slug="other-branch", region="Wadajir")
            printer = User(username="printer-test", full_name="Printer Test", role="staff")
            printer.set_password("temporary-printer-password")
            printer.set_permissions({"books_printer"})
            unrelated = User(username="printer-other", full_name="Other Printer", role="staff")
            unrelated.set_password("temporary-printer-password")
            center = BookPrintCenter(name="Nearby Print", phone="252611234567", whatsapp="252611234567", address="Hodan", branches=[branch])
            db.session.add_all([branch, other_branch, printer, unrelated, center])
            db.session.flush()
            center.users.append(printer)
            branch_id, other_branch_id = branch.id, other_branch.id
            printer_id, unrelated_id, center_id = printer.id, unrelated.id, center.id
            db.session.commit()

        created = _upload(self.client, catalog_type="local", branch_ids=[branch_id])
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        book = created.json["book"]
        self.assertEqual(book["catalogType"], "local")
        self.assertEqual(book["branchIds"], [branch_id])

        public = self.client.get("/books")
        self.assertIn("Buugaagta Maxalliga ah", public.get_data(as_text=True))
        self.assertIn("Nearby Print", public.get_data(as_text=True))

        rejected = self.client.post("/books/order", data={
            "book_id": book["id"], "branch_id": other_branch_id, "print_center_id": center_id,
            "requester_name": "Parent", "requester_phone": "252612345678", "quantity": "2",
            "fulfillment": "pickup",
        })
        self.assertEqual(rejected.status_code, 302)
        with self.app.app_context():
            self.assertEqual(BookOrder.query.count(), 0)

        response = self.client.post("/books/order", data={
            "book_id": book["id"], "branch_id": branch_id, "print_center_id": center_id,
            "requester_name": "Parent Example", "requester_phone": "+252 61 234 5678", "quantity": "2",
            "fulfillment": "pickup", "customer_note": "Please call before printing.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/books/orders/SB-", response.location)
        self.assertIn("key=", response.location)
        status_path = response.location
        status_page = self.client.get(status_path)
        self.assertEqual(status_page.status_code, 200)
        self.assertEqual(status_page.headers.get("Cache-Control"), "private, no-store, max-age=0")
        self.assertIn(b"Parent Example", status_page.data)
        self.assertIn("Kaydi xogta raadraaca".encode(), status_page.data)
        tracking_key = parse_qs(urlsplit(status_path).query)["key"][0]
        order_code = status_path.split("/books/orders/", 1)[1].split("?", 1)[0]
        lookup = self.client.post("/books/orders", data={
            "order_code": order_code.lower(), "tracking_secret": tracking_key.replace("-", ""),
        })
        self.assertEqual(lookup.status_code, 302)
        self.assertEqual(lookup.location, status_path)
        rejected_lookup = self.client.post("/books/orders", data={
            "order_code": order_code, "tracking_secret": "ABCDE-FGHJK-LMNPQ",
        })
        self.assertEqual(rejected_lookup.status_code, 200)
        self.assertIn("sax ma aha".encode(), rejected_lookup.data)
        self.assertEqual(self.client.get("/admin/books/centers").status_code, 200)
        self.assertEqual(self.client.get("/admin/books/orders").status_code, 200)
        center_update = self.client.post("/admin/books/centers", data={
            "action":"update-center", "item_id":str(center_id), "name":"Nearby Print Updated",
            "phone":"252611234567", "whatsapp":"252611234567", "address":"Updated Hodan",
            "branch_ids":[str(branch_id)],
        })
        self.assertEqual(center_update.status_code, 302)

        with self.app.app_context():
            order = BookOrder.query.one()
            self.assertEqual(order.status, "requested")
            self.assertEqual(len(order.token_hash), 64)
            self.assertEqual(len(order.lookup_secret_hash), 64)
            self.assertNotIn(tracking_key, order.lookup_secret_hash)
            order_id = order.id

        printer_client = self.app.test_client()
        printer_login = printer_client.post("/printer/login", data={
            "username":"printer-test", "password":"temporary-printer-password",
        })
        self.assertEqual(printer_login.status_code, 302)
        self.assertEqual(printer_login.location, "/printer/orders")
        self.assertIn(b"SB-", printer_client.get("/printer/orders").data)
        quote = printer_client.post(f"/printer/orders/{order_id}", data={"status":"quoted", "quoted_price":"12.50"})
        self.assertEqual(quote.status_code, 302)
        self.assertIn(b"12.50 USD", self.client.get(status_path).data)
        accepted = self.client.post(status_path, data={"action":"accept", "payment_preference":"mobile_money"})
        self.assertEqual(accepted.status_code, 302)
        self.assertIn(b"waa la aqbalay", self.client.get(status_path).data)
        self.client.post(status_path, data={"action":"cancel"})
        self.assertIn(b"Qiimaha waa la aqbalay", self.client.get(status_path).data)
        printing = printer_client.post(f"/printer/orders/{order_id}", data={"status":"printing"})
        self.assertEqual(printing.status_code, 302)
        payment = {"action":"payment", "order_id":str(order_id), "payment_method":"mobile_money", "payment_reference":"TEST-RECEIPT-1"}
        self.assertEqual(self.client.post("/admin/books/orders", data=payment).status_code, 302)
        self.client.post("/admin/books/orders", data=payment)

        unrelated_client = self.app.test_client()
        with unrelated_client.session_transaction() as session:
            session["_user_id"] = str(unrelated_id)
            session["_fresh"] = True
        self.assertEqual(unrelated_client.get("/printer/orders").status_code, 403)

        self.client.post("/admin/books/centers", data={
            "action":"remove-staff", "center_id":str(center_id), "staff_user_id":str(printer_id),
        })
        self.assertEqual(printer_client.get("/printer/orders").status_code, 403)
        self.client.post("/admin/books/centers", data={
            "action":"assign-staff", "center_id":str(center_id), "staff_user_id":str(printer_id),
        })
        self.assertEqual(printer_client.get("/printer/orders").status_code, 200)

        with self.app.app_context():
            order = db.session.get(BookOrder, order_id)
            self.assertEqual(order.status, "printing")
            self.assertEqual(order.payment_preference, "mobile_money")
            self.assertEqual(order.payment_status, "verified")
            self.assertEqual([event.new_value for event in order.events if event.event_type == "status"], ["quoted", "accepted", "printing"])
            self.assertEqual(sum(event.event_type == "payment" for event in order.events), 1)

    @unittest.skip("The Books printer-account portal has been removed.")
    def test_admin_can_create_manage_and_reset_printer_accounts(self):
        branch_response = self.client.post("/admin/books/centers", data={
            "action":"create-branch", "name":"Printer Account Branch",
        })
        self.assertEqual(branch_response.status_code, 302)
        with self.app.app_context():
            branch_id = BookBranch.query.filter_by(name="Printer Account Branch").one().id
        center_response = self.client.post("/admin/books/centers", data={
            "action":"create-center", "name":"Managed Print House", "phone":"252611234567",
            "address":"Hodan Main Road", "branch_ids":[str(branch_id)],
        })
        self.assertEqual(center_response.status_code, 302)
        with self.app.app_context():
            center_id = BookPrintCenter.query.filter_by(name="Managed Print House").one().id

        created = self.client.post("/admin/books/centers", data={
            "action":"create-printer-user", "center_id":str(center_id), "full_name":"Printer Operator",
            "username":"print.operator", "password":"PrinterSecure123", "password_confirm":"PrinterSecure123",
        })
        self.assertEqual(created.status_code, 302)
        self.assertIn(b"print.operator", self.client.get("/admin/books/centers").data)
        with self.app.app_context():
            user = User.query.filter_by(username="print.operator").one()
            user_id = user.id
            self.assertEqual(user.role, "staff")
            self.assertTrue(user.has_permission("books_printer"))
            self.assertNotEqual(user.password_hash, "PrinterSecure123")
            self.assertTrue(user.check_password("PrinterSecure123"))
            self.assertEqual([center.id for center in user.book_print_centers], [center_id])

        printer_client = self.app.test_client()
        self.assertEqual(printer_client.get("/printer/login").status_code, 200)
        wrong_password = printer_client.post("/printer/login", data={
            "username":"print.operator", "password":"wrong-password",
        })
        self.assertEqual(wrong_password.status_code, 200)
        self.assertIn("sax ma aha".encode(), wrong_password.data)
        self.assertEqual(printer_client.get("/printer/orders").status_code, 302)
        login = printer_client.post("/printer/login", data={
            "username":"print.operator", "password":"PrinterSecure123",
        })
        self.assertEqual(login.status_code, 302)
        self.assertEqual(printer_client.get("/printer/orders").status_code, 200)
        self.assertEqual(printer_client.get("/admin/books").status_code, 403)
        self.assertEqual(printer_client.post("/printer/logout").status_code, 302)
        self.assertEqual(printer_client.get("/printer/orders").status_code, 302)

        admin_login_client = self.app.test_client()
        redirected = admin_login_client.post("/admin/login", data={
            "username":"print.operator", "password":"PrinterSecure123",
        })
        self.assertEqual(redirected.status_code, 302)
        self.assertEqual(redirected.location, "/printer/login")
        with admin_login_client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

        reset = self.client.post("/admin/books/centers", data={
            "action":"reset-printer-password", "center_id":str(center_id),
            "staff_user_id":str(user_id), "password":"NewPrinterSafe456", "password_confirm":"NewPrinterSafe456",
        })
        self.assertEqual(reset.status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, user_id)
            self.assertTrue(user.check_password("NewPrinterSafe456"))

        disabled = self.client.post("/admin/books/centers", data={
            "action":"toggle-printer-user", "center_id":str(center_id), "staff_user_id":str(user_id),
        })
        self.assertEqual(disabled.status_code, 302)
        printer_client = self.app.test_client()
        denied = printer_client.post("/printer/login", data={
            "username":"print.operator", "password":"NewPrinterSafe456",
        })
        self.assertEqual(denied.status_code, 200)
        with self.app.app_context():
            self.assertFalse(db.session.get(User, user_id).is_active)

        enabled = self.client.post("/admin/books/centers", data={
            "action":"toggle-printer-user", "center_id":str(center_id), "staff_user_id":str(user_id),
        })
        self.assertEqual(enabled.status_code, 302)
        with self.app.app_context():
            self.assertTrue(db.session.get(User, user_id).is_active)

    @unittest.skip("The Books printer-account portal has been removed.")
    def test_unmanaged_staff_account_cannot_use_printer_login(self):
        with self.app.app_context():
            branch = BookBranch(name="Legacy Printer Branch", slug="legacy-printer-branch")
            user = User(username="legacy-printer", full_name="Legacy Printer", role="staff")
            user.set_password("LegacyPrinter123")
            center = BookPrintCenter(name="Legacy Print House", phone="252611234567", address="Hodan", branches=[branch])
            db.session.add_all([branch, center, user])
            db.session.flush()
            center.users.append(user)
            db.session.commit()
        client = self.app.test_client()
        response = client.post("/printer/login", data={
            "username":"legacy-printer", "password":"LegacyPrinter123",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("sax ma aha".encode(), response.data)
        with client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

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

            def delete_objects(self, Bucket, Delete):
                self.deleted_batches.append(Delete["Objects"])
                for item in Delete["Objects"]:
                    self.objects.pop(item["Key"], None)
                return {"Deleted": Delete["Objects"]}

            deleted_batches = []

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
            self.assertEqual(len(fake_r2.deleted_batches), 1)
            self.assertEqual(len(fake_r2.deleted_batches[0]), 2)
            self.assertEqual(fake_r2.objects, {})
