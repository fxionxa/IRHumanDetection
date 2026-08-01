// Infrared Human AI Edge Detection - browser control surface.
//
// The browser owns key/button state and emits a combined axis vector on every
// change. The Python side forwards it to the MCU, which integrates it into
// smooth motion. We never send repeats from here -- the backend heartbeats.

const ui = new WebUI();

// ------------------------------------------------------------- video stream
// The annotated feed (boxes + labels) is served by the model runner itself on
// port 4912, not through this page's server. Retry until it answers.
(function mountVideo() {
  const iframe = document.getElementById('videoStream');
  const placeholder = document.getElementById('videoPlaceholder');
  const url = `http://${window.location.hostname}:4912/embed`;
  let timer = null;

  iframe.onload = () => {
    if (timer) clearInterval(timer);
    placeholder.style.display = 'none';
    iframe.style.display = 'block';
  };

  const attempt = () => {
    iframe.src = url;
  };
  timer = setInterval(attempt, 1000);
  attempt();
})();

// ---------------------------------------------------------- connection pill
const connectionEl = document.getElementById('connection');

ui.on_connect(() => {
  connectionEl.textContent = 'Connected';
  connectionEl.className = 'pill pill-online';
  refreshState();
});

ui.on_disconnect(() => {
  connectionEl.textContent = 'Disconnected';
  connectionEl.className = 'pill pill-offline';
});

// ------------------------------------------------------------ pan/tilt input
//
// Held inputs live in a Set so several at once compose naturally (e.g. W+D
// pans and tilts together) and releasing one doesn't cancel the other.

const HELD = new Set();

const KEY_MAP = {
  KeyW: 'up',
  KeyS: 'down',
  KeyA: 'left',
  KeyD: 'right',
  ArrowUp: 'up',
  ArrowDown: 'down',
  ArrowLeft: 'left',
  ArrowRight: 'right',
};

const VECTORS = {
  up: { pan: 0, tilt: 1 },
  down: { pan: 0, tilt: -1 },
  left: { pan: -1, tilt: 0 },
  right: { pan: 1, tilt: 0 },
};

let lastSent = { pan: 0, tilt: 0 };

function sendAxis() {
  let pan = 0;
  let tilt = 0;
  for (const dir of HELD) {
    pan += VECTORS[dir].pan;
    tilt += VECTORS[dir].tilt;
  }
  // Opposite directions cancel; clamp so diagonals stay within range.
  pan = Math.max(-1, Math.min(1, pan));
  tilt = Math.max(-1, Math.min(1, tilt));

  if (pan === lastSent.pan && tilt === lastSent.tilt) return;
  lastSent = { pan, tilt };
  ui.send_message('pantilt_input', { pan, tilt });
  updateActiveButtons();
}

function press(dir) {
  if (!dir || HELD.has(dir)) return;
  HELD.add(dir);
  sendAxis();
}

function release(dir) {
  if (!dir || !HELD.has(dir)) return;
  HELD.delete(dir);
  sendAxis();
}

function releaseAll() {
  if (HELD.size === 0) return;
  HELD.clear();
  sendAxis();
}

// --- keyboard
window.addEventListener('keydown', e => {
  const dir = KEY_MAP[e.code];
  if (!dir) return;
  if (e.repeat) return; // auto-repeat adds nothing; motion is velocity-based
  e.preventDefault(); // stop arrow keys scrolling the page
  press(dir);
});

window.addEventListener('keyup', e => {
  const dir = KEY_MAP[e.code];
  if (!dir) return;
  e.preventDefault();
  release(dir);
});

// --- fail-safes: a lost focus must not leave the camera panning
window.addEventListener('blur', releaseAll);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) releaseAll();
});

// --- on-screen buttons (pointer events cover mouse and touch alike)
document.querySelectorAll('.dpad-btn[data-pan]').forEach(btn => {
  const dir =
    btn.dataset.tilt === '1'
      ? 'up'
      : btn.dataset.tilt === '-1'
        ? 'down'
        : btn.dataset.pan === '-1'
          ? 'left'
          : 'right';

  btn.addEventListener('pointerdown', e => {
    e.preventDefault();
    // Keep receiving the pointerup even if the cursor leaves the button.
    btn.setPointerCapture(e.pointerId);
    press(dir);
  });
  const stop = () => release(dir);
  btn.addEventListener('pointerup', stop);
  btn.addEventListener('pointercancel', stop);
  // Don't let a held button also scroll/zoom on touch.
  btn.addEventListener('contextmenu', e => e.preventDefault());
});

