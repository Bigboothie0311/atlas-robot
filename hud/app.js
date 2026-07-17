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
    options.push(`NETWORK LINK ${latestStats.network.ip} STABLE`);
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

    const question = document.createElement("div");
    question.className = "qa-question";
    question.textContent = entry.question;

    const answer = document.createElement("div");
    answer.className = "qa-answer";
    answer.textContent = entry.answer;

    wrapper.appendChild(question);
    wrapper.appendChild(answer);
    container.appendChild(wrapper);
  }

  container.scrollTop = container.scrollHeight;
}

async function pollState() {
  try {
    const response = await fetch("/state");
    const state = await response.json();
    applyState(state);
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

    const weather = stats.weather;
    document.getElementById("weather-temp").textContent =
      weather.temp_f !== null ? `${weather.temp_f}°F` : "--°F";
    document.getElementById("weather-condition").textContent =
      weather.stale ? `${weather.condition} (stale)` : weather.condition;
    document.getElementById("weather-range").textContent =
      `H ${weather.high_f ?? "--"} / L ${weather.low_f ?? "--"}`;
    document.getElementById("weather-precip").textContent =
      `Rain ${weather.precip_chance ?? "--"}%`;

    document.getElementById("network-ip").textContent = stats.network.ip || "--";

    const hours = Math.floor(stats.uptime_seconds / 3600);
    const minutes = Math.floor((stats.uptime_seconds % 3600) / 60);
    document.getElementById("uptime").textContent = `${hours}h ${minutes}m`;

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
  } catch (error) {
    console.error("stats poll failed", error);
  }
}

updateClock();
setInterval(updateClock, 1000);

pollState();
setInterval(pollState, STATE_POLL_MS);

pollStats();
setInterval(pollStats, STATS_POLL_MS);

addCosmeticTerminalLine();
setInterval(addCosmeticTerminalLine, COSMETIC_TERMINAL_INTERVAL_MS);
