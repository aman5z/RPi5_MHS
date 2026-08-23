# RPi5_MHS
Custom Display Pages for Raspberry Pi 5 with MHS 3.5" Touchscreen

## What's included

- `dashboard.py` &mdash; renders the clock/weather, system status, network
  info, and CPU stats screens directly to the framebuffer (`/dev/fb0`).
- `dashboard_config.py` &mdash; shared configuration schema/loader used by both
  the dashboard and the web UI. Settings are persisted to `config.json`
  (created automatically on first save; not committed to the repo).
- `web_config.py` + `static/` &mdash; a small local web server with an HTML
  page for configuring the dashboard from any browser on the network.

## Installing dependencies

```bash
pip install -r requirements.txt
```

## Running the dashboard

```bash
python3 dashboard.py
```

## Configuring the dashboard from a web browser

Run the configuration web server (on the Pi, or anywhere with access to the
same `config.json` the dashboard reads):

```bash
python3 web_config.py
```

Then open `http://<pi-ip-address>:8080/` in a browser.

### Login

The configuration UI is protected by a login page.

| Field    | Default value |
|----------|---------------|
| Username | `admin`       |
| Password | `06112024`    |

> **Security warning**: These are hardcoded defaults intended for a trusted
> home network only. Before exposing the Pi to the wider internet, change
> `ADMIN_USERNAME` and `ADMIN_PASSWORD` near the top of `web_config.py`.

### What you can configure

From the web UI you can:

- **Theme** &mdash; background, foreground, and secondary/label colors; six
  built-in presets (Classic, Green, Amber, Ocean Blue, Sunset, Monochrome).
- **Display** &mdash; screen orientation (normal / flipped / left / right) and
  backlight brightness 0–100 (applied via sysfs when a backlight device is
  available; silently ignored otherwise).
- **Fonts** &mdash; font family (from fonts installed on the system) and
  individual sizes for every text element (clock, date, values, labels, etc.).
- **Alignment** &mdash; left/center/right for the clock, date, value labels,
  and footer hint lines, independently.
- **Screens** &mdash; enable/disable and reorder (drag) the four built-in
  screens:
  - **Clock & Weather** &mdash; time, date, temperature and humidity.
  - **System Status** &mdash; CPU load, temperature, RAM, disk, fan speed, IP.
  - **Network Info** &mdash; hostname, LAN IP, Wi-Fi SSID, system uptime.
  - **CPU Stats** &mdash; CPU history sparkline, top process, swap usage.
- **Footer lines** &mdash; each screen has a configurable footer hint shown at
  the bottom. You can edit the text or hide it entirely per screen.
- **Timing & Animation** &mdash; FPS, screen duration, transition duration,
  enable/disable slide transitions, enable/disable icon animations.
- **Weather & Location** &mdash; `auto` mode (geolocates via ipwho.is, the
  existing default) or `manual` mode (enter latitude & longitude directly,
  skipping the IP geolocation call).

Changes are saved to `config.json` and picked up by the running
`dashboard.py` process automatically (within about a second), no restart
required.

### Securing the config server

`web_config.py` already requires a username/password login (see above).

For programmatic/API access you can additionally set the `MHS_CONFIG_TOKEN`
environment variable; clients then bypass the session login by sending the
token as `?token=...` or an `X-Config-Token` header:

```bash
MHS_CONFIG_TOKEN=some-long-random-value python3 web_config.py
```

### Running both as services on boot

Example systemd unit files are provided in `systemd/`:

- `mhs-dashboard.service` &mdash; runs the framebuffer dashboard
- `mhs-dashboard-config.service` &mdash; runs the web configuration UI

Copy them to `/etc/systemd/system/`, adjust the paths/user if needed, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mhs-dashboard.service mhs-dashboard-config.service
```
