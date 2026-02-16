// static/app.js
// UPDATED for Samsung Galaxy Flip 3 (Android WebView): fixes Cordova STT getting wedged / double-start,
// adds watchdog timeout + hard resets, and removes the extra startListening() call after TTS.

const API_ANALYZE = (window.AI_COACH_CONFIG?.apiUrl
  ? window.AI_COACH_CONFIG.apiUrl("/analyze_pose")
  : "http://localhost:8000/analyze_pose"
);

const video = document.getElementById("video");
const canvas = document.getElementById("overlay");
const ctx = canvas.getContext("2d");

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const analyzeBtn = document.getElementById("analyzeBtn");

// OPTIONAL UI (only wired if present)
const backBtn = document.getElementById("backBtn");
const camFacingSel = document.getElementById("camFacing");     // select: user/environment
const switchCamBtn = document.getElementById("switchCamBtn");   // button
const micBtn = document.getElementById("micBtn");               // button
const voiceStatusEl = document.getElementById("voiceStatus");   // div/span

// -------------------- Initial button state --------------------
startBtn.disabled = true;     // 🔒 until handedness chosen
stopBtn.disabled = true;
if (analyzeBtn) analyzeBtn.disabled = true;

// Professional UI elements (these exist in your HTML)
const userTextEl = document.getElementById("userText");
const answerEl = document.getElementById("answer");
const posTextEl = document.getElementById("posText");
const impTextEl = document.getElementById("impText");
const debugEl = document.getElementById("debug");
const camOverlay = document.getElementById("camOverlay");
const overlayTitle = document.getElementById("overlayTitle");
const overlaySub = document.getElementById("overlaySub");
const toggleDebugBtn = document.getElementById("toggleDebugBtn");
const handedSelect = document.getElementById("handedSelect");

// Collapsible setup help (exists in updated batting.html)
const setupHelp = document.getElementById("setupHelp");

// -------------------- Debug toggle wiring --------------------

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(console.error);
  });
}


if (toggleDebugBtn && debugEl) {
  debugEl.style.display = "none";
  toggleDebugBtn.textContent = "Show";
  toggleDebugBtn.onclick = () => {
    const isHidden = debugEl.style.display === "none";
    debugEl.style.display = isHidden ? "block" : "none";
    toggleDebugBtn.textContent = isHidden ? "Hide" : "Show";
  };
}

const micDot = document.getElementById("micDot");
const camDot = document.getElementById("camDot");
const sessionDot = document.getElementById("sessionDot");

// Freeze snapshot (optional, keeps overlay stable during analysis)
let frozenSnapshot = null; // { imageBitmap, keypoints }
let drawFrozen = false;

// STT control state (GLOBAL gating flags)
let _sttStarting = false;    // prevents concurrent start attempts
let _sttActive = false;      // true only while native/web recognizer is actually in-flight
let _sttRetries = 0;
const STT_MAX_RETRIES = 5;

let detector = null;
let recognition = null;
let running = false;
let analysisInProgress = false;

let lastPose = null;
let lastPoseUpper = null;

let isSpeaking = false;
let preferredVoice = null;

// camera stream tracking (for switch)
let currentStream = null;
let currentFacingMode = "environment"; // default rear

// IMPORTANT: handedness must be declared before any restore/usage
let handedness = null; // "right" | "left"

// ===================== small UI helpers =====================

function prettyHanded(h) {
  if (!h) return "Not selected";
  return h === "right" ? "Right-handed" : "Left-handed";
}

function persistHandedness(h) {
  try { localStorage.setItem("AI_COACH_HANDED", h || ""); } catch {}
}

function restoreHandedness() {
  try {
    const v = (localStorage.getItem("AI_COACH_HANDED") || "").trim();
    return (v === "right" || v === "left") ? v : null;
  } catch {
    return null;
  }
}

// helper
function hasWebSpeechRec() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

// show fallback if not available:
if (!hasWebSpeechRec()) {
  console.warn("Web Speech Recognition not available - enabling record-and-upload fallback for iOS.");
  // Update your UI to show a "Record voice" button instead of "Use voice" OR enable both.
  // e.g. showRecordFallbackUI();
}

// Hide/show live camera while keeping canvas visible (more stable than display:none on Android)
function setLiveHidden(hide) {
  if (!video || !canvas) return;

  if (hide) {
    video.style.display = "none";
  } else {
    video.style.display = "block";
  }

  canvas.style.display = "block";
}

// Tap the camera area to toggle live feed visibility (mobile friendly)
const videoWrap = document.querySelector(".video-wrap");
if (videoWrap) {
  videoWrap.addEventListener("click", () => {
    if (frozenSnapshot?.imageBitmap) {
      const isHidden = (video.style.display === "none");
      setLiveHidden(!isHidden);
      logDebug({ type: "toggle_live_view", liveHidden: !isHidden });
    }
  });
}

