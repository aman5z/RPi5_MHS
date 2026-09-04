# RPi5_MHS
Custom Display Pages for Raspberry Pi 5 with MHS 3.5" Touchscreen

![20260824_095102](https://github.com/user-attachments/assets/c44b9535-0b8d-4e79-b953-4f1a2d221db9)
![20260824_095110](https://github.com/user-attachments/assets/a72de3f8-1cbd-4b20-96ad-ae36d0c43690)
![20260824_095055](https://github.com/user-attachments/assets/22cf25ba-1932-4569-bad6-340270a93c9d)
![20260824_095115](https://github.com/user-attachments/assets/a68ced64-94f7-4f84-950a-4d76cfa7738a)
![20260824_095121](https://github.com/user-attachments/assets/fa705bcc-25e2-4619-bfd6-beb773cbffeb)
![20260824_095048](https://github.com/user-attachments/assets/c5fbd9dc-a579-496d-9517-5cab24dab239)


## What's included

- `dashboard.py` &mdash; renders all screens directly to the framebuffer (`/dev/fb0`).
- `dashboard_config.py` &mdash; shared configuration schema/loader. Settings are persisted to `config.json`.
- `web_config.py` + `static/` &mdash; a small local web server for configuring the dashboard from any browser.
- `tailscale_status.py` &mdash; helper that runs `tailscale status --json` locally to list VPN peers.
- `proxmox_status.py` &mdash; helper that calls the Proxmox REST API to fetch backup task history.
- `agents/` &mdash; lightweight reporter scripts for remote devices (Linux, Windows).

## Screens

| Screen ID       | Description |
|-----------------|-------------|
| `clock`         | Clock, date, temperature, humidity |
| `system`        | CPU, temperature, RAM, disk, fan, IP |
| `network`       | Hostname, LAN IP, Wi-Fi SSID, uptime, Tailscale peer count |
| `stats`         | CPU history sparkline, top process, swap |
| `devices`       | Remote device stats (CPU/RAM/disk mini-bars) + Proxmox backup banner |
| `ping`          | Latency sparklines per configured host |
| `alerts`        | Aggregated alerts (offline devices, thresholds, backup failures, ping timeouts) |
| `notifications` | Notification inbox (requires a companion app to populate) |
| `uptime_kuma`   | Uptime Kuma monitor list |
| `firewall`      | pfSense/OPNsense WAN + throughput summary |
| `pihole`        | Pi-hole query/block stats |
| `countdowns`    | Event countdown list |
| `habits`        | Habit streak overview |
| `quote`         | Quote/motivation of the day |
| `slideshow`     | Local image slideshow from `photos/` |

All screens can be enabled/disabled and reordered from the web config UI.

## New integrations and options

- **Uptime Kuma**: uses the status-page JSON endpoint `GET {url}/api/status-page/{slug}` (configured via `uptime_kuma.enabled/url/slug`; `api_key` optional).
- **Port scan reminder**: `port_scan.enabled/interval_hours/target` performs lightweight socket checks on common ports and alerts on newly-opened ports (`port_baseline.json` baseline).
- **ARP watch**: `arp_watch.enabled/interface` polls `/proc/net/arp` and raises alerts for new LAN MAC addresses (`arp_baseline.json` baseline). Acknowledge devices with `POST /api/arp/allowlist`.
- **Firewall**: `firewall.enabled/platform/host/api_key/api_secret/verify_ssl` supports OPNsense (`/api/core/system/status`) and pfSense REST plugin style status probing.
- **Pi-hole**: `pihole.enabled/url/api_token` uses classic token API (`/admin/api.php?summaryRaw&auth=...`).
- **Scheduling + night mode**: `scheduling.enabled/rules` restricts rotating screens by time/day; `scheduling.night_mode` can auto-dim brightness.
- **Remote screenshot**: `GET /api/screenshot` serves `/tmp/mhs_last_frame.png` (updated about once per second from renderer).
- **Weather extras**: `weather.show_aqi`, `weather.show_moon_phase`, `weather.show_sun_times`.
- **Quotes**: local bundled `quotes.json` (100 entries) with `quotes.enabled/rotate_daily`.
- **Profiles**: save/load full config snapshots in `profiles/*.json` via `/api/profiles`.
- **Slideshow**: `slideshow.enabled/folder/interval_s/fit_mode`; drop images into `photos/` (or configured folder). List via `GET /api/slideshow/images`.
- **Matrix effect**: `theme.background_effect` (`none` or `matrix_rain`).

### New API endpoints

- `GET /api/uptime-kuma`
- `GET /api/firewall`
- `GET /api/pihole`
- `GET /api/arp/devices`
- `POST /api/arp/allowlist` (`{"mac":"aa:bb:cc:dd:ee:ff"}`)
- `GET /api/habits`
- `POST /api/habits/{id}/toggle`
- `GET /api/profiles`
- `POST /api/profiles/{name}`
- `POST /api/profiles/{name}/apply`
- `DELETE /api/profiles/{name}`
- `GET /api/slideshow/images`
- `GET /api/screenshot`

## Installing dependencies

```bash
pip install -r requirements.txt
```

## Running the dashboard

```bash
python3 dashboard.py
```

## Configuring the dashboard from a web browser

```bash
python3 web_config.py
```

Then open `http://<pi-ip-address>:8080/` in a browser.

### Login

| Field    | Default value |
|----------|---------------|
| Username | `admin`       |
| Password | `06112024`    |

> **Security warning**: These are hardcoded defaults intended for a trusted
> home network only. Change `ADMIN_USERNAME`/`ADMIN_PASSWORD` in `web_config.py`
> before exposing the Pi to the wider internet.

### What you can configure

- **Theme** &mdash; background, foreground, secondary/label colors; alert and
  warning accent colors; six built-in presets.
- **Display** &mdash; orientation and backlight brightness.
- **Fonts** &mdash; family and individual sizes for every text element.
- **Alignment** &mdash; clock, date, values, footer (independently).
- **Screens** &mdash; enable/disable and drag to reorder; per-screen footer text.
- **Timing & Animation** &mdash; FPS, screen duration, transitions, icon animations.
- **Weather & Location** &mdash; auto (IP geolocation) or manual coordinates.
  The location shown on the clock screen footer is **IP-based geolocation**, not
  a physical GPS module. If you have real GPS hardware, adapt the `update_location`
  function in `dashboard.py` as a starting point.
- **Ping Targets** &mdash; add/remove hosts for the latency graph screen.
- **Proxmox** &mdash; connection settings (see below).
- **Alert Thresholds** &mdash; CPU/temp/disk warning levels, device offline timeout.
- **Notifications** &mdash; inbox size cap.

## Remote device stats reporting

Remote Linux and Windows machines can report their stats to the dashboard using
the provided agent scripts. The dashboard's **Remote Devices** screen displays
them with CPU/RAM/disk mini-bars and an online/offline indicator.

### Linux agent

```bash
# 1. Copy and configure the script
cp agents/linux_report.sh /usr/local/bin/linux_report.sh
chmod +x /usr/local/bin/linux_report.sh
# Edit PI_IP at the top of the script to match your Pi's IP address.

# 2. Install as a systemd timer (runs every 30 seconds)
cp agents/linux-report.service /etc/systemd/system/
cp agents/linux-report.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now linux-report.timer
```

### Windows agent

```powershell
# 1. Copy agents/windows_report.ps1 to a convenient location and edit $PiIP.
# 2. Create a Task Scheduler task (see the comments at the top of the script).
```

### Android

Android devices can use **Termux** with a shell script that posts the same JSON
payload as `linux_report.sh` (adapt `/proc` paths as needed) and run it via the
Termux `crond` or `termux-job-scheduler`. This is less turnkey than the Linux
agent — refer to the Termux documentation for scheduling details.

### API endpoint

Agents POST to:

```
POST http://<pi-ip>:8080/api/devices/report
Content-Type: application/json

{
  "device_id":  "my-server",
  "name":       "My Server",
  "type":       "linux",
  "cpu":        12.4,
  "ram_used":   4.2,
  "ram_total":  16,
  "disk_used":  120,
  "disk_total": 500,
  "uptime_s":   123456,
  "timestamp":  1234567890
}
```

If `MHS_CONFIG_TOKEN` is set on the server, agents must include the token as
`X-Config-Token: <token>` header or `?token=<token>` query parameter.

## Tailscale network status

If Tailscale is installed on the Pi and `tailscale` is in `$PATH`, the
**Network Info** screen shows a compact peer count and the **Tailscale API**
endpoint (`GET /api/tailscale`) returns the full peer list. No additional
configuration is required — the helper runs `tailscale status --json` locally.

## Proxmox backup status

1. In the Proxmox web UI: **Datacenter → Permissions → API Tokens → Add**.
   - User: `root@pam` (or a dedicated audit user).
   - Token name: e.g. `dashboard`.
   - Uncheck "Privilege Separation" to inherit the user's Audit role.
   - Copy the displayed secret (shown only once).

2. In the web config UI, fill in the **Proxmox** section:
   - **Host**: Proxmox hostname or IP (e.g. `proxmox.lan`).
   - **Token ID**: `root@pam!dashboard`.
   - **Token Secret**: paste the copied secret.
   - Uncheck **Verify SSL** if you are using Proxmox's default self-signed certificate.

3. Enable **Proxmox integration** and save.

The latest backup status appears as a compact banner at the bottom of the
**Remote Devices** screen, and backup failures trigger an alert on the
**Alerts** screen.

## Notification inbox

The dashboard provides a server-side notification inbox:

- `POST /api/notifications` &mdash; receive a notification from a companion app.
- `GET /api/notifications` &mdash; list stored notifications.
- `POST /api/notifications/clear` &mdash; wipe the inbox.

The **Notifications** screen shows the four most recent entries. Populating the
inbox requires a companion app:

- **Android**: A small app using `NotificationListenerService` to forward
  notifications (out of scope for this project — this is the receiving side).
- **Windows**: A background app using `UserNotificationListener` (UWP).

## Securing the config server

Set the `MHS_CONFIG_TOKEN` environment variable; clients must then include it as
`?token=...` or `X-Config-Token` header:

```bash
MHS_CONFIG_TOKEN=some-long-random-value python3 web_config.py
```

## Running both as services on boot

```bash
sudo cp systemd/mhs-dashboard.service       /etc/systemd/system/
sudo cp systemd/mhs-dashboard-config.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mhs-dashboard.service mhs-dashboard-config.service
```


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
