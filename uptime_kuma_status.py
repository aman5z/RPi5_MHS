#!/usr/bin/env python3
"""Uptime Kuma status-page helper.

Uses the public status-page endpoint:
GET {url}/api/status-page/{slug}
This avoids socket.io/session complexity and works with a short timeout.
"""

import time
import requests

POLL_INTERVAL = 30
_TIMEOUT = 4
_cache = None
_last_poll = 0.0


def get_status(cfg, force=False):
    global _cache, _last_poll
    if not cfg.get("enabled"):
        return {"available": False, "error": "disabled", "monitors": []}
    now = time.time()
    if not force and _cache is not None and now - _last_poll < POLL_INTERVAL:
        return _cache
    _last_poll = now
    base = str(cfg.get("url", "")).rstrip("/")
    slug = str(cfg.get("slug", "")).strip()
    if not base or not slug:
        _cache = {"available": False, "error": "not configured", "monitors": []}
        return _cache
    headers = {}
    params = {}
    api_key = str(cfg.get("api_key", "")).strip()
    if api_key:
        params["apikey"] = api_key
    try:
        r = requests.get(f"{base}/api/status-page/{slug}", timeout=_TIMEOUT, headers=headers, params=params)
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        _cache = {"available": False, "error": str(exc), "monitors": []}
        return _cache
    monitors = []
    for m in d.get("publicGroupList", []):
        for item in m.get("monitorList", []):
            monitors.append({
                "name": item.get("name", "unknown"),
                "status": "up" if int(item.get("status", 0)) == 1 else "down",
                "uptime_pct": round(float(item.get("uptime", 0.0)) * 100, 2) if isinstance(item.get("uptime"), (int, float)) else None,
            })
    if not monitors:
        for item in d.get("monitorList", []):
            monitors.append({
                "name": item.get("name", "unknown"),
                "status": "up" if int(item.get("status", 0)) == 1 else "down",
                "uptime_pct": None,
            })
    _cache = {"available": True, "error": None, "monitors": monitors}
    return _cache
