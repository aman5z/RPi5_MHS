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
import json
import os
import secrets
import time

from flask import (
    Flask, jsonify, redirect, Response,
    request, send_from_directory, session, url_for,
)
import hmac

import dashboard_config as cfgmod
import tailscale_status
import proxmox_status
import uptime_kuma_status
import firewall_status
import pihole_status

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEVICES_FILE = os.path.join(BASE_DIR, "devices.json")
NOTIFICATIONS_FILE = os.path.join(BASE_DIR, "notifications.json")
PROXMOX_LAYOUT_FILE = os.path.join(BASE_DIR, "proxmox_layout.json")
LAST_FRAME_PNG = "/tmp/mhs_last_frame.png"

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

# ---------------------------------------------------------------------------
# In-memory state shared with dashboard.py when running in the same process.
# ---------------------------------------------------------------------------

# Dict[device_id -> report_dict]: latest stats from each remote device.
# Persisted to devices.json so last-known state survives restarts.
_devices: dict = {}

# List (most-recent first) of received notifications.
_notifications: list = []


def _load_devices():
    """Populate _devices from devices.json on startup."""
    global _devices
    try:
        with open(DEVICES_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            _devices = data
    except (FileNotFoundError, json.JSONDecodeError):
        _devices = {}


def _save_devices():
    tmp = DEVICES_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_devices, f, indent=2)
    os.replace(tmp, DEVICES_FILE)


def _load_notifications():
    """Populate _notifications from notifications.json on startup."""
    global _notifications
    try:
        with open(NOTIFICATIONS_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            _notifications = data
    except (FileNotFoundError, json.JSONDecodeError):
        _notifications = []


def _save_notifications():
    tmp = NOTIFICATIONS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_notifications, f, indent=2)
    os.replace(tmp, NOTIFICATIONS_FILE)


# Load persisted state at import time (i.e. at server startup).
_load_devices()
_load_notifications()

# Wire the shared stores into dashboard.py if it is loaded in the same
# process (e.g. when running both together under a process supervisor
# or in tests).  Fails silently when dashboard.py is absent.
try:
    import dashboard as _dash
    _dash.set_devices_store(_devices)
    _dash.set_notifications_store(_notifications)
except ImportError:
    pass


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
    # /api/devices/report is the only write endpoint that device agents call
    # without a browser session; it uses the same token-bypass auth below.
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


# ---------------------------------------------------------------------------
# Live physical display mirror
# ---------------------------------------------------------------------------

