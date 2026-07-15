const STATE_POLL_MS = 1000;
const STATS_POLL_MS = 5000;

let lastQaTimestamp = 0;

function pad(value) {
  return String(value).padStart(2, "0");
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
}

function applyState(state) {
  const expression = state.expression || "happy";
  const speaking = Boolean(state.speaking);

  let stateClass = "state-idle";
  let label = "IDLE";

  if (speaking || expression === "talking") {
    stateClass = "state-speaking";
    label = "SPEAKING";
  } else if (expression === "listening") {
    stateClass = "state-listening";
    label = "LISTENING";
  } else if (expression === "thinking") {
    stateClass = "state-thinking";
    label = "THINKING";
  }

  document.body.className = stateClass;
  document.getElementById("status-label").textContent = label;
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
    applyQaLog(state);
  } catch (error) {
    console.error("state poll failed", error);
  }
}

async function pollStats() {
  try {
    const response = await fetch("/hud/stats");
    const stats = await response.json();

    document.getElementById("cpu-percent").textContent = `${stats.cpu.percent}%`;
    document.getElementById("cpu-temp").textContent =
      stats.cpu.temp_c !== null ? `${stats.cpu.temp_c} °C` : "-- °C";

    document.getElementById("disk-percent").textContent = `${stats.disk.percent}%`;
    document.getElementById("disk-detail").textContent =
      `${stats.disk.used_gb} / ${stats.disk.total_gb} GB`;

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
