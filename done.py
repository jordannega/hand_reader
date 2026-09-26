#!/usr/bin/env python3
"""
✋ GODTIER Hand Sign Reader v2 — single-file Python app.

Runs a tiny local HTTP server + opens your browser.
The HTML/JS (using MediaPipe Tasks Vision) is embedded below.

USAGE:
    python hand_reader_v2.py

Then the browser opens automatically to http://127.0.0.1:8000/
Click START CAMERA, allow webcam.

v2 CHANGES:
  - Thumb detection rebuilt as a 3-signal ENSEMBLE VOTE (angle + two scale-
    invariant distance ratios) instead of one strict AND condition. This is
    what fixes "5 gets read as 4 / thumb not recognized" — a single brittle
    threshold was throwing out the extended-thumb vote too easily; a
    majority-vote across independent signals is much more forgiving.
  - A sensitivity slider so you can tune the thumb vote live for your
    camera/lighting instead of being stuck with hardcoded constants.
  - Two-hand COMBO gestures (not just number-summing): both palms open and
    close together = 🙏, etc.
  - New single-hand signs: 🫰 finger heart, 🤞 crossed fingers (best-effort),
    ASL-ish pinky-only "I".
  - Tally Counter mode: 👍 taps up a running counter, ✊ resets it.
  - Practice / Simon-Says mini-game: the app calls out a random sign, you
    have to make it before the timer runs out, score + streak tracked.
  - Voice feedback (speaks the reading aloud) via the Web Speech API.
  - Live hand-detection confidence bar + FPS meter.
  - Snapshot button — download the current camera+overlay frame as a PNG.
  - Gesture history log (last 10 readings, timestamped).
"""

import http.server
import socketserver
import webbrowser
import threading
import sys
import socket

PORT = 8000
HOST = "127.0.0.1"

