#!/usr/bin/env python3
"""Pi-hole status helper using classic token API (/admin/api.php)."""

import time
import requests

POLL_INTERVAL = 20
_TIMEOUT = 4
_cache = None
_last_poll = 0.0


def get_status(cfg, force=False):
    global _cache, _last_poll
    if not cfg.get("enabled"):
        return {"available": False, "error": "disabled"}
    now = time.time()
    if not force and _cache is not None and now - _last_poll < POLL_INTERVAL:
        return _cache
    _last_poll = now
    base = str(cfg.get("url", "")).rstrip("/")
    token = str(cfg.get("api_token", "")).strip()
    if not base:
        _cache = {"available": False, "error": "not configured"}
        return _cache
    if "/admin" not in base:
        base = base + "/admin"
    params = {"summaryRaw": ""}
    if token:
        params["auth"] = token
    try:
        r = requests.get(f"{base}/api.php", params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        _cache = {
            "available": True,
            "error": None,
            "queries_today": int(d.get("dns_queries_today", 0) or 0),
            "ads_blocked_today": int(d.get("ads_blocked_today", 0) or 0),
            "ads_percentage_today": float(d.get("ads_percentage_today", 0.0) or 0.0),
            "status": d.get("status", "unknown"),
            "top_blocked": d.get("top_ads", {}),
            "top_clients": d.get("top_sources", {}),
        }
    except Exception as exc:
        _cache = {"available": False, "error": str(exc)}
    return _cache
