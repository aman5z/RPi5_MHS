#!/usr/bin/env python3
"""Proxmox backup status helper.

Fetches recent backup (vzdump) task history from the Proxmox REST API
using API token authentication.  Handles connection errors and TLS
verification failures gracefully — the dashboard loop is never blocked
or crashed.

Results are cached and refreshed at most every ``POLL_INTERVAL`` seconds
to keep network calls infrequent and non-blocking during the render loop.

**How to create a Proxmox API token**
  1. Log in to the Proxmox web UI.
  2. Go to Datacenter → Permissions → API Tokens → Add.
  3. Choose a user (e.g. ``root@pam``), give the token a name (e.g.
     ``dashboard``), uncheck "Privilege Separation" unless your user
     already has Audit role.
  4. Copy the displayed secret — it is shown only once.
  5. In config.json set::

       "proxmox": {
           "enabled": true,
           "host": "proxmox.lan",      // or IP address
           "token_id": "root@pam!dashboard",
           "token_secret": "<copied secret>",
           "verify_ssl": false         // if using the default self-signed cert
       }
"""

import time

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

POLL_INTERVAL = 60  # seconds between Proxmox API polls
_API_TIMEOUT = 5    # seconds per HTTP request

_cache = None
_last_poll = 0.0


def _fetch_backups(cfg):
    """Return a list of recent vzdump tasks from the Proxmox API.

    Each entry is a dict with keys: vmid, status, endtime, description.
    Returns an empty list on any error.
    """
    if not _HAS_REQUESTS:
        return []

    host   = cfg.get("host", "").strip()
    tok_id = cfg.get("token_id", "").strip()
    tok_sec = cfg.get("token_secret", "").strip()
    verify = bool(cfg.get("verify_ssl", True))

    if not host or not tok_id or not tok_sec:
        return []

    base = f"https://{host}:8006/api2/json"
    headers = {"Authorization": f"PVEAPIToken={tok_id}={tok_sec}"}

    try:
        # Fetch the task log for all nodes; we only need the first page
        # (most recent tasks).  Filter by type=vzdump.
        # First discover all nodes.
        r = _requests.get(
            f"{base}/nodes",
            headers=headers,
            verify=verify,
            timeout=_API_TIMEOUT,
        )
        r.raise_for_status()
        nodes = [n["node"] for n in r.json().get("data", [])]
    except Exception:
        return []

    results = []
    for node in nodes[:3]:  # cap at 3 nodes to keep latency bounded
        try:
            r = _requests.get(
                f"{base}/nodes/{node}/tasks",
                headers=headers,
                params={"typefilter": "vzdump", "limit": 20},
                verify=verify,
                timeout=_API_TIMEOUT,
            )
            r.raise_for_status()
            for task in r.json().get("data", []):
                results.append({
                    "node":        node,
                    "vmid":        str(task.get("id") or task.get("upid", "")[:20]),
                    "status":      task.get("status", "unknown"),
                    "endtime":     task.get("endtime"),
                    "starttime":   task.get("starttime"),
                    "description": task.get("upid", "")[:40],
                })
        except Exception:
            continue

    # Sort most recent first.
    results.sort(key=lambda x: x.get("endtime") or 0, reverse=True)
    return results[:20]


def get_backups(cfg, force=False):
    """Return cached backup status dict.

    cfg must be the ``proxmox`` section from config.json.

    Returns::

        {
            "available": bool,
            "error": str or None,
            "backups": [
                {
                    "node": str,
                    "vmid": str,
                    "status": str,   # "OK", "warnings", "errors", …
                    "endtime": int,  # Unix timestamp or None
                    "description": str,
                }
            ],
            "last_ok_age_s": float or None,  # seconds since last OK backup
            "any_failed": bool,
        }
    """
    global _cache, _last_poll

    if not cfg.get("enabled"):
        return {"available": False, "error": "disabled", "backups": [],
                "last_ok_age_s": None, "any_failed": False}

    now = time.time()
    if not force and _cache is not None and (now - _last_poll) < POLL_INTERVAL:
        return _cache

    _last_poll = now

    try:
        backups = _fetch_backups(cfg)
    except Exception:
        backups = []

    if not backups and not (cfg.get("host") and cfg.get("token_id")):
        _cache = {"available": False, "error": "not configured", "backups": [],
                  "last_ok_age_s": None, "any_failed": False}
        return _cache

    any_failed = any(
        b["status"] not in ("OK", "ok", "running") for b in backups
    )

    # Find the most recent successfully-completed backup.
    last_ok_age_s = None
    for b in backups:
        if b["status"] in ("OK", "ok") and b.get("endtime"):
            last_ok_age_s = now - b["endtime"]
            break

    _cache = {
        "available": True,
        "error": None,
        "backups": backups,
        "last_ok_age_s": last_ok_age_s,
        "any_failed": any_failed,
    }
    return _cache
