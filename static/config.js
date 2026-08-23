// Theme presets are fetched from /api/themes; this array is used as a
// fallback while the fetch is in-flight (avoids an empty grid flash).
let THEME_PRESETS = [
  { name: "Classic (B/W)",   background: "#000000", foreground: "#FFFFFF", text_secondary: "#AAAAAA" },
  { name: "Classic Green",   background: "#000000", foreground: "#00FF66", text_secondary: "#007733" },
  { name: "Amber Terminal",  background: "#0A0A0A", foreground: "#FFB000", text_secondary: "#7A5500" },
  { name: "Ocean Blue",      background: "#0A1128", foreground: "#E8F1FF", text_secondary: "#6FA8DC" },
  { name: "Sunset Orange",   background: "#1A0B1F", foreground: "#FFB37B", text_secondary: "#994400" },
  { name: "Monochrome",      background: "#181818", foreground: "#E0E0E0", text_secondary: "#808080" },
];

let screenLabels = {};

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    throw new Error(data.error || `Request failed (${res.status})`);
  }

  return data;
}

function showStatus(message, isError) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.hidden = false;
  el.className = `status ${isError ? "error" : "ok"}`;

  clearTimeout(showStatus._t);
  showStatus._t = setTimeout(() => {
    el.hidden = true;
  }, 4000);
}

function renderThemePresets(presets) {
  const container = document.getElementById("theme-presets");
  container.innerHTML = "";

  presets.forEach((preset) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preset-btn";
    btn.textContent = preset.name;
    btn.style.borderLeftColor = preset.foreground;
    btn.addEventListener("click", () => {
      document.getElementById("theme-background").value = preset.background;
      document.getElementById("theme-foreground").value = preset.foreground;
      if (preset.text_secondary) {
        document.getElementById("theme-text_secondary").value = preset.text_secondary;
      }
    });
    container.appendChild(btn);
  });
}

function renderFontOptions(families, selected) {
  const select = document.getElementById("font-family");
  select.innerHTML = "";

  families.forEach((family) => {
    const opt = document.createElement("option");
    opt.value = family;
    opt.textContent = family;
    select.appendChild(opt);
  });

  select.value = selected;
}

function renderScreenList(screens) {
  const list = document.getElementById("screen-list");
  list.innerHTML = "";

  screens.forEach((entry) => {
    const li = document.createElement("li");
    li.className = "screen-item";
    li.draggable = true;
    li.dataset.id = entry.id;

    const footerEnabled = entry.footer_enabled !== false;
    const footerText = entry.footer_text || "";

    li.innerHTML = `
      <span class="handle">&#9776;</span>
      <div class="screen-item-content">
        <label class="screen-enable-label">
          <input type="checkbox" class="screen-enabled" ${entry.enabled ? "checked" : ""}>
          <strong>${screenLabels[entry.id] || entry.id}</strong>
        </label>
        <div class="footer-config">
          <label class="checkbox-label footer-toggle">
            <input type="checkbox" class="screen-footer-enabled" ${footerEnabled ? "checked" : ""}>
            Show footer line
          </label>
          <label class="footer-text-label">
            Footer text
            <input type="text" class="screen-footer-text" value="${footerText.replace(/"/g, "&quot;")}">
          </label>
        </div>
      </div>
    `;

    li.addEventListener("dragstart", () => li.classList.add("dragging"));
    li.addEventListener("dragend", () => li.classList.remove("dragging"));

    list.appendChild(li);
  });
}

function initScreenListDragging() {
  const list = document.getElementById("screen-list");

  list.addEventListener("dragover", (e) => {
    e.preventDefault();
    const dragging = list.querySelector(".dragging");
    const after = getDragAfterElement(list, e.clientY);

    if (!dragging) return;

    if (after == null) {
      list.appendChild(dragging);
    } else {
      list.insertBefore(dragging, after);
    }
  });
}

