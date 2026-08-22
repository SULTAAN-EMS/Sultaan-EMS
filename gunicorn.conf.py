"""Production-safe Gunicorn defaults for Render and Railway.

Keep these settings in a config file as well as in the Procfile so a hosting
dashboard Start Command override cannot silently drop the public bind or the
Render overlay-filesystem sendfile workaround.
"""

import os


bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"
sendfile = False
