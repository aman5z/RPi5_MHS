#!/usr/bin/env python3
"""Small local web server exposing a REST API + static HTML page for
configuring the MHS dashboard (theme, fonts, alignment, timing,
screens). Meant to run alongside dashboard.py; writes to the same
config.json which dashboard.py hot-reloads.

Run with:
    python3 web_config.py
Then browse to http://<pi-ip>:8080/ from any device on the network.
"""

import copy
import os
import secrets

from flask import (
    Flask, jsonify, redirect,
    request, send_from_directory, session, url_for,
)
import hmac

import dashboard_config as cfgmod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

# A fresh secret key is generated each run so session cookies are
# invalidated on restart (acceptable for a local dashboard server).
app.secret_key = secrets.token_hex(32)

# ---------------------------------------------------------------------------
# Login credentials.
# Override via environment variables MHS_ADMIN_USER / MHS_ADMIN_PASSWORD
# for per-instance secrets; the strings below are fallback defaults that
# work out-of-the-box on a trusted home LAN.
# CHANGE THESE (or set the env-vars) for any non-trusted-LAN deployment.
# ---------------------------------------------------------------------------
ADMIN_USERNAME = os.environ.get("MHS_ADMIN_USER", "admin")       # Change for production use.
ADMIN_PASSWORD = os.environ.get("MHS_ADMIN_PASSWORD", "06112024") # Change for production use.
# ---------------------------------------------------------------------------

# Optional shared-secret protection: set the MHS_CONFIG_TOKEN environment
# variable to require clients to send it as `?token=...` or an
# `X-Config-Token` header before they can read/change the config. Left
# unset by default since this is meant for a trusted home LAN, but
# recommended if the Pi is reachable beyond your own network.
API_TOKEN = os.environ.get("MHS_CONFIG_TOKEN")


def _is_authenticated():
    """Return True if the current request is authenticated via either
    the session cookie (browser login) or the MHS_CONFIG_TOKEN bypass
    (programmatic / API access)."""

    # Session-cookie path (browser UI).
    if session.get("logged_in"):
        return True

    # Token bypass — allows existing scripts/integrations to keep working
    # even when the MHS_CONFIG_TOKEN env-var is set.
    if API_TOKEN:
        supplied = (
            request.headers.get("X-Config-Token")
            or request.args.get("token")
        )
        if supplied and hmac.compare_digest(supplied, API_TOKEN):
            return True
        return False

    # No token configured and no session — unauthenticated.
    return False


_STATIC_EXTS = frozenset({".css", ".js", ".png", ".jpg", ".ico", ".svg", ".woff", ".woff2", ".ttf"})


@app.before_request
def require_auth():
    """Gate every route that is not the login/logout page or a static asset."""
    public = {"/login", "/logout"}
    if request.path in public:
        return None
    # Allow static asset files (CSS, JS, images) through so the login page
    # itself can load its stylesheet without being redirected.
    _, ext = os.path.splitext(request.path)
    if ext.lower() in _STATIC_EXTS:
        return None
    if not _is_authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        # Use compare_digest to guard against timing-based side-channels.
        if (
            hmac.compare_digest(username, ADMIN_USERNAME)
            and hmac.compare_digest(password, ADMIN_PASSWORD)
        ):
            session["logged_in"] = True
            return redirect(url_for("index"))
        # Bad credentials — return the login page with HTTP 401 so the
        # client-side JS can detect the failure and show an error message.
        response = send_from_directory(STATIC_DIR, "login.html")
        response.status_code = 401
        return response
    return send_from_directory(STATIC_DIR, "login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "config.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(cfgmod.load_config())


@app.route("/api/config", methods=["POST"])
def update_config():
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    saved = cfgmod.save_config(body)

    return jsonify(saved)


@app.route("/api/config/reset", methods=["POST"])
def reset_config():
    saved = cfgmod.save_config(copy.deepcopy(cfgmod.DEFAULT_CONFIG))

    return jsonify(saved)


@app.route("/api/fonts", methods=["GET"])
def get_fonts():
    families = sorted(cfgmod.list_available_fonts().keys())

    return jsonify({"families": families})


@app.route("/api/screens", methods=["GET"])
def get_screens():
    return jsonify({"screens": cfgmod.AVAILABLE_SCREENS})


@app.route("/api/themes", methods=["GET"])
def get_themes():
    return jsonify({"themes": cfgmod.BUILTIN_THEMES})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
