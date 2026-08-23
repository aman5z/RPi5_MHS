#!/usr/bin/env python3

import os
import glob
import time
import math
import json
import socket
import threading
import subprocess
import requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

import numpy as np

import dashboard_config as cfgmod
import tailscale_status
import proxmox_status

FB = "/dev/fb0"
HWMON_ROOT = "/sys/class/hwmon"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"

W, H = 480, 320

# ---------------------------------------------------------
# CONFIG (theme / fonts / alignment / timing / screens), loaded from
# config.json and hot-reloaded while the dashboard runs so changes made
# through the web configuration UI are picked up without a restart.
# ---------------------------------------------------------

CONFIG = cfgmod.load_config()
_config_mtime = cfgmod.get_mtime()

WHITE = cfgmod.hex_to_rgb(CONFIG["theme"]["foreground"])
BLACK = cfgmod.hex_to_rgb(CONFIG["theme"]["background"])
SECONDARY = cfgmod.hex_to_rgb(CONFIG["theme"]["text_secondary"])

FPS = CONFIG["timing"]["fps"]
FRAME_INTERVAL = 1.0 / FPS
SCREEN_DURATION = CONFIG["timing"]["screen_duration"]
TRANSITION_DURATION = CONFIG["timing"]["transition_duration"]
TRANSITIONS_ENABLED = CONFIG["timing"]["transitions_enabled"]
ICON_ANIMATIONS_ENABLED = CONFIG["timing"]["icon_animations_enabled"]

_font_cache = {}

# Backlight sysfs path discovered once and cached.
_backlight_path = None
_backlight_checked = False


def _find_backlight():
    """Return the sysfs brightness file path, or None if not available."""
    global _backlight_path, _backlight_checked
    if _backlight_checked:
        return _backlight_path
    _backlight_checked = True
    try:
        for entry in glob.glob("/sys/class/backlight/*/brightness"):
            _backlight_path = entry
            break
    except Exception:
        pass
    return _backlight_path


def set_brightness(value):
    """Write brightness (0-100) to the sysfs backlight device.

    Silently no-ops with a printed message if no backlight device is
    found (e.g. HDMI displays, displays without kernel driver support).
    """
    path = _find_backlight()
    if not path:
        # No backlight device available on this system — skip silently.
        return
    try:
        max_path = os.path.join(os.path.dirname(path), "max_brightness")
        max_brightness = 255
        try:
            with open(max_path) as f:
                max_brightness = int(f.read().strip())
        except Exception:
            pass
        raw = int(max_brightness * value / 100)
        with open(path, "w") as f:
            f.write(str(raw))
    except Exception as exc:
        print(f"Brightness write failed ({path}): {exc}")


def get_font(path, size):
    key = (path, size)

    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except Exception:
            _font_cache[key] = ImageFont.truetype(cfgmod.FALLBACK_FONT_REGULAR, size)

    return _font_cache[key]


def apply_config(cfg):
    """(Re)compute every derived global (colors, fonts, timing, screen
    list) from a config dict. Called at startup and whenever
    config.json changes on disk."""

    global CONFIG, WHITE, BLACK, SECONDARY, FPS, FRAME_INTERVAL
    global SCREEN_DURATION, TRANSITION_DURATION, TRANSITIONS_ENABLED
    global ICON_ANIMATIONS_ENABLED
    global clock_font, date_font, value_font, big_value_font
    global small_font, ip_font, title_font
    global SCREEN_RENDERERS, NUM_SCREENS

    CONFIG = cfg

    WHITE     = cfgmod.hex_to_rgb(cfg["theme"]["foreground"])
    BLACK     = cfgmod.hex_to_rgb(cfg["theme"]["background"])
    SECONDARY = cfgmod.hex_to_rgb(cfg["theme"]["text_secondary"])

    timing = cfg["timing"]
    FPS                       = timing["fps"]
    FRAME_INTERVAL            = 1.0 / FPS
    SCREEN_DURATION           = timing["screen_duration"]
    TRANSITION_DURATION       = timing["transition_duration"]
    TRANSITIONS_ENABLED       = timing["transitions_enabled"]
    ICON_ANIMATIONS_ENABLED   = timing["icon_animations_enabled"]

    fonts = cfg["fonts"]
    font_regular, font_bold = cfgmod.resolve_font_paths(fonts["family"])

    clock_font     = get_font(font_bold, fonts["clock_size"])
    date_font      = get_font(font_bold, fonts["date_size"])
    value_font     = get_font(font_bold, fonts["value_size"])
    big_value_font = get_font(font_bold, fonts["big_value_size"])
    small_font     = get_font(font_regular, fonts["small_size"])
    ip_font        = get_font(font_bold, fonts["ip_size"])
    title_font     = get_font(font_bold, fonts["title_size"])

    enabled_ids = [s["id"] for s in cfg["screens"] if s["enabled"]]

    if not enabled_ids:
        enabled_ids = ["clock"]

    SCREEN_RENDERERS = [SCREEN_RENDERER_MAP[sid] for sid in enabled_ids if sid in SCREEN_RENDERER_MAP]

    if not SCREEN_RENDERERS:
        SCREEN_RENDERERS = [draw_screen_clock]

    NUM_SCREENS = len(SCREEN_RENDERERS)

    # Apply brightness to backlight if configured and available.
    brightness = cfg.get("display", {}).get("brightness", 100)
    set_brightness(brightness)


def reload_config_if_changed():
    global _config_mtime

    mtime = cfgmod.get_mtime()

    if mtime != _config_mtime:
        _config_mtime = mtime
        apply_config(cfgmod.load_config())


# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------

weather_temp = "--"
weather_humidity = "--"
last_weather = 0
weather_lat = None
weather_lon = None

last_cpu_total = 0
last_cpu_idle = 0

# smoothed temperature value used for the animated thermometer fill
displayed_temp = 0.0

# cached hwmon path for the PWM fan, resolved once then reused
_fan_hwmon_path = None
_fan_hwmon_checked = False

# ---------------------------------------------------------
# LOCATION STATE (auto reverse-geocoded or manual)
# ---------------------------------------------------------
# Cached resolved location string shown on the clock screen footer.
_location_string = ""
_location_last_update = 0.0
_LOCATION_CACHE_SECS = 1800  # re-resolve at most every 30 minutes


# ---------------------------------------------------------
# PING STATE
# ---------------------------------------------------------
# Dict: host -> deque of (timestamp, latency_ms or None)
_ping_history = {}  # populated lazily from CONFIG ping_targets
_PING_HISTORY_LEN = 40


# ---------------------------------------------------------
# DEVICES STATE
# ---------------------------------------------------------
# Managed by web_config.py in-process; dashboard reads the shared dict.
# Imported lazily to avoid circular imports.
_devices_store = None  # set by web_config after import