// ✅ Restore last handedness ONCE
(() => {
  const saved = restoreHandedness();
  if (!saved || !handedSelect) return;

  handedSelect.value = saved;
  handedness = saved;
  startBtn.disabled = false;

  logDebug({ type: "handedness_restored", handedness });
})();

if (handedSelect) {
  handedSelect.addEventListener("change", () => {
    handedness = handedSelect.value || null;

    if (handedness) {
      persistHandedness(handedness);
      startBtn.disabled = false;

      if (setupHelp && setupHelp.open) setupHelp.open = false;

      const camCard = document.querySelector(".video-wrap");
      if (camCard) camCard.scrollIntoView({ behavior: "smooth", block: "start" });

      logDebug({ type: "handedness_set", handedness });
    } else {
      startBtn.disabled = true;
    }
  });
}

function setVoiceStatus(msg) {
  if (voiceStatusEl) voiceStatusEl.textContent = msg || "";
}

function showOverlay(title, sub) {
  if (!camOverlay) return;
  overlayTitle.textContent = title || "Loading…";
  overlaySub.textContent = sub || "";
  camOverlay.classList.remove("hidden");
}

function hideOverlay() {
  if (!camOverlay) return;
  camOverlay.classList.add("hidden");
}

function clearCoachPanels(statusText = "—") {
  answerEl.textContent = statusText;
  posTextEl.textContent = "—";
  impTextEl.textContent = "—";
}

async function setCoachPanelsAndSpeak(posText, impText, answerText = "—") {
  answerEl.textContent = answerText || "—";
  posTextEl.textContent = posText || "—";
  impTextEl.textContent = impText || "—";
  await speakAsync(`Good. ${posText}`);
  await speakAsync(`One thing to work on. ${impText}`);
}

function resetAnalysisUI() {
  answerEl.textContent = "—";
  userTextEl.textContent = "—";
  posTextEl.textContent = "—";
  impTextEl.textContent = "—";
}

function setMicListening(isOn) {
  if (!micDot) return;
  micDot.classList.toggle("listening", !!isOn);
}

function logDebug(obj) {
  if (!debugEl) return;
  const ts = new Date().toLocaleTimeString();
  const msg = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  debugEl.textContent = `[${ts}] ${msg}\n\n` + debugEl.textContent;
}