function getDragAfterElement(container, y) {
  const items = [...container.querySelectorAll(".screen-item:not(.dragging)")];

  return items.reduce(
    (closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;

      if (offset < 0 && offset > closest.offset) {
        return { offset, element: child };
      }

      return closest;
    },
    { offset: Number.NEGATIVE_INFINITY, element: null }
  ).element;
}

function collectScreens() {
  return [...document.querySelectorAll("#screen-list .screen-item")].map((li) => ({
    id: li.dataset.id,
    enabled: li.querySelector(".screen-enabled").checked,
    footer_enabled: li.querySelector(".screen-footer-enabled").checked,
    footer_text: li.querySelector(".screen-footer-text").value,
  }));
}

function applyConfigToForm(cfg) {
  document.getElementById("theme-background").value = cfg.theme.background;
  document.getElementById("theme-foreground").value = cfg.theme.foreground;
  document.getElementById("theme-text_secondary").value = cfg.theme.text_secondary || "#AAAAAA";
  document.getElementById("theme-alert_color").value = (cfg.theme && cfg.theme.alert_color) || "#FF3333";
  document.getElementById("theme-warn_color").value = (cfg.theme && cfg.theme.warn_color) || "#FFAA00";

  // Display
  const disp = cfg.display || {};
  document.getElementById("display-orientation").value = disp.orientation || "normal";
  const bri = disp.brightness !== undefined ? disp.brightness : 100;
  document.getElementById("display-brightness").value = bri;
  document.getElementById("display-brightness-val").textContent = bri;

  // Fonts
  Object.entries(cfg.fonts).forEach(([key, value]) => {
    const el = document.getElementById(`font-${key}`);
    if (el) el.value = value;
  });

  // Alignment
  const align = cfg.alignment || {};
  document.getElementById("align-clock").value = align.clock || "center";
  document.getElementById("align-date").value = align.date || "center";
  document.getElementById("align-values").value = align.values || "left";
  document.getElementById("align-footer").value = align.footer || "center";

  // Timing
  document.getElementById("timing-fps").value = cfg.timing.fps;
  document.getElementById("timing-screen_duration").value = cfg.timing.screen_duration;
  document.getElementById("timing-transition_duration").value = cfg.timing.transition_duration;
  document.getElementById("timing-transitions_enabled").checked = cfg.timing.transitions_enabled;
  document.getElementById("timing-icon_animations_enabled").checked =
    cfg.timing.icon_animations_enabled !== false;

  // Weather
  const wx = cfg.weather || {};
  document.getElementById("weather-mode").value = wx.mode || "auto";
  document.getElementById("weather-latitude").value = wx.latitude !== null && wx.latitude !== undefined ? wx.latitude : "";
  document.getElementById("weather-longitude").value = wx.longitude !== null && wx.longitude !== undefined ? wx.longitude : "";
  toggleWeatherManualFields(wx.mode || "auto");

  // Location
  const loc = cfg.location || {};
  document.getElementById("location-mode").value = loc.mode || "auto";
  document.getElementById("location-name").value = loc.name || "";
  toggleLocationManualFields(loc.mode || "auto");

  // Ping targets
  renderPingList(cfg.ping_targets || []);

  // Proxmox
  const prx = cfg.proxmox || {};
  document.getElementById("proxmox-enabled").checked = !!prx.enabled;
  document.getElementById("proxmox-host").value = prx.host || "";
  document.getElementById("proxmox-token_id").value = prx.token_id || "";
  document.getElementById("proxmox-token_secret").value = prx.token_secret || "";
  document.getElementById("proxmox-staleness_hours").value = prx.staleness_hours !== undefined ? prx.staleness_hours : 24;
  document.getElementById("proxmox-verify_ssl").checked = prx.verify_ssl !== false;

  // Alerts
  const alt = cfg.alerts || {};
  document.getElementById("alerts-cpu_warn_pct").value = alt.cpu_warn_pct !== undefined ? alt.cpu_warn_pct : 85;
  document.getElementById("alerts-temp_warn_c").value = alt.temp_warn_c !== undefined ? alt.temp_warn_c : 75;
  document.getElementById("alerts-disk_warn_pct").value = alt.disk_warn_pct !== undefined ? alt.disk_warn_pct : 90;
  document.getElementById("alerts-device_offline_s").value = alt.device_offline_s !== undefined ? alt.device_offline_s : 90;

  // Notifications
  const notif = cfg.notifications || {};
  document.getElementById("notifications-max_count").value = notif.max_count !== undefined ? notif.max_count : 100;

  renderScreenList(cfg.screens);
}