def set_devices_store(store):
    """Called by web_config to share the in-memory device store."""
    global _devices_store
    _devices_store = store


# ---------------------------------------------------------
# NOTIFICATIONS STATE
# ---------------------------------------------------------
_notifications_store = None  # set by web_config after import


def set_notifications_store(store):
    """Called by web_config to share the in-memory notifications list."""
    global _notifications_store
    _notifications_store = store


# ---------------------------------------------------------
# SYSTEM DATA
# ---------------------------------------------------------

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.0.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def get_temp():
    try:
        with open(THERMAL_ZONE) as f:
            return float(f.read()) / 1000
    except Exception:
        return 0


def get_ram():
    data = {}

    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            data[k] = int(v.strip().split()[0])

    total = data["MemTotal"] / 1024 / 1024
    available = data["MemAvailable"] / 1024 / 1024
    used = total - available

    return used, total


def get_disk():
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free

    return used / 1024**3, total / 1024**3


def get_cpu():
    global last_cpu_total, last_cpu_idle

    with open("/proc/stat") as f:
        line = f.readline()

    values = list(map(int, line.split()[1:]))

    idle = values[3] + values[4]
    total = sum(values)

    if last_cpu_total == 0:
        last_cpu_total = total
        last_cpu_idle = idle
        return 0

    total_delta = total - last_cpu_total
    idle_delta = idle - last_cpu_idle

    last_cpu_total = total
    last_cpu_idle = idle

    if total_delta == 0:
        return 0

    return max(0, min(100, 100 * (1 - idle_delta / total_delta)))


def _find_fan_hwmon():
    """Locate the hwmon directory whose 'name' file is 'pwmfan'.

    This is resolved once and cached, since hwmon numbering (hwmon0,
    hwmon1, ...) is not guaranteed to stay the same across reboots.
    """

    global _fan_hwmon_path, _fan_hwmon_checked

    if _fan_hwmon_checked:
        return _fan_hwmon_path

    _fan_hwmon_checked = True

    try:
        for hw in glob.glob(os.path.join(HWMON_ROOT, "hwmon*")):
            name_path = os.path.join(hw, "name")

            if not os.path.exists(name_path):
                continue

            with open(name_path) as f:
                name = f.read().strip()

            if name == "pwmfan":
                fan_input = os.path.join(hw, "fan1_input")

                if os.path.exists(fan_input):
                    _fan_hwmon_path = fan_input
                    break

    except Exception:
        _fan_hwmon_path = None

    return _fan_hwmon_path


def get_fan_rpm():
    path = _find_fan_hwmon()

    if not path:
        return 0

    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return 0


# ---------------------------------------------------------
# WEATHER
# ---------------------------------------------------------

def update_weather():

    global weather_temp
    global weather_humidity
    global last_weather
    global weather_lat
    global weather_lon

    if time.time() - last_weather < 600:
        return

    try:
        weather_cfg = CONFIG.get("weather", {})
        mode = weather_cfg.get("mode", "auto")

        if mode == "manual":
            # Use coordinates supplied directly in config; skip IP geolocation.
            cfg_lat = weather_cfg.get("latitude")
            cfg_lon = weather_cfg.get("longitude")
            if cfg_lat is not None and cfg_lon is not None:
                weather_lat = float(cfg_lat)
                weather_lon = float(cfg_lon)
        else:
            # Auto mode: geolocate via ipwho.is when coords are unknown.
            if weather_lat is None:
                r = requests.get("https://ipwho.is/", timeout=5)
                d = r.json()
                weather_lat = d.get("latitude")
                weather_lon = d.get("longitude")

        if weather_lat is None:
            return

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={weather_lat}"
            f"&longitude={weather_lon}"
            "&current=temperature_2m,relative_humidity_2m"
        )

        r = requests.get(url, timeout=5)
        d = r.json()["current"]

        weather_temp = round(d["temperature_2m"], 1)
        weather_humidity = int(d["relative_humidity_2m"])

        last_weather = time.time()

    except Exception:
        pass


# ---------------------------------------------------------
# LOCATION
# ---------------------------------------------------------

def update_location():
    """Reverse-geocode the current weather lat/lon to a city/region
    string and cache it.  Re-runs at most every _LOCATION_CACHE_SECS.
    Falls back silently to empty string on any error."""

    global _location_string, _location_last_update

    loc_cfg = CONFIG.get("location", {})
    mode = loc_cfg.get("mode", "auto")

    if mode == "disabled":
        _location_string = ""
        return

    if mode == "manual":
        _location_string = str(loc_cfg.get("name", "")).strip()
        return

    # auto mode — reverse-geocode from weather coords
    now = time.time()
    if now - _location_last_update < _LOCATION_CACHE_SECS:
        return

    # Need lat/lon from weather state.
    if weather_lat is None:
        return

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": weather_lat,
                "lon": weather_lon,
                "format": "json",
            },
            headers={"User-Agent": "RPi5-MHS-Dashboard/1.0"},
            timeout=5,
        )
        d = r.json()
        addr = d.get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("municipality")
            or ""
        )
        state = addr.get("state", "")
        country_code = addr.get("country_code", "").upper()
        parts = [p for p in [city, state, country_code] if p]
        _location_string = ", ".join(parts[:2]) if parts else ""
        _location_last_update = now
    except Exception:
        pass


# ---------------------------------------------------------
# PING SAMPLER
# ---------------------------------------------------------