function normalize(s) {
  return (s || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function setDot(el, state) {
  if (!el) return;
  el.classList.remove("good", "bad");
  if (state === "good") el.classList.add("good");
  if (state === "bad") el.classList.add("bad");
}

function resetUIForNewSession() {
  answerEl.textContent = "—";
  userTextEl.textContent = "—";
  posTextEl.textContent = "—";
  impTextEl.textContent = "—";

  frozenSnapshot = null;
  drawFrozen = false;
  analysisInProgress = false;

  setDot(sessionDot, "bad");
  setDot(micDot, "bad");
  setVoiceStatus("Not listening");
}

// ===================== permissions =====================

async function ensurePermissions() {
  try {
    const perms = window.Capacitor?.Plugins?.Camera;
    if (!perms) {
      logDebug("Camera plugin not installed; relying on browser permissions.");
      return;
    }
    const res = await perms.requestPermissions({ permissions: ["camera", "photos"] });
    logDebug({ type: "camera_permissions", res });
  } catch (e) {
    logDebug({ type: "permission_error", error: String(e) });
  }
}

// ===================== TTS helpers =====================

function hasWebSpeechSynthesis() {
  return (typeof window !== "undefined"
    && "speechSynthesis" in window
    && typeof window.speechSynthesis?.speak === "function"
    && typeof SpeechSynthesisUtterance !== "undefined");
}

function getCapacitorTTS() {
  return window.Capacitor?.Plugins?.TextToSpeech || null;
}

async function stopTTS() {
  try { window.speechSynthesis?.cancel?.(); } catch {}
  try {
    const tts = getCapacitorTTS();
    if (tts?.stop) await tts.stop();
  } catch {}
}

async function speak(text) {
  text = (text || "").toString().trim();
  if (!text) return;

  if (hasWebSpeechSynthesis()) {
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "en-US";
      u.rate = 1.0;
      u.pitch = 1.0;
      window.speechSynthesis.speak(u);
      return;
    } catch {}
  }

  try {
    const tts = getCapacitorTTS();
    if (!tts) throw new Error("TextToSpeech plugin not available");

    await tts.speak({
      text,
      lang: "en-US",
      rate: 1.0,
      pitch: 1.0,
      volume: 1.0,
      category: "playback",
    });
  } catch (e) {
    console.warn("TTS failed:", e);
    logDebug({ type: "tts_warn", error: String(e) });
  }
}

function loadPreferredVoice() {
  if (!hasWebSpeechSynthesis()) {
    preferredVoice = null;
    logDebug({ type: "voice_selected", name: "(native)", lang: "en-US" });
    return;
  }

  const voices = window.speechSynthesis.getVoices();
  const preferredNames = [
    "Microsoft Aria Online (Natural)",
    "Microsoft Jenny Online (Natural)",
    "Google US English",
    "Microsoft Zira",
    "Samantha"
  ];

  preferredVoice =
    voices.find(v => preferredNames.includes(v.name)) ||
    voices.find(v => v.lang === "en-US") ||
    voices[0] ||
    null;

  logDebug({ type: "voice_selected", name: preferredVoice?.name, lang: preferredVoice?.lang });
}

if (hasWebSpeechSynthesis()) {
  window.speechSynthesis.onvoiceschanged = loadPreferredVoice;
  loadPreferredVoice();
}

/**
 * UPDATED speakAsync:
 * - stops STT before speaking (hard reset flags)
 * - restarts STT after TTS ends ONLY via this pathway
 *   (we removed the extra setTimeout(startListening, 600) from startBtn)
 */
async function speakAsync(text) {
  return new Promise((resolve) => {
    if (!text) return resolve();

    // Stop STT while speaking
    try { stopListening(); } catch {}
    isSpeaking = true;

    logDebug({ type: "tts_start", mode: hasWebSpeechSynthesis() ? "web" : "native", text: text.slice(0, 160) });

    const restartAfterTts = () => {
      setTimeout(() => {
        if (!running || isSpeaking || analysisInProgress) return;

        if (!_sttStarting && !_sttActive) {
          try {
            startListening();
            logDebug({ type: "start_listen_called_after_tts" });
          } catch (e) {
            logDebug({ type: "start_listen_error_after_tts", error: String(e) });
          }
        } else {
          logDebug({ type: "start_listen_skipped_after_tts", _sttStarting, _sttActive });
        }
      }, 950); // Flip 3 needs slightly longer audio-focus settle time
    };

    if (hasWebSpeechSynthesis()) {
      try {
        const u = new SpeechSynthesisUtterance(text);
        u.voice = preferredVoice || null;
        u.lang = "en-US";
        u.rate = 0.95;
        u.pitch = 1.05;
        u.volume = 1.0;

        u.onend = () => {
          isSpeaking = false;
          logDebug({ type: "tts_end" });
          if (running) restartAfterTts();
          resolve();
        };

        u.onerror = (e) => {
          isSpeaking = false;
          logDebug({ type: "tts_error", error: String(e) });
          if (running) restartAfterTts();
          resolve();
        };

        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
        return;
      } catch (e) {
        logDebug({ type: "tts_web_error_fallback", error: String(e) });
      }
    }

    (async () => {
      try {
        await speak(text);
        logDebug({ type: "tts_end" });
      } catch (e) {
        logDebug({ type: "tts_error", error: String(e) });
      } finally {
        isSpeaking = false;
        if (running) restartAfterTts();
        resolve();
      }
    })();
  });
}

// ===================== camera (front/back support) =====================

function stopStream(stream) {
  try {
    if (stream) stream.getTracks().forEach(t => t.stop());
  } catch {}
}

function getRequestedFacingMode() {
  const v = camFacingSel?.value;
  return v || currentFacingMode || "environment";
}

async function startCamera(facingMode) {
  try {
    currentFacingMode = facingMode || getRequestedFacingMode();

    video.setAttribute("playsinline", "true");
    video.setAttribute("autoplay", "true");
    video.muted = true;
    video.playsInline = true;

    stopStream(currentStream);
    currentStream = null;

    const constraints = {
      audio: false,
      video: {
        facingMode: { ideal: currentFacingMode },
        width: { ideal: 640 },
        height: { ideal: 480 },
        frameRate: { ideal: 30, max: 30 }
      }
    };

    logDebug({ type: "camera_request", constraints });

    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    currentStream = stream;
    video.srcObject = stream;

    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("video metadata timeout")), 6000);
      video.onloadedmetadata = () => { clearTimeout(t); resolve(); };
    });

    await video.play();

    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;

    logDebug({
      type: "camera_started",
      facingMode: currentFacingMode,
      width: canvas.width,
      height: canvas.height,
      trackLabel: stream.getVideoTracks()?.[0]?.label
    });

    setDot(camDot, "good");
    return stream;
  } catch (err) {
    const e = err?.name ? { name: err.name, message: err.message } : { message: String(err) };
    logDebug({ type: "camera_error", ...e });

    showOverlay("Camera blocked", `${e.name || "Error"}: ${e.message || "Could not access camera"}`);
    setDot(camDot, "bad");

    throw err;
  }
}

