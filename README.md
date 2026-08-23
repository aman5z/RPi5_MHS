# RPi5_MHS
Custom Display Pages for Raspberry Pi 5 with MHS 3.5" Touchscreen

## What's included

- `dashboard.py` &mdash; renders the clock/weather and system status screens
  directly to the framebuffer (`/dev/fb0`).
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

Then open `http://<pi-ip-address>:8080/` in a browser. From there you can:

- Pick a theme (background/foreground colors), or choose from a few presets
- Choose a font family (from the fonts installed on the system) and adjust
  the size of each text element (clock, date, values, labels, etc.)
- Set text alignment (left/center/right) for the clock and date
- Enable/disable and reorder the screens shown in the slideshow (drag to
  reorder)
- Adjust timing (FPS, how long each screen is shown) and enable/disable
  and time the slide transition animation

Changes are saved to `config.json` and picked up by the running
`dashboard.py` process automatically (within about a second), no restart
required.

### Securing the config server

`web_config.py` listens on all network interfaces with no authentication
by default, which is fine on a trusted home LAN. If the Pi is reachable
from a less trusted network, set the `MHS_CONFIG_TOKEN` environment
variable before starting the server to require a shared secret:

```bash
MHS_CONFIG_TOKEN=some-long-random-value python3 web_config.py
```

Clients then need to send it as `?token=...` or an `X-Config-Token`
header (the bundled `static/config.html` UI is unauthenticated convenience
tooling for trusted networks; add the token to the URL as
`http://<pi-ip>:8080/?token=...` if you enable it).

### Running both as services on boot

Example systemd unit files are provided in `systemd/`:

- `mhs-dashboard.service` &mdash; runs the framebuffer dashboard
- `mhs-dashboard-config.service` &mdash; runs the web configuration UI

Copy them to `/etc/systemd/system/`, adjust the paths/user if needed, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mhs-dashboard.service mhs-dashboard-config.service
```