function toggleWeatherManualFields(mode) {
  const fields = document.getElementById("weather-manual-fields");
  fields.style.display = mode === "manual" ? "flex" : "none";
}

function toggleLocationManualFields(mode) {
  const fields = document.getElementById("location-manual-fields");
  fields.style.display = mode === "manual" ? "flex" : "none";
}

// ---------------------------------------------------------------------------
// Ping targets list editor
// ---------------------------------------------------------------------------

function renderPingList(targets) {
  const list = document.getElementById("ping-list");
  list.innerHTML = "";
  (targets || []).forEach((tgt, i) => {
    const li = document.createElement("li");
    li.className = "screen-item";
    li.dataset.idx = i;
    li.innerHTML = `
      <div class="screen-item-content">
        <label>Label <input type="text" class="ping-label" value="${(tgt.label || "").replace(/"/g, "&quot;")}" placeholder="Router"></label>
        <label>Host <input type="text" class="ping-host" value="${(tgt.host || "").replace(/"/g, "&quot;")}" placeholder="192.168.1.1"></label>
        <button type="button" class="secondary ping-remove-btn" data-idx="${i}">Remove</button>
      </div>`;
    list.appendChild(li);
  });
  list.querySelectorAll(".ping-remove-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.closest("li").remove();
    });
  });
}

function collectPingTargets() {
  return [...document.querySelectorAll("#ping-list .screen-item")].map((li) => ({
    label: li.querySelector(".ping-label").value.trim(),
    host:  li.querySelector(".ping-host").value.trim(),
  })).filter((t) => t.host);
}

function buildConfigFromForm() {
  const wxMode = document.getElementById("weather-mode").value;
  const wxLat = document.getElementById("weather-latitude").value;
  const wxLon = document.getElementById("weather-longitude").value;
  const locMode = document.getElementById("location-mode").value;

  return {
    theme: {
      background:     document.getElementById("theme-background").value,
      foreground:     document.getElementById("theme-foreground").value,
      text_secondary: document.getElementById("theme-text_secondary").value,
      alert_color:    document.getElementById("theme-alert_color").value,
      warn_color:     document.getElementById("theme-warn_color").value,
    },
    display: {
      orientation: document.getElementById("display-orientation").value,
      brightness:  Number(document.getElementById("display-brightness").value),
    },
    fonts: {
      family:         document.getElementById("font-family").value,
      clock_size:     Number(document.getElementById("font-clock_size").value),
      date_size:      Number(document.getElementById("font-date_size").value),
      value_size:     Number(document.getElementById("font-value_size").value),
      big_value_size: Number(document.getElementById("font-big_value_size").value),
      small_size:     Number(document.getElementById("font-small_size").value),
      ip_size:        Number(document.getElementById("font-ip_size").value),
      title_size:     Number(document.getElementById("font-title_size").value),
    },
    alignment: {
      clock:  document.getElementById("align-clock").value,
      date:   document.getElementById("align-date").value,
      values: document.getElementById("align-values").value,
      footer: document.getElementById("align-footer").value,
    },
    timing: {
      fps:                      Number(document.getElementById("timing-fps").value),
      screen_duration:          Number(document.getElementById("timing-screen_duration").value),
      transition_duration:      Number(document.getElementById("timing-transition_duration").value),
      transitions_enabled:      document.getElementById("timing-transitions_enabled").checked,
      icon_animations_enabled:  document.getElementById("timing-icon_animations_enabled").checked,
    },
    weather: {
      mode:      wxMode,
      latitude:  wxMode === "manual" && wxLat !== "" ? Number(wxLat) : null,
      longitude: wxMode === "manual" && wxLon !== "" ? Number(wxLon) : null,
    },
    location: {
      mode: locMode,
      name: document.getElementById("location-name").value.trim(),
    },
    ping_targets: collectPingTargets(),
    proxmox: {
      enabled:         document.getElementById("proxmox-enabled").checked,
      host:            document.getElementById("proxmox-host").value.trim(),
      token_id:        document.getElementById("proxmox-token_id").value.trim(),
      token_secret:    document.getElementById("proxmox-token_secret").value,
      staleness_hours: Number(document.getElementById("proxmox-staleness_hours").value),
      verify_ssl:      document.getElementById("proxmox-verify_ssl").checked,
    },
    alerts: {
      cpu_warn_pct:     Number(document.getElementById("alerts-cpu_warn_pct").value),
      temp_warn_c:      Number(document.getElementById("alerts-temp_warn_c").value),
      disk_warn_pct:    Number(document.getElementById("alerts-disk_warn_pct").value),
      device_offline_s: Number(document.getElementById("alerts-device_offline_s").value),
    },
    notifications: {
      max_count: Number(document.getElementById("notifications-max_count").value),
    },
    screens: collectScreens(),
  };
}

