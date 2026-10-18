"""Shared HTML-to-PDF rendering for printable reports."""

import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlparse

from flask import abort, current_app


def html_report_to_pdf(html_text, base_url):
    """Render HTML as PDF, preferring Chromium for print CSS fidelity."""
    chrome_candidates = [
        os.environ.get("CHROME_BIN"),
        os.environ.get("CHROMIUM_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    chrome = next(
        (candidate for candidate in chrome_candidates if candidate and Path(candidate).exists()),
        None,
    )
    if chrome:
        with TemporaryDirectory(prefix="sultaan-report-pdf-") as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            pdf_path = Path(temp_dir) / "report.pdf"
            html_path.write_text(
                html_text.replace("<head>", f'<head><base href="{base_url}">', 1),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}",
                    html_path.as_uri(),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
            return pdf_path.read_bytes()

    from xhtml2pdf import pisa

    pdf_buffer = BytesIO()
    def resolve_asset(uri, _relative_to):
        if uri.startswith("data:"):
            return uri
        parsed = urlparse(uri)
        if parsed.scheme in ("http", "https"):
            return uri
        path = unquote(parsed.path).lstrip("/")
        if path.startswith("static/"):
            path = path[len("static/"):]
        else:
            return uri
        static_root = Path(current_app.static_folder).resolve()
        asset_path = (static_root / path).resolve()
        if static_root in asset_path.parents and asset_path.is_file():
            return str(asset_path)
        return uri

    result = pisa.CreatePDF(
        html_text,
        dest=pdf_buffer,
        encoding="UTF-8",
        capacity=100 * 1024 * 1024,
        link_callback=resolve_asset,
    )
    if result.err:
        abort(500, description="PDF-ga warbixinta lama abuuri karin.")
    return pdf_buffer.getvalue()
