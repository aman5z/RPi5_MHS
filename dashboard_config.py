#!/usr/bin/env python3
"""Configuration schema + persistence for the MHS dashboard.

Settings that used to be hard-coded constants at the top of
``dashboard.py`` (colors, fonts, sizes, alignment, timing, which
screens are shown and in what order, transition/animation settings)
now live in a JSON file (``config.json``) that both the framebuffer
renderer (``dashboard.py``) and the web configuration UI
(``web_config.py``) read from and write to.
"""

import copy
import glob
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# Fallback font used whenever a configured font file cannot be found.
FALLBACK_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FALLBACK_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# Directories scanned when discovering fonts installed on the system.
FONT_SEARCH_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
]

# All screens the dashboard knows how to render. "id" is the stable
# identifier stored in config.json / used by the renderer lookup table.
AVAILABLE_SCREENS = [
    {"id": "clock",         "label": "Clock & Weather"},
    {"id": "system",        "label": "System Status"},
    {"id": "network",       "label": "Network Info"},
    {"id": "stats",         "label": "CPU Stats & Processes"},
    {"id": "devices",       "label": "Remote Devices"},
    {"id": "ping",          "label": "Ping / Latency Graph"},
    {"id": "alerts",        "label": "Alerts"},
    {"id": "notifications", "label": "Notifications"},
]

VALID_ALIGNMENTS = ("left", "center", "right")
VALID_ORIENTATIONS = ("normal", "flipped", "left", "right")
VALID_WEATHER_MODES = ("auto", "manual")
VALID_LOCATION_MODES = ("auto", "manual", "disabled")

# Built-in named themes exposed via the /api/themes endpoint so the
# web UI can offer one-click apply without hard-coding them there.
BUILTIN_THEMES = [
    {"name": "Classic (B/W)",    "background": "#000000", "foreground": "#FFFFFF", "text_secondary": "#AAAAAA"},
    {"name": "Classic Green",    "background": "#000000", "foreground": "#00FF66", "text_secondary": "#007733"},
    {"name": "Amber Terminal",   "background": "#0A0A0A", "foreground": "#FFB000", "text_secondary": "#7A5500"},
    {"name": "Ocean Blue",       "background": "#0A1128", "foreground": "#E8F1FF", "text_secondary": "#6FA8DC"},
    {"name": "Sunset Orange",    "background": "#1A0B1F", "foreground": "#FFB37B", "text_secondary": "#994400"},
    {"name": "Monochrome",       "background": "#181818", "foreground": "#E0E0E0", "text_secondary": "#808080"},
]

