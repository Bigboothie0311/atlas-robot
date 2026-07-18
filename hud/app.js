const STATE_POLL_MS = 1000;
const STATS_POLL_MS = 5000;

// The dashboard is built at a fixed 1920x1080 design size (see #dashboard
// in style.css). Real screens vary a lot — this kiosk has run on a
// 1920x1080 dev monitor and an 800x480 panel so far — so scale the whole
// page down to fit whatever the actual screen is, instead of maintaining
// a separate layout per resolution.
const DESIGN_WIDTH = 1920;
const DESIGN_HEIGHT = 1080;

function applyViewportScale() {
  const scale = Math.min(
    window.innerWidth / DESIGN_WIDTH,
    window.innerHeight / DESIGN_HEIGHT
  );
  document.documentElement.style.zoom = scale;
}

applyViewportScale();
window.addEventListener("resize", applyViewportScale);

const COSMETIC_TERMINAL_LINES = [
  "CALIBRATING NEURAL ARRAY... OK",
  "SYNC UPLINK 4/4",
  "BUFFER 0x7F2A ALIGNED",
  "RUNNING DIAGNOSTIC SWEEP...",
  "MEMORY LATTICE STABLE",
  "DECRYPTING TELEMETRY STREAM",
  "PHASE ARRAY LOCKED",
  "QUANTUM CACHE WARM",
  "SIGNAL INTEGRITY 99.7%",
  "RECALIBRATING SENSOR ARRAY",
  "HANDSHAKE COMPLETE 0xA3",
  "OPTIMIZING PATHFINDING MATRIX",
  "SUBROUTINE 12 NOMINAL",
  "COOLANT FLOW NOMINAL",
  "AUX CORE SPINNING UP",
  "INDEXING KNOWLEDGE GRAPH",
  "NETWORK MESH STABLE",
  "ENCRYPTION KEYS ROTATED",
];

const COSMETIC_TERMINAL_MAX_LINES = 60;
const COSMETIC_TERMINAL_INTERVAL_MS = 350;
// How often a line is real telemetry instead of pure flavor text — high
// enough to feel alive, low enough that it still reads as a system log
// rather than a stats dashboard.
const REAL_STATUS_LINE_CHANCE = 0.3;

let latestStats = null;

function buildRealStatusLine() {
  if (!latestStats) {
    return null;
  }

  const options = [
    `MEMORY LOAD ${latestStats.memory.percent}% NOMINAL`,
    `DISK USAGE ${latestStats.disk.percent}%`,
  ];

  if (latestStats.network.ip) {
    options.push("NETWORK LINK STABLE");
  }

  if (latestStats.network.device_count) {
    options.push(`${latestStats.network.device_count} NODES ON LOCAL MESH`);
  }

  const hours = Math.floor(latestStats.uptime_seconds / 3600);
  const minutes = Math.floor((latestStats.uptime_seconds % 3600) / 60);
  options.push(`UPTIME ${hours}H ${minutes}M NOMINAL`);

  const gamingPc = latestStats.gaming_pc;
  if (gamingPc && gamingPc.online) {
    options.push(`REMOTE NODE ONLINE — CPU ${gamingPc.cpu_percent}% / ${gamingPc.cpu_temp_c}°C`);
    options.push(`REMOTE NODE GPU ${gamingPc.gpu_percent}% / ${gamingPc.gpu_temp_c}°C`);
  }

  return options[Math.floor(Math.random() * options.length)];
}

function addCosmeticTerminalLine() {
  const container = document.getElementById("cosmetic-terminal-lines");
  const line = document.createElement("div");

  const realLine = Math.random() < REAL_STATUS_LINE_CHANCE
    ? buildRealStatusLine()
    : null;

  const phrase = realLine
    || COSMETIC_TERMINAL_LINES[Math.floor(Math.random() * COSMETIC_TERMINAL_LINES.length)];

  const now = new Date();
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  line.appendChild(time);
  line.appendChild(document.createTextNode(`> ${phrase}`));
  container.appendChild(line);

  while (container.children.length > COSMETIC_TERMINAL_MAX_LINES) {
    container.removeChild(container.firstChild);
  }

  container.scrollTop = container.scrollHeight;
}

