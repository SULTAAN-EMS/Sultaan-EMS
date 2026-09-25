import os
from pathlib import Path

os.environ.setdefault("SENDFILE", "0")

from app import create_app

app = create_app()


def _development_watch_files():
    """Watch source, template, stylesheet, and script edits during local runs."""
    root = Path(__file__).resolve().parent / "app"
    watched_suffixes = {".py", ".html", ".jinja2", ".css", ".js", ".json"}
    return [
        str(path)
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in watched_suffixes
        and "__pycache__" not in path.parts
    ]

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        use_reloader=True,
        reloader_type="stat",
        extra_files=_development_watch_files(),
    )