def _ping_host(host):
    """Ping once; return latency_ms (float) or None on timeout/error."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", host],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if "time=" in line:
                for part in line.split():
                    if part.startswith("time="):
                        return float(part[5:])
        return None
    except Exception:
        return None


def update_ping_samples():
    """Sample each configured ping target (non-blocking; runs in a
    background thread so a slow host does not stall the render loop)."""

    targets = CONFIG.get("ping_targets", [])

    def _sample():
        now = time.time()
        for tgt in targets:
            host = tgt.get("host", "")
            if not host:
                continue
            latency = _ping_host(host)
            hist = _ping_history.setdefault(host, [])
            hist.append((now, latency))
            if len(hist) > _PING_HISTORY_LEN:
                hist.pop(0)

    t = threading.Thread(target=_sample, daemon=True)
    t.start()

def centered(draw, text, font, y):
    aligned(draw, text, font, y, "center")


def aligned(draw, text, font, y, align="center", margin=12):
    """Draw ``text`` on one line, positioned per ``align``
    ("left"/"center"/"right"), matching the configurable clock/date
    alignment settings."""

    box = draw.textbbox((0, 0), text, font=font)
    text_w = box[2] - box[0]

    if align == "left":
        x = margin
    elif align == "right":
        x = W - margin - text_w
    else:
        x = (W - text_w) // 2

    draw.text(
        (x, y),
        text,
        font=font,
        fill=WHITE
    )


def labeled_block(draw, x, label, value, value_font_=None):
    """Draws a small LABEL / value stack used across both screens."""

    draw.text((x, 0), label, font=small_font, fill=SECONDARY)
    draw.text((x, 17), value, font=value_font_ or value_font, fill=WHITE)


def _anim_t(t):
    """Return an animation time value; returns a fixed constant when
    icon animations are disabled so all icons are frozen."""
    return t if ICON_ANIMATIONS_ENABLED else 0.0


def _draw_footer(draw, screen_id, default_text=""):
    """Draw the footer hint line for a screen, respecting per-screen
    footer_text and footer_enabled config fields."""
    screen_cfg = next(
        (s for s in CONFIG.get("screens", []) if s.get("id") == screen_id),
        None,
    )
    if screen_cfg is None:
        return
    if not screen_cfg.get("footer_enabled", True):
        return
    text = screen_cfg.get("footer_text") or default_text
    footer_align = CONFIG.get("alignment", {}).get("footer", "center")
    aligned(draw, text, small_font, 292, footer_align)


# ---------------------------------------------------------
# ICONS (all accept t = animation time in seconds)
# ---------------------------------------------------------

def icon_cpu(draw, x, y, t, load_pct=0):

    # subtle pulse on the outer body when load is high
    pulse = 1 if int(t * 3) % 2 == 0 and load_pct > 60 else 0
    width = 3 if pulse else 2

    draw.rectangle(
        (x+8, y+8, x+32, y+32),
        outline=WHITE,
        width=width
    )

    draw.rectangle(
        (x+14, y+14, x+26, y+26),
        outline=WHITE,
        width=2
    )

    # pins
    for p in [12, 20, 28]:

        draw.line((x+p, y+2, x+p, y+8), fill=WHITE, width=2)
        draw.line((x+p, y+32, x+p, y+38), fill=WHITE, width=2)

        draw.line((x+2, y+p, x+8, y+p), fill=WHITE, width=2)
        draw.line((x+32, y+p, x+38, y+p), fill=WHITE, width=2)

def icon_thermometer(draw, x, y, fill_ratio=0.5):
    """fill_ratio 0..1 controls how full the animated mercury column is."""

    fill_ratio = max(0.0, min(1.0, fill_ratio))

    draw.line(
        (x+20, y+5, x+20, y+27),
        fill=WHITE,
        width=4
    )

    draw.ellipse(
        (x+12, y+21, x+28, y+37),
        outline=WHITE,
        width=2
    )

    # animated mercury: fills from the bulb upward
    top = 27 - int(17 * fill_ratio)

    draw.line(
        (x+20, y+top, x+20, y+27),
        fill=WHITE,
        width=2
    )

    draw.ellipse(
        (x+15, y+24, x+25, y+34),
        fill=WHITE
    )


def icon_ram(draw, x, y, t):

    draw.rectangle(
        (x+3, y+12, x+37, y+29),
        outline=WHITE,
        width=2
    )

    for p in range(8, 37, 7):

        draw.line(
            (x+p, y+29, x+p, y+35),
            fill=WHITE,
            width=2
        )

    # chips blink in sequence, one "active" at a time
    active = int(t * 2) % 4
    chip_positions = list(range(8, 37, 7))

    for i, p in enumerate(chip_positions):

        if i == active:
            draw.rectangle(
                (x+p-1, y+16, x+p+4, y+24),
                fill=WHITE
            )
        else:
            draw.rectangle(
                (x+p, y+17, x+p+3, y+23),
                fill=WHITE
            )


def icon_disk(draw, x, y, t):

    draw.rounded_rectangle(
        (x+5, y+4, x+35, y+35),
        radius=3,
        outline=WHITE,
        width=2
    )

    draw.ellipse(
        (x+14, y+12, x+26, y+24),
        outline=WHITE,
        width=2
    )

    # spinning read head marker orbiting the platter hub
    angle = (t * 220) % 360
    rad = math.radians(angle)
    cx, cy, r = x+20, y+18, 4

    hx = cx + r * math.cos(rad)
    hy = cy + r * math.sin(rad)

    draw.ellipse((hx-1.5, hy-1.5, hx+1.5, hy+1.5), fill=WHITE)

    draw.line(
        (x+10, y+29, x+30, y+29),
        fill=WHITE,
        width=2
    )


def icon_drop(draw, x, y, t):

    # gentle bob up/down
    bob = int(2 * math.sin(t * 2))

    draw.polygon(
        [
            (x+20, y+2+bob),
            (x+8, y+20+bob),
            (x+8, y+27+bob),
            (x+12, y+34+bob),
            (x+20, y+38+bob),
            (x+28, y+34+bob),
            (x+32, y+27+bob),
            (x+32, y+20+bob)
        ],
        outline=WHITE
    )

    draw.arc(
        (x+14, y+22+bob, x+25, y+32+bob),
        0,
        180,
        fill=WHITE,
        width=2
    )


def icon_weather(draw, x, y, t):

    # sun
    draw.ellipse(
        (x+4, y+7, x+23, y+26),
        outline=WHITE,
        width=2
    )

    # rotating rays
    cx, cy, r1, r2 = x+13.5, y+16.5, 10, 15
    for i in range(8):
        angle = math.radians(i * 45 + t * 25)
        x1 = cx + r1 * math.cos(angle)
        y1 = cy + r1 * math.sin(angle)
        x2 = cx + r2 * math.cos(angle)
        y2 = cy + r2 * math.sin(angle)
        draw.line((x1, y1, x2, y2), fill=WHITE, width=2)

    # cloud
    draw.ellipse(
        (x+17, y+16, x+36, y+32),
        outline=WHITE,
        width=2
    )

    draw.rounded_rectangle(
        (x+12, y+24, x+38, y+34),
        radius=5,
        outline=WHITE,
        width=2
    )


def icon_network(draw, x, y, t):

    # router
    draw.rounded_rectangle(
        (x+2, y+23, x+38, y+34),
        radius=2,
        outline=WHITE,
        width=2
    )

    draw.ellipse(
        (x+9, y+27, x+12, y+30),
        fill=WHITE
    )

    draw.ellipse(
        (x+28, y+27, x+31, y+30),
        fill=WHITE
    )

    # wifi arcs pulse outward in sequence
    cycle = (t * 1.5) % 1.5
    outer_on = cycle < 1.0
    inner_on = 0.35 < cycle

    draw.arc(
        (x+10, y+5, x+30, y+25),
        210,
        330,
        fill=WHITE,
        width=3 if outer_on else 1
    )

    draw.arc(
        (x+14, y+10, x+26, y+22),
        210,
        330,
        fill=WHITE,
        width=3 if inner_on else 1
    )

    draw.ellipse(
        (x+19, y+18, x+21, y+20),
        fill=WHITE
    )


def icon_fan(draw, x, y, t, rpm=0):
    """Spinning cooler fan icon. Spin speed is tied to the real RPM
    reading (falls back to a slow idle spin when the fan is stopped
    or unreadable, so the icon never looks broken)."""

    cx, cy, r = x+20, y+20, 15

    draw.ellipse(
        (cx-r, cy-r, cx+r, cy+r),
        outline=WHITE,
        width=2
    )

    draw.ellipse(
        (cx-3, cy-3, cx+3, cy+3),
        fill=WHITE
    )

    spin_speed = rpm if rpm > 0 else 120  # deg/sec fallback idle spin
    angle0 = math.radians((t * spin_speed / 60 * 360) % 360)

    for i in range(3):
        a = angle0 + math.radians(i * 120)
        tip_x = cx + r * 0.85 * math.cos(a)
        tip_y = cy + r * 0.85 * math.sin(a)
        side_a = a + math.radians(28)
        side_x = cx + r * 0.35 * math.cos(side_a)
        side_y = cy + r * 0.35 * math.sin(side_a)

        draw.polygon(
            [(cx, cy), (side_x, side_y), (tip_x, tip_y)],
            fill=WHITE
        )


def icon_location(draw, x, y, t):
    """Animated GPS/location pin: the outer ring pulses outward."""

    cx, cy = x + 20, y + 16

    # pulsing outer ring
    pulse_r = 12 + int(3 * math.sin(t * 3))
    draw.ellipse(
        (cx - pulse_r, cy - pulse_r, cx + pulse_r, cy + pulse_r),
        outline=WHITE,
        width=1,
    )

    # pin body (teardrop)
    draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), outline=WHITE, width=2)
    draw.polygon(
        [(cx - 5, cy + 6), (cx + 5, cy + 6), (cx, cy + 18)],
        outline=WHITE,
    )
    # inner dot
    draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=WHITE)


def icon_alert(draw, x, y, t):
    """Warning triangle with a pulsing fill when active."""

    pulse = int(t * 2) % 2 == 0
    cx = x + 20

    draw.polygon(
        [(cx, y + 5), (x + 5, y + 35), (x + 35, y + 35)],
        outline=WHITE,
        width=2,
    )
    # exclamation mark
    draw.rectangle((cx - 1, y + 14, cx + 1, y + 27), fill=WHITE)
    if pulse:
        draw.ellipse((cx - 2, y + 30, cx + 2, y + 34), fill=WHITE)
    else:
        draw.ellipse((cx - 2, y + 30, cx + 2, y + 34), outline=WHITE, width=1)


# ---------------------------------------------------------
# FRAMEBUFFER (vectorised with numpy for speed at higher FPS)
# ---------------------------------------------------------

def _apply_orientation(image):
    """Rotate/transpose the composited frame per display.orientation config.

    Orientations:
      "normal"  -> 0° (no change)
      "flipped" -> 180° — image is simply rotated; size stays 480×320.
      "left"    -> 90° CCW — image is rotated without expand so the content
                   rotates inside the 480×320 canvas (corners are clipped;
                   a true portrait layout would require separate portrait-
                   resolution renderers, which is outside the scope here).
      "right"   -> 90° CW — same note as "left".
    """
    orientation = CONFIG.get("display", {}).get("orientation", "normal")

    if orientation == "flipped":
        return image.rotate(180)
    if orientation == "left":
        # 90° CCW, keeping the 480×320 canvas (content is rotated in-place).
        return image.rotate(90, expand=False)
    if orientation == "right":
        # 90° CW, keeping the 480×320 canvas.
        return image.rotate(-90, expand=False)
    return image


def write_fb(image):

    image = _apply_orientation(image)

    rgb = np.asarray(image.convert("RGB"), dtype=np.uint16)

    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]

    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    out = np.empty((rgb565.shape[0], rgb565.shape[1], 2), dtype=np.uint8)
    out[:, :, 0] = (rgb565 & 0xFF).astype(np.uint8)
    out[:, :, 1] = ((rgb565 >> 8) & 0xFF).astype(np.uint8)

    with open(FB, "wb") as f:
        f.write(out.tobytes())


# ---------------------------------------------------------
# SCREEN 0: CLOCK / WEATHER / FAN / IP
# ---------------------------------------------------------

def draw_screen_clock(t, sysdata):
    """Screen 1: Time, Day, Date, Weather (temperature + humidity) only."""

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    at = _anim_t(t)
    now = datetime.now()

    aligned(draw, now.strftime("%I:%M:%S %p"), clock_font, 34, CONFIG["alignment"]["clock"])
    aligned(draw, now.strftime("%A, %d %b %Y").upper(), date_font, 100, CONFIG["alignment"]["date"])

    draw.line((12, 140, W-12, 140), fill=WHITE, width=1)

    # WEATHER | HUMIDITY row, given extra room now that this screen
    # only carries clock + weather
    icon_weather(draw, 40, 168, at)
    draw.text((92, 172), "WEATHER", font=small_font, fill=SECONDARY)
    draw.text((92, 191), f"{weather_temp}°C", font=big_value_font, fill=WHITE)

    draw.line((240, 158, 240, 238), fill=WHITE, width=1)

    icon_drop(draw, 270, 168, at)
    draw.text((322, 172), "HUMIDITY", font=small_font, fill=SECONDARY)
    draw.text((322, 191), f"{weather_humidity}%", font=big_value_font, fill=WHITE)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)

    # Footer: show resolved location (if enabled) with animated pin icon,
    # otherwise fall back to the per-screen footer_text config.
    loc_cfg = CONFIG.get("location", {})
    loc_mode = loc_cfg.get("mode", "disabled")
    if loc_mode != "disabled" and _location_string:
        icon_location(draw, 4, 258, at)
        draw.text((48, 264), _location_string[:30], font=small_font, fill=SECONDARY)
    else:
        _draw_footer(draw, "clock", "SYSTEM STATUS NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 1: CPU / TEMP / RAM / DISK STATUS
# ---------------------------------------------------------

def draw_screen_system(t, sysdata):

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    at = _anim_t(t)
    cpu = sysdata["cpu"]
    temp = sysdata["temp"]
    ram_used, ram_total = sysdata["ram"]
    disk_used, disk_total = sysdata["disk"]
    ip = sysdata["ip"]

    now = datetime.now()

    draw.text((16, 14), "SYSTEM STATUS", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)

    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    # =====================================================
    # FOUR SYSTEM COLUMNS
    # =====================================================

    icon_cpu(draw, 34, 55, at, cpu)
    draw.text((37, 89), "CPU", font=small_font, fill=SECONDARY)
    draw.text((43, 106), f"{cpu:.0f}%", font=value_font, fill=WHITE)

    global displayed_temp
    displayed_temp += (temp - displayed_temp) * 0.15
    icon_thermometer(draw, 155, 55, displayed_temp / 100)
    draw.text((145, 89), "TEMP", font=small_font, fill=SECONDARY)
    draw.text((132, 106), f"{temp:.1f}°C", font=value_font, fill=WHITE)

    icon_ram(draw, 276, 55, at)
    draw.text((282, 89), "RAM", font=small_font, fill=SECONDARY)
    draw.text((264, 106), f"{ram_used:.1f}/{ram_total:.0f}GB", font=value_font, fill=WHITE)

    icon_disk(draw, 397, 55, at)
    draw.text((401, 89), "DISK", font=small_font, fill=SECONDARY)
    draw.text((389, 106), f"{disk_used:.0f}/{disk_total:.0f}GB", font=value_font, fill=WHITE)

    # dividers
    draw.line((120, 53, 120, 160), fill=WHITE)
    draw.line((240, 53, 240, 160), fill=WHITE)
    draw.line((360, 53, 360, 160), fill=WHITE)

    # =====================================================
    # PROGRESS BARS
    # =====================================================

    draw.rectangle((20, 137, 105, 144), outline=WHITE)
    draw.rectangle((22, 139, 22 + int(81 * cpu / 100), 142), fill=WHITE)

    temp_pct = min(max((temp / 100), 0), 1)
    draw.rectangle((138, 137, 223, 144), outline=WHITE)
    draw.rectangle((140, 139, 140 + int(81 * temp_pct), 142), fill=WHITE)

    ram_pct = ram_used / ram_total if ram_total else 0
    draw.rectangle((258, 137, 343, 144), outline=WHITE)
    draw.rectangle((260, 139, 260 + int(81 * ram_pct), 142), fill=WHITE)

    disk_pct = disk_used / disk_total if disk_total else 0
    draw.rectangle((378, 137, 463, 144), outline=WHITE)
    draw.rectangle((380, 139, 380 + int(81 * disk_pct), 142), fill=WHITE)

    draw.line((12, 160, W-12, 160), fill=WHITE, width=1)

    # =====================================================
    # FAN | LAN IP row
    # =====================================================

    icon_fan(draw, 25, 170, at, sysdata["fan_rpm"])
    draw.text((68, 172), "FAN SPEED", font=small_font, fill=SECONDARY)
    fan_txt = f"{sysdata['fan_rpm']} RPM" if sysdata["fan_rpm"] else "STOPPED"
    draw.text((68, 189), fan_txt, font=value_font, fill=WHITE)

    draw.line((240, 167, 240, 212), fill=WHITE, width=1)

    icon_network(draw, 258, 170, at)
    draw.text((304, 172), "LAN IP", font=small_font, fill=SECONDARY)
    draw.text((304, 189), ip, font=ip_font, fill=WHITE)

    draw.line((12, 219, W-12, 219), fill=WHITE, width=1)

    _draw_footer(draw, "system", "CLOCK & WEATHER NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 2: NETWORK INFO
# ---------------------------------------------------------

def _get_wifi_ssid():
    """Return the connected Wi-Fi SSID string, or None if not available."""
    try:
        result = subprocess.run(
            ["iwgetid", "-r"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        ssid = result.stdout.strip()
        return ssid if ssid else None
    except Exception:
        return None


def _get_uptime():
    """Return system uptime as a human-readable string."""
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        if days:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        return f"{hours:02d}h {minutes:02d}m"
    except Exception:
        return "--"


def draw_screen_network(t, sysdata):
    """Screen 2: Hostname, LAN IP, Wi-Fi SSID, uptime."""

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    at = _anim_t(t)
    now = datetime.now()

    draw.text((16, 14), "NETWORK INFO", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)

    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    # Hostname
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "--"

    icon_network(draw, 20, 55, at)
    draw.text((75, 58), "HOSTNAME", font=small_font, fill=SECONDARY)
    draw.text((75, 75), hostname, font=ip_font, fill=WHITE)

    draw.line((12, 110, W-12, 110), fill=WHITE, width=1)

    # LAN IP
    draw.text((20, 120), "LAN IP", font=small_font, fill=SECONDARY)
    draw.text((20, 138), sysdata["ip"], font=big_value_font, fill=WHITE)

    draw.line((240, 115, 240, 175), fill=WHITE, width=1)

    # Wi-Fi SSID
    ssid = _get_wifi_ssid() or "Not connected"
    draw.text((260, 120), "WI-FI SSID", font=small_font, fill=SECONDARY)
    draw.text((260, 138), ssid[:16], font=ip_font, fill=WHITE)

    draw.line((12, 185, W-12, 185), fill=WHITE, width=1)

    # Uptime (left column)
    draw.text((20, 195), "UPTIME", font=small_font, fill=SECONDARY)
    draw.text((20, 213), _get_uptime(), font=big_value_font, fill=WHITE)

    # Tailscale peer summary (right column)
    # Shows compact "N/M online" count so the network screen is the natural
    # home for it without overcrowding. Full peer list is on the devices screen.
    ts = tailscale_status.get_status()
    draw.line((240, 190, 240, 245), fill=WHITE, width=1)
    draw.text((260, 195), "TAILSCALE", font=small_font, fill=SECONDARY)
    if ts["available"]:
        draw.text((260, 213), f"{ts['online_count']}/{ts['total_count']} online",
                  font=ip_font, fill=WHITE)
    else:
        draw.text((260, 213), "Not running", font=small_font, fill=SECONDARY)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)

    _draw_footer(draw, "network", "SYSTEM STATUS NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 3: CPU STATS & PROCESSES
# ---------------------------------------------------------

# Ring buffer for CPU history sparkline (last 40 samples at ~1 s each).
_cpu_history = [0.0] * 40


def _update_cpu_history(cpu_pct):
    _cpu_history.append(cpu_pct)
    if len(_cpu_history) > 40:
        _cpu_history.pop(0)


def _get_top_process():
    """Return (name, cpu%) for the top CPU-consuming process."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "comm,%cpu", "--sort=-%cpu", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        line = result.stdout.splitlines()[0].strip()
        parts = line.rsplit(None, 1)
        name = parts[0][:14] if parts else "--"
        pct = float(parts[1]) if len(parts) > 1 else 0.0
        return name, pct
    except Exception:
        return "--", 0.0


