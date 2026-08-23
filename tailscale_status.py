#!/usr/bin/env python3
"""Tailscale network status helper.

Runs ``tailscale status --json`` locally (best-effort) and returns a
summary of peers.  Handles ``FileNotFoundError`` (Tailscale not
installed) and non-zero exit codes silently so the dashboard never
crashes if Tailscale is absent or not logged in.

The result is cached and re-fetched at most every ``POLL_INTERVAL``
seconds to avoid hammering the CLI on every sysdata refresh.
"""

import json
import subprocess
import time

POLL_INTERVAL = 30  # seconds between Tailscale CLI polls


_cache = None
_last_poll = 0.0


def _run_tailscale():
    """Return parsed JSON from ``tailscale status --json``, or None."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        # Tailscale CLI not installed on this system.
        return None
    except Exception:
        return None


def get_status(force=False):
    """Return a dict with Tailscale peer info.

    Returns::

        {
            "available": bool,   # False when Tailscale is not installed/running
            "self_ip":   str,    # Our own Tailscale IP, or ""
            "peers": [
                {
                    "hostname": str,
                    "tailscale_ip": str,
                    "online": bool,
                }
            ],
            "online_count": int,
            "total_count":  int,
        }
    """
    global _cache, _last_poll

    now = time.time()
    if not force and _cache is not None and (now - _last_poll) < POLL_INTERVAL:
        return _cache

    _last_poll = now
    data = _run_tailscale()

    if data is None:
        _cache = {
            "available": False,
            "self_ip": "",
            "peers": [],
            "online_count": 0,
            "total_count": 0,
        }
        return _cache

    peers = []
    peer_map = data.get("Peer") or {}
    for _key, peer in peer_map.items():
        ips = peer.get("TailscaleIPs") or []
        tailscale_ip = ips[0] if ips else ""
        hostname = peer.get("HostName") or peer.get("DNSName") or tailscale_ip
        # Strip trailing .ts.net or similar domain suffix for brevity.
        if "." in hostname:
            hostname = hostname.split(".")[0]
        online = bool(peer.get("Online", False))
        peers.append({
            "hostname": hostname,
            "tailscale_ip": tailscale_ip,
            "online": online,
        })

    # Our own Tailscale IP.
    self_info = data.get("Self") or {}
    self_ips = self_info.get("TailscaleIPs") or []
    self_ip = self_ips[0] if self_ips else ""

    online_count = sum(1 for p in peers if p["online"])

    _cache = {
        "available": True,
        "self_ip": self_ip,
        "peers": peers,
        "online_count": online_count,
        "total_count": len(peers),
    }
    return _cache
