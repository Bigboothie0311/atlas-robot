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

const AGENT_PHASE_LABELS = {
  planning: "PLANNING",
  plan_ready: "PLAN READY",
  executing: "EXECUTING",
  waiting_confirmation: "AUTHORIZATION",
  completed: "COMPLETE",
  failed: "FAILED",
};

const AGENT_MASTHEAD_LABELS = {
  planning: "CONSTRUCTING EXECUTION PLAN",
  plan_ready: "MISSION PLAN VALIDATED",
  executing: "AUTONOMOUS WORKFLOW ACTIVE",
  waiting_confirmation: "OWNER AUTHORIZATION REQUIRED",
  completed: "MISSION ACCOMPLISHED",
  failed: "MISSION EXECUTION FAILED",
};

function applyState(state) {
  const expression = state.expression || "happy";
  const speaking = Boolean(state.speaking);
  const agent = state.agent || {};
  const agentActive = Boolean(agent.active);

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
  } else if (expression === "thinking" || agentActive) {
    stateClass = "state-thinking";
    label = agentActive
      ? (AGENT_PHASE_LABELS[agent.phase] || "AGENT ACTIVE")
      : (state.activity_label || "THINKING");
    mastheadText = agentActive
      ? (AGENT_MASTHEAD_LABELS[agent.phase] || "AUTONOMOUS CORE ACTIVE")
      : "PROCESSING";
  }

  // Only touch the state-* classes here — updateClock owns quiet-hours, so
  // this can't be a wholesale className overwrite anymore.
  document.body.classList.remove(...STATE_CLASSES);
  document.body.classList.add(stateClass);
  document.getElementById("status-label").textContent = label;
  document.getElementById("masthead-state-text").textContent = mastheadText;
}

