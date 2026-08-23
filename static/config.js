const THEME_PRESETS = [
  { name: "Classic (B/W)", background: "#000000", foreground: "#FFFFFF" },
  { name: "Midnight Blue", background: "#0a1128", foreground: "#e8f1ff" },
  { name: "Amber Terminal", background: "#0a0a0a", foreground: "#ffb000" },
  { name: "Matrix Green", background: "#000000", foreground: "#00ff66" },
  { name: "Sunset", background: "#1a0b1f", foreground: "#ffb37b" },
];

let screenLabels = {};

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
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

function renderThemePresets() {
  const container = document.getElementById("theme-presets");
  container.innerHTML = "";

  THEME_PRESETS.forEach((preset) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "preset-btn";
    btn.textContent = preset.name;
    btn.addEventListener("click", () => {
      document.getElementById("theme-background").value = preset.background;
      document.getElementById("theme-foreground").value = preset.foreground;
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

    li.innerHTML = `
      <span class="handle">&#9776;</span>
      <label>
        <input type="checkbox" ${entry.enabled ? "checked" : ""}>
        ${screenLabels[entry.id] || entry.id}
      </label>
    `;

    li.addEventListener("dragstart", () => li.classList.add("dragging"));
    li.addEventListener("dragend", () => li.classList.remove("dragging"));

    list.appendChild(li);
  });

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
    enabled: li.querySelector("input[type=checkbox]").checked,
  }));
}

function applyConfigToForm(cfg) {
  document.getElementById("theme-background").value = cfg.theme.background;
  document.getElementById("theme-foreground").value = cfg.theme.foreground;

  Object.entries(cfg.fonts).forEach(([key, value]) => {
    const el = document.getElementById(`font-${key}`);
    if (el && key !== "family") el.value = value;
  });

  document.getElementById("align-clock").value = cfg.alignment.clock;
  document.getElementById("align-date").value = cfg.alignment.date;

  document.getElementById("timing-fps").value = cfg.timing.fps;
  document.getElementById("timing-screen_duration").value = cfg.timing.screen_duration;
  document.getElementById("timing-transition_duration").value = cfg.timing.transition_duration;
  document.getElementById("timing-transitions_enabled").checked = cfg.timing.transitions_enabled;

  renderScreenList(cfg.screens);

  return cfg.fonts.family;
}

function buildConfigFromForm() {
  return {
    theme: {
      background: document.getElementById("theme-background").value,
      foreground: document.getElementById("theme-foreground").value,
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
    },
    timing: {
      fps: Number(document.getElementById("timing-fps").value),
      screen_duration: Number(document.getElementById("timing-screen_duration").value),
      transition_duration: Number(document.getElementById("timing-transition_duration").value),
      transitions_enabled: document.getElementById("timing-transitions_enabled").checked,
    },
    screens: collectScreens(),
  };
}

async function init() {
  renderThemePresets();

  const [config, fontsData, screensData] = await Promise.all([
    fetchJSON("/api/config"),
    fetchJSON("/api/fonts"),
    fetchJSON("/api/screens"),
  ]);

  screensData.screens.forEach((s) => {
    screenLabels[s.id] = s.label;
  });

  renderFontOptions(fontsData.families, config.fonts.family);
  const selectedFamily = applyConfigToForm(config);
  document.getElementById("font-family").value = selectedFamily;

  document.getElementById("save-btn").addEventListener("click", async () => {
    try {
      const cfg = buildConfigFromForm();
      const saved = await fetchJSON("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      applyConfigToForm(saved);
      showStatus("Saved. The dashboard will update within a second.", false);
    } catch (err) {
      showStatus(`Failed to save: ${err.message}`, true);
    }
  });

  document.getElementById("reset-btn").addEventListener("click", async () => {
    try {
      const saved = await fetchJSON("/api/config/reset", { method: "POST" });
      applyConfigToForm(saved);
      showStatus("Reset to defaults.", false);
    } catch (err) {
      showStatus(`Failed to reset: ${err.message}`, true);
    }
  });
}

init().catch((err) => showStatus(`Failed to load config: ${err.message}`, true));
