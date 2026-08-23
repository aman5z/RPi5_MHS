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

  renderScreenList(cfg.screens);
}

function toggleWeatherManualFields(mode) {
  const fields = document.getElementById("weather-manual-fields");
  fields.style.display = mode === "manual" ? "flex" : "none";
}

function buildConfigFromForm() {
  const wxMode = document.getElementById("weather-mode").value;
  const wxLat = document.getElementById("weather-latitude").value;
  const wxLon = document.getElementById("weather-longitude").value;

  return {
    theme: {
      background: document.getElementById("theme-background").value,
      foreground: document.getElementById("theme-foreground").value,
      text_secondary: document.getElementById("theme-text_secondary").value,
    },
    display: {
      orientation: document.getElementById("display-orientation").value,
      brightness: Number(document.getElementById("display-brightness").value),
    },
    fonts: {
      family: document.getElementById("font-family").value,
      clock_size: Number(document.getElementById("font-clock_size").value),
      date_size: Number(document.getElementById("font-date_size").value),
      value_size: Number(document.getElementById("font-value_size").value),
      big_value_size: Number(document.getElementById("font-big_value_size").value),
      small_size: Number(document.getElementById("font-small_size").value),
      ip_size: Number(document.getElementById("font-ip_size").value),
      title_size: Number(document.getElementById("font-title_size").value),
    },
    alignment: {
      clock: document.getElementById("align-clock").value,
      date: document.getElementById("align-date").value,
      values: document.getElementById("align-values").value,
      footer: document.getElementById("align-footer").value,
    },
    timing: {
      fps: Number(document.getElementById("timing-fps").value),
      screen_duration: Number(document.getElementById("timing-screen_duration").value),
      transition_duration: Number(document.getElementById("timing-transition_duration").value),
      transitions_enabled: document.getElementById("timing-transitions_enabled").checked,
      icon_animations_enabled: document.getElementById("timing-icon_animations_enabled").checked,
    },
    weather: {
      mode: wxMode,
      latitude: wxMode === "manual" && wxLat !== "" ? Number(wxLat) : null,
      longitude: wxMode === "manual" && wxLon !== "" ? Number(wxLon) : null,
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