function updateActiveButtons() {
  document.querySelectorAll('.dpad-btn[data-pan]').forEach(btn => {
    const dir =
      btn.dataset.tilt === '1'
        ? 'up'
        : btn.dataset.tilt === '-1'
          ? 'down'
          : btn.dataset.pan === '-1'
            ? 'left'
            : 'right';
    btn.classList.toggle('active', HELD.has(dir));
  });
}

// --- recenter
document.getElementById('centerBtn').addEventListener('click', () => {
  releaseAll();
  ui.send_message('pantilt_center', {});
  setTimeout(refreshState, 400);
});

// --- speed
const speedSlider = document.getElementById('speedSlider');
const speedValue = document.getElementById('speedValue');
speedSlider.addEventListener('input', () => {
  const v = parseFloat(speedSlider.value);
  speedValue.textContent = `${Math.round(v * 100)}%`;
  ui.send_message('pantilt_speed', { speed: v });
});

// ------------------------------------------------------------- angle readout
const panValue = document.getElementById('panValue');
const tiltValue = document.getElementById('tiltValue');

async function refreshState() {
  try {
    const res = await fetch('/api/pantilt/state');
    const s = await res.json();
    if (s.error) return;
    panValue.textContent = `${s.pan.toFixed(1)}°`;
    tiltValue.textContent = `${s.tilt.toFixed(1)}°`;
  } catch {
    /* transient - the next poll will pick it up */
  }
}
setInterval(refreshState, 500);

// ------------------------------------------------------------- confidence
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceValue = document.getElementById('confidenceValue');
let confidenceTimer = null;
confidenceSlider.addEventListener('input', () => {
  const v = parseFloat(confidenceSlider.value);
  confidenceValue.textContent = v.toFixed(2);
  // Each override opens a WebSocket to the runner, so debounce the drag.
  clearTimeout(confidenceTimer);
  confidenceTimer = setTimeout(() => ui.send_message('override_th', v), 250);
});

// ------------------------------------------------------------- detections
const countsEl = document.getElementById('counts');
const recentEl = document.getElementById('recentList');
const MAX_RECENT = 8;
const recent = [];

let lastRender = 0;
const RENDER_INTERVAL = 200; // detections arrive per-frame; throttle the DOM

ui.on_message('detections', raw => {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return;
  }

  const now = Date.now();
  if (now - lastRender < RENDER_INTERVAL) return;
  lastRender = now;

  renderCounts(payload.counts || {});

  const priority = (payload.detections || []).filter(d => d.priority);
  const best = (priority.length ? priority : payload.detections || []).sort(
    (a, b) => b.confidence - a.confidence
  )[0];

  if (best) {
    recent.unshift({
      label: best.label,
      confidence: best.confidence,
      priority: best.priority,
      time: new Date(payload.timestamp),
    });
    if (recent.length > MAX_RECENT) recent.pop();
    renderRecent();
  }
});

function renderCounts(counts) {
  const labels = Object.keys(counts);
  if (labels.length === 0) {
    countsEl.innerHTML = '<p class="empty">Nothing detected.</p>';
    return;
  }
  countsEl.innerHTML = labels
    .map(
      label =>
        `<span class="chip${label === 'person' ? ' chip-priority' : ''}">${label}<b>${counts[label]}</b></span>`
    )
    .join('');
}

function renderRecent() {
  recentEl.innerHTML = recent
    .map(
      r => `<li class="${r.priority ? 'priority' : ''}">
              <span class="recent-label">${r.label}</span>
              <span class="recent-conf">${(r.confidence * 100).toFixed(0)}%</span>
              <span class="recent-time">${r.time.toLocaleTimeString()}</span>
            </li>`
    )
    .join('');
}
