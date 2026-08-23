#!/usr/bin/env python3
"""Small local web server exposing a REST API + static HTML page for
configuring the MHS dashboard (theme, fonts, alignment, timing,
screens). Meant to run alongside dashboard.py; writes to the same
config.json which dashboard.py hot-reloads.

Run with:
    python3 web_config.py
Then browse to http://<pi-ip>:8080/ from any device on the network.
"""

import os

from flask import Flask, jsonify, request, send_from_directory
import hmac

import dashboard_config as cfgmod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

# Optional shared-secret protection: set the MHS_CONFIG_TOKEN environment
# variable to require clients to send it as `?token=...` or an
# `X-Config-Token` header before they can read/change the config. Left
# unset by default since this is meant for a trusted home LAN, but
# recommended if the Pi is reachable beyond your own network.
API_TOKEN = os.environ.get("MHS_CONFIG_TOKEN")


@app.before_request
def check_token():
    if not API_TOKEN:
        return None

    supplied = request.headers.get("X-Config-Token") or request.args.get("token")

    if not supplied or not hmac.compare_digest(supplied, API_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401

    return None


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
    import copy

    saved = cfgmod.save_config(copy.deepcopy(cfgmod.DEFAULT_CONFIG))

    return jsonify(saved)


@app.route("/api/fonts", methods=["GET"])
def get_fonts():
    families = sorted(cfgmod.list_available_fonts().keys())

    return jsonify({"families": families})


@app.route("/api/screens", methods=["GET"])
def get_screens():
    return jsonify({"screens": cfgmod.AVAILABLE_SCREENS})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
