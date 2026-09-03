#!/usr/bin/env python3
"""pfSense/OPNsense lightweight status polling helper."""

import time
import requests

POLL_INTERVAL = 20
_TIMEOUT = 4
_cache = None
_last_poll = 0.0


def _auth(cfg):
    key = str(cfg.get("api_key", "")).strip()
    secret = str(cfg.get("api_secret", "")).strip()
    return (key, secret) if key and secret else None


def _safe_get(url, cfg):
    return requests.get(url, timeout=_TIMEOUT, verify=bool(cfg.get("verify_ssl", True)), auth=_auth(cfg))


def get_status(cfg, force=False):
    global _cache, _last_poll
    if not cfg.get("enabled"):
        return {"available": False, "error": "disabled", "wan_up": None, "throughput": {}, "block_count": None}
    now = time.time()
    if not force and _cache is not None and now - _last_poll < POLL_INTERVAL:
        return _cache
    _last_poll = now
    host = str(cfg.get("host", "")).strip().rstrip("/")
    if not host:
        _cache = {"available": False, "error": "not configured", "wan_up": None, "throughput": {}, "block_count": None}
        return _cache
    base = host if host.startswith("http") else f"https://{host}"
    platform = cfg.get("platform", "opnsense")
    try:
        if platform == "opnsense":
            sysr = _safe_get(f"{base}/api/core/system/status", cfg)
            sysr.raise_for_status()
            sdata = sysr.json()
            ifr = _safe_get(f"{base}/api/diagnostics/interface/getInterfaceStatistics", cfg)
            ifr.raise_for_status()
            idata = ifr.json()
            wan_up = str(sdata.get("status", {}).get("status", "")).lower() in ("ok", "up")
            throughput = {}
            for iface in (idata.get("rows") or [])[:1]:
                throughput = {
                    "rx_kbps": float(iface.get("inbytes") or 0) / 1024.0,
                    "tx_kbps": float(iface.get("outbytes") or 0) / 1024.0,
                    "name": iface.get("interface", "wan"),
                }
            block_count = None
        else:
            # pfSense REST plugin schemas vary; use a conservative endpoint.
            sr = _safe_get(f"{base}/api/v1/status/system", cfg)
            sr.raise_for_status()
            sdata = sr.json()
            wan_up = bool(sdata.get("status", "up") in ("up", True, "ok"))
            throughput = {}
            block_count = None
        _cache = {"available": True, "error": None, "wan_up": wan_up, "throughput": throughput, "block_count": block_count}
    except Exception as exc:
        _cache = {"available": False, "error": str(exc), "wan_up": None, "throughput": {}, "block_count": None}
    return _cache