def _get_swap():
    """Return (used_GB, total_GB) for swap."""
    try:
        data = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                data[k.strip()] = int(v.strip().split()[0])
        total = data.get("SwapTotal", 0) / 1024 / 1024
        free = data.get("SwapFree", 0) / 1024 / 1024
        return total - free, total
    except Exception:
        return 0.0, 0.0


def draw_screen_stats(t, sysdata):
    """Screen 3: CPU history sparkline, top process, swap usage."""

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    now = datetime.now()
    cpu = sysdata["cpu"]
    _update_cpu_history(cpu)

    draw.text((16, 14), "CPU STATS", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)

    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    # ---- Sparkline ----
    spark_x0, spark_y0 = 16, 105
    spark_w, spark_h = W - 32, 55
    draw.text((16, 48), "CPU HISTORY", font=small_font, fill=SECONDARY)
    draw.rectangle((spark_x0, spark_y0, spark_x0 + spark_w, spark_y0 + spark_h), outline=WHITE)

    samples = _cpu_history[-40:]
    if len(samples) > 1:
        step = spark_w / (len(samples) - 1)
        pts = []
        for i, v in enumerate(samples):
            sx = spark_x0 + int(i * step)
            sy = spark_y0 + spark_h - int(spark_h * v / 100)
            pts.append((sx, sy))
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=WHITE, width=2)

    draw.text((spark_x0 + spark_w - 50, 48), f"{cpu:.0f}%", font=value_font, fill=WHITE)

    draw.line((12, 170, W-12, 170), fill=WHITE, width=1)

    # ---- Top process ----
    proc_name, proc_cpu = _get_top_process()
    draw.text((16, 178), "TOP PROCESS", font=small_font, fill=SECONDARY)
    draw.text((16, 196), proc_name, font=ip_font, fill=WHITE)
    draw.text((260, 178), "CPU", font=small_font, fill=SECONDARY)
    draw.text((260, 196), f"{proc_cpu:.1f}%", font=big_value_font, fill=WHITE)

    draw.line((12, 222, W-12, 222), fill=WHITE, width=1)

    # ---- Swap ----
    swap_used, swap_total = _get_swap()
    draw.text((16, 230), "SWAP", font=small_font, fill=SECONDARY)
    if swap_total > 0:
        draw.text((16, 248), f"{swap_used:.1f}/{swap_total:.1f}GB", font=ip_font, fill=WHITE)
        swap_pct = swap_used / swap_total
        draw.rectangle((16, 268, W - 16, 275), outline=WHITE)
        draw.rectangle((18, 270, 18 + int((W - 36) * swap_pct), 273), fill=WHITE)
    else:
        draw.text((16, 248), "No swap", font=ip_font, fill=SECONDARY)

    _draw_footer(draw, "stats", "CLOCK & WEATHER NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 4: REMOTE DEVICES
# ---------------------------------------------------------

def _mini_bar(draw, x, y, w, h, pct, fill_color=None):
    """Draw a small progress bar at (x,y) of size w×h."""
    draw.rectangle((x, y, x + w, y + h), outline=WHITE)
    filled = max(0, min(w - 2, int((w - 2) * pct)))
    if filled:
        draw.rectangle((x + 1, y + 1, x + 1 + filled, y + h - 1),
                       fill=fill_color or WHITE)


def _device_type_label(dtype):
    """Short label for device type shown next to device name."""
    return {"linux": "[L]", "windows": "[W]", "android": "[A]"}.get(str(dtype).lower(), "[?]")


def draw_screen_devices(t, sysdata):
    """Screen 4: Remote device stats + Tailscale peer list + Proxmox backup summary.

    Remote devices are sourced from the shared in-memory store populated by
    web_config.py's POST /api/devices/report endpoint.

    Proxmox backup status is shown as a compact banner at the bottom of the
    devices list — it is the natural home since Proxmox is itself a remote
    device/hypervisor being monitored.
    """

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    at = _anim_t(t)
    now = datetime.now()
    ALERT = cfgmod.hex_to_rgb(CONFIG.get("theme", {}).get("alert_color", "#FF3333"))
    WARN  = cfgmod.hex_to_rgb(CONFIG.get("theme", {}).get("warn_color",  "#FFAA00"))

    draw.text((16, 14), "REMOTE DEVICES", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)
    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    devices = list((_devices_store or {}).values())
    offline_threshold = CONFIG.get("alerts", {}).get("device_offline_s", 90)

    if not devices:
        draw.text((20, 60), "No devices reporting.", font=ip_font, fill=SECONDARY)
        draw.text((20, 80), "Run agents/linux_report.sh or", font=small_font, fill=SECONDARY)
        draw.text((20, 95), "agents/windows_report.ps1 on", font=small_font, fill=SECONDARY)
        draw.text((20, 110), "remote machines.", font=small_font, fill=SECONDARY)
    else:
        now_ts = time.time()
        row_h = 38
        max_rows = 4
        for i, dev in enumerate(devices[:max_rows]):
            y0 = 48 + i * row_h
            online = (now_ts - dev.get("last_seen", 0)) < offline_threshold
            status_color = WHITE if online else ALERT
            type_label = _device_type_label(dev.get("type", ""))
            name = (dev.get("name") or dev.get("device_id", "?"))[:14]
            draw.text((12, y0), f"{type_label} {name}", font=small_font, fill=status_color)
            draw.text((12, y0 + 13), "ON" if online else "OFF",
                      font=small_font, fill=status_color)

            cpu_pct  = (dev.get("cpu",  0) or 0) / 100
            ram_used  = dev.get("ram_used",  0) or 0
            ram_total = dev.get("ram_total", 1) or 1
            disk_used  = dev.get("disk_used",  0) or 0
            disk_total = dev.get("disk_total", 1) or 1

            # Mini bar: CPU
            draw.text((130, y0), "CPU", font=small_font, fill=SECONDARY)
            _mini_bar(draw, 160, y0 + 2, 70, 10, cpu_pct)

            # Mini bar: RAM
            draw.text((245, y0), "RAM", font=small_font, fill=SECONDARY)
            _mini_bar(draw, 275, y0 + 2, 70, 10, ram_used / ram_total)

            # Mini bar: Disk
            draw.text((360, y0), "DSK", font=small_font, fill=SECONDARY)
            _mini_bar(draw, 388, y0 + 2, 70, 10, disk_used / disk_total)

            if i < max_rows - 1:
                draw.line((12, y0 + row_h - 2, W-12, y0 + row_h - 2),
                          fill=SECONDARY, width=1)

        if len(devices) > max_rows:
            draw.text((12, 48 + max_rows * row_h),
                      f"+{len(devices)-max_rows} more", font=small_font, fill=SECONDARY)

    # ---- Proxmox backup banner ----------------------------------------
    # Shown at the bottom of this screen; see proxmox_status.py for details.
    prx_cfg = CONFIG.get("proxmox", {})
    if prx_cfg.get("enabled"):
        bk = proxmox_status.get_backups(prx_cfg)
        draw.line((12, 218, W-12, 218), fill=WHITE, width=1)
        if bk["available"] and bk["backups"]:
            b = bk["backups"][0]
            age_s = bk.get("last_ok_age_s")
            age_str = ""
            if age_s is not None:
                if age_s < 3600:
                    age_str = f"{int(age_s/60)}m ago"
                elif age_s < 86400:
                    age_str = f"{int(age_s/3600)}h ago"
                else:
                    age_str = f"{int(age_s/86400)}d ago"
            status_txt = b.get("status", "?")
            color = WHITE
            staleness_h = prx_cfg.get("staleness_hours", 24)
            if bk["any_failed"] or (age_s is not None and age_s > staleness_h * 3600):
                color = WARN
            draw.text((12, 222), "BACKUP", font=small_font, fill=SECONDARY)
            draw.text((80, 222), f"{status_txt}  {age_str}",
                      font=small_font, fill=color)
        else:
            draw.text((12, 222), "BACKUP", font=small_font, fill=SECONDARY)
            draw.text((80, 222), bk.get("error") or "No data", font=small_font, fill=SECONDARY)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)
    _draw_footer(draw, "devices", "CLOCK & WEATHER NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 5: PING / LATENCY GRAPH
# ---------------------------------------------------------

def draw_screen_ping(t, sysdata):
    """Screen 5: Sparkline latency graph per configured ping target."""

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    now = datetime.now()
    ALERT = cfgmod.hex_to_rgb(CONFIG.get("theme", {}).get("alert_color", "#FF3333"))

    draw.text((16, 14), "PING / LATENCY", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)
    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    targets = CONFIG.get("ping_targets", [])

    if not targets:
        draw.text((20, 60), "No ping targets configured.", font=ip_font, fill=SECONDARY)
        draw.text((20, 78), "Add targets in the web config.", font=small_font, fill=SECONDARY)
        _draw_footer(draw, "ping", "CLOCK & WEATHER NEXT")
        return image

    # Layout: up to 3 targets, each in a horizontal band ~80px tall.
    band_h = min(70, (280 - 50) // min(len(targets), 3))
    spark_w = W - 32

    for i, tgt in enumerate(targets[:3]):
        host  = tgt.get("host", "")
        label = tgt.get("label", host)[:14]
        hist  = _ping_history.get(host, [])

        y0 = 48 + i * (band_h + 8)
        spark_y = y0 + 16
        spark_h = band_h - 20

        # Latest latency value
        last_ms = None
        if hist:
            _, last_ms = hist[-1]
        val_str = f"{last_ms:.1f}ms" if last_ms is not None else "TIMEOUT"
        val_color = ALERT if last_ms is None else WHITE

        draw.text((12, y0), label, font=small_font, fill=SECONDARY)
        draw.text((W - 100, y0), val_str, font=small_font, fill=val_color)

        # Sparkline box
        draw.rectangle((12, spark_y, 12 + spark_w, spark_y + spark_h), outline=WHITE)

        if len(hist) > 1:
            samples = hist[-40:]
            # Scale: max latency visible = 500ms; anything above clips.
            max_ms = max((v for _, v in samples if v is not None), default=500) or 500
            max_ms = max(max_ms, 10)
            step = spark_w / (len(samples) - 1)
            pts = []
            for j, (_, v) in enumerate(samples):
                sx = 12 + int(j * step)
                if v is None:
                    pts.append(None)
                else:
                    sy = spark_y + spark_h - int(spark_h * min(v, max_ms) / max_ms)
                    pts.append((sx, sy))
            # Draw segments, skipping across None (timeout) gaps
            for j in range(len(pts) - 1):
                if pts[j] is None:
                    # Red dot for timeout
                    bx = 12 + int(j * step)
                    draw.ellipse((bx - 2, spark_y + spark_h // 2 - 2,
                                  bx + 2, spark_y + spark_h // 2 + 2),
                                 fill=ALERT)
                elif pts[j + 1] is not None:
                    draw.line([pts[j], pts[j + 1]], fill=WHITE, width=2)

        if i < min(len(targets), 3) - 1:
            draw.line((12, y0 + band_h + 4, W-12, y0 + band_h + 4),
                      fill=SECONDARY, width=1)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)
    _draw_footer(draw, "ping", "CLOCK & WEATHER NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 6: ALERTS
# ---------------------------------------------------------

def _collect_alerts(sysdata):
    """Return a list of (severity, message) tuples for current alert conditions.

    severity is 'alert' (critical) or 'warn' (warning).
    Conditions checked:
      - Remote devices offline
      - Proxmox backup failed/stale
      - Local CPU/temp/disk thresholds
      - Ping target timeouts
    """
    alerts_cfg = CONFIG.get("alerts", {})
    offline_threshold = alerts_cfg.get("device_offline_s", 90)
    cpu_warn    = alerts_cfg.get("cpu_warn_pct",  85)
    temp_warn   = alerts_cfg.get("temp_warn_c",   75)
    disk_warn   = alerts_cfg.get("disk_warn_pct", 90)
    now_ts = time.time()

    items = []

    # Remote devices
    for dev in list((_devices_store or {}).values()):
        age = now_ts - dev.get("last_seen", 0)
        if age >= offline_threshold:
            name = dev.get("name") or dev.get("device_id", "?")
            items.append(("alert", f"DEVICE OFFLINE: {name[:18]}"))

    # Proxmox backup
    prx_cfg = CONFIG.get("proxmox", {})
    if prx_cfg.get("enabled"):
        bk = proxmox_status.get_backups(prx_cfg)
        if bk["available"]:
            staleness_s = prx_cfg.get("staleness_hours", 24) * 3600
            if bk["any_failed"]:
                items.append(("alert", "PROXMOX BACKUP FAILED"))
            elif bk.get("last_ok_age_s") is not None and bk["last_ok_age_s"] > staleness_s:
                age_h = int(bk["last_ok_age_s"] / 3600)
                items.append(("warn", f"BACKUP STALE: {age_h}h ago"))

    # Local CPU
    cpu = sysdata.get("cpu", 0)
    if cpu >= cpu_warn:
        items.append(("warn", f"HIGH CPU: {cpu:.0f}%"))

    # Local temp
    temp = sysdata.get("temp", 0)
    if temp >= temp_warn:
        items.append(("warn", f"HIGH TEMP: {temp:.1f}°C"))

    # Local disk
    disk_used, disk_total = sysdata.get("disk", (0, 1))
    disk_pct = 100 * disk_used / disk_total if disk_total else 0
    if disk_pct >= disk_warn:
        items.append(("warn", f"DISK FULL: {disk_pct:.0f}%"))

    # Ping timeouts
    for tgt in CONFIG.get("ping_targets", []):
        host = tgt.get("host", "")
        hist = _ping_history.get(host, [])
        if hist:
            _, last_ms = hist[-1]
            if last_ms is None:
                label = tgt.get("label", host)[:14]
                items.append(("warn", f"PING FAIL: {label}"))

    return items


def draw_screen_alerts(t, sysdata):
    """Screen 6: Aggregated system alerts with severity colour coding."""

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    at = _anim_t(t)
    now = datetime.now()
    ALERT = cfgmod.hex_to_rgb(CONFIG.get("theme", {}).get("alert_color", "#FF3333"))
    WARN  = cfgmod.hex_to_rgb(CONFIG.get("theme", {}).get("warn_color",  "#FFAA00"))

    draw.text((16, 14), "ALERTS", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)
    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    items = _collect_alerts(sysdata)

    if not items:
        icon_network(draw, 210, 100, at)
        draw.text((20, 90), "All systems normal.", font=ip_font, fill=WHITE)
        draw.text((20, 112), "No active alerts.", font=small_font, fill=SECONDARY)
    else:
        icon_alert(draw, W - 44, 8, at)
        # Cap to the number of lines that fit in 320px (header=40, footer=68)
        max_lines = 8
        line_h = 26
        visible = items[:max_lines]
        for idx, (sev, msg) in enumerate(visible):
            y = 48 + idx * line_h
            color = ALERT if sev == "alert" else WARN
            prefix = "! " if sev == "alert" else "~ "
            draw.text((12, y), prefix + msg[:36], font=small_font, fill=color)
        if len(items) > max_lines:
            extra = len(items) - max_lines
            draw.text((12, 48 + max_lines * line_h),
                      f"+{extra} more", font=small_font, fill=SECONDARY)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)
    _draw_footer(draw, "alerts", "CLOCK & WEATHER NEXT")

    return image


# ---------------------------------------------------------
# SCREEN 7: NOTIFICATIONS
# ---------------------------------------------------------

def _relative_time(ts):
    """Return a human-readable relative time string like '5m ago'."""
    if not ts:
        return ""
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff/60)}m ago"
    if diff < 86400:
        return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"


