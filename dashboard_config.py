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
    {"id": "clock", "label": "Clock & Weather"},
    {"id": "system", "label": "System Status"},
]

VALID_ALIGNMENTS = ("left", "center", "right")

DEFAULT_CONFIG = {
    "theme": {
        "background": "#000000",
        "foreground": "#FFFFFF",
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
    },
    "timing": {
        "fps": 15,
        "screen_duration": 5.0,
        "transitions_enabled": True,
        "transition_duration": 0.45,
    },
    # Only screens present here (and enabled) are shown, in this order.
    "screens": [
        {"id": "clock", "enabled": True},
        {"id": "system", "enabled": True},
    ],
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
    for key in ("clock", "date"):
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

    theme = cfg.setdefault("theme", {})
    for key in ("background", "foreground"):
        value = theme.get(key)
        if not isinstance(value, str) or not _is_hex_color(value):
            theme[key] = DEFAULT_CONFIG["theme"][key]

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
            cleaned.append({"id": sid, "enabled": bool(entry.get("enabled", True))})

        # Any known screen missing from the list is appended (disabled),
        # so newly added screen types show up for the user to enable.
        for screen in AVAILABLE_SCREENS:
            if screen["id"] not in seen:
                cleaned.append({"id": screen["id"], "enabled": False})

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