DEFAULT_CONFIG = {
    "theme": {
        "background": "#000000",
        "foreground": "#FFFFFF",
        # Secondary / accent color used for labels and minor decorations.
        "text_secondary": "#AAAAAA",
        # Alert / warning accent colors used on the alerts screen.
        "alert_color": "#FF3333",
        "warn_color":  "#FFAA00",
    },
    "display": {
        # Screen rotation: "normal" (0°), "flipped" (180°),
        # "left" (90° CCW), "right" (90° CW).
        "orientation": "normal",
        # Backlight brightness 0-100 (best-effort; requires a sysfs
        # backlight device; silently ignored when none is present).
        "brightness": 100,
    },
    "fonts": {
        # "family" is a display name that maps to a discovered font (see
        # list_available_fonts()). If it can't be resolved, the fallback
        # DejaVu Sans Mono font bundled with the OS is used instead.
        "family": "DejaVuSansMono",
        "clock_size": 55,
        "date_size": 16,
        "value_size": 19,
        "big_value_size": 22,
        "small_size": 14,
        "ip_size": 15,
        "title_size": 15,
    },
    "alignment": {
        "clock": "center",
        "date": "center",
        # Alignment for data value labels (e.g. CPU %, temperature …).
        "values": "left",
        # Alignment for footer hint lines shown at the bottom of each screen.
        "footer": "center",
    },
    "timing": {
        "fps": 15,
        "screen_duration": 5.0,
        "transitions_enabled": True,
        "transition_duration": 0.45,
        # When False the icons are "frozen" (no spin/pulse/bob animations).
        "icon_animations_enabled": True,
    },
    "weather": {
        # "auto"   – geolocate via ipwho.is (existing behaviour).
        # "manual" – use the latitude/longitude fields below directly,
        #            skipping the IP geolocation call entirely.
        "mode": "auto",
        "latitude": None,
        "longitude": None,
    },
    # Location shown on the clock screen footer.
    # mode: "auto" = reverse-geocode from weather lat/lon via Nominatim,
    #        "manual" = use the "name" string below,
    #        "disabled" = fall back to the screen's footer_text.
    "location": {
        "mode": "auto",
        "name": "",
    },
    # Ping latency graph targets. Each entry: {"label": str, "host": str}.
    "ping_targets": [
        {"label": "Router",  "host": "192.168.1.1"},
        {"label": "1.1.1.1", "host": "1.1.1.1"},
    ],
    # Proxmox backup-status integration.
    "proxmox": {
        "enabled": False,
        "host": "",
        "token_id": "",
        "token_secret": "",
        "verify_ssl": True,
        # Warn if the most recent backup is older than this many hours.
        "staleness_hours": 24,
    },
    # Alert thresholds for local system metrics.
    "alerts": {
        "cpu_warn_pct":  85,
        "temp_warn_c":   75,
        "disk_warn_pct": 90,
        # Seconds without a report before a remote device is considered offline.
        "device_offline_s": 90,
    },
    # Notification inbox – keep last N notifications in memory + file.
    "notifications": {
        "max_count": 100,
    },
    # Only screens present here (and enabled) are shown, in this order.
    # footer_text / footer_enabled are per-screen overrides; defaults
    # preserve the original hard-coded strings so old configs are unaffected.
    "screens": [
        {"id": "clock",         "enabled": True,  "footer_text": "SYSTEM STATUS NEXT",   "footer_enabled": True},
        {"id": "system",        "enabled": True,  "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
        {"id": "network",       "enabled": False, "footer_text": "SYSTEM STATUS NEXT",   "footer_enabled": True},
        {"id": "stats",         "enabled": False, "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
        {"id": "devices",       "enabled": False, "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
        {"id": "ping",          "enabled": False, "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
        {"id": "alerts",        "enabled": False, "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
        {"id": "notifications", "enabled": False, "footer_text": "CLOCK & WEATHER NEXT", "footer_enabled": True},
    ],
}

# Default footer text per screen id (used when adding a previously-
# unknown screen to the sanitised list for the first time).
_DEFAULT_FOOTER = {
    "clock":         "SYSTEM STATUS NEXT",
    "system":        "CLOCK & WEATHER NEXT",
    "network":       "SYSTEM STATUS NEXT",
    "stats":         "CLOCK & WEATHER NEXT",
    "devices":       "CLOCK & WEATHER NEXT",
    "ping":          "CLOCK & WEATHER NEXT",
    "alerts":        "CLOCK & WEATHER NEXT",
    "notifications": "CLOCK & WEATHER NEXT",
}


def _deep_merge(base, override):
    """Merge ``override`` into a copy of ``base``, recursively for dicts.

    Unknown/extra keys in ``override`` are kept so the config file can be
    forward compatible; missing keys fall back to ``base`` (the defaults).
    """

    result = copy.deepcopy(base)

    if not isinstance(override, dict):
        return result

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def _sanitize(cfg):
    """Clamp/validate values so a bad config.json can never crash the
    renderer or produce something unreadable on the 480x320 screen."""

    fonts = cfg.setdefault("fonts", {})
    for key in (
        "clock_size", "date_size", "value_size", "big_value_size",
        "small_size", "ip_size", "title_size",
    ):
        try:
            fonts[key] = max(6, min(120, int(fonts.get(key, DEFAULT_CONFIG["fonts"][key]))))
        except (TypeError, ValueError):
            fonts[key] = DEFAULT_CONFIG["fonts"][key]

    align = cfg.setdefault("alignment", {})
    for key in ("clock", "date", "values", "footer"):
        if align.get(key) not in VALID_ALIGNMENTS:
            align[key] = DEFAULT_CONFIG["alignment"][key]

    timing = cfg.setdefault("timing", {})
    try:
        timing["fps"] = max(1, min(30, int(timing.get("fps", DEFAULT_CONFIG["timing"]["fps"]))))
    except (TypeError, ValueError):
        timing["fps"] = DEFAULT_CONFIG["timing"]["fps"]

    try:
        timing["screen_duration"] = max(
            1.0, min(120.0, float(timing.get("screen_duration", DEFAULT_CONFIG["timing"]["screen_duration"])))
        )
    except (TypeError, ValueError):
        timing["screen_duration"] = DEFAULT_CONFIG["timing"]["screen_duration"]

    try:
        timing["transition_duration"] = max(
            0.05, min(3.0, float(timing.get("transition_duration", DEFAULT_CONFIG["timing"]["transition_duration"])))
        )
    except (TypeError, ValueError):
        timing["transition_duration"] = DEFAULT_CONFIG["timing"]["transition_duration"]

    timing["transitions_enabled"] = bool(
        timing.get("transitions_enabled", DEFAULT_CONFIG["timing"]["transitions_enabled"])
    )

    timing["icon_animations_enabled"] = bool(
        timing.get("icon_animations_enabled", DEFAULT_CONFIG["timing"]["icon_animations_enabled"])
    )

    theme = cfg.setdefault("theme", {})
    for key in ("background", "foreground", "text_secondary", "alert_color", "warn_color"):
        value = theme.get(key)
        if not isinstance(value, str) or not _is_hex_color(value):
            theme[key] = DEFAULT_CONFIG["theme"][key]

    # ---- display (orientation + brightness) ----
    display = cfg.setdefault("display", {})
    if display.get("orientation") not in VALID_ORIENTATIONS:
        display["orientation"] = DEFAULT_CONFIG["display"]["orientation"]
    try:
        display["brightness"] = max(0, min(100, int(display.get("brightness", DEFAULT_CONFIG["display"]["brightness"]))))
    except (TypeError, ValueError):
        display["brightness"] = DEFAULT_CONFIG["display"]["brightness"]

    # ---- weather ----
    weather = cfg.setdefault("weather", {})
    if weather.get("mode") not in VALID_WEATHER_MODES:
        weather["mode"] = DEFAULT_CONFIG["weather"]["mode"]
    for coord in ("latitude", "longitude"):
        val = weather.get(coord)
        if val is not None:
            try:
                weather[coord] = float(val)
            except (TypeError, ValueError):
                weather[coord] = None

    # ---- location ----
    location = cfg.setdefault("location", {})
    if location.get("mode") not in VALID_LOCATION_MODES:
        location["mode"] = DEFAULT_CONFIG["location"]["mode"]
    if not isinstance(location.get("name"), str):
        location["name"] = DEFAULT_CONFIG["location"]["name"]

    # ---- ping_targets ----
    pt = cfg.get("ping_targets")
    if not isinstance(pt, list):
        cfg["ping_targets"] = copy.deepcopy(DEFAULT_CONFIG["ping_targets"])
    else:
        cleaned_pt = []
        for entry in pt:
            if isinstance(entry, dict) and isinstance(entry.get("host"), str) and entry["host"]:
                cleaned_pt.append({
                    "label": str(entry.get("label", entry["host"]))[:20],
                    "host":  str(entry["host"])[:64],
                })
        cfg["ping_targets"] = cleaned_pt

    # ---- proxmox ----
    prx = cfg.setdefault("proxmox", {})
    prx.setdefault("enabled",         DEFAULT_CONFIG["proxmox"]["enabled"])
    prx.setdefault("host",            DEFAULT_CONFIG["proxmox"]["host"])
    prx.setdefault("token_id",        DEFAULT_CONFIG["proxmox"]["token_id"])
    prx.setdefault("token_secret",    DEFAULT_CONFIG["proxmox"]["token_secret"])
    prx["verify_ssl"] = bool(prx.get("verify_ssl", DEFAULT_CONFIG["proxmox"]["verify_ssl"]))
    try:
        prx["staleness_hours"] = max(1, int(prx.get("staleness_hours", DEFAULT_CONFIG["proxmox"]["staleness_hours"])))
    except (TypeError, ValueError):
        prx["staleness_hours"] = DEFAULT_CONFIG["proxmox"]["staleness_hours"]

    # ---- alerts thresholds ----
    alt = cfg.setdefault("alerts", {})
    def _int_clamp(val, default, lo, hi):
        try:
            return max(lo, min(hi, int(val)))
        except (TypeError, ValueError):
            return default
    alt["cpu_warn_pct"]   = _int_clamp(alt.get("cpu_warn_pct"),   DEFAULT_CONFIG["alerts"]["cpu_warn_pct"],   1, 100)
    alt["temp_warn_c"]    = _int_clamp(alt.get("temp_warn_c"),    DEFAULT_CONFIG["alerts"]["temp_warn_c"],    1, 150)
    alt["disk_warn_pct"]  = _int_clamp(alt.get("disk_warn_pct"),  DEFAULT_CONFIG["alerts"]["disk_warn_pct"],  1, 100)
    alt["device_offline_s"] = _int_clamp(alt.get("device_offline_s"), DEFAULT_CONFIG["alerts"]["device_offline_s"], 10, 3600)

    # ---- notifications ----
    notif = cfg.setdefault("notifications", {})
    try:
        notif["max_count"] = max(1, min(10000, int(notif.get("max_count", DEFAULT_CONFIG["notifications"]["max_count"]))))
    except (TypeError, ValueError):
        notif["max_count"] = DEFAULT_CONFIG["notifications"]["max_count"]

    # ---- screens ----
    valid_ids = {s["id"] for s in AVAILABLE_SCREENS}
    screens = cfg.get("screens")

    if not isinstance(screens, list) or not screens:
        screens = copy.deepcopy(DEFAULT_CONFIG["screens"])
    else:
        cleaned = []
        seen = set()

        for entry in screens:
            if not isinstance(entry, dict):
                continue

            sid = entry.get("id")

            if sid not in valid_ids or sid in seen:
                continue

            seen.add(sid)

            # Preserve / default footer settings.
            footer_text = entry.get("footer_text")
            if not isinstance(footer_text, str):
                footer_text = _DEFAULT_FOOTER.get(sid, "")

            cleaned.append({
                "id": sid,
                "enabled": bool(entry.get("enabled", True)),
                "footer_text": footer_text,
                "footer_enabled": bool(entry.get("footer_enabled", True)),
            })

        # Any known screen missing from the list is appended (disabled),
        # so newly added screen types show up for the user to enable.
        for screen in AVAILABLE_SCREENS:
            if screen["id"] not in seen:
                default_entry = next(
                    (s for s in DEFAULT_CONFIG["screens"] if s["id"] == screen["id"]),
                    None,
                )
                cleaned.append({
                    "id": screen["id"],
                    "enabled": default_entry["enabled"] if default_entry else False,
                    "footer_text": _DEFAULT_FOOTER.get(screen["id"], ""),
                    "footer_enabled": True,
                })

        screens = cleaned or copy.deepcopy(DEFAULT_CONFIG["screens"])

    if not any(s["enabled"] for s in screens):
        screens[0]["enabled"] = True

    cfg["screens"] = screens

    return cfg


def _is_hex_color(value):
    if not value.startswith("#") or len(value) not in (4, 7):
        return False

    try:
        int(value[1:], 16)
        return True
    except ValueError:
        return False


def load_config(path=CONFIG_PATH):
    """Load config.json, falling back to defaults for anything missing
    or invalid. Never raises: a broken/missing file just yields defaults."""

    cfg = copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(path) as f:
            user_cfg = json.load(f)
        cfg = _deep_merge(cfg, user_cfg)
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError):
        pass

    return _sanitize(cfg)


def save_config(cfg, path=CONFIG_PATH):
    cfg = _sanitize(_deep_merge(DEFAULT_CONFIG, cfg))

    tmp_path = f"{path}.tmp"

    with open(tmp_path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    os.replace(tmp_path, path)

    return cfg


def get_mtime(path=CONFIG_PATH):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def list_available_fonts():
    """Discover installed TrueType fonts, grouped by family name.

    Returns a dict: {family_name: {"regular": path, "bold": path|None}}
    A family is derived from the filename (e.g. "DejaVuSansMono-Bold.ttf"
    -> family "DejaVuSansMono", bold variant). Only .ttf files are
    considered since PIL's ImageFont.truetype expects TTF/OTF.
    """

    families = {}

    for base_dir in FONT_SEARCH_DIRS:
        if not os.path.isdir(base_dir):
            continue

        for path in glob.glob(os.path.join(base_dir, "**", "*.ttf"), recursive=True):
            filename = os.path.splitext(os.path.basename(path))[0]

            is_bold = False
            family = filename

            for suffix in ("-Bold", "_Bold", " Bold"):
                if filename.endswith(suffix):
                    family = filename[: -len(suffix)]
                    is_bold = True
                    break

            entry = families.setdefault(family, {"regular": None, "bold": None})

            if is_bold:
                entry["bold"] = entry["bold"] or path
            else:
                entry["regular"] = entry["regular"] or path

    # Ensure the fallback family is always present even on a system with
    # no fonts discovered (shouldn't normally happen on a Pi w/ DejaVu).
    families.setdefault(
        "DejaVuSansMono",
        {"regular": FALLBACK_FONT_REGULAR, "bold": FALLBACK_FONT_BOLD},
    )

    return families


def resolve_font_paths(family):
    """Return (regular_path, bold_path) for a configured font family
    name, falling back to DejaVu Sans Mono if the family/file can't be
    found (e.g. the font was removed after being selected)."""

    fonts = list_available_fonts()
    entry = fonts.get(family)

    if not entry:
        return FALLBACK_FONT_REGULAR, FALLBACK_FONT_BOLD

    regular = entry.get("regular") or entry.get("bold") or FALLBACK_FONT_REGULAR
    bold = entry.get("bold") or entry.get("regular") or FALLBACK_FONT_BOLD

    if not os.path.exists(regular):
        regular = FALLBACK_FONT_REGULAR

    if not os.path.exists(bold):
        bold = FALLBACK_FONT_BOLD

    return regular, bold


def hex_to_rgb(value):
    value = value.lstrip("#")

    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)

    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