def draw_screen_notifications(t, sysdata):
    """Screen 7: Most-recent 4 notifications from the inbox.

    Note: Populating this inbox requires a companion app on Android
    (NotificationListenerService) or Windows (UserNotificationListener)
    that POSTs to /api/notifications.  Those companion apps are out of
    scope for this implementation — this builds the receiving/display side.
    """

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

    now = datetime.now()

    draw.text((16, 14), "NOTIFICATIONS", font=title_font, fill=WHITE)
    draw.text((W-16-70, 14), now.strftime("%H:%M:%S"), font=title_font, fill=WHITE)
    draw.line((12, 40, W-12, 40), fill=WHITE, width=1)

    notifs = list(_notifications_store or [])

    if not notifs:
        draw.text((20, 80), "No notifications.", font=ip_font, fill=SECONDARY)
        draw.text((20, 100), "Requires a companion app", font=small_font, fill=SECONDARY)
        draw.text((20, 116), "on Android or Windows.", font=small_font, fill=SECONDARY)
    else:
        visible = notifs[:4]  # show 4 most recent
        row_h = 50
        for i, n in enumerate(visible):
            y0 = 48 + i * row_h
            app   = (n.get("app") or n.get("device_id") or "")[:10]
            title = (n.get("title") or "")[:28]
            body  = (n.get("body") or "")[:34]
            ts    = n.get("timestamp")
            rel   = _relative_time(ts)

            draw.text((12, y0),      f"[{app}] {title}", font=small_font, fill=WHITE)
            draw.text((12, y0 + 14), body,               font=small_font, fill=SECONDARY)
            draw.text((W - 80, y0),  rel,                font=small_font, fill=SECONDARY)
            if i < len(visible) - 1:
                draw.line((12, y0 + row_h - 4, W-12, y0 + row_h - 4),
                          fill=SECONDARY, width=1)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)
    _draw_footer(draw, "notifications", "CLOCK & WEATHER NEXT")

    return image