async function switchCamera() {
  const next = (currentFacingMode === "environment") ? "user" : "environment";
  if (camFacingSel) camFacingSel.value = next;
  await startCamera(next);
}

// ===================== TF pose capture =====================

async function captureSnapshotAndPose() {
  const tmp = document.createElement("canvas");
  tmp.width = canvas.width;
  tmp.height = canvas.height;
  const tctx = tmp.getContext("2d");
  tctx.drawImage(video, 0, 0, tmp.width, tmp.height);

  const blob = await new Promise(resolve => tmp.toBlob(resolve, "image/jpeg", 0.9));
  const imageBitmap = await createImageBitmap(blob);

  let kp = [];
  try {
    const poses = await detector.estimatePoses(tmp, { maxPoses: 1, flipHorizontal: false });
    if (poses && poses.length > 0 && poses[0].keypoints) kp = poses[0].keypoints;
  } catch (e) {
    logDebug({ type: "snapshot_pose_error", error: String(e) });
  }

  return { imageBitmap, keypoints: kp };
}

// ===================== TF Pose load =====================

async function loadDetector() {
  if (!window.poseDetection) {
    logDebug("❌ poseDetection not found on window. Fix CDN in index.html.");
    throw new Error("poseDetection not available");
  }

  if (window.tf) {
    try {
      await tf.setBackend("webgl");
      await tf.ready();
      logDebug("✅ TF backend: " + tf.getBackend());
    } catch (e) {
      logDebug("⚠️ TF backend issue: " + String(e));
    }
  }

  detector = await poseDetection.createDetector(
    poseDetection.SupportedModels.MoveNet,
    { modelType: poseDetection.movenet.modelType.SINGLEPOSE_LIGHTNING }
  );

  logDebug("✅ Pose detector created.");
}

const UPPER_NAMES = new Set([
  "nose",
  "left_eye", "right_eye",
  "left_ear", "right_ear",
  "left_shoulder", "right_shoulder",
  "left_elbow", "right_elbow",
  "left_wrist", "right_wrist"
]);

function toName(kp) {
  return kp.name || kp.part;
}

function filterUpper(keypoints) {
  return (keypoints || []).filter(kp => UPPER_NAMES.has(toName(kp)));
}

function kpMap(keypoints) {
  const m = {};
  (keypoints || []).forEach(k => { m[toName(k)] = k; });
  return m;
}

function poseQualityUpper(keypoints, minScore = 0.35) {
  const m = kpMap(keypoints);
  const need = ["left_shoulder", "right_shoulder", "left_wrist", "right_wrist"];
  const missing = need.filter(n => !m[n] || (m[n].score || 0) < minScore);
  return { ok: missing.length === 0, missing };
}

function drawSkeleton(keypoints, opts = {}) {
  const alpha = opts.alpha ?? 1.0;
  const minScore = opts.minScore ?? 0.2;
  const width = opts.lineWidth ?? 4;
  const radius = opts.radius ?? 5;

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.lineWidth = width;
  ctx.strokeStyle = opts.lineColor || "lime";
  ctx.fillStyle = opts.pointColor || "red";

  const map = {};
  keypoints.forEach(kp => map[toName(kp)] = kp);

  const edges = [
    ["left_shoulder", "right_shoulder"],
    ["left_shoulder", "left_elbow"],
    ["left_elbow", "left_wrist"],
    ["right_shoulder", "right_elbow"],
    ["right_elbow", "right_wrist"],
  ];

  edges.forEach(([a, b]) => {
    const ka = map[a], kb = map[b];
    if (ka && kb && (ka.score || 0) > minScore && (kb.score || 0) > minScore) {
      ctx.beginPath();
      ctx.moveTo(ka.x, ka.y);
      ctx.lineTo(kb.x, kb.y);
      ctx.stroke();
    }
  });

  keypoints.forEach(kp => {
    if ((kp.score || 0) > minScore) {
      ctx.beginPath();
      ctx.arc(kp.x, kp.y, radius, 0, 2 * Math.PI);
      ctx.fill();
    }
  });

  ctx.restore();
}

// ===================== backend =====================