function applyAgentState(state) {
  const agent = state.agent || {};
  const mission = document.getElementById("agent-mission");
  const phase = String(agent.phase || "idle");
  const terminalPhase = ["completed", "failed"].includes(phase);
  const visible = Boolean(agent.active) || terminalPhase;

  const agentClasses = [
    "agent-visible",
    "agent-active",
    "agent-planning",
    "agent-executing",
    "agent-waiting",
    "agent-completed",
    "agent-failed",
  ];

  document.body.classList.remove(...agentClasses);
  document.body.classList.toggle("agent-visible", visible);
  document.body.classList.toggle("agent-active", Boolean(agent.active));
  document.body.classList.toggle(
    "agent-planning",
    phase === "planning" || phase === "plan_ready",
  );
  document.body.classList.toggle("agent-executing", phase === "executing");
  document.body.classList.toggle(
    "agent-waiting",
    phase === "waiting_confirmation",
  );
  document.body.classList.toggle("agent-completed", phase === "completed");
  document.body.classList.toggle("agent-failed", phase === "failed");

  mission.classList.toggle("visible", visible);

  if (!visible) {
    document.getElementById("agent-phase").textContent = "MISSION STANDBY";
    document.getElementById("agent-goal").textContent = "";
    document.getElementById("agent-step").textContent = "";
    document.getElementById("agent-detail").textContent = "";
    document.getElementById("agent-progress-fill").style.width = "0%";
    return;
  }

  document.getElementById("agent-phase").textContent =
    AGENT_PHASE_LABELS[phase] || "AGENT ACTIVE";

  document.getElementById("agent-goal").textContent =
    agent.goal || "AUTONOMOUS MISSION";

  const stepCount = Math.max(0, Number(agent.step_count) || 0);
  const currentStep = Math.max(0, Number(agent.current_step) || 0);
  const completedSteps = Math.max(0, Number(agent.completed_steps) || 0);

  let progress = 0;

  if (phase === "completed") {
    progress = 100;
  } else if (stepCount > 0) {
    progress = Math.min(100, (completedSteps / stepCount) * 100);
  }

  document.getElementById("agent-progress-fill").style.width =
    `${progress}%`;

  let stepText = "";

  if (phase === "planning") {
    stepText = "GENERATING VERIFIED STEPS";
  } else if (phase === "plan_ready") {
    stepText = `${stepCount} STEP${stepCount === 1 ? "" : "S"} VALIDATED`;
  } else if (phase === "executing") {
    const tool = String(agent.tool_name || "")
      .replaceAll("_", " ")
      .replaceAll(".", " · ")
      .toUpperCase();

    stepText = stepCount > 0
      ? `STEP ${Math.max(1, currentStep)}/${stepCount}${tool ? ` · ${tool}` : ""}`
      : (tool || "EXECUTING WORKFLOW");
  } else if (phase === "waiting_confirmation") {
    stepText = "VOICE CONFIRMATION REQUIRED";
  } else if (phase === "completed") {
    stepText = stepCount > 0
      ? `${completedSteps}/${stepCount} STEPS VERIFIED`
      : "ALL OBJECTIVES VERIFIED";
  } else if (phase === "failed") {
    stepText = agent.error
      ? String(agent.error).toUpperCase()
      : "EXECUTION TERMINATED";
  }

  document.getElementById("agent-step").textContent = stepText;

  const detailParts = [];
  const target = String(agent.target || "")
    .replaceAll("_", " ")
    .toUpperCase();

  if (target && (phase === "executing" || phase === "waiting_confirmation")) {
    detailParts.push(`TARGET ${target}`);
  }

  const evidence = agent.evidence;

  if (evidence && typeof evidence === "object") {
    const evidencePairs = Object.entries(evidence)
      .slice(0, 3)
      .map(([key, value]) =>
        `${String(key).replaceAll("_", " ").toUpperCase()} ${String(value).toUpperCase()}`);

    detailParts.push(...evidencePairs);
  }

  if (agent.retry_count > 0) {
    detailParts.push(`PLAN RETRIES ${agent.retry_count}`);
  }

  const inputTokens = Number(agent.input_tokens) || 0;
  const outputTokens = Number(agent.output_tokens) || 0;

  if (inputTokens > 0 || outputTokens > 0) {
    detailParts.push(`TOKENS ${inputTokens}/${outputTokens}`);
  }

  document.getElementById("agent-detail").textContent =
    detailParts.join(" · ");

  if (
    !agent.active
    && terminalPhase
    && !state.speaking
    && state.expression !== "listening"
  ) {
    document.getElementById("status-label").textContent =
      AGENT_PHASE_LABELS[phase];
    document.getElementById("masthead-state-text").textContent =
      AGENT_MASTHEAD_LABELS[phase];
  }
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

// --- JARVIS boot self-test overlay (runs once on load) --------------
(function runBootSequence() {
  const container = document.getElementById("boot-lines");
  const seq = document.getElementById("boot-sequence");
  if (!container || !seq) return;

  const subsystems = [
    "REACTOR CORE", "NEURAL LATTICE", "VOICE SYNTH", "OPTICAL SENSOR",
    "NETWORK MESH", "SECURITY GATE", "TELEMETRY LINK", "ALL SYSTEMS",
  ];
  subsystems.forEach((name, i) => {
    const row = document.createElement("div");
    row.className = "boot-line";
    row.style.animationDelay = `${0.4 + i * 0.35}s`;
    row.innerHTML = `<span>${name}</span><span class="boot-ok">ONLINE</span>`;
    container.appendChild(row);
  });

  // Remove from the DOM after the fade so it never blocks interaction.
  setTimeout(() => seq.classList.add("done"), 4600);
})();

function applyGreetingAndThreat(state) {
  const hour = new Date().getHours();
  const greetingEl = document.getElementById("masthead-greeting");
  if (greetingEl) {
    const g = hour < 5 ? "BURNING THE MIDNIGHT OIL"
      : hour < 12 ? "GOOD MORNING"
      : hour < 17 ? "GOOD AFTERNOON"
      : hour < 22 ? "GOOD EVENING" : "WORKING LATE";
    greetingEl.textContent = g;
  }

  const threat = state.threat || { level: "green" };
  const tEl = document.getElementById("threat-level");
  if (tEl) {
    tEl.textContent = threat.level.toUpperCase();
    tEl.className = threat.level;
  }
}

function applyAlertAndScreen(state) {
  const redAlert = state.red_alert || {};
  document.body.classList.toggle("red-alert", Boolean(redAlert.active));
  document.body.classList.toggle("screen-dark", Boolean(state.screen_dark));
  document.body.classList.toggle("brightness-boost", Boolean(state.brightness_boost));
  document.body.classList.toggle("recording-active", Boolean(state.recording_active));

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
  } else if (layout === "diagnostics") {
    renderDiagnostics(state);
  }
}

let lastDiagnosticsKey = "";