SCREEN_RENDERER_MAP = {
    "clock":         draw_screen_clock,
    "system":        draw_screen_system,
    "network":       draw_screen_network,
    "stats":         draw_screen_stats,
    "devices":       draw_screen_devices,
    "ping":          draw_screen_ping,
    "alerts":        draw_screen_alerts,
    "notifications": draw_screen_notifications,
}

apply_config(CONFIG)


def collect_sysdata():
    return {
        "cpu": get_cpu(),
        "temp": get_temp(),
        "ram": get_ram(),
        "disk": get_disk(),
        "ip": get_ip(),
        "fan_rpm": get_fan_rpm(),
    }


def render_screen(idx, t, sysdata):
    return SCREEN_RENDERERS[idx](t, sysdata)


def render_transition(a_idx, b_idx, prog, t, sysdata):
    """Horizontal slide: screen A slides out to the left while
    screen B slides in from the right."""

    img_a = render_screen(a_idx, t, sysdata)
    img_b = render_screen(b_idx, t, sysdata)

    canvas = Image.new("RGB", (W, H), BLACK)

    # ease-out for a slightly nicer feel than linear
    eased = 1 - (1 - prog) ** 2
    offset = int(W * eased)

    canvas.paste(img_a, (-offset, 0))
    canvas.paste(img_b, (W - offset, 0))

    return canvas


