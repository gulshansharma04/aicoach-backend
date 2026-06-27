/* Jarvis-style onboarding wizard */
(() => {
  const API = window.CRM_CONFIG;
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  const TOTAL = 7;
  let step = 0;
  let distributorId = null;
  let token = null;
  let voiceOn = true;

  const consent = {
    manage_customers: true, auto_followups: true, find_social: true,
    social_posting: "draft", ceo_content: "draft",
  };

  // ---------- helpers ----------
  let toastTimer;
  function toast(m) {
    const t = $("#toast"); t.textContent = m; t.classList.remove("hidden");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }
  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (token) headers["X-Distributor-Token"] = token;
    const res = await fetch(API.apiUrl(path), { ...opts, headers });
    if (!res.ok) {
      let d = res.statusText; try { d = (await res.json()).detail || d; } catch (e) {}
      throw new Error(d);
    }
    return res.json();
  }
  function speak(text) {
    if (!voiceOn || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.03;
    window.speechSynthesis.speak(u);
  }

  // ---------- step navigation ----------
  function renderDots() {
    $("#dots").innerHTML = Array.from({ length: TOTAL }, (_, i) =>
      `<div class="sdot ${i <= step ? "on" : ""}"></div>`).join("");
  }
  function goto(n) {
    step = n;
    $$(".step").forEach(s => s.classList.toggle("active", +s.dataset.step === n));
    renderDots();
    SPEECH[n] && speak(SPEECH[n]);
  }

  const SPEECH = {
    0: "Hi! I'm your Distributor Buddy. I'll manage your customers, track their journey, send follow-ups, find them on social media, and even turn executive posts into ready content. Let's get you set up.",
    1: "First, let's create your account. Pop in your email or phone and I'll send you a verification code.",
    2: "Great. Enter the six digit code I just sent to verify it's really you.",
    3: "Now, what should I be allowed to do? You're always in control, and you can change this later.",
    4: "Which social platforms should I keep an eye on?",
    5: "To pull in your customers, help me log in to your myHerbalife account. Your credentials stay private and are never shared.",
    6: "All set! I'll start working and bring you a briefing every morning.",
  };

  // ---------- Step 1: welcome ----------
  $("#startBtn").addEventListener("click", () => goto(1));
  $("#muteBtn").addEventListener("click", () => {
    voiceOn = !voiceOn;
    $("#muteBtn").textContent = voiceOn ? "🔊" : "🔇";
    if (!voiceOn) window.speechSynthesis?.cancel();
  });

  // ---------- Step 2: signup ----------
  $("#sendCodeBtn").addEventListener("click", async () => {
    const name = $("#suName").value.trim();
    const email = $("#suEmail").value.trim();
    const phone = $("#suPhone").value.trim();
    $("#suErr").textContent = "";
    if (!email && !phone) { $("#suErr").textContent = "Enter an email or phone."; return; }
    try {
      const d = await api("/api/auth/signup", { method: "POST", body: JSON.stringify({ name, email, phone }) });
      distributorId = d.distributor_id;
      $("#verifySub").textContent = `I sent a 6-digit code to ${d.destination}.`;
      $("#devCode").textContent = d.dev_otp ? `Dev mode — your code is ${d.dev_otp}` : "";
      goto(2);
    } catch (e) { $("#suErr").textContent = e.message; }
  });

  // ---------- Step 3: verify ----------
  $("#verifyBtn").addEventListener("click", async () => {
    const code = $("#otpInput").value.trim();
    $("#otpErr").textContent = "";
    try {
      const d = await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ distributor_id: distributorId, code }) });
      token = d.token;
      localStorage.setItem("CRM_TOKEN", token);
      // pre-fill name into consent step
      goto(3);
    } catch (e) { $("#otpErr").textContent = e.message; }
  });

  // ---------- Step 4: consent toggles ----------
  $$(".switch").forEach(sw => sw.addEventListener("click", () => {
    sw.classList.toggle("on");
    consent[sw.dataset.k] = sw.classList.contains("on");
  }));
  $$(".seg").forEach(seg => {
    const k = seg.dataset.k;
    seg.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      consent[k] = b.dataset.v;
    }));
  });
  $("#consentNextBtn").addEventListener("click", async () => {
    try { await api("/api/me/consent", { method: "PATCH", body: JSON.stringify({ consent }) }); } catch (e) {}
    goto(4);
  });

  // ---------- Step 5: platforms ----------
  $("#platNextBtn").addEventListener("click", async () => {
    const tracked = $$("#platforms input:checked").map(i => i.value);
    try { await api("/api/me/consent", { method: "PATCH", body: JSON.stringify({ tracked_platforms: tracked }) }); } catch (e) {}
    goto(5);
  });

  // ---------- Step 6: connect Herbalife ----------
  $("#connectBtn").addEventListener("click", async () => {
    const username = $("#hbUser").value.trim();
    const password = $("#hbPass").value;
    $("#hbErr").textContent = "";
    if (!username || !password) { $("#hbErr").textContent = "Enter your username and password."; return; }
    try {
      await api("/api/connect/herbalife", { method: "POST", body: JSON.stringify({ username, password }) });
      await finishOnboarding();
      toast("Connected securely.");
      goto(6);
    } catch (e) { $("#hbErr").textContent = e.message; }
  });
  $("#skipConnectBtn").addEventListener("click", async () => { await finishOnboarding(); goto(6); });

  async function finishOnboarding() {
    try { await api("/api/me/consent", { method: "PATCH", body: JSON.stringify({ onboarded: true }) }); } catch (e) {}
  }

  // ---------- Step 7: enter ----------
  $("#enterBtn").addEventListener("click", () => { location.href = "./index.html"; });

  // ---------- init ----------
  // If already onboarded, skip straight to the app.
  (async () => {
    const existing = localStorage.getItem("CRM_TOKEN");
    if (existing) {
      token = existing;
      try {
        const me = await api("/api/me");
        if (me.onboarded) { location.href = "./index.html"; return; }
      } catch (e) { localStorage.removeItem("CRM_TOKEN"); token = null; }
    }
    renderDots();
    setTimeout(() => speak(SPEECH[0]), 600);
  })();
})();