function renderDiagnostics(state) {
  const report = state.diagnostics_report || {};
  const findings = Array.isArray(report.findings) ? report.findings : [];

  const key = JSON.stringify(findings);
  if (key === lastDiagnosticsKey) {
    return;
  }
  lastDiagnosticsKey = key;

  const problems = findings.filter((f) => !f.ok);
  const verdict = document.getElementById("diagnostics-verdict");

  verdict.textContent = findings.length
    ? `${findings.length - problems.length}/${findings.length} NOMINAL`
    : "NO CHECKS RUN";

  const grid = document.getElementById("diagnostics-grid");
  grid.innerHTML = "";

  for (const finding of findings) {
    const row = document.createElement("div");
    row.className = `diagnostics-row ${finding.ok ? "ok" : "problem"}`;

    const status = document.createElement("div");
    status.className = "diagnostics-status";

    const text = document.createElement("div");
    text.className = "diagnostics-text";

    const component = document.createElement("div");
    component.className = "diagnostics-component";
    component.textContent = String(finding.component || "").replaceAll("_", " ");

    const detail = document.createElement("div");
    detail.className = "diagnostics-detail";
    detail.textContent = String(finding.detail || "");

    text.appendChild(component);
    text.appendChild(detail);
    row.appendChild(status);
    row.appendChild(text);
    grid.appendChild(row);
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
    applyAgentState(state);
    applyTimers(state);
    applyAuth(state);
    applyAlertAndScreen(state);
    applyGreetingAndThreat(state);
    applyLayout(state);
    applyImage(state);
    applyGallery(state);
    applyQaLog(state);
    applyWeatherOverlay(state);
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

    // JARVIS upgrade: the reactor breathes faster under real CPU load —
    // an ambient "the machine is working" cue. 40s idle -> ~12s busy.
    const spin = Math.max(12, 40 - (stats.cpu.percent / 100) * 28);
    document.documentElement.style.setProperty("--reactor-spin", `${spin}s`);
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

    const instagram = stats.instagram || {};
    const instagramPanel = document.getElementById("instagram-panel");
    const instagramFollowers = document.getElementById("instagram-followers");
    const instagramAccount = document.getElementById("instagram-account");
    const instagramLatest = document.getElementById("instagram-latest");

    if (instagram.available) {
      instagramPanel.classList.remove("offline");
      instagramFollowers.textContent = `${instagram.followers_count ?? "--"}`;
      instagramAccount.textContent =
        `${instagram.username || "ATLAS"} · ${instagram.media_count ?? "--"} POSTS`;
      const latest = instagram.latest || {};
      const performance = [];
      if (latest.views !== null && latest.views !== undefined) performance.push(`${latest.views} VIEWS`);
      if (latest.reach !== null && latest.reach !== undefined) performance.push(`${latest.reach} REACH`);
      if (latest.likes !== null && latest.likes !== undefined) performance.push(`${latest.likes} LIKES`);
      instagramLatest.textContent = performance.length
        ? `LATEST · ${performance.join(" · ")}`
        : "LATEST POST · METRICS PENDING";
    } else if (instagram.configured) {
      instagramPanel.classList.add("offline");
      instagramFollowers.textContent = "--";
      instagramAccount.textContent = "SOCIAL LINK RETRYING";
      instagramLatest.textContent = "INSTAGRAM API UNAVAILABLE";
    } else {
      instagramPanel.classList.add("offline");
      instagramFollowers.textContent = "--";
      instagramAccount.textContent = "SOCIAL LINK STANDBY";
      instagramLatest.textContent = "INSTAGRAM NOT CONNECTED";
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

// --- Full-screen weather + radar survey -----------------------------
// A dedicated overlay (separate from the always-on weather panel) with
// current conditions, a live NWS radar loop, and hourly/multi-day rain
// outlook. Data comes from /hud/weather; the overlay only polls while it
// is open, and the radar GIF is cache-busted on each refresh so the loop
// stays current on a long-running kiosk.
const WEATHER_OVERLAY_REFRESH_MS = 10 * 60 * 1000;
// Rain probability at/above this tips a bar or day amber — the umbrella cue.
const WET_THRESHOLD = 50;

let weatherOverlayTimer = null;

async function loadWeatherOverlay() {
  try {
    const response = await fetch("/hud/weather");
    const data = await response.json();
    renderWeatherOverlay(data);
  } catch (error) {
    console.error("weather overlay load failed", error);
  }
}

function renderWeatherOverlay(data) {
  const overlay = document.getElementById("weather-overlay");
  overlay.classList.toggle("stale", Boolean(data.stale));

  document.getElementById("weather-screen-city").textContent =
    (data.city || "--").toUpperCase();

  const current = data.current || {};
  document.getElementById("weather-screen-temp").textContent =
    current.temp_f !== null && current.temp_f !== undefined ? `${current.temp_f}°` : "--°";
  document.getElementById("weather-screen-cond").textContent =
    current.condition || "--";
  document.getElementById("weather-screen-humidity").textContent =
    current.humidity !== null && current.humidity !== undefined ? `${current.humidity}%` : "--%";
  document.getElementById("weather-screen-wind").textContent =
    current.wind_mph !== null && current.wind_mph !== undefined ? `${current.wind_mph} MPH` : "-- MPH";
  document.getElementById("weather-screen-precip").textContent =
    current.precip !== null && current.precip !== undefined ? `${current.precip} IN` : "-- IN";

  renderWeatherHourly(data.hourly || []);
  renderWeatherDaily(data.daily || []);

  const radar = document.getElementById("weather-radar-img");
  if (data.radar_loop_url) {
    const sep = data.radar_loop_url.includes("?") ? "&" : "?";
    radar.src = `${data.radar_loop_url}${sep}t=${Date.now()}`;
  }
}

function renderWeatherHourly(hourly) {
  const container = document.getElementById("weather-hourly");
  container.innerHTML = "";

  for (const hour of hourly) {
    const chance = hour.precip_chance ?? 0;
    const cell = document.createElement("div");
    cell.className = chance >= WET_THRESHOLD ? "weather-hour wet" : "weather-hour";

    const precip = document.createElement("div");
    precip.className = "weather-hour-precip";
    precip.textContent = `${chance}%`;

    const track = document.createElement("div");
    track.className = "weather-hour-bar-track";
    const bar = document.createElement("div");
    bar.className = "weather-hour-bar";
    bar.style.height = `${chance}%`;
    track.appendChild(bar);

    const temp = document.createElement("div");
    temp.className = "weather-hour-temp";
    temp.textContent = hour.temp_f !== null && hour.temp_f !== undefined ? `${hour.temp_f}°` : "--";

    const label = document.createElement("div");
    label.className = "weather-hour-label";
    label.textContent = hour.label || "";

    cell.append(precip, track, temp, label);
    container.appendChild(cell);
  }
}

function renderWeatherDaily(daily) {
  const container = document.getElementById("weather-daily");
  container.innerHTML = "";

  for (const day of daily) {
    const chance = day.precip_chance ?? 0;
    const row = document.createElement("div");
    row.className = chance >= WET_THRESHOLD ? "weather-day wet" : "weather-day";

    const label = document.createElement("div");
    label.className = "weather-day-label";
    label.textContent = day.label || "--";

    const cond = document.createElement("div");
    cond.className = "weather-day-cond";
    cond.textContent = day.condition || "";

    const precip = document.createElement("div");
    precip.className = "weather-day-precip";
    precip.textContent = `${chance}%`;

    const range = document.createElement("div");
    range.className = "weather-day-range";
    const hi = day.high_f ?? "--";
    const lo = day.low_f ?? "--";
    range.innerHTML = `${hi}° <span class="lo">${lo}°</span>`;

    row.append(label, cond, precip, range);
    container.appendChild(row);
  }
}

function isWeatherOverlayOpen() {
  return document.getElementById("weather-overlay").classList.contains("visible");
}

function openWeatherOverlay() {
  document.getElementById("weather-overlay").classList.add("visible");
  loadWeatherOverlay();
  clearInterval(weatherOverlayTimer);
  weatherOverlayTimer = setInterval(loadWeatherOverlay, WEATHER_OVERLAY_REFRESH_MS);
}

function closeWeatherOverlay() {
  document.getElementById("weather-overlay").classList.remove("visible");
  clearInterval(weatherOverlayTimer);
  weatherOverlayTimer = null;
}

// Server-driven open/close (voice command -> /hud/weather_overlay -> here)
// converges with the local 'w' keypress below via pushWeatherOverlayState,
// so either trigger keeps both sides in sync. lastKnownWeatherOverlayState
// starts null so the first poll always applies the server's initial value.
let lastKnownWeatherOverlayState = null;

function applyWeatherOverlay(state) {
  const shouldBeOpen = Boolean(state.weather_overlay);
  if (lastKnownWeatherOverlayState === shouldBeOpen) {
    return;
  }
  lastKnownWeatherOverlayState = shouldBeOpen;

  if (shouldBeOpen) {
    openWeatherOverlay();
  } else {
    closeWeatherOverlay();
  }
}

function pushWeatherOverlayState(open) {
  lastKnownWeatherOverlayState = open;
  fetch("/hud/weather_overlay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ open }),
  }).catch((error) => console.error("weather overlay push failed", error));
}

function toggleWeatherOverlay() {
  const next = !isWeatherOverlayOpen();
  if (next) {
    openWeatherOverlay();
  } else {
    closeWeatherOverlay();
  }
  pushWeatherOverlayState(next);
}

window.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (key === "w") {
    toggleWeatherOverlay();
  } else if (key === "escape" && isWeatherOverlayOpen()) {
    closeWeatherOverlay();
    pushWeatherOverlayState(false);
  }
});

updateClock();
setInterval(updateClock, 1000);

pollState();
setInterval(pollState, STATE_POLL_MS);

pollStats();
setInterval(pollStats, STATS_POLL_MS);

addCosmeticTerminalLine();
setInterval(addCosmeticTerminalLine, COSMETIC_TERMINAL_INTERVAL_MS);