# ---------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------

def main():

    current_screen = 0
    screen_start = time.time()
    transitioning = False
    transition_start = 0.0
    sysdata = collect_sysdata()
    last_sysdata_refresh = time.time()

    while True:

        loop_start = time.time()
        now = loop_start

        try:
            # refresh weather + system stats about once a second;
            # no need to hit /proc, statvfs etc. at 15fps
            if now - last_sysdata_refresh >= 1.0:
                update_weather()
                update_location()
                update_ping_samples()
                sysdata = collect_sysdata()
                last_sysdata_refresh = now

                # pick up any changes saved from the web config UI
                reload_config_if_changed()
                current_screen %= NUM_SCREENS

            elapsed = now - screen_start

            if not transitioning and elapsed >= SCREEN_DURATION:
                transitioning = True
                transition_start = now

            if transitioning and not TRANSITIONS_ENABLED:
                transitioning = False
                current_screen = (current_screen + 1) % NUM_SCREENS
                screen_start = now
                frame = render_screen(current_screen, now, sysdata)
            elif transitioning:
                prog = (now - transition_start) / TRANSITION_DURATION

                if prog >= 1.0:
                    transitioning = False
                    current_screen = (current_screen + 1) % NUM_SCREENS
                    screen_start = now
                    frame = render_screen(current_screen, now, sysdata)
                else:
                    next_screen = (current_screen + 1) % NUM_SCREENS
                    frame = render_transition(
                        current_screen, next_screen, prog, now, sysdata
                    )
            else:
                frame = render_screen(current_screen, now, sysdata)

            write_fb(frame)

        except Exception as e:
            print("Dashboard error:", e)

        sleep_left = FRAME_INTERVAL - (time.time() - loop_start)

        if sleep_left > 0:
            time.sleep(sleep_left)


if __name__ == "__main__":
    main()