let lastQaTimestamp = 0;
let lastGalleryKey = null;

function pad(value) {
  return String(value).padStart(2, "0");
}

// Matches robot_hub.py's QUIET_HOURS_START/END — same overnight window gets
// a dimmer, calmer look here and a softer, shorter-spoken voice there.
const QUIET_HOURS_START = 23;
const QUIET_HOURS_END = 6;

function isQuietHours(now) {
  const hour = now.getHours();
  return hour >= QUIET_HOURS_START || hour < QUIET_HOURS_END;
}

function updateClock() {
  const now = new Date();
  const hours = pad(now.getHours());
  const minutes = pad(now.getMinutes());
  const seconds = pad(now.getSeconds());
  document.getElementById("clock").textContent = `${hours}:${minutes}:${seconds}`;

  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const months = ["January", "February", "March", "April", "May", "June", "July",
                   "August", "September", "October", "November", "December"];
  document.getElementById("date").textContent =
    `${days[now.getDay()]}, ${months[now.getMonth()]} ${now.getDate()}`;

  document.body.classList.toggle("quiet-hours", isQuietHours(now));
}

const STATE_CLASSES = ["state-idle", "state-listening", "state-thinking", "state-speaking"];

function applyState(state) {
  const expression = state.expression || "happy";
  const speaking = Boolean(state.speaking);

  let stateClass = "state-idle";
  let label = "IDLE";
  let mastheadText = "ALL SYSTEMS NOMINAL";

  if (speaking || expression === "talking") {
    stateClass = "state-speaking";
    label = "SPEAKING";
    mastheadText = "TRANSMITTING";
  } else if (expression === "listening") {
    stateClass = "state-listening";
    label = "LISTENING";
    mastheadText = "AUDIO CHANNEL OPEN";
  } else if (expression === "thinking") {
    stateClass = "state-thinking";
    label = state.activity_label || "THINKING";
    mastheadText = "PROCESSING";
  }

  // Only touch the state-* classes here — updateClock owns quiet-hours, so
  // this can't be a wholesale className overwrite anymore.
  document.body.classList.remove(...STATE_CLASSES);
  document.body.classList.add(stateClass);
  document.getElementById("status-label").textContent = label;
  document.getElementById("masthead-state-text").textContent = mastheadText;
}

function applyImage(state) {
  const overlay = document.getElementById("image-overlay");
  const img = document.getElementById("image-overlay-img");

  if (state.image_path) {
    img.src = `/hud/display_image?t=${Date.now()}`;
    overlay.classList.add("visible");
  } else {
    overlay.classList.remove("visible");
    img.removeAttribute("src");
  }
}

function applyGallery(state) {
  const overlay = document.getElementById("gallery-overlay");
  const grid = document.getElementById("gallery-grid");
  const paths = state.gallery_image_paths || [];

  if (paths.length > 0) {
    overlay.classList.add("visible");

    const key = `${paths.join(",")}|${state.gallery_caption || ""}|${state.gallery_until || ""}`;
    if (key === lastGalleryKey) {
      return;
    }
    lastGalleryKey = key;

    grid.innerHTML = "";
    for (let i = 0; i < paths.length; i++) {
      const img = document.createElement("img");
      img.src = `/hud/gallery_image/${i}?t=${Date.now()}`;
      grid.appendChild(img);
    }
  } else {
    lastGalleryKey = null;
    overlay.classList.remove("visible");
    grid.innerHTML = "";
  }
}