@app.route("/api/display/frame", methods=["GET"])
def display_frame():
    """Return the latest exact frame rendered for the physical MHS display."""
    frame_file = "/run/mhs-display.jpg"

    try:
        with open(frame_file, "rb") as f:
            frame = f.read()
    except (FileNotFoundError, OSError):
        return Response(status=503)

    if not frame:
        return Response(status=503)

    return Response(
        frame,
        mimetype="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# Remote device reporting
# ---------------------------------------------------------------------------



@app.route("/api/proxmox-layout", methods=["GET"])
def get_proxmox_layout():
    """Return the editable Proxmox display layout."""
    try:
        with open(PROXMOX_LAYOUT_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid layout")
        return jsonify(data)
    except Exception:
        return jsonify({
            "version": 2,
            "canvas": {
                "width": 480,
                "height": 320,
                "safe_margin": 12
            },
            "items": {}
        })


@app.route("/api/proxmox-layout", methods=["POST"])
def save_proxmox_layout():
    """Save the editable Proxmox display layout."""
    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400

    items = body.get("items")

    if not isinstance(items, dict):
        return jsonify({"error": "items object required"}), 400

    clean = {
        "version": 2,
        "canvas": {
            "width": 480,
            "height": 320,
            "safe_margin": 12
        },
        "items": {}
    }

    allowed = {
        "title", "clock",
        "cpu", "temp", "ram", "disk",
        "uptime", "tailscale"
    }

    for name in allowed:
        if name not in items:
            continue

        item = items[name]

        try:
            x = max(0, min(480, float(item.get("x", 0))))
            y = max(0, min(320, float(item.get("y", 0))))
        except Exception:
            continue

        clean["items"][name] = {
            "x": round(x, 2),
            "y": round(y, 2)
        }

    tmp = PROXMOX_LAYOUT_FILE + ".tmp"

    with open(tmp, "w") as f:
        json.dump(clean, f, indent=2)
        f.write("\\n")

    os.replace(tmp, PROXMOX_LAYOUT_FILE)

    return jsonify({
        "ok": True,
        "layout": clean
    })


@app.route("/api/devices/report", methods=["POST"])
def device_report():
    """Accept a stats report from a remote device agent.

    Expected JSON body::

        {
            "device_id": "proxmox-01",
            "name": "Proxmox Host",
            "type": "linux",
            "cpu": 12.4,
            "ram_used": 4.2,
            "ram_total": 16,
            "disk_used": 120,
            "disk_total": 500,
            "uptime_s": 123456,
            "timestamp": 1234567890
        }

    ``last_seen`` is set server-side on receipt.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400

    device_id = str(body.get("device_id") or "").strip()
    if not device_id:
        return jsonify({"error": "device_id required"}), 400

    body["last_seen"] = time.time()
    _devices[device_id] = body

    try:
        _save_devices()
    except Exception:
        pass  # Non-fatal; in-memory state is still updated.

    return jsonify({"ok": True}), 200


@app.route("/api/devices", methods=["GET"])
def get_devices():
    """Return all known devices with a computed ``online`` field."""
    cfg = cfgmod.load_config()
    threshold = cfg.get("alerts", {}).get("device_offline_s", 90)
    now = time.time()

    result = []
    for dev in _devices.values():
        d = dict(dev)
        d["online"] = (now - d.get("last_seen", 0)) < threshold
        result.append(d)

    return jsonify({"devices": result})


# ---------------------------------------------------------------------------
# Tailscale status
# ---------------------------------------------------------------------------

@app.route("/api/tailscale", methods=["GET"])
def get_tailscale():
    """Return Tailscale peer list from the local ``tailscale`` CLI."""
    return jsonify(tailscale_status.get_status())


# ---------------------------------------------------------------------------
# Proxmox backup status
# ---------------------------------------------------------------------------

@app.route("/api/proxmox/backups", methods=["GET"])
def get_proxmox_backups():
    """Return Proxmox backup task history."""
    cfg = cfgmod.load_config()
    prx_cfg = cfg.get("proxmox", {})
    return jsonify(proxmox_status.get_backups(prx_cfg, force=True))


# ---------------------------------------------------------------------------
# Notifications inbox
# ---------------------------------------------------------------------------

@app.route("/api/notifications", methods=["GET"])
def get_notifications():
    """Return stored notifications, most recent first."""
    return jsonify({"notifications": _notifications})


@app.route("/api/notifications", methods=["POST"])
def post_notification():
    """Accept a notification from a companion app.

    Expected JSON body::

        {
            "device_id": "my-phone",
            "title": "WhatsApp",
            "body": "Hey, are you there?",
            "app": "com.whatsapp",   // optional
            "timestamp": 1234567890  // optional; server uses now() if absent
        }
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required"}), 400

    body.setdefault("timestamp", int(time.time()))
    body.setdefault("received_at", time.time())

    cfg = cfgmod.load_config()
    max_count = cfg.get("notifications", {}).get("max_count", 100)

    _notifications.insert(0, body)
    # Evict oldest beyond cap
    del _notifications[max_count:]

    try:
        _save_notifications()
    except Exception:
        pass

    return jsonify({"ok": True}), 200


@app.route("/api/notifications/clear", methods=["POST"])
def clear_notifications():
    """Wipe all stored notifications."""
    _notifications.clear()
    try:
        _save_notifications()
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/uptime-kuma", methods=["GET"])
def get_uptime_kuma():
    cfg = cfgmod.load_config().get("uptime_kuma", {})
    payload = {"available": False, "error": "unavailable", "monitors": []}
    if "_dash" in globals() and hasattr(_dash, "get_uptime_kuma_data"):
        payload = _dash.get_uptime_kuma_data(force=True) or payload
    else:
        payload = uptime_kuma_status.get_status(cfg, force=True) or payload
    return jsonify({
        "available": bool(payload.get("available")),
        "error": None if payload.get("available") else "unavailable",
        "monitors": payload.get("monitors", []),
    })


@app.route("/api/firewall", methods=["GET"])
def get_firewall():
    cfg = cfgmod.load_config().get("firewall", {})
    payload = {"available": False, "error": "unavailable", "wan_up": None, "throughput": {}, "block_count": None}
    if "_dash" in globals() and hasattr(_dash, "get_firewall_data"):
        payload = _dash.get_firewall_data(force=True) or payload
    else:
        payload = firewall_status.get_status(cfg, force=True) or payload
    return jsonify({
        "available": bool(payload.get("available")),
        "error": None if payload.get("available") else "unavailable",
        "wan_up": payload.get("wan_up"),
        "throughput": payload.get("throughput", {}),
        "block_count": payload.get("block_count"),
    })


@app.route("/api/pihole", methods=["GET"])
def get_pihole():
    cfg = cfgmod.load_config().get("pihole", {})
    payload = {"available": False, "error": "unavailable"}
    if "_dash" in globals() and hasattr(_dash, "get_pihole_data"):
        payload = _dash.get_pihole_data(force=True) or payload
    else:
        payload = pihole_status.get_status(cfg, force=True) or payload
    return jsonify({
        "available": bool(payload.get("available")),
        "error": None if payload.get("available") else "unavailable",
        "queries_today": payload.get("queries_today", 0),
        "ads_blocked_today": payload.get("ads_blocked_today", 0),
        "ads_percentage_today": payload.get("ads_percentage_today", 0.0),
        "status": payload.get("status", "unknown"),
        "top_blocked": payload.get("top_blocked", {}),
        "top_clients": payload.get("top_clients", {}),
    })


@app.route("/api/arp/devices", methods=["GET"])
def get_arp_devices():
    if "_dash" in globals() and hasattr(_dash, "get_arp_devices"):
        return jsonify({"devices": _dash.get_arp_devices()})
    return jsonify({"devices": {}})


@app.route("/api/arp/allowlist", methods=["POST"])
def arp_allowlist():
    body = request.get_json(silent=True) or {}
    mac = str(body.get("mac", "")).strip()
    if not mac:
        return jsonify({"error": "mac required"}), 400
    ok = False
    if "_dash" in globals() and hasattr(_dash, "allowlist_arp_device"):
        ok = _dash.allowlist_arp_device(mac)
    return jsonify({"ok": bool(ok)})


@app.route("/api/habits", methods=["GET"])
def habits_list():
    if "_dash" in globals() and hasattr(_dash, "get_habits_view"):
        return jsonify({"habits": _dash.get_habits_view()})
    return jsonify({"habits": []})


@app.route("/api/habits/<habit_id>/toggle", methods=["POST"])
def habits_toggle(habit_id):
    ok = False
    if "_dash" in globals() and hasattr(_dash, "toggle_habit_today"):
        ok = _dash.toggle_habit_today(habit_id)
    return jsonify({"ok": bool(ok)})


@app.route("/api/profiles", methods=["GET"])
def profiles_list():
    return jsonify({"profiles": cfgmod.list_profiles()})


@app.route("/api/profiles/<name>", methods=["POST"])
def profiles_save(name):
    saved_name = cfgmod.save_profile(name, cfgmod.load_config())
    return jsonify({"ok": True, "name": saved_name})


@app.route("/api/profiles/<name>/apply", methods=["POST"])
def profiles_apply(name):
    cfg = cfgmod.load_profile(name)
    cfgmod.save_config(cfg)
    return jsonify(cfg)


@app.route("/api/profiles/<name>", methods=["DELETE"])
def profiles_delete(name):
    try:
        deleted = cfgmod.delete_profile(name)
        return jsonify({"ok": True, "name": deleted})
    except FileNotFoundError:
        return jsonify({"error": "Profile not found"}), 404


@app.route("/api/slideshow/images", methods=["GET"])
def slideshow_images():
    if "_dash" in globals() and hasattr(_dash, "list_slideshow_images"):
        folder, images = _dash.list_slideshow_images()
        return jsonify({"folder": folder, "images": images})
    return jsonify({"folder": "", "images": []})


@app.route("/api/screenshot", methods=["GET"])
def screenshot_png():
    try:
        with open(LAST_FRAME_PNG, "rb") as f:
            data = f.read()
    except (OSError, FileNotFoundError):
        return Response(status=503)
    return Response(
        data,
        mimetype="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
