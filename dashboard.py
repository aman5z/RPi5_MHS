#!/usr/bin/env python3

import os
import glob
import time
import math
import socket
import requests
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

import numpy as np

import dashboard_config as cfgmod

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

FPS = CONFIG["timing"]["fps"]
FRAME_INTERVAL = 1.0 / FPS
SCREEN_DURATION = CONFIG["timing"]["screen_duration"]
TRANSITION_DURATION = CONFIG["timing"]["transition_duration"]
TRANSITIONS_ENABLED = CONFIG["timing"]["transitions_enabled"]

_font_cache = {}


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

    global CONFIG, WHITE, BLACK, FPS, FRAME_INTERVAL
    global SCREEN_DURATION, TRANSITION_DURATION, TRANSITIONS_ENABLED
    global clock_font, date_font, value_font, big_value_font
    global small_font, ip_font, title_font
    global SCREEN_RENDERERS, NUM_SCREENS

    CONFIG = cfg

    WHITE = cfgmod.hex_to_rgb(cfg["theme"]["foreground"])
    BLACK = cfgmod.hex_to_rgb(cfg["theme"]["background"])

    timing = cfg["timing"]
    FPS = timing["fps"]
    FRAME_INTERVAL = 1.0 / FPS
    SCREEN_DURATION = timing["screen_duration"]
    TRANSITION_DURATION = timing["transition_duration"]
    TRANSITIONS_ENABLED = timing["transitions_enabled"]

    fonts = cfg["fonts"]
    font_regular, font_bold = cfgmod.resolve_font_paths(fonts["family"])

    clock_font = get_font(font_bold, fonts["clock_size"])
    date_font = get_font(font_bold, fonts["date_size"])
    value_font = get_font(font_bold, fonts["value_size"])
    big_value_font = get_font(font_bold, fonts["big_value_size"])
    small_font = get_font(font_regular, fonts["small_size"])
    ip_font = get_font(font_bold, fonts["ip_size"])
    title_font = get_font(font_bold, fonts["title_size"])

    enabled_ids = [s["id"] for s in cfg["screens"] if s["enabled"]]

    if not enabled_ids:
        enabled_ids = ["clock"]

    SCREEN_RENDERERS = [SCREEN_RENDERER_MAP[sid] for sid in enabled_ids if sid in SCREEN_RENDERER_MAP]

    if not SCREEN_RENDERERS:
        SCREEN_RENDERERS = [draw_screen_clock]

    NUM_SCREENS = len(SCREEN_RENDERERS)


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

        if weather_lat is None:

            r = requests.get(
                "https://ipwho.is/",
                timeout=5
            )

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
# TEXT HELPERS
# ---------------------------------------------------------

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

    draw.text((x, 0), label, font=small_font, fill=WHITE)
    draw.text((x, 17), value, font=value_font_ or value_font, fill=WHITE)


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


# ---------------------------------------------------------
# FRAMEBUFFER (vectorised with numpy for speed at higher FPS)
# ---------------------------------------------------------

def write_fb(image):

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

    now = datetime.now()

    aligned(draw, now.strftime("%I:%M:%S %p"), clock_font, 34, CONFIG["alignment"]["clock"])
    aligned(draw, now.strftime("%A, %d %b %Y").upper(), date_font, 100, CONFIG["alignment"]["date"])

    draw.line((12, 140, W-12, 140), fill=WHITE, width=1)

    # WEATHER | HUMIDITY row, given extra room now that this screen
    # only carries clock + weather
    icon_weather(draw, 40, 168, t)
    draw.text((92, 172), "WEATHER", font=small_font, fill=WHITE)
    draw.text((92, 191), f"{weather_temp}°C", font=big_value_font, fill=WHITE)

    draw.line((240, 158, 240, 238), fill=WHITE, width=1)

    icon_drop(draw, 270, 168, t)
    draw.text((322, 172), "HUMIDITY", font=small_font, fill=WHITE)
    draw.text((322, 191), f"{weather_humidity}%", font=big_value_font, fill=WHITE)

    draw.line((12, 252, W-12, 252), fill=WHITE, width=1)

    centered(draw, "SYSTEM STATUS NEXT", small_font, 292)

    return image


# ---------------------------------------------------------
# SCREEN 1: CPU / TEMP / RAM / DISK STATUS
# ---------------------------------------------------------

def draw_screen_system(t, sysdata):

    image = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(image)

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

    icon_cpu(draw, 34, 55, t, cpu)
    draw.text((37, 89), "CPU", font=small_font, fill=WHITE)
    draw.text((43, 106), f"{cpu:.0f}%", font=value_font, fill=WHITE)

    global displayed_temp
    displayed_temp += (temp - displayed_temp) * 0.15
    icon_thermometer(draw, 155, 55, displayed_temp / 100)
    draw.text((145, 89), "TEMP", font=small_font, fill=WHITE)
    draw.text((132, 106), f"{temp:.1f}°C", font=value_font, fill=WHITE)

    icon_ram(draw, 276, 55, t)
    draw.text((282, 89), "RAM", font=small_font, fill=WHITE)
    draw.text((264, 106), f"{ram_used:.1f}/{ram_total:.0f}GB", font=value_font, fill=WHITE)

    icon_disk(draw, 397, 55, t)
    draw.text((401, 89), "DISK", font=small_font, fill=WHITE)
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

    icon_fan(draw, 25, 170, t, sysdata["fan_rpm"])
    draw.text((68, 172), "FAN SPEED", font=small_font, fill=WHITE)
    fan_txt = f"{sysdata['fan_rpm']} RPM" if sysdata["fan_rpm"] else "STOPPED"
    draw.text((68, 189), fan_txt, font=value_font, fill=WHITE)

    draw.line((240, 167, 240, 212), fill=WHITE, width=1)

    icon_network(draw, 258, 170, t)
    draw.text((304, 172), "LAN IP", font=small_font, fill=WHITE)
    draw.text((304, 189), ip, font=ip_font, fill=WHITE)

    draw.line((12, 219, W-12, 219), fill=WHITE, width=1)

    centered(draw, "CLOCK & WEATHER NEXT", small_font, 296)

    return image


SCREEN_RENDERER_MAP = {
    "clock": draw_screen_clock,
    "system": draw_screen_system,
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