# ─────────────────────────────────────────────────────────
#  EMBEDDED HTML APP
# ─────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>✋ GODTIER Hand Sign Reader</title>
<style>
  * { box-sizing: border-box; font-family: system-ui, -apple-system, sans-serif; }
  html, body {
    margin: 0; padding: 0; min-height: 100vh;
    background: radial-gradient(circle at 50% 0%, #1a2540 0%, #0a0e18 60%);
    color: #eef4ff;
    display: flex; flex-direction: column; align-items: center;
    gap: 16px; padding: 24px;
  }
  h1 {
    margin: 0; font-weight: 600; font-size: 1.8rem; letter-spacing: 2px;
    background: linear-gradient(90deg, #7aa9ff, #b3e0ff, #7aa9ff);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    text-align: center; width: 100%;
  }
  .layout {
    display: flex; gap: 22px; align-items: flex-start; flex-wrap: wrap;
    justify-content: center; width: 100%; max-width: 1200px; margin: 0 auto;
  }
  .stagecol { display: flex; flex-direction: column; gap: 14px; align-items: center; order: 2; }
  .stage {
    position: relative;
    width: min(640px, 92vw);
    aspect-ratio: 4 / 3;
    border-radius: 24px;
    overflow: hidden;
    background: #050810;
    box-shadow: 0 25px 60px rgba(0,0,0,0.75), 0 0 0 1px rgba(120,160,255,0.25),
                0 0 60px rgba(60,110,255,0.15);
    transition: box-shadow 0.25s;
    flex-shrink: 0;
  }
  .stage.active {
    box-shadow: 0 25px 60px rgba(0,0,0,0.75), 0 0 0 2px #4c8bff,
                0 0 80px rgba(60,110,255,0.5);
  }
  .stage video, .stage canvas {
    position: absolute; inset: 0;
    width: 100%; height: 100%; object-fit: cover;
    transform: scaleX(-1);
  }
  .stage canvas { pointer-events: none; }
  .overlay-msg {
    position: absolute; inset: 0; display: flex;
    align-items: center; justify-content: center;
    color: #9bb5d9; font-size: 1.05rem; text-align: center;
    padding: 20px; line-height: 1.6;
    background: rgba(5,8,16,0.85);
    backdrop-filter: blur(6px);
    transition: opacity 0.3s; z-index: 5;
  }
  .overlay-msg.hidden { opacity: 0; pointer-events: none; }
  .overlay-msg .err { color: #ff8a8a; font-weight: 700; font-size: 1.15rem; }
  .practice-banner {
    position: absolute; top: 10px; left: 10px; right: 10px; z-index: 6;
    background: rgba(10,15,28,0.88); border: 1px solid #4c8bff; border-radius: 14px;
    padding: 10px 14px; display: none; align-items: center; justify-content: space-between;
    gap: 10px; backdrop-filter: blur(6px);
  }
  .practice-banner.show { display: flex; }
  .practice-banner .target { font-size: 1.6rem; }
  .practice-banner .ptimer { font-weight: 800; color: #ffd966; font-size: 1.1rem; }
  .practice-banner.correct { border-color: #63e6a3; }

  .btnrow { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }
  button {
    background: linear-gradient(180deg, #3b62a0, #2d4c82);
    border: 1px solid #5580c0; color: #fff;
    font-size: 1rem; font-weight: 700; letter-spacing: 0.5px;
    padding: 13px 32px; border-radius: 50px; cursor: pointer;
    transition: transform 0.15s, box-shadow 0.2s;
    box-shadow: 0 8px 20px rgba(0,60,180,0.5);
  }
  button.secondary { background: linear-gradient(180deg, #2c3549, #1f2636); border-color: #3a465e; box-shadow: none; }
  button.danger { background: linear-gradient(180deg, #a03b3b, #822d2d); border-color: #c05555; }
  button:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 12px 26px rgba(0,80,220,0.6); }
  button:disabled { opacity: 0.55; pointer-events: none; }
  .hint { color: #7a90ad; font-size: 0.8rem; text-align: center; max-width: 640px; }

  .sidepanel {
    width: min(340px, 92vw);
    display: flex; flex-direction: column; gap: 14px;
    max-height: 82vh; overflow-y: auto; padding-right: 4px;
    order: 1;
  }
  .sidepanel::-webkit-scrollbar { width: 6px; }
  .sidepanel::-webkit-scrollbar-thumb { background: #2e3b52; border-radius: 4px; }
  .card {
    background: rgba(26,33,48,0.9); border: 1px solid #2e3b52;
    border-radius: 18px; padding: 16px 18px; backdrop-filter: blur(8px);
    flex-shrink: 0;
  }
  .card h3 {
    margin: 0 0 10px; font-size: 0.78rem; letter-spacing: 1.5px;
    color: #7a90ad; text-transform: uppercase;
    display: flex; justify-content: space-between; align-items: center;
  }
  .digit {
    font-size: clamp(3.6rem, 11vw, 5.4rem);
    font-weight: 900; line-height: 1;
    min-height: 1.1em; text-align: center;
    color: #fff; letter-spacing: 4px;
    text-shadow: 0 0 20px #3f7eff, 0 0 60px #003cff, 0 0 100px #003cff;
    transition: transform 0.15s;
  }
  .digit.pop { transform: scale(1.08); }
  .gesture-name {
    text-align: center; font-size: 1.1rem; font-weight: 700; color: #ffd966;
    min-height: 1.4em; text-shadow: 0 0 16px rgba(255,217,102,0.5);
  }
  .gesture-emoji { font-size: 2.2rem; text-align: center; display: block; margin-bottom: 2px; }

  .hand-row { display: flex; gap: 10px; margin-top: 6px; }
  .hand-block { flex: 1; background: #16202f; border-radius: 12px; padding: 8px 10px; }
  .hand-block .hlabel { font-size: 0.7rem; color: #7a90ad; margin-bottom: 6px; }
  .fingers { display: flex; gap: 4px; justify-content: space-between; }
  .fdot {
    flex: 1; height: 24px; border-radius: 7px; display: flex; align-items: center;
    justify-content: center; font-size: 0.6rem; font-weight: 800; color: #465b7a;
    background: #202e44; transition: all 0.15s; border: 1px solid #2c3b55;
  }
  .fdot.on {
    background: linear-gradient(180deg, #4c8bff, #2d5fc7); color: #fff;
    box-shadow: 0 0 10px rgba(76,139,255,0.7); border-color: #6fa2ff;
  }

  .status { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 10px; }
  .badge {
    background: #2e3f5e; padding: 5px 12px; border-radius: 40px;
    color: #c6ddff; font-size: 0.78rem;
    display: flex; gap: 6px; align-items: center;
  }
  .badge span {
    color: #fff; font-weight: 800; background: #3d5a8a;
    padding: 2px 9px; border-radius: 30px; min-width: 22px; text-align: center;
  }
  .meter { height: 6px; border-radius: 4px; background: #202e44; overflow: hidden; margin-top: 6px; }
  .meter-fill { height: 100%; background: linear-gradient(90deg, #4c8bff, #63e6a3); width: 0%; transition: width 0.2s; }

  .tally-value { font-size: 2.6rem; font-weight: 900; text-align: center; color: #63e6a3;
    text-shadow: 0 0 18px rgba(99,230,163,0.6); }
  .tally-sub { text-align: center; color: #7a90ad; font-size: 0.75rem; margin-top: 2px; }

  .glossary { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; }
  .glossary div { font-size: 0.75rem; color: #a9c0e0; display: flex; gap: 6px; align-items: center; }
  .glossary div b { font-size: 1rem; }

  .history-list { display: flex; flex-direction: column; gap: 5px; max-height: 180px; overflow-y: auto; }
  .history-item {
    display: flex; justify-content: space-between; align-items: center;
    background: #16202f; padding: 5px 10px; border-radius: 8px; font-size: 0.78rem;
  }
  .history-item .htime { color: #5f7392; font-size: 0.68rem; }
  .history-empty { color: #5f7392; font-size: 0.78rem; text-align: center; padding: 6px; }

  .toggle-row { display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; color: #c6ddff; margin-bottom: 8px; }
  .toggle-row input[type=checkbox] { width: 18px; height: 18px; accent-color: #4c8bff; }
  .slider-row { display: flex; flex-direction: column; gap: 4px; font-size: 0.78rem; color: #a9c0e0; }
  .slider-row input[type=range] { accent-color: #4c8bff; width: 100%; }
  .miniBtn {
    padding: 8px 14px; font-size: 0.82rem; border-radius: 30px; width: 100%; margin-top: 8px;
  }
</style>
</head>
<body>

<h1>✋ GODTIER HAND SIGN READER</h1>

<div class="layout">
  <div class="stagecol">
    <div class="stage" id="stage">
      <video id="video" autoplay playsinline muted></video>
      <canvas id="canvas" width="640" height="480"></canvas>
      <div class="practice-banner" id="practiceBanner">
        <div>Show: <span class="target" id="practiceTarget">—</span></div>
        <div class="ptimer" id="practiceTimer">–</div>
        <div>Score <b id="practiceScore">0</b> · Streak <b id="practiceStreak">0</b></div>
      </div>
      <div class="overlay-msg" id="overlay">
        Click <b>START CAMERA</b> below to begin
      </div>
    </div>
    <div class="btnrow">
      <button id="startBtn">▶ START CAMERA</button>
      <button id="practiceBtn" class="secondary" disabled>🎮 Practice Mode</button>
      <button id="snapBtn" class="secondary" disabled>📸 Snapshot</button>
    </div>
    <div class="hint">
      Show 1–2 hands. Numbers 0–5 per hand, or combine both hands for up to 10.
      Move both palms close together for combo signs like 🙏.
    </div>
  </div>

  <div class="sidepanel">
    <div class="card">
      <h3>Reading</h3>
      <span class="gesture-emoji" id="gestureEmoji">🖐️</span>
      <div class="digit" id="digit">—</div>
      <div class="gesture-name" id="gestureName">show a hand…</div>
    </div>

    <div class="card">
      <h3>Hands</h3>
      <div class="hand-row" id="handRow">
        <div class="hand-block">
          <div class="hlabel">HAND 1</div>
          <div class="fingers" id="fingers0">
            <div class="fdot">T</div><div class="fdot">I</div><div class="fdot">M</div>
            <div class="fdot">R</div><div class="fdot">P</div>
          </div>
        </div>
        <div class="hand-block">
          <div class="hlabel">HAND 2</div>
          <div class="fingers" id="fingers1">
            <div class="fdot">T</div><div class="fdot">I</div><div class="fdot">M</div>
            <div class="fdot">R</div><div class="fdot">P</div>
          </div>
        </div>
      </div>
      <div class="status">
        <div class="badge">🔢 total <span id="totalCount">0</span></div>
        <div class="badge">⚙️ <span id="statusText">idle</span></div>
        <div class="badge">🎞️ <span id="fpsText">0</span> fps</div>
      </div>
      <div style="margin-top:8px; font-size:0.72rem; color:#7a90ad;">detection confidence</div>
      <div class="meter"><div class="meter-fill" id="confMeter"></div></div>
    </div>

    <div class="card">
      <h3>Tally Counter</h3>
      <div class="tally-value" id="tallyValue">0</div>
      <div class="tally-sub">👍 hold to +1 · ✊ hold to reset</div>
      <button class="secondary miniBtn" id="tallyResetBtn">↺ Reset Tally</button>
    </div>

    <div class="card">
      <h3>Settings</h3>
      <div class="toggle-row">
        <span>🔊 Speak readings aloud</span>
        <input type="checkbox" id="voiceToggle" />
      </div>
      <div class="slider-row">
        <span>Thumb sensitivity: <b id="sensVal">1.00</b></span>
        <input type="range" id="sensSlider" min="0.7" max="1.6" step="0.05" value="1.0" />
      </div>
    </div>

    <div class="card">
      <h3>Supported Signs</h3>
      <div class="glossary">
        <div><b>✊</b> 0 / Fist</div>
        <div><b>☝️</b> 1 / Point</div>
        <div><b>✌️</b> 2 / Peace</div>
        <div><b>🤟</b> 3</div>
        <div><b>🖖</b> 4</div>
        <div><b>🖐️</b> 5 / Palm</div>
        <div><b>🔟</b> 6–10 (2 hands)</div>
        <div><b>👍</b> Thumbs Up</div>
        <div><b>👎</b> Thumbs Down</div>
        <div><b>👌</b> OK Sign</div>
        <div><b>🤘</b> Rock On</div>
        <div><b>🤙</b> Call Me / 6</div>
        <div><b>🥰</b> I Love You</div>
        <div><b>🔫</b> Gun / L-Shape</div>
        <div><b>🖖</b> Vulcan Salute</div>
        <div><b>🫰</b> Finger Heart</div>
        <div><b>🤞</b> Crossed Fingers*</div>
        <div><b>🤙</b> ASL "I" (pinky)</div>
        <div><b>🙏</b> Prayer (2 hands)</div>
        <div><b>💕</b> Heart (2 hands)</div>
        <div><b>👍👍</b> Double Thumbs Up</div>
      </div>
      <div style="font-size:0.65rem;color:#5f7392;margin-top:6px;">*best-effort, can be finicky</div>
    </div>

    <div class="card">
      <h3>History <button class="secondary miniBtn" id="clearHistBtn" style="width:auto;margin:0;padding:4px 10px;font-size:0.7rem;">clear</button></h3>
      <div class="history-list" id="historyList">
        <div class="history-empty">nothing yet</div>
      </div>
    </div>
  </div>
</div>

<script type="module">
const video          = document.getElementById('video');
const canvas         = document.getElementById('canvas');
const ctx             = canvas.getContext('2d');
const digitEl         = document.getElementById('digit');
const gestureNameEl   = document.getElementById('gestureName');
const gestureEmojiEl  = document.getElementById('gestureEmoji');
const totalCountEl    = document.getElementById('totalCount');
const statusEl        = document.getElementById('statusText');
const fpsEl            = document.getElementById('fpsText');
const confMeter        = document.getElementById('confMeter');
const startBtn         = document.getElementById('startBtn');
const practiceBtn      = document.getElementById('practiceBtn');
const snapBtn          = document.getElementById('snapBtn');
const stage            = document.getElementById('stage');
const overlay          = document.getElementById('overlay');
const fingerRows       = [document.getElementById('fingers0'), document.getElementById('fingers1')];
const tallyValueEl     = document.getElementById('tallyValue');
const tallyResetBtn    = document.getElementById('tallyResetBtn');
const voiceToggle      = document.getElementById('voiceToggle');
const sensSlider       = document.getElementById('sensSlider');
const sensVal          = document.getElementById('sensVal');
const historyList      = document.getElementById('historyList');
const clearHistBtn     = document.getElementById('clearHistBtn');
const practiceBanner   = document.getElementById('practiceBanner');
const practiceTargetEl = document.getElementById('practiceTarget');
const practiceTimerEl  = document.getElementById('practiceTimer');
const practiceScoreEl  = document.getElementById('practiceScore');
const practiceStreakEl = document.getElementById('practiceStreak');

let landmarker = null;
let stream     = null;
let running    = false;
let rafId      = null;
let lastVideoT = -1;

// ── tunables ─────────────────────────────────────────────
let thumbSensitivity = 1.0; // adjustable live via slider
sensSlider.addEventListener('input', () => {
  thumbSensitivity = parseFloat(sensSlider.value);
  sensVal.textContent = thumbSensitivity.toFixed(2);
});

// ── temporal smoothing ──────────────────────────────────
// require the same reading for N consecutive frames before it "sticks",
// so results don't flicker between two guesses every frame.
const STABLE_FRAMES = 4;
let candidate = null;
let candidateStreak = 0;
let stableReading = null; // { display, emoji, name }

// ── tally counter (edge-triggered so it doesn't spam +1 every frame) ──
let tally = 0;
let tallyArmed = true; // re-arms once the hand leaves the thumbs-up pose
tallyResetBtn.addEventListener('click', () => { tally = 0; tallyValueEl.textContent = '0'; });

// ── history log ──────────────────────────────────────────
let history = [];
clearHistBtn.addEventListener('click', () => { history = []; renderHistory(); });
function pushHistory(reading) {
  if (!reading) return;
  const t = new Date();
  const stamp = t.toLocaleTimeString([], { hour12: false });
  history.unshift({ stamp, emoji: reading.emoji, name: reading.name });
  history = history.slice(0, 10);
  renderHistory();
}
function renderHistory() {
  if (history.length === 0) {
    historyList.innerHTML = '<div class="history-empty">nothing yet</div>';
    return;
  }
  historyList.innerHTML = history.map(h =>
    `<div class="history-item"><span>${h.emoji} ${h.name}</span><span class="htime">${h.stamp}</span></div>`
  ).join('');
}

// ── voice feedback ───────────────────────────────────────
function speak(text) {
  if (!voiceToggle.checked) return;
  if (!('speechSynthesis' in window)) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.15; u.pitch = 1.0; u.volume = 0.9;
    window.speechSynthesis.speak(u);
  } catch (e) { /* speech not available, ignore */ }
}

// ── practice / simon-says mode ───────────────────────────
const PRACTICE_POOL = [
  { display: '0', name: 'Fist (0)' },
  { display: '1', name: 'Point (1)' },
  { display: '2', name: 'Peace (2)' },
  { display: '5', name: 'Open Palm (5)' },
  { display: '👍', name: 'Thumbs Up' },
  { display: '👎', name: 'Thumbs Down' },
  { display: 'OK', name: 'OK Sign' },
  { display: '🤘', name: 'Rock On' },
  { display: '6', name: 'Call Me / Shaka (6)' },
  { display: '🥰', name: 'I Love You' },
  { display: '🫰', name: 'Finger Heart' }
];
let practiceActive = false;
let practiceTarget = null;
let practiceScore = 0;
let practiceStreak = 0;
let practiceTimeLeft = 0;
let practiceIntervalId = null;
const PRACTICE_ROUND_SECONDS = 9;

function pickPracticeTarget() {
  let next;
  do { next = PRACTICE_POOL[Math.floor(Math.random() * PRACTICE_POOL.length)]; }
  while (practiceTarget && next.name === practiceTarget.name && PRACTICE_POOL.length > 1);
  practiceTarget = next;
  practiceTimeLeft = PRACTICE_ROUND_SECONDS;
  practiceTargetEl.textContent = practiceTarget.display + ' ' + practiceTarget.name;
  practiceBanner.classList.remove('correct');
}

function startPractice() {
  practiceActive = true;
  practiceScore = 0; practiceStreak = 0;
  practiceScoreEl.textContent = '0'; practiceStreakEl.textContent = '0';
  practiceBanner.classList.add('show');
  practiceBtn.textContent = '⏹ Stop Practice';
  pickPracticeTarget();
  clearInterval(practiceIntervalId);
  practiceIntervalId = setInterval(() => {
    practiceTimeLeft -= 1;
    practiceTimerEl.textContent = practiceTimeLeft + 's';
    if (practiceTimeLeft <= 0) {
      practiceStreak = 0;
      practiceStreakEl.textContent = '0';
      pickPracticeTarget();
    }
  }, 1000);
}
function stopPractice() {
  practiceActive = false;
  practiceBanner.classList.remove('show');
  practiceBtn.textContent = '🎮 Practice Mode';
  clearInterval(practiceIntervalId);
}
practiceBtn.addEventListener('click', () => {
  if (practiceActive) stopPractice(); else startPractice();
});
function checkPracticeMatch(reading) {
  if (!practiceActive || !reading || !practiceTarget) return;
  if (reading.name === practiceTarget.name) {
    practiceScore++; practiceStreak++;
    practiceScoreEl.textContent = String(practiceScore);
    practiceStreakEl.textContent = String(practiceStreak);
    practiceBanner.classList.add('correct');
    speak('Yes! ' + practiceTarget.name);
    setTimeout(pickPracticeTarget, 500);
  }
}

// ── snapshot ──────────────────────────────────────────────
snapBtn.addEventListener('click', () => {
  const out = document.createElement('canvas');
  out.width = canvas.width; out.height = canvas.height;
  const octx = out.getContext('2d');
  // mirror to match what's on screen
  octx.translate(out.width, 0); octx.scale(-1, 1);
  octx.drawImage(video, 0, 0, out.width, out.height);
  octx.drawImage(canvas, 0, 0, out.width, out.height);
  const link = document.createElement('a');
  link.download = 'hand-sign-' + Date.now() + '.png';
  link.href = out.toDataURL('image/png');
  link.click();
});

// ─────────────────────────────────────────────────────────
//  MODEL LOADING
// ─────────────────────────────────────────────────────────
async function loadModel() {
  statusEl.textContent = 'loading model…';
  const vision = await import(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs'
  );
  const fileset = await vision.FilesetResolver.forVisionTasks(
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm'
  );
  landmarker = await vision.HandLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath:
        'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
      delegate: 'GPU'
    },
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.5,
    minHandPresenceConfidence: 0.5,
    minTrackingConfidence: 0.5
  });
  statusEl.textContent = 'model ready';
}

// ─────────────────────────────────────────────────────────
//  GEOMETRY HELPERS
// ─────────────────────────────────────────────────────────
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

// angle (degrees) at vertex b, formed by rays b->a and b->c.
// ~180° = perfectly straight, small = sharply bent.
function angleAt(a, b, c) {
  const v1 = { x: a.x - b.x, y: a.y - b.y };
  const v2 = { x: c.x - b.x, y: c.y - b.y };
  const m1 = Math.hypot(v1.x, v1.y), m2 = Math.hypot(v2.x, v2.y);
  if (m1 < 1e-6 || m2 < 1e-6) return 180;
  const cosv = (v1.x * v2.x + v1.y * v2.y) / (m1 * m2);
  return Math.acos(Math.min(1, Math.max(-1, cosv))) * 180 / Math.PI;
}

// ─────────────────────────────────────────────────────────
//  THUMB: 3-SIGNAL ENSEMBLE VOTE (this is the fix for "5 not working")
//
//  Old approach demanded a single AND of angle + one distance check, which
//  is brittle: any one noisy measurement kills the whole detection and an
//  actually-open thumb gets called "folded", so 5 collapses into 4. Here
//  three independent, mostly-uncorrelated signals each cast a vote, and we
//  just need a majority (2 of 3). A sensitivity slider scales how easy it
//  is to trigger each vote, for different hands/cameras/lighting.
// ─────────────────────────────────────────────────────────
function isThumbExtended(lm, handSize, sensitivity) {
  // Signal 1: how straight the thumb is at the IP joint.
  const thumbAngle = angleAt(lm[2], lm[3], lm[4]);
  const angleVote = thumbAngle > (140 - (sensitivity - 1) * 25);

  // Signal 2: scale-invariant — tip should be farther from the pinky-side
  // of the palm than the IP joint is, once the thumb splays outward.
  const tipFromPinky = dist(lm[4], lm[17]);
  const ipFromPinky  = dist(lm[3], lm[17]);
  const spreadVote = tipFromPinky > ipFromPinky * (1.0 / sensitivity);

  // Signal 3: a folded thumb tucks its tip in close to the index-finger
  // base; an extended thumb's tip sits well clear of it.
  const tipFromIndexMcp = dist(lm[4], lm[5]) / handSize;
  const clearanceVote = tipFromIndexMcp > (0.5 / sensitivity);

  const votes = [angleVote, spreadVote, clearanceVote].filter(Boolean).length;
  return votes >= 2;
}

// ─────────────────────────────────────────────────────────
//  ROTATION-INVARIANT FINGER STATE ANALYSIS
// ─────────────────────────────────────────────────────────
function analyzeHand(lm) {
  const wrist = lm[0];
  const palmCenter = {
    x: (lm[0].x + lm[5].x + lm[9].x + lm[13].x + lm[17].x) / 5,
    y: (lm[0].y + lm[5].y + lm[9].y + lm[13].y + lm[17].y) / 5
  };
  const handSize = dist(lm[0], lm[9]) || 0.001; // wrist -> middle MCP, used to normalize

  const fingerDefs = {
    index:  [5, 6, 8],
    middle: [9, 10, 12],
    ring:   [13, 14, 16],
    pinky:  [17, 18, 20]
  };
  const fingers = {};
  for (const [name, [mcp, pip, tip]] of Object.entries(fingerDefs)) {
    const ang = angleAt(lm[mcp], lm[pip], lm[tip]);
    const tipFar = dist(lm[tip], wrist) > dist(lm[pip], wrist);
    fingers[name] = ang > 158 && tipFar;
  }
  fingers.thumb = isThumbExtended(lm, handSize, thumbSensitivity);

  const count = Object.values(fingers).filter(Boolean).length;

  // thumb direction, used for thumbs-up / thumbs-down — computed relative
  // to the hand's own "up" axis (wrist -> middle MCP) so it works even if
  // the hand/camera is tilted, not just relative to raw screen Y.
  const handUp = { x: lm[9].x - lm[0].x, y: lm[9].y - lm[0].y };
  const thumbVec = { x: lm[4].x - lm[2].x, y: lm[4].y - lm[2].y };
  const handUpMag = Math.hypot(handUp.x, handUp.y) || 1;
  const thumbMag = Math.hypot(thumbVec.x, thumbVec.y) || 1;
  const dotp = (handUp.x * thumbVec.x + handUp.y * thumbVec.y) / (handUpMag * thumbMag);
  const thumbPointsUp = dotp < -0.4;
  const thumbPointsDown = dotp > 0.4;

  return { fingers, count, palmCenter, handSize, thumbPointsUp, thumbPointsDown, lm };
}

// ─────────────────────────────────────────────────────────
//  GESTURE CLASSIFICATION (single hand)
// ─────────────────────────────────────────────────────────
function classifyGesture(hand) {
  const f = hand.fingers;
  const on  = (...names) => names.every(n => f[n]);
  const off = (...names) => names.every(n => !f[n]);
  const lm = hand.lm;
  const hs = hand.handSize;

  const thumbIndexDist = dist(lm[4], lm[8]) / hs;

  // Finger heart 🫰: thumb + index tips crossed/touching, other three curled.
  if (thumbIndexDist < 0.35 && off('middle', 'ring', 'pinky')) {
    return { display: '🫰', emoji: '🫰', name: 'Finger Heart' };
  }

  // OK sign: thumb tip and index tip pinched together, other 3 extended.
  if (thumbIndexDist < 0.45 && on('middle', 'ring', 'pinky')) {
    return { display: 'OK', emoji: '👌', name: 'OK Sign' };
  }

  // thumbs up / down: only thumb extended.
  if (on('thumb') && off('index', 'middle', 'ring', 'pinky')) {
    if (hand.thumbPointsUp)   return { display: '👍', emoji: '👍', name: 'Thumbs Up' };
    if (hand.thumbPointsDown) return { display: '👎', emoji: '👎', name: 'Thumbs Down' };
    return { display: '1', emoji: '👍', name: 'Thumb Out' };
  }

  // rock on: index + pinky extended, middle/ring/thumb curled.
  if (on('index', 'pinky') && off('middle', 'ring', 'thumb')) {
    return { display: '🤘', emoji: '🤘', name: 'Rock On' };
  }

  // call me / shaka: thumb + pinky extended, others curled.
  if (on('thumb', 'pinky') && off('index', 'middle', 'ring')) {
    return { display: '6', emoji: '🤙', name: 'Call Me / Shaka (6)' };
  }

  // pinky only (ASL "I")
  if (on('pinky') && off('thumb', 'index', 'middle', 'ring')) {
    return { display: 'I', emoji: '🤙', name: 'Pinky / ASL "I"' };
  }

  // I love you (ASL): thumb + index + pinky extended, middle/ring curled.
  if (on('thumb', 'index', 'pinky') && off('middle', 'ring')) {
    return { display: '🥰', emoji: '🥰', name: 'I Love You' };
  }

  // gun / L-shape: thumb + index extended, others curled, tips apart.
  if (on('thumb', 'index') && off('middle', 'ring', 'pinky') && thumbIndexDist > 0.35) {
    return { display: 'L', emoji: '🔫', name: 'Gun / L-Shape' };
  }

  // crossed fingers (best-effort): index + middle extended and tips very
  // close together with their left-right order flipped versus their bases.
  if (on('index', 'middle') && off('ring', 'pinky')) {
    const tipGap = dist(lm[8], lm[12]) / hs;
    const mcpOrder = lm[5].x < lm[9].x;
    const tipOrder = lm[8].x < lm[12].x;
    if (tipGap < 0.28 && mcpOrder !== tipOrder) {
      return { display: '🤞', emoji: '🤞', name: 'Crossed Fingers' };
    }
  }

  // vulcan / 4-way split check
  if (on('index', 'middle', 'ring', 'pinky') && off('thumb')) {
    const gap = dist(lm[12], lm[16]) / hs;
    const idxMidGap = dist(lm[8], lm[12]) / hs;
    const ringPinkyGap = dist(lm[16], lm[20]) / hs;
    if (gap > idxMidGap * 1.8 && gap > ringPinkyGap * 1.8) {
      return { display: '🖖', emoji: '🖖', name: 'Vulcan Salute' };
    }
    return { display: '4', emoji: '🖖', name: '4' };
  }

  // plain numeric fallbacks 0–5
  if (off('thumb', 'index', 'middle', 'ring', 'pinky')) {
    return { display: '0', emoji: '✊', name: 'Fist (0)' };
  }
  if (on('index') && off('thumb', 'middle', 'ring', 'pinky')) {
    return { display: '1', emoji: '☝️', name: 'Point (1)' };
  }
  if (on('index', 'middle') && off('thumb', 'ring', 'pinky')) {
    return { display: '2', emoji: '✌️', name: 'Peace (2)' };
  }
  if (on('thumb', 'index', 'middle') && off('ring', 'pinky')) {
    return { display: '3', emoji: '🤟', name: '3' };
  }
  if (on('index', 'middle', 'ring') && off('thumb', 'pinky')) {
    return { display: '3', emoji: '🖐️', name: '3' };
  }
  if (on('thumb', 'index', 'middle', 'ring', 'pinky')) {
    return { display: '5', emoji: '🖐️', name: 'Open Palm (5)' };
  }

  return { display: String(hand.count), emoji: '🖐️', name: `${hand.count} fingers` };
}

// ─────────────────────────────────────────────────────────
//  TWO-HAND COMBO GESTURES
// ─────────────────────────────────────────────────────────
function classifyTwoHand(h1, h2, g1, g2) {
  const proximity = dist(h1.palmCenter, h2.palmCenter) / ((h1.handSize + h2.handSize) / 2);
  const close = proximity < 2.2;

  if (close && g1.name.startsWith('Open Palm') && g2.name.startsWith('Open Palm')) {
    return { display: '🙏', emoji: '🙏', name: 'Prayer Hands' };
  }
  if (close && g1.name === 'Finger Heart' && g2.name === 'Finger Heart') {
    return { display: '💕', emoji: '💕', name: 'Two-Hand Heart' };
  }
  if (g1.name === 'Thumbs Up' && g2.name === 'Thumbs Up') {
    return { display: '👍👍', emoji: '👍', name: 'Double Thumbs Up' };
  }
  if (g1.name === 'Rock On' && g2.name === 'Rock On') {
    return { display: '🤘🤘', emoji: '🤘', name: 'Double Rock On' };
  }
  if (close && g1.name === 'Fist (0)' && g2.name === 'Fist (0)') {
    return { display: '👊👊', emoji: '👊', name: 'Fist Bump' };
  }
  return null;
}

// ─────────────────────────────────────────────────────────
//  DRAWING
// ─────────────────────────────────────────────────────────
const CONNECTIONS = [
  [0,1],[1,2],[2,3],[3,4],
  [0,5],[5,6],[6,7],[7,8],
  [5,9],[9,10],[10,11],[11,12],
  [9,13],[13,14],[14,15],[15,16],
  [13,17],[17,18],[18,19],[19,20],
  [0,17]
];
function drawSkeleton(lm, colorMain, colorTip) {
  ctx.lineWidth = 3;
  ctx.strokeStyle = colorMain;
  ctx.shadowColor = colorMain;
  ctx.shadowBlur = 8;
  for (const [i, j] of CONNECTIONS) {
    ctx.beginPath();
    ctx.moveTo(lm[i].x * canvas.width, lm[i].y * canvas.height);
    ctx.lineTo(lm[j].x * canvas.width, lm[j].y * canvas.height);
    ctx.stroke();
  }
  ctx.shadowBlur = 12;
  for (let i = 0; i < lm.length; i++) {
    const isTip = [4,8,12,16,20].includes(i);
    ctx.beginPath();
    ctx.arc(lm[i].x * canvas.width, lm[i].y * canvas.height,
            isTip ? 7 : 4, 0, Math.PI * 2);
    ctx.fillStyle = isTip ? colorTip : colorMain;
    ctx.shadowColor = isTip ? colorTip : colorMain;
    ctx.fill();
  }
  ctx.shadowBlur = 0;
}
const HAND_COLORS = [
  { main: '#4c8bff', tip: '#ffd966' },
  { main: '#ff6bd6', tip: '#7fffd4' }
];

// ─────────────────────────────────────────────────────────
//  UI UPDATE
// ─────────────────────────────────────────────────────────
function updateFingerRow(rowEl, fingers) {
  const order = ['thumb', 'index', 'middle', 'ring', 'pinky'];
  const dots = rowEl.querySelectorAll('.fdot');
  order.forEach((name, i) => dots[i].classList.toggle('on', !!(fingers && fingers[name])));
}
function applyReading(reading) {
  digitEl.textContent = reading.display;
  gestureNameEl.textContent = reading.name;
  gestureEmojiEl.textContent = reading.emoji;
  digitEl.classList.add('pop');
  setTimeout(() => digitEl.classList.remove('pop'), 150);
  pushHistory(reading);
  speak(reading.name);
  checkPracticeMatch(reading);

  // tally counter: edge-triggered +1 on Thumbs Up, reset on Fist.
  if (reading.name === 'Thumbs Up' && tallyArmed) {
    tally++; tallyValueEl.textContent = String(tally); tallyArmed = false;
  } else if (reading.name === 'Fist (0)') {
    tally = 0; tallyValueEl.textContent = '0'; tallyArmed = false;
  } else {
    tallyArmed = true;
  }
}
function readingKey(r) { return r ? r.display + '|' + r.name : 'none'; }

// ── FPS tracking ─────────────────────────────────────────
let lastFrameTime = performance.now();
let fpsSmoothed = 0;

// ─────────────────────────────────────────────────────────
//  MAIN LOOP
// ─────────────────────────────────────────────────────────
function loop() {
  if (!running) return;
  if (video.readyState >= 2 && video.currentTime !== lastVideoT) {
    lastVideoT = video.currentTime;

    const now = performance.now();
    const instFps = 1000 / Math.max(1, now - lastFrameTime);
    lastFrameTime = now;
    fpsSmoothed = fpsSmoothed ? fpsSmoothed * 0.9 + instFps * 0.1 : instFps;
    fpsEl.textContent = Math.round(fpsSmoothed);

    let results;
    try {
      results = landmarker.detectForVideo(video, now);
    } catch (e) {
      console.error(e);
      rafId = requestAnimationFrame(loop);
      return;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const handsLm = results.landmarks || [];
    const handednesses = results.handednesses || [];
    let reading = null;
    let totalFingers = 0;
    let avgConfidence = 0;

    if (handsLm.length === 0) {
      updateFingerRow(fingerRows[0], null);
      updateFingerRow(fingerRows[1], null);
      statusEl.textContent = 'no hand';
    } else {
      const analyzed = handsLm.map(lm => analyzeHand(lm));
      analyzed.forEach((h, i) => {
        const colors = HAND_COLORS[i % HAND_COLORS.length];
        drawSkeleton(h.lm, colors.main, colors.tip);
        totalFingers += h.count;
      });
      updateFingerRow(fingerRows[0], analyzed[0] ? analyzed[0].fingers : null);
      updateFingerRow(fingerRows[1], analyzed[1] ? analyzed[1].fingers : null);

      handednesses.forEach(h => { if (h && h[0]) avgConfidence += h[0].score; });
      avgConfidence = handednesses.length ? avgConfidence / handednesses.length : 0;

      if (analyzed.length === 1) {
        reading = classifyGesture(analyzed[0]);
        statusEl.textContent = 'one hand';
      } else {
        const g1 = classifyGesture(analyzed[0]);
        const g2 = classifyGesture(analyzed[1]);
        const combo = classifyTwoHand(analyzed[0], analyzed[1], g1, g2);
        if (combo) {
          reading = combo;
          statusEl.textContent = 'combo!';
        } else {
          const total = Math.min(10, totalFingers);
          reading = { display: String(total), emoji: total >= 6 ? '🙌' : '🖐️', name: `${total} (two hands)` };
          statusEl.textContent = 'two hands';
        }
      }
    }

    totalCountEl.textContent = String(totalFingers);
    confMeter.style.width = Math.round(avgConfidence * 100) + '%';

    // temporal smoothing
    const key = readingKey(reading);
    if (key === readingKey(candidate)) {
      candidateStreak++;
    } else {
      candidate = reading;
      candidateStreak = 1;
    }
    if (candidateStreak >= STABLE_FRAMES && readingKey(stableReading) !== key) {
      stableReading = reading;
      if (reading) applyReading(reading);
      else {
        digitEl.textContent = '—';
        gestureNameEl.textContent = 'show a hand…';
        gestureEmojiEl.textContent = '🖐️';
      }
    }
  }
  rafId = requestAnimationFrame(loop);
}

// ─────────────────────────────────────────────────────────
//  START / CAMERA
// ─────────────────────────────────────────────────────────
async function start() {
  startBtn.disabled = true;
  startBtn.textContent = '⏳ Loading…';
  overlay.classList.remove('hidden');
  overlay.innerHTML = 'Loading AI model… (first time only, ~3 seconds)';

  try {
    if (!landmarker) await loadModel();

    overlay.innerHTML = 'Requesting camera permission…';
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
      audio: false
    });
    video.srcObject = stream;

    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('video timeout')), 8000);
      video.onloadedmetadata = () => { clearTimeout(t); resolve(); };
    });
    await video.play();

    running = true;
    stage.classList.add('active');
    overlay.classList.add('hidden');
    startBtn.textContent = '● RUNNING';
    statusEl.textContent = 'detecting…';
    practiceBtn.disabled = false;
    snapBtn.disabled = false;
    loop();
  } catch (err) {
    console.error(err);
    running = false;
    startBtn.disabled = false;
    startBtn.textContent = '▶ RETRY';
    overlay.classList.remove('hidden');
    overlay.innerHTML =
      `<div><div class="err">⚠ ${err.name || 'Error'}</div>
       <div style="margin-top:8px;font-size:0.9rem">${err.message}</div></div>`;
    statusEl.textContent = 'error';
  }
}

startBtn.addEventListener('click', start);
window.addEventListener('beforeunload', () => {
  running = false;
  if (rafId) cancelAnimationFrame(rafId);
  if (stream) stream.getTracks().forEach(t => t.stop());
});
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────
#  HTTP SERVER
# ─────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html", "/hand.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Permissions-Policy", "camera=*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] %s\n" % (fmt % args))


def find_free_port(start=8000, end=8100):
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    raise RuntimeError("No free port found in range")


def main():
    global PORT
    PORT = find_free_port(8000, 8100)

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer((HOST, PORT), Handler)

    url = f"http://{HOST}:{PORT}/"
    print("=" * 60)
    print("  ✋  GODTIER Hand Sign Reader v2 is running!")
    print("=" * 60)
    print(f"  Open in your browser:  {url}")
    print()
    print("  Numbers 0-10, gesture library, tally counter, practice mode,")
    print("  voice feedback, snapshots, and two-hand combo signs.")
    print("  Press Ctrl+C in this terminal to stop.")
    print("=" * 60)

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down…")
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