async function sendPoseForAnalysis(liveUpperKeypoints) {
  const payload = {
    handedness: handedness || "right",
    live_keypoints: (liveUpperKeypoints || []).map(k => ({
      name: toName(k),
      x: k.x,
      y: k.y,
      score: k.score || 0
    })),
    ref_keypoints: []
  };

  logDebug({ type: "pose_analysis_request", handedness: payload.handedness, live: payload.live_keypoints.length });

  const res = await fetch(API_ANALYZE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error("Analyze HTTP " + res.status);

  const feedback = await res.json();
  logDebug({ type: "pose_analysis_response_raw", feedback });

  await displayFeedback(feedback || {});
  return feedback;
}

async function displayFeedback(feedback) {
  const pos = feedback.Positives;
  const imp = feedback.Improvements;

  const posText = (pos?.advice || "").trim() || "Nice work — you’re set up well.";
  const impText = (imp?.advice || "").trim() || "Everything looks solid. Hold that stance and try again.";

  posTextEl.textContent = posText;
  impTextEl.textContent = impText;

  await speakAsync(`Good. ${posText}`);
  await speakAsync(`One thing to work on. ${impText}`);
}

// ===================== analysis trigger =====================

async function performAnalysis(triggerText = "") {
  if (!running) return;
  if (analysisInProgress) return;

  analysisInProgress = true;
  clearCoachPanels("Checking your setup…");

  try {
    drawFrozen = false;
    frozenSnapshot = null;
    await new Promise(res => setTimeout(res, 80));

    await speakAsync("Got it. Let me check your setup.");

    const snap = await captureSnapshotAndPose();
    snap.keypoints = filterUpper(snap.keypoints || []);
    frozenSnapshot = snap;
    drawFrozen = true;

    setLiveHidden(true);

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(frozenSnapshot.imageBitmap, 0, 0, canvas.width, canvas.height);
    drawSkeleton(frozenSnapshot.keypoints, {
      minScore: 0.15,
      lineWidth: 5,
      radius: 6,
      lineColor: "#00ff99",
      pointColor: "#ff4444"
    });

    const upper = filterUpper(snap.keypoints || []);
    const q = poseQualityUpper(upper, 0.35);

    if (!q.ok) {
      await setCoachPanelsAndSpeak(
        "I can see you — good start.",
        "I can’t see both shoulders and both wrists clearly. Step back a bit, aim the camera at your chest, and try again.",
        "Pose not clear"
      );
      return;
    }

    const m = kpMap(upper);
    const ls = m.left_shoulder, rs = m.right_shoulder;
    const lw = m.left_wrist, rw = m.right_wrist;

    if (!ls || !rs || !lw || !rw) {
      await speakAsync("I need to see both shoulders and both wrists clearly. Please adjust and try again.");
      return;
    }

    const sw = Math.abs(rs.x - ls.x) || 1;
    const handsX = (lw.x + rw.x) / 2;
    const handsY = (lw.y + rw.y) / 2;
    const backShoulder = (handedness === "left") ? ls : rs;
    const handToBackShoulder = Math.hypot(handsX - backShoulder.x, handsY - backShoulder.y) / sw;

    if (handToBackShoulder > 1.25) {
      await setCoachPanelsAndSpeak(
        "Nice — you’re in frame.",
        "Bring your hands closer to your back shoulder, then say Start or tap Analyze again.",
        "Not in stance yet"
      );
      return;
    }

    await sendPoseForAnalysis(upper);
    await speakAsync("Nice. Fix that one thing, then say start again or tap Analyze whenever you’re ready.");
    setVoiceStatus("Snapshot analyzed (tap camera to show live)");

  } catch (err) {
    setVoiceStatus("Analysis failed (tap camera to show live)");
    setLiveHidden(false);

    logDebug({ type: "analysis_error", error: String(err) });
    await speakAsync("Something went wrong. Please try again.");
  } finally {
    analysisInProgress = false;
  }
}

// ===================== speech recognition =====================

function stopListening() {
  // stop whatever mode we’re using (cordova wrapper or web)
  try {
    if (recognition) {
      if (typeof recognition.stop === "function") recognition.stop();
      if (typeof recognition.abort === "function") recognition.abort();
    }
  } catch (e) {
    logDebug({ type: "stop_listen_error", error: String(e) });
  }

  // HARD reset STT flags so we never get stuck “active”
  _sttStarting = false;
  _sttActive = false;
  _sttRetries = 0;

  setDot(micDot, "bad");
  setMicListening(false);
  setVoiceStatus("Not listening");
}

async function startListening() {
  if (!recognition || !running || isSpeaking || analysisInProgress) return;

  // Never double-start
  if (_sttStarting || _sttActive) {
    logDebug({ type: "stt_start_ignored", _sttStarting, _sttActive });
    return;
  }

  _sttStarting = true;

  try {
    // Cordova wrapper
    if (typeof recognition.start === "function" && ("_active" in recognition)) {
      recognition.start();
      logDebug({ type: "stt_start_called", mode: "cordova_wrapper" });
      return;
    }

    // Web SpeechRecognition
    if (typeof recognition.start === "function") {
      recognition.start();
      logDebug({ type: "stt_start_called", mode: "web" });
      return;
    }

    throw new Error("Recognition start not available");
  } catch (err) {
    logDebug({ type: "stt_start_error", error: String(err) });
    _sttStarting = false;
    _sttActive = false;
    setVoiceStatus("Speech start failed (see debug)");
  }
}

// Key change: On mobile, prefer Cordova plugin FIRST.
// UPDATED Cordova wrapper: pending guard + watchdog + correct _sttActive lifecycle.
function setupRecognition() {
  const cordovaRec = window.plugins?.speechRecognition;

  if (cordovaRec) {
    logDebug({ type: "stt_available", mode: "cordova" });

    const WATCH_MS = 7500; // Flip 3 / Samsung: recognizer can hang after TTS; watchdog recovers.

    const api = {
      _active: false,      // user wants continuous loop
      _pending: false,     // native startListening in-flight
      _watchdog: null,

      start: () => {
        if (!running || isSpeaking || analysisInProgress) {
          _sttStarting = false;
          return;
        }

        // If already active + (pending or currently listening), ignore.
        if (api._active && (api._pending || _sttActive || _sttStarting)) {
          logDebug({ type: "stt_start_ignored", _sttStarting, _sttActive, pending: api._pending, active: api._active });
          _sttStarting = false;
          return;
        }

        api._active = true;
        _sttStarting = false;
        _sttRetries = 0;

        setDot(micDot, "good");
        setMicListening(true);
        setVoiceStatus("Listening… say “Go” or “Start”");

        // Permissions (non-blocking)
        try {
          cordovaRec.hasPermission(
            (ok) => {
              if (ok) return;
              cordovaRec.requestPermission(
                () => logDebug({ type: "stt_permission", ok: true }),
                (e) => logDebug({ type: "stt_permission", ok: false, error: String(e) })
              );
            },
            () => {}
          );
        } catch (e) {
          logDebug({ type: "stt_permission_check_failed", error: String(e) });
        }

        // start first listen (buffer helps Samsung audio focus)
        setTimeout(() => {
          if (api._active) listenOnce();
        }, 550);
      },

      stop: () => {
        api._active = false;

        try { if (api._watchdog) clearTimeout(api._watchdog); } catch {}
        api._watchdog = null;

        api._pending = false;
        _sttStarting = false;
        _sttActive = false;
        _sttRetries = 0;

        try { cordovaRec.stopListening(() => {}, () => {}); } catch (e) {
          logDebug({ type: "cordova_stop_error", error: String(e) });
        }

        setDot(micDot, "bad");
        setMicListening(false);
        setVoiceStatus("Not listening");
      },

      abort: () => api.stop()
    };

    function armWatchdog() {
      try { if (api._watchdog) clearTimeout(api._watchdog); } catch {}
      api._watchdog = setTimeout(() => {
        // If still pending and no callback happened -> recover.
        if (!api._active || !api._pending) return;

        logDebug({ type: "stt_watchdog_timeout", mode: "cordova" });

        api._pending = false;
        _sttActive = false;
        _sttStarting = false;

        // Force stop to free resources
        try { cordovaRec.stopListening(() => {}, () => {}); } catch {}

        // backoff retry
        _sttRetries++;
        if (_sttRetries >= STT_MAX_RETRIES) {
          api._active = false;
          setDot(micDot, "bad");
          setMicListening(false);
          setVoiceStatus("Speech unavailable (try again)");
          return;
        }

        const delay = Math.round(700 * Math.pow(1.6, _sttRetries - 1) + Math.random() * 250);
        setTimeout(() => {
          if (api._active) listenOnce();
        }, delay);
      }, WATCH_MS);
    }

    function listenOnce() {
      if (!api._active) return;

      if (!running || isSpeaking || analysisInProgress) {
        setTimeout(() => { if (api._active) listenOnce(); }, 800);
        return;
      }

      // Prevent the Android error: "startListening called while listening is in progress"
      if (api._pending) {
        logDebug({ type: "stt_listen_skipped_pending" });
        return;
      }

      api._pending = true;
      _sttActive = true;      // we are truly in a native listen now
      _sttStarting = false;

      armWatchdog();

      try {
        cordovaRec.startListening(
          async (matches) => {
            // Native completed successfully
            api._pending = false;
            _sttActive = false;
            _sttStarting = false;
            _sttRetries = 0;

            try { if (api._watchdog) clearTimeout(api._watchdog); } catch {}
            api._watchdog = null;

            if (!api._active) return;

            const textRaw = Array.isArray(matches) ? (matches[0] || "") : String(matches || "");
            const norm = normalize(textRaw);

            logDebug({ type: "stt_result", mode: "cordova", transcript: textRaw });
            userTextEl.textContent = norm || "—";
            setVoiceStatus(norm ? `Heard: ${norm}` : "Listening…");

            if (norm.includes("stop")) {
              try { stopBtn.click(); } catch {}
              return;
            }

            const containsTrigger =
              norm === "go" ||
              norm === "start" ||
              norm.includes("lets go") ||
              norm.includes("let's go") ||
              norm.includes("analyze") ||
              norm.includes("begin") ||
              norm.includes("ready");

            if (containsTrigger) await performAnalysis(norm);

            // Next listen cycle
            setTimeout(() => {
              if (api._active) listenOnce();
            }, 420);
          },
          (err) => {
            // Native error callback
            api._pending = false;
            _sttActive = false;
            _sttStarting = false;

            try { if (api._watchdog) clearTimeout(api._watchdog); } catch {}
            api._watchdog = null;

            _sttRetries++;
            logDebug({ type: "stt_error", mode: "cordova", error: String(err), retry: _sttRetries });
            setVoiceStatus("Speech error (see debug)");

            if (_sttRetries >= STT_MAX_RETRIES) {
              api._active = false;
              setDot(micDot, "bad");
              setMicListening(false);
              setVoiceStatus("Speech unavailable (try again)");
              return;
            }

            const delay = Math.round(700 * Math.pow(1.6, _sttRetries - 1) + Math.random() * 250);
            setTimeout(() => {
              if (api._active) listenOnce();
            }, delay);
          },
          {
            language: "en-US",
            matches: 5,
            showPopup: false,
            prompt: "Say Go or Start"
          }
        );
      } catch (e) {
        // Synchronous exception
        api._pending = false;
        _sttActive = false;
        _sttStarting = false;

        try { if (api._watchdog) clearTimeout(api._watchdog); } catch {}
        api._watchdog = null;

        _sttRetries++;
        logDebug({ type: "stt_start_exception", mode: "cordova", error: String(e), retry: _sttRetries });

        if (_sttRetries >= STT_MAX_RETRIES) {
          api._active = false;
          setDot(micDot, "bad");
          setMicListening(false);
          setVoiceStatus("Speech unavailable (try again)");
          return;
        }

        const delay = Math.round(700 * Math.pow(1.6, _sttRetries - 1) + Math.random() * 250);
        setTimeout(() => {
          if (api._active) listenOnce();
        }, delay);
      }
    }

    return api;
  }

  // 2) Web Speech (desktop) — keep your existing behavior
  const WebSpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (WebSpeechRec) {
    const r = new WebSpeechRec();
    r.lang = "en-US";
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.continuous = true;

    const MIN_CONF_LONG = 0.45;
    const MIN_CONF_SHORT = 0.20;

    r.onstart = () => {
      logDebug({ type: "stt_start", mode: "web" });
      _sttActive = true;
      _sttStarting = false;
      setDot(micDot, "good");
      setMicListening(true);
      setVoiceStatus("Listening… say “Go” or “Start”");
    };
    r.onend = () => {
      logDebug({ type: "stt_end", mode: "web" });
      _sttActive = false;
      _sttStarting = false;
      setDot(micDot, "bad");
      setMicListening(false);
      setVoiceStatus("Not listening");
      if (running && !isSpeaking && !analysisInProgress) {
        setTimeout(() => {
          try { r.start(); } catch (e) { logDebug({ type: "recognition_restart_error", error: String(e) }); }
        }, 450);
      }
    };
    r.onerror = (e) => {
      logDebug({ type: "stt_error", mode: "web", error: e.error || e.message || String(e) });
      _sttStarting = false;
    };

    r.onresult = async (event) => {
      if (!running || isSpeaking) return;

      const resIndex = event.results.length - 1;
      const alt = event.results[resIndex][0];
      const textRaw = (alt.transcript || "").trim();
      const conf = typeof alt.confidence === "number" ? alt.confidence : 0;
      const isFinal = !!event.results[resIndex].isFinal;

      logDebug({ type: "stt_result", mode: "web", transcript: textRaw, confidence: conf, isFinal });

      const norm = normalize(textRaw);
      userTextEl.textContent = norm || "—";
      setVoiceStatus(norm ? `Heard: ${norm}` : "Listening…");

      if (norm.includes("stop")) {
        try { r.abort(); } catch {}
        try { stopBtn.click(); } catch {}
        return;
      }

      const triggerVariants = ["start", "go", "let's go", "lets go", "analyze", "begin", "ready"];
      const containsTrigger = triggerVariants.some(v => norm.includes(v));
      const isShort = norm === "go" || norm === "start";
      const confOk = isShort ? (conf >= MIN_CONF_SHORT) : (conf >= MIN_CONF_LONG || isFinal);

      if (!containsTrigger || !confOk) return;
      await performAnalysis(norm);
    };

    return r;
  }

  logDebug("Speech recognition not available (no Web Speech, no cordova-plugin-speechrecognition).");
  return null;
}

// ===================== draw loop =====================
async function drawLoop() {
  if (!running) return;

  if (drawFrozen && frozenSnapshot?.imageBitmap) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.drawImage(frozenSnapshot.imageBitmap, 0, 0, canvas.width, canvas.height);

    if (frozenSnapshot.keypoints?.length) {
      drawSkeleton(frozenSnapshot.keypoints, {
        minScore: 0.15,
        lineWidth: 5,
        radius: 6,
        lineColor: "#00ff99",
        pointColor: "#ff4444"
      });
    }

    requestAnimationFrame(drawLoop);
    return;
  }

  ctx.clearRect(0, 0, canvas.width, canvas.height);

  try {
    const poses = await detector.estimatePoses(video, { maxPoses: 1, flipHorizontal: false });

    if (poses?.[0]?.keypoints) {
      lastPose = poses[0].keypoints;
      lastPoseUpper = filterUpper(lastPose);

      drawSkeleton(lastPoseUpper, {
        minScore: 0.2,
        lineWidth: 4,
        radius: 5
      });
    }
  } catch (e) {
    logDebug({ type: "pose_error", error: String(e) });
  }

  requestAnimationFrame(drawLoop);
}