function applyQaLog(state) {
  const entries = state.qa_log || [];
  const container = document.getElementById("qa-log");

  const newest = entries.length ? entries[entries.length - 1].timestamp : 0;
  if (newest === lastQaTimestamp) {
    return;
  }
  lastQaTimestamp = newest;

  container.innerHTML = "";
  for (const entry of entries) {
    const wrapper = document.createElement("div");
    wrapper.className = "qa-entry";

    // System-originated entries ([proactive], [notification], ...) have
    // no user side — showing them as "YOU ▸ [proactive]" misattributes
    // them, so only the ATLAS line renders.
    const isSystemEntry = entry.question.startsWith("[");

    if (!isSystemEntry) {
      const question = document.createElement("div");
      question.className = "qa-question";
      question.textContent = entry.question;
      wrapper.appendChild(question);
    }

    const answer = document.createElement("div");
    answer.className = "qa-answer";
    answer.textContent = entry.answer;

    wrapper.appendChild(answer);
    container.appendChild(wrapper);
  }

  container.scrollTop = container.scrollHeight;
}

function formatCountdown(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${pad(minutes)}:${pad(seconds)}`;
}

function applyTimers(state) {
  const readout = document.getElementById("timer-readout");
  const timer = state.timer;
  const focus = state.focus;

  document.body.classList.toggle("focus-mode", Boolean(focus));
  document.body.classList.toggle("timer-alert", Boolean(state.timer_alert));

  if (timer) {
    readout.textContent = `TIMER ${formatCountdown(timer.remaining_seconds)}`;
    readout.classList.add("visible");
    readout.classList.remove("focus");
  } else if (focus) {
    readout.textContent = `FOCUS ${formatCountdown(focus.remaining_seconds)}`;
    readout.classList.add("visible", "focus");
  } else {
    readout.textContent = "";
    readout.classList.remove("visible", "focus");
  }

  // During an idle focus session the masthead says why it's so quiet.
  if (focus && document.body.classList.contains("state-idle")) {
    document.getElementById("masthead-state-text").textContent =
      "FOCUS PROTOCOL ACTIVE";
  }
}

const AUTH_LABELS = {
  VERIFIED: "AUTH VERIFIED",
  STALE: "AUTH STANDBY",
  OFF: "GATE OFF",
  UNTRAINED: "",
};

function applyAlertAndScreen(state) {
  const redAlert = state.red_alert || {};
  document.body.classList.toggle("red-alert", Boolean(redAlert.active));
  document.body.classList.toggle("screen-dark", Boolean(state.screen_dark));

  if (redAlert.active && document.body.classList.contains("state-idle")) {
    document.getElementById("masthead-state-text").textContent = "RED ALERT";
  }
}

// Contextual HUD layouts. Most panels are shared; layout classes let CSS
// promote the security/diagnostics/alert views over the idle default.
const LAYOUT_CLASSES = [
  "layout-idle",
  "layout-security",
  "layout-diagnostics",
  "layout-red_alert",
];

function applyLayout(state) {
  const layout = state.hud_layout || "idle";
  document.body.classList.remove(...LAYOUT_CLASSES);
  document.body.classList.add(`layout-${layout}`);

  if (layout === "security") {
    renderSecurity(state);
  }
}

let lastSecurityKey = "";

function renderSecurity(state) {
  const records = state.intruder_records || [];
  const active = state.active_intruder_photo;

  // The list of all intruder records (left rail).
  const key = JSON.stringify(records.map((r) => r.id));
  if (key !== lastSecurityKey) {
    lastSecurityKey = key;
    const list = document.getElementById("intruder-list");
    list.innerHTML = "";
    for (const record of records) {
      const entry = document.createElement("div");
      entry.className = "intruder-entry";
      const time = new Date(record.timestamp * 1000).toLocaleTimeString();
      const denied = record.denied_commands || [];
      entry.innerHTML =
        `<span class="intruder-time">${time}</span>` +
        (denied.length
          ? `<div class="intruder-denied">Tried: ${denied.map(escapeHtml).join("; ")}</div>`
          : `<div class="intruder-denied">No commands attempted</div>`);
      list.appendChild(entry);
    }
  }

  // The full-screen photo of whichever record is being narrated.
  const photo = document.getElementById("intruder-photo");
  const meta = document.getElementById("intruder-meta");

  if (active) {
    photo.src = `/hud/intruder_photo/${active.id}?t=${Math.floor(active.until)}`;
    const time = new Date(active.timestamp * 1000).toLocaleString();
    const denied = active.denied_commands || [];
    meta.textContent = denied.length
      ? `${time} — tried: ${denied.join("; ")}`
      : `${time} — no commands attempted`;
  } else {
    photo.removeAttribute("src");
    meta.textContent = "";
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function applyAuth(state) {
  const indicator = document.getElementById("auth-indicator");
  const auth = state.auth || {};
  let label = AUTH_LABELS[auth.status] ?? "";

  if (auth.unreviewed_intruders > 0) {
    label = `${auth.unreviewed_intruders} INTRUDER ALERT${auth.unreviewed_intruders > 1 ? "S" : ""}`;
  }

  indicator.textContent = label ? `${label} · ` : "";
  indicator.classList.toggle("alert", auth.unreviewed_intruders > 0);
  indicator.classList.toggle("verified", auth.status === "VERIFIED");
}

async function pollState() {
  try {
    const response = await fetch("/state");
    const state = await response.json();
    applyState(state);
    applyTimers(state);
    applyAuth(state);
    applyAlertAndScreen(state);
    applyLayout(state);
    applyImage(state);
    applyGallery(state);
    applyQaLog(state);
  } catch (error) {
    console.error("state poll failed", error);
  }
}

async function pollStats() {
  try {
    const response = await fetch("/hud/stats");
    const stats = await response.json();
    latestStats = stats;

    // Matches robot_hub.py's CPU_WARNING_THRESHOLD — the raw CPU number
    // isn't shown here by design, just a status word, so the voice warning
    // (sustained 3+ minutes above this) is the source of truth for
    // whether it's actually worth mentioning.
    const CPU_WARNING_THRESHOLD = 75;
    const systemStatusPanel = document.querySelector(".panel-system-status");
    const isHot = stats.cpu.percent >= CPU_WARNING_THRESHOLD;
    systemStatusPanel.classList.toggle("warning", isHot);
    document.getElementById("system-status-detail").textContent =
      `MEM ${stats.memory.percent}% · ${isHot ? "WARNING" : "NOMINAL"}`;

    document.getElementById("mem-gauge").style.width = `${stats.memory.percent}%`;

    document.getElementById("core-cpu").textContent = `${stats.cpu.percent}%`;
    document.getElementById("core-gauge").style.width = `${stats.cpu.percent}%`;
    document.querySelector(".panel-core").classList.toggle("warning", isHot);

    document.getElementById("disk-percent").textContent = `${stats.disk.percent}%`;
    document.getElementById("disk-detail").textContent =
      `${stats.disk.used_gb} / ${stats.disk.total_gb} GB`;
    document.getElementById("disk-gauge").style.width = `${stats.disk.percent}%`;

    if (stats.station_name) {
      document.getElementById("station-name").textContent = stats.station_name;
    }

    const weather = stats.weather;
    document.getElementById("weather-city").textContent = weather.city || "--";
    document.getElementById("weather-temp").textContent =
      weather.temp_f !== null ? `${weather.temp_f}°F` : "--°F";
    document.getElementById("weather-condition").textContent =
      weather.stale ? `${weather.condition} (stale)` : weather.condition;
    document.getElementById("weather-range").textContent =
      `H ${weather.high_f ?? "--"} / L ${weather.low_f ?? "--"}`;
    document.getElementById("weather-precip").textContent =
      `Rain ${weather.precip_chance ?? "--"}%`;

    // LAN roster panel was removed for privacy; only the device COUNT is
    // shown (on the masthead). The full roster stays server-side for
    // "what's on my network".
    const deviceCount = stats.network.device_count || 0;
    document.getElementById("device-count-mast").textContent =
      deviceCount > 0 ? `${deviceCount} DEVICES · ` : "";

    const hours = Math.floor(stats.uptime_seconds / 3600);
    const minutes = Math.floor((stats.uptime_seconds % 3600) / 60);
    document.getElementById("uptime").textContent = `UPTIME ${hours}H ${minutes}M`;

    const gamingPc = stats.gaming_pc || { online: false };
    const gamingPcPanel = document.querySelector(".panel-gaming-pc");
    const gamingPcStatus = document.getElementById("gaming-pc-status");

    if (gamingPc.online) {
      gamingPcPanel.classList.remove("offline");
      gamingPcStatus.textContent = "ONLINE";
      document.getElementById("gaming-pc-cpu").textContent =
        `CPU ${gamingPc.cpu_percent}% / ${gamingPc.cpu_temp_c}°C`;
      document.getElementById("gaming-pc-gpu").textContent =
        `GPU ${gamingPc.gpu_percent}% / ${gamingPc.gpu_temp_c}°C`;
      document.getElementById("gaming-pc-ram").textContent =
        `RAM ${gamingPc.ram_percent}%`;
    } else {
      gamingPcPanel.classList.add("offline");
      gamingPcStatus.textContent = "OFFLINE";
      document.getElementById("gaming-pc-cpu").textContent = "CPU -- % / -- °C";
      document.getElementById("gaming-pc-gpu").textContent = "GPU -- % / -- °C";
      document.getElementById("gaming-pc-ram").textContent = "RAM -- %";
    }
    const printer = stats.printer || { online: false };
    const printerPanel = document.getElementById("printer-panel");

    if (printer.online) {
      printerPanel.classList.add("visible");
      document.getElementById("printer-state").textContent =
        (printer.state || "online").toUpperCase();

      const progress = printer.progress_percent;
      document.getElementById("printer-gauge").style.width =
        progress !== null && progress !== undefined ? `${progress}%` : "0%";

      const detailParts = [];
      if (progress !== null && progress !== undefined) {
        detailParts.push(`${progress}%`);
      }
      if (printer.layer) {
        detailParts.push(`LAYER ${printer.layer}`);
      }
      if (printer.eta_minutes) {
        const h = Math.floor(printer.eta_minutes / 60);
        const m = printer.eta_minutes % 60;
        detailParts.push(`ETA ${h > 0 ? `${h}H ` : ""}${m}M`);
      }
      document.getElementById("printer-detail").textContent =
        detailParts.join(" · ");
    } else {
      printerPanel.classList.remove("visible");
    }

    applyHeadlines(stats.headlines || []);
  } catch (error) {
    console.error("stats poll failed", error);
  }
}

// News wire: show HEADLINES_PER_PAGE at a time, fading to the next batch
// on a timer — readable at low resolution, unlike a scrolling marquee.
const HEADLINES_PER_PAGE = 3;
const HEADLINE_ROTATE_MS = 15000;

let currentHeadlines = [];
let headlinePage = 0;

function renderHeadlinePage() {
  const container = document.getElementById("news-headlines");
  container.innerHTML = "";

  if (!currentHeadlines.length) {
    return;
  }

  const start = headlinePage * HEADLINES_PER_PAGE;
  const page = currentHeadlines.slice(start, start + HEADLINES_PER_PAGE);

  for (const headline of page) {
    const row = document.createElement("div");
    row.className = "headline";
    row.textContent = headline;
    container.appendChild(row);
  }
}

function rotateHeadlines() {
  if (currentHeadlines.length <= HEADLINES_PER_PAGE) {
    return;
  }

  const container = document.getElementById("news-headlines");
  const pages = Math.ceil(currentHeadlines.length / HEADLINES_PER_PAGE);
  container.classList.add("fading");

  setTimeout(() => {
    headlinePage = (headlinePage + 1) % pages;
    renderHeadlinePage();
    container.classList.remove("fading");
  }, 600);
}

setInterval(rotateHeadlines, HEADLINE_ROTATE_MS);

function applyHeadlines(headlines) {
  const key = headlines.join("|");
  const previousKey = currentHeadlines.join("|");

  if (key === previousKey) {
    return;
  }

  currentHeadlines = headlines;
  headlinePage = 0;
  renderHeadlinePage();
}

updateClock();
setInterval(updateClock, 1000);

pollState();
setInterval(pollState, STATE_POLL_MS);

pollStats();
setInterval(pollStats, STATS_POLL_MS);

addCosmeticTerminalLine();
setInterval(addCosmeticTerminalLine, COSMETIC_TERMINAL_INTERVAL_MS);