async function init() {
  // Brightness slider live update.
  const briSlider = document.getElementById("display-brightness");
  const briVal = document.getElementById("display-brightness-val");
  briSlider.addEventListener("input", () => {
    briVal.textContent = briSlider.value;
  });

  // Weather mode toggle.
  document.getElementById("weather-mode").addEventListener("change", (e) => {
    toggleWeatherManualFields(e.target.value);
  });

  // Location mode toggle.
  document.getElementById("location-mode").addEventListener("change", (e) => {
    toggleLocationManualFields(e.target.value);
  });

  // Ping add button.
  document.getElementById("ping-add-btn").addEventListener("click", () => {
    const list = document.getElementById("ping-list");
    const existing = [...list.querySelectorAll(".screen-item")].map((li) => ({
      label: li.querySelector(".ping-label").value.trim(),
      host:  li.querySelector(".ping-host").value.trim(),
    }));
    existing.push({ label: "", host: "" });
    renderPingList(existing);
  });

  const [config, fontsData, screensData, themesData] = await Promise.all([
    fetchJSON("/api/config"),
    fetchJSON("/api/fonts"),
    fetchJSON("/api/screens"),
    fetchJSON("/api/themes").catch(() => ({ themes: THEME_PRESETS })),
  ]);

  if (!config) return; // redirected to login

  THEME_PRESETS = (themesData && themesData.themes) || THEME_PRESETS;
  renderThemePresets(THEME_PRESETS);

  screensData.screens.forEach((s) => {
    screenLabels[s.id] = s.label;
  });

  renderFontOptions(fontsData.families, config.fonts.family);
  applyConfigToForm(config);
  initScreenListDragging();

  document.getElementById("save-btn").addEventListener("click", async () => {
    try {
      const cfg = buildConfigFromForm();
      const saved = await fetchJSON("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      if (!saved) return;
      applyConfigToForm(saved);
      showStatus("Saved. The dashboard will update within a second.", false);
    } catch (err) {
      showStatus(`Failed to save: ${err.message}`, true);
    }
  });

  document.getElementById("reset-btn").addEventListener("click", async () => {
    try {
      const saved = await fetchJSON("/api/config/reset", { method: "POST" });
      if (!saved) return;
      applyConfigToForm(saved);
      showStatus("Reset to defaults.", false);
    } catch (err) {
      showStatus(`Failed to reset: ${err.message}`, true);
    }
  });
}

init().catch((err) => showStatus(`Failed to load config: ${err.message}`, true));