// ===================== UI actions =====================

if (backBtn) {
  backBtn.addEventListener("click", () => {
    try { stopBtn.click(); } catch {}
    window.location.href = "index.html";
  });
}

if (camFacingSel) {
  camFacingSel.addEventListener("change", async (e) => {
    if (!running) return;
    try { await startCamera(e.target.value); } catch {}
  });
}

if (switchCamBtn) {
  switchCamBtn.addEventListener("click", async () => {
    if (!running) return;
    try { await switchCamera(); } catch {}
  });
}

if (micBtn) {
  micBtn.addEventListener("click", () => {
    if (!running) return;
    startListening();
  });
}

if (analyzeBtn) {
  analyzeBtn.onclick = async () => {
    if (!running) return;
    userTextEl.textContent = "(button) analyze";
    await performAnalysis("analyze");
  };
}

startBtn.onclick = async () => {
  handedness = handedSelect?.value || null;
  if (!handedness) {
    alert("Please select handedness first.");
    return;
  }

  resetAnalysisUI();
  resetUIForNewSession();

  if (running) return;

  try {
    showOverlay("Starting camera", "Please allow camera access…");

    await ensurePermissions();

    currentFacingMode = getRequestedFacingMode();
    await startCamera(currentFacingMode);

    setLiveHidden(false);

    showOverlay("Loading pose model", "This may take ~5–15 seconds the first time…");
    await loadDetector();
    hideOverlay();

    recognition = setupRecognition();

    running = true;
    startBtn.disabled = true;
    stopBtn.disabled = false;
    if (analyzeBtn) analyzeBtn.disabled = false;

    setDot(sessionDot, "good");
    requestAnimationFrame(drawLoop);

    await speakAsync(
      `Conversation started. You are set as a ${handedness}-handed hitter. ` +
      `Stand sideways to the camera aimed at your chest. Step back so I can see your shoulders, elbows, and wrists. ` +
      `When you're ready, say Start or Go.`
    );

    // IMPORTANT: REMOVED the extra setTimeout(startListening, 600)
    // because speakAsync already restarts listening after TTS ends.

  } catch (e) {
    logDebug({ type: "start_error", error: String(e) });
    showOverlay("Could not start", "Open Debug → fix the error, then refresh.");

    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    setDot(sessionDot, "bad");
  }
};

stopBtn.onclick = () => {
  running = false;

  startBtn.disabled = false;
  stopBtn.disabled = true;
  if (analyzeBtn) analyzeBtn.disabled = true;

  analysisInProgress = false;
  frozenSnapshot = null;
  drawFrozen = false;

  setLiveHidden(false);

  try { if (recognition) recognition.abort?.(); } catch {}
  stopListening();
  stopTTS();

  try {
    stopStream(currentStream);
    currentStream = null;
    const stream = video.srcObject;
    if (stream) stopStream(stream);
    video.srcObject = null;
  } catch {}

  setDot(sessionDot, "bad");
  setDot(camDot, "bad");
  setDot(micDot, "bad");

  logDebug("Session stopped.");
};
