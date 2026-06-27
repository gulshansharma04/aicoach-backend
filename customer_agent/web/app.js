/* Distributor CRM — AI Customer Agent (frontend) */
(() => {
  const API = window.CRM_CONFIG;

  // ---------- tiny helpers ----------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m]));
  const money = (n) => "$" + (Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });

  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return "—";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  function ago(iso) {
    if (!iso) return "never";
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    if (isNaN(days)) return "—";
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return `${days}d ago`;
    if (days < 365) return `${Math.floor(days / 30)}mo ago`;
    return `${Math.floor(days / 365)}y ago`;
  }
  function healthColor(score) {
    if (score >= 75) return "var(--good)";
    if (score >= 50) return "var(--warn)";
    return "var(--bad)";
  }

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    const tok = localStorage.getItem("CRM_TOKEN");
    if (tok) headers["X-Distributor-Token"] = tok;
    const res = await fetch(API.apiUrl(path), { ...opts, headers });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  // ---------- state ----------
  let customersCache = [];
  let aiEnabled = false;

  // ---------- tabs ----------
  $$(".tab").forEach(btn => btn.addEventListener("click", () => {
    $$(".tab").forEach(b => b.classList.remove("active"));
    $$(".tabpane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "customers") loadCustomers();
    if (btn.dataset.tab === "reminders") loadReminders();
    if (btn.dataset.tab === "approvals") loadApprovals();
    if (btn.dataset.tab === "content") loadCeoFeed();
  }));

  // ===================================================================
  // Jarvis morning briefing + voice
  // ===================================================================
  let lastNarration = "";
  $("#runDigestBtn").addEventListener("click", () => loadBriefing(true));
  $("#playBriefingBtn").addEventListener("click", () => speak(lastNarration || "No briefing yet."));
  $("#stopVoiceBtn").addEventListener("click", stopSpeaking);
  $("#talkBtn").addEventListener("click", startListening);

  async function loadBriefing(speakIt) {
    const btn = $("#runDigestBtn");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Catching up…';
    $("#focusList").innerHTML = '<span class="spinner"></span> Reviewing your customers…';
    try {
      const d = await api("/api/agent/briefing?days=7&run_agent=true");
      lastNarration = d.narration;
      $("#briefingText").textContent = d.narration;
      $("#briefingTime").textContent = "updated " + new Date().toLocaleTimeString();
      renderTiles(d.stats);
      renderInTown(d.in_town);
      renderNextSteps(d.next_steps);
      const ops = d.ops || {};
      if (ops.monitor?.auto_replied || ops.tick?.auto_sent) {
        toast(`Auto-handled ${(ops.monitor?.auto_replied||0)+(ops.tick?.auto_sent||0)} action(s).`);
      }
      refreshReminderBadge(); refreshApprovalBadge();
      if (speakIt) speak(d.narration);
    } catch (e) {
      $("#focusList").innerHTML = `<p class="muted">Couldn't load briefing: ${esc(e.message)}</p>`;
    } finally {
      btn.disabled = false; btn.innerHTML = "⚡ Refresh briefing";
    }
  }

  function renderTiles(s) {
    const tiles = [
      { n: s.new_customers, l: "New customers", cls: "good" },
      { n: s.ordered_customers, l: `Ordered (${money(s.order_revenue)})`, cls: "good" },
      { n: s.in_town, l: "In town / traveling", cls: "warn" },
      { n: s.posts_replied + "/" + s.posts_reviewed, l: "Posts replied / reviewed", cls: "" },
      { n: s.pending_approvals, l: "Awaiting approval", cls: s.pending_approvals ? "bad" : "" },
      { n: s.total_customers, l: "Total customers", cls: "" },
    ];
    $("#statTiles").innerHTML = tiles.map(x =>
      `<div class="tile ${x.cls}"><div class="n">${x.n}</div><div class="l">${esc(x.l)}</div></div>`).join("");
  }

  function renderInTown(list) {
    const card = $("#inTownCard");
    if (!list || !list.length) { card.style.display = "none"; return; }
    card.style.display = "";
    $("#inTownList").innerHTML = list.map(c => `
      <div class="focus-item" style="margin-bottom:8px;">
        <div class="focus-top">
          <span class="focus-name" data-id="${c.id}">${esc(c.name)}</span>
          <span class="mini">${esc(c.platform)}</span>
        </div>
        <div class="reason">“${esc(c.post)}”</div>
      </div>`).join("");
    $$("#inTownList .focus-name").forEach(n => n.addEventListener("click", () => openCustomer(n.dataset.id)));
  }

  function renderNextSteps(steps) {
    const wrap = $("#focusList");
    if (!steps || !steps.length) {
      wrap.innerHTML = `<p class="muted">🎉 All clear — no customers need urgent attention right now.</p>`;
      return;
    }
    wrap.innerHTML = "";
    steps.forEach(s => {
      const item = el(`
        <div class="focus-item">
          <div class="focus-top">
            <div><span class="focus-name" data-id="${s.id}">${esc(s.name)}</span>
              <span class="urg ${s.urgency}">${s.urgency}</span></div>
            <span class="health ${s.score >= 75 ? "healthy" : s.score >= 50 ? "nurture" : "at_risk"}">${s.score}/100</span>
          </div>
          <div class="reason">${esc(s.action)}</div>
          <div class="focus-actions">
            <button class="btn btn-xs btn-primary act-draft">✍️ Draft ${esc(s.channel)}</button>
            <button class="btn btn-xs act-open">Open profile</button>
          </div>
          <div class="draftHere"></div>
        </div>`);
      item.querySelector(".focus-name").addEventListener("click", () => openCustomer(s.id));
      item.querySelector(".act-open").addEventListener("click", () => openCustomer(s.id));
      item.querySelector(".act-draft").addEventListener("click", async (e) => {
        const box = item.querySelector(".draftHere");
        box.innerHTML = '<span class="spinner"></span>';
        try {
          const d = await api(`/api/customers/${s.id}/draft`, {
            method: "POST", body: JSON.stringify({ channel: s.channel, goal: s.action }),
          });
          box.innerHTML = `<div class="draft-box" style="margin-top:8px;"><b>✍️ ${esc(d.channel)}:</b>\n${esc(d.message)}</div>
            <button class="btn btn-xs btn-primary copyD" style="margin-top:6px;">Copy</button>`;
          box.querySelector(".copyD").addEventListener("click", () => { navigator.clipboard?.writeText(d.message); toast("Copied."); });
        } catch (err) { box.textContent = "Error: " + err.message; }
      });
      wrap.appendChild(item);
    });
  }

  // ---------- Voice (Web Speech API) ----------
  function speak(text) {
    if (!("speechSynthesis" in window)) { toast("Voice not supported on this browser."); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.02; u.pitch = 1.0;
    $("#voiceStatus").textContent = "🔊 speaking…";
    u.onend = () => { $("#voiceStatus").textContent = ""; };
    window.speechSynthesis.speak(u);
  }
  function stopSpeaking() { window.speechSynthesis?.cancel(); $("#voiceStatus").textContent = ""; }

  function startListening() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { toast("Speech recognition not supported here. Try Chrome."); return; }
    const rec = new SR();
    rec.lang = "en-US"; rec.interimResults = false; rec.maxAlternatives = 1;
    $("#voiceStatus").textContent = "🎤 listening…";
    rec.onresult = async (ev) => {
      const text = ev.results[0][0].transcript;
      $("#voiceStatus").textContent = "“" + text + "”";
      try {
        const d = await api("/api/agent/chat", { method: "POST", body: JSON.stringify({ message: text }) });
        $("#briefingText").textContent = d.answer;
        speak(d.answer);
      } catch (e) { toast("Error: " + e.message); }
    };
    rec.onerror = () => { $("#voiceStatus").textContent = "Couldn't hear that."; };
    rec.onend = () => { if ($("#voiceStatus").textContent === "🎤 listening…") $("#voiceStatus").textContent = ""; };
    rec.start();
  }

  // ===================================================================
  // Approvals (sensitive agent actions)
  // ===================================================================
  async function loadApprovals() {
    const wrap = $("#approvalList");
    wrap.innerHTML = '<span class="spinner"></span>';
    try {
      const d = await api("/api/agent/actions?status=pending");
      if (!d.count) { wrap.innerHTML = `<p class="muted">Nothing waiting — the agent has handled everything low-risk on its own. ✅</p>`; return; }
      wrap.innerHTML = "";
      d.actions.forEach(a => {
        const item = el(`
          <div class="rem-item">
            <div class="rem-top">
              <div><span class="urg ${a.risk === "sensitive" ? "high" : "low"}">${esc(a.risk)}</span>
                <b style="margin-left:6px;cursor:pointer;" class="a-name">${esc(a.customer_name)}</b>
                <span class="muted"> · ${esc(a.kind)}${a.platform ? " · " + esc(a.platform) : ""}${a.channel ? " · " + esc(a.channel) : ""}</span></div>
            </div>
            <div class="reason">${esc(a.summary)}</div>
            <textarea class="input a-draft">${esc(a.draft)}</textarea>
            <div class="focus-actions">
              <button class="btn btn-xs btn-good a-approve">✓ Approve & send</button>
              <button class="btn btn-xs btn-danger a-reject">Reject</button>
            </div>
          </div>`);
        item.querySelector(".a-name").addEventListener("click", () => openCustomer(a.customer_id));
        item.querySelector(".a-approve").addEventListener("click", () =>
          decideAction(a.id, "approve", item.querySelector(".a-draft").value));
        item.querySelector(".a-reject").addEventListener("click", () => decideAction(a.id, "reject"));
        wrap.appendChild(item);
      });
    } catch (e) { wrap.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
  }

  async function decideAction(id, decision, edited) {
    try {
      await api(`/api/agent/actions/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ decision, edited_draft: edited ?? null }),
      });
      toast(decision === "approve" ? "Approved & sent." : "Rejected.");
      loadApprovals(); refreshApprovalBadge();
    } catch (e) { toast("Error: " + e.message); }
  }

  async function refreshApprovalBadge() {
    try {
      const d = await api("/api/agent/actions?status=pending");
      $("#apprCount").textContent = d.count ? d.count : "";
    } catch (e) {}
  }

  // ===================================================================
  // Notifications + email scan
  // ===================================================================
  $("#bellBtn").addEventListener("click", showNotifications);
  $("#scanEmailBtn").addEventListener("click", scanEmail);

  async function refreshNotifications(fireUrgent) {
    try {
      const d = await api("/api/notifications");
      $("#bellCount").textContent = d.unread ? d.unread : "";
      if (fireUrgent && "Notification" in window && Notification.permission === "granted") {
        (d.notifications || []).filter(n => !n.read && n.level === "urgent")
          .slice(0, 3).forEach(n => new Notification("Jarvis: " + n.title, { body: n.body }));
      }
    } catch (e) {}
  }

  async function showNotifications() {
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
    let d;
    try { d = await api("/api/notifications"); } catch (e) { toast("Couldn't load notifications."); return; }
    const body = !d.notifications.length
      ? `<p class="muted">No notifications yet.</p>`
      : d.notifications.map(n => `
          <div class="rem-item" style="margin-bottom:8px;${n.read ? "opacity:.55;" : ""}">
            <div class="rem-top"><b>${esc(n.title)}</b><span class="urg ${n.level === "urgent" ? "urgent" : n.level === "important" ? "high" : "low"}">${esc(n.level)}</span></div>
            <div class="reason">${esc(n.body)}</div>
            <div class="muted">${esc(n.source)} · ${ago(n.created_at)}</div>
          </div>`).join("") +
        `<button class="btn btn-xs" id="markAllRead" style="margin-top:6px;">Mark all read</button>`;
    openModal("🔔 Notifications", body);
    $("#markAllRead")?.addEventListener("click", async () => {
      await api("/api/notifications/read-all", { method: "POST" });
      closeModal(); refreshNotifications(false);
    });
  }

  async function scanEmail() {
    const btn = $("#scanEmailBtn");
    btn.disabled = true; btn.textContent = "📧 Checking…";
    try {
      const d = await api("/api/agent/scan-email", { method: "POST" });
      toast(d.configured ? `Scanned ${d.scanned} email(s), ${d.notified} important.` : d.message);
      refreshNotifications(true);
    } catch (e) { toast("Error: " + e.message); }
    finally { btn.disabled = false; btn.textContent = "📧 Check my inbox"; }
  }

  // ===================================================================
  // Website builder
  // ===================================================================
  $("#genSiteBtn").addEventListener("click", generateSite);

  async function generateSite() {
    const product = $("#siteProduct").value.trim();
    if (!product) { toast("Enter a product/brand."); return; }
    const btn = $("#genSiteBtn"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Designing…';
    const out = $("#siteResult");
    try {
      const d = await api("/api/website/plan", {
        method: "POST", body: JSON.stringify({ product, goal: $("#siteGoal").value.trim(), build: true }),
      });
      const b = d.brief;
      out.innerHTML = `
        <div class="focus-item">
          <div class="focus-top"><b>${esc(b.title)}</b></div>
          <div class="reason">${esc(b.tagline || "")}</div>
          ${(b.sections || []).map(s => `
            <div class="draft-box" style="margin-top:8px;"><b>${esc(s.name)}</b> — ${esc(s.headline || "")}
            \n${esc(s.body || "")}${s.cta ? "\n[ " + esc(s.cta) + " ]" : ""}</div>`).join("")}
          <div class="muted" style="margin-top:8px;">${d.build && !d.build.configured ? esc(d.build.message) : "Ready to build."}</div>
        </div>`;
    } catch (e) { out.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
    finally { btn.disabled = false; btn.innerHTML = "Generate website plan"; }
  }

  // ===================================================================
  // Content ideas (CEO repost inspiration)
  // ===================================================================
  $("#genContentBtn").addEventListener("click", generateContent);

  async function loadCeoFeed() {
    try {
      const d = await api("/api/content/ceo-feed");
      $("#ceoFeedNote").textContent = d.configured
        ? `Pulling the latest posts about the CEO (${(d.posts || []).length} found).`
        : d.message;
    } catch (e) {}
  }

  async function generateContent() {
    const btn = $("#genContentBtn");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Writing…';
    const out = $("#contentIdeas");
    try {
      const d = await api("/api/content/ideas", {
        method: "POST",
        body: JSON.stringify({ topic: $("#contentTopic").value, source_post: $("#contentSource").value }),
      });
      out.innerHTML = d.ideas.map(i => `
        <div class="focus-item" style="margin-bottom:10px;">
          <div class="focus-top"><span class="mini">${esc(i.platform)}</span></div>
          <div style="white-space:pre-wrap;font-size:13px;line-height:1.55;">${esc(i.caption)}</div>
          <div class="muted">${esc(i.hashtags)}</div>
          <div class="focus-actions"><button class="btn btn-xs btn-primary copyIdea">Copy</button></div>
        </div>`).join("");
      $$(".copyIdea", out).forEach((b, idx) => b.addEventListener("click", () => {
        const i = d.ideas[idx];
        navigator.clipboard?.writeText(i.caption + "\n\n" + i.hashtags); toast("Copied.");
      }));
    } catch (e) { out.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
    finally { btn.disabled = false; btn.innerHTML = "Generate repost ideas"; }
  }

  // ===================================================================
  // Customers
  // ===================================================================
  $("#addCustomerBtn").addEventListener("click", openAddCustomer);
  $("#importLeadsBtn").addEventListener("click", openImportLeads);
  $("#customerSearch").addEventListener("input", () => renderCustomerList());

  async function loadCustomers() {
    try {
      const d = await api("/api/customers");
      customersCache = d.customers;
      renderCustomerList();
    } catch (e) { toast("Failed to load customers: " + e.message); }
  }

  function renderCustomerList() {
    const q = $("#customerSearch").value.toLowerCase().trim();
    const list = customersCache.filter(c =>
      !q || c.name.toLowerCase().includes(q) ||
      (c.company || "").toLowerCase().includes(q) ||
      (c.tags || []).join(" ").toLowerCase().includes(q));
    const wrap = $("#customerList");
    if (!list.length) { wrap.innerHTML = `<p class="muted">No customers yet. Click “+ Add customer”.</p>`; return; }
    wrap.innerHTML = "";
    list.forEach(c => {
      const h = c.health;
      const card = el(`
        <div class="cust-card">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
            <div>
              <div class="nm">${esc(c.name)}</div>
              <div class="sub">${esc(c.company || c.stage)}${c.health.needs_support ? ' · <span style="color:var(--bad)">needs support</span>' : ''}</div>
            </div>
            <span class="health ${h.status}">${h.score}</span>
          </div>
          <div class="hbar"><div style="width:${h.score}%;background:${healthColor(h.score)}"></div></div>
          <div class="cust-meta">
            <span class="mini">${c.order_count} orders</span>
            <span class="mini">${money(c.total_revenue)}</span>
            <span class="mini">seen ${ago(h.signals.last_contact)}</span>
            ${c.open_reminders ? `<span class="mini" style="color:var(--warn)">${c.open_reminders} reminder(s)</span>` : ""}
          </div>
        </div>`);
      card.addEventListener("click", () => openCustomer(c.id));
      wrap.appendChild(card);
    });
  }

  // ===================================================================
  // Customer drawer
  // ===================================================================
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerOverlay").addEventListener("click", (e) => { if (e.target.id === "drawerOverlay") closeDrawer(); });
  function closeDrawer() { $("#drawerOverlay").classList.add("hidden"); }

  async function openCustomer(id) {
    $("#drawerOverlay").classList.remove("hidden");
    $("#drawerTitle").textContent = "Loading…";
    $("#drawerBody").innerHTML = '<div class="spinner"></div>';
    try {
      const c = await api(`/api/customers/${id}`);
      renderDrawer(c);
    } catch (e) {
      $("#drawerBody").innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`;
    }
  }

  function section(title, bodyHtml, extraBtn = "") {
    return `<div class="sec">
      <div class="sec-head"><h4>${title}</h4><div>${extraBtn}</div></div>
      <div class="sec-body">${bodyHtml}</div>
    </div>`;
  }

  function renderDrawer(c) {
    const h = c.health;
    $("#drawerTitle").innerHTML = `${esc(c.name)} <span class="health ${h.status}" style="margin-left:8px;">${h.score}/100</span>`;

    const socials = Object.entries(c.socials || {})
      .map(([p, hnd]) => `<span class="mini">${esc(p)}: ${esc(hnd)}</span>`).join(" ") || '<span class="muted">none</span>';
    const tags = (c.tags || []).map(t => `<span class="mini">${esc(t)}</span>`).join(" ") || '<span class="muted">none</span>';

    const profile = `
      <div class="kv"><span>Stage</span><b>${esc(c.stage)}</b></div>
      <div class="kv"><span>Phone</span><b>${esc(c.phone || "—")}</b></div>
      <div class="kv"><span>Email</span><b>${esc(c.email || "—")}</b></div>
      <div class="kv"><span>Preferred channel</span><b>${esc(c.preferred_channel)}</b></div>
      <div class="kv"><span>Socials</span><div class="taglist">${socials}</div></div>
      <div class="kv"><span>Tags</span><div class="taglist">${tags}</div></div>
      ${c.notes ? `<div class="kv"><span>Notes</span><b style="max-width:60%;text-align:right;">${esc(c.notes)}</b></div>` : ""}`;

    const healthHtml = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
        <div class="hbar" style="flex:1;"><div style="width:${h.score}%;background:${healthColor(h.score)}"></div></div>
        <span class="health ${h.status}">${h.status.replace("_", " ")}</span>
      </div>
      ${h.needs_support ? `<div class="reason" style="color:var(--bad)">⚠ ${esc(h.support_reason)} → suggest <b>${esc(h.suggested_channel)}</b></div>` : `<div class="reason">No action needed right now. 👍</div>`}
      ${h.reasons.length ? `<ul class="reasons">${h.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <div class="focus-actions" style="margin-top:10px;">
        <button class="btn btn-xs btn-primary" id="genTips">💡 Get AI tips</button>
        <button class="btn btn-xs" id="genDraft">✍️ Draft message</button>
      </div>
      <div id="tipsBox"></div>
      <div id="draftBox"></div>`;

    const orders = (c.orders || []).length
      ? `<div class="timeline">${c.orders.map(o => `
          <div class="tl-item"><div class="when">${fmtDate(o.ordered_at)} · ${esc(o.status)}</div>
          <div><b>${esc(o.product)}</b> — ${money(o.amount)}</div></div>`).join("")}</div>`
      : '<p class="muted">No orders yet.</p>';

    const acts = (c.activities || []).length
      ? `<div class="timeline">${c.activities.map(a => `
          <div class="tl-item"><div class="when">${fmtDate(a.occurred_at)} · ${esc(a.platform)} · ${esc(a.kind)}
            <span class="sent-${esc(a.sentiment)}">●</span>${a.needs_response ? ' · <b style="color:var(--warn)">needs reply</b>' : ''}</div>
          <div>${esc(a.content) || "—"}</div></div>`).join("")}</div>`
      : '<p class="muted">No social activity logged.</p>';

    const prog = (c.progress || []).length
      ? `<div class="timeline">${c.progress.map(p => `
          <div class="tl-item"><div class="when">${esc(p.status)}</div>
          <div><b>${esc(p.title)}</b>${p.note ? " — " + esc(p.note) : ""}</div></div>`).join("")}</div>`
      : '<p class="muted">No progress milestones yet.</p>';

    const inter = (c.interactions || []).length
      ? `<div class="timeline">${c.interactions.map(i => `
          <div class="tl-item"><div class="when">${fmtDate(i.occurred_at)} · ${esc(i.channel)}</div>
          <div>${esc(i.summary) || "—"}</div></div>`).join("")}</div>`
      : '<p class="muted">No catch-ups logged.</p>';

    const addBtn = (label, fn) => `<button class="btn btn-xs" data-add="${fn}">+ ${label}</button>`;

    $("#drawerBody").innerHTML =
      section("Health & AI", healthHtml) +
      section("Profile", profile, `<button class="btn btn-xs" id="editCust">Edit</button>`) +
      section("Orders", orders, addBtn("Order", "order")) +
      section("Social activity & comments", acts, addBtn("Activity", "activity")) +
      section("Progress", prog, addBtn("Milestone", "progress")) +
      section("Catch-up log", inter, addBtn("Log", "interaction")) +
      section("Communication plans", '<div id="plansSection"><span class="spinner"></span></div>',
              `<button class="btn btn-xs" id="enrollBtn">+ Enroll</button>`) +
      `<button class="btn btn-danger btn-xs" id="delCust" style="margin-top:6px;">Delete customer</button>`;

    // wire section actions
    $("#genTips").addEventListener("click", () => loadTips(c.id));
    $("#genDraft").addEventListener("click", () => loadDraft(c));
    $("#editCust").addEventListener("click", () => openEditCustomer(c));
    $("#delCust").addEventListener("click", async () => {
      if (!confirm(`Delete ${c.name}? This removes all their records.`)) return;
      await api(`/api/customers/${c.id}`, { method: "DELETE" });
      toast("Customer deleted."); closeDrawer(); loadCustomers();
    });
    $$("[data-add]", $("#drawerBody")).forEach(b =>
      b.addEventListener("click", () => openSubForm(c.id, b.dataset.add)));
    $("#enrollBtn").addEventListener("click", () => openEnroll(c.id));
    loadPlansSection(c.id);
  }

  async function loadPlansSection(customerId) {
    const box = $("#plansSection");
    if (!box) return;
    try {
      const d = await api(`/api/customers/${customerId}/enrollments`);
      if (!d.enrollments.length) { box.innerHTML = `<p class="muted">Not enrolled in any plan yet.</p>`; return; }
      box.innerHTML = d.enrollments.map(e => `
        <div class="kv"><span>${esc(e.plan_name)}</span>
          <b>${esc(e.status)} · step ${e.current_step + 1}${e.next_due ? " · next " + fmtDate(e.next_due) : ""}</b></div>`).join("");
    } catch (e) { box.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
  }

  async function openEnroll(customerId) {
    let plans = [];
    try { plans = (await api("/api/plans")).plans; } catch (e) { toast("Couldn't load plans."); return; }
    if (!plans.length) { toast("No plans defined yet."); return; }
    openModal("Enroll in a communication plan", `
      <label class="fld">Plan</label>
      <select class="input" id="planSel" style="width:100%;">
        ${plans.map(p => `<option value="${p.id}">${esc(p.name)} (${p.steps.length} steps)</option>`).join("")}
      </select>
      <div class="muted" style="margin-top:8px;">${esc(plans[0].description || "")}</div>
      <button class="btn btn-primary" id="doEnroll" style="margin-top:12px;width:100%;">Enroll</button>`);
    $("#doEnroll").addEventListener("click", async () => {
      try {
        await api("/api/enrollments", { method: "POST", body: JSON.stringify({ customer_id: customerId, plan_id: parseInt($("#planSel").value, 10) }) });
        toast("Enrolled."); closeModal(); openCustomer(customerId);
      } catch (e) { toast("Error: " + e.message); }
    });
  }

  async function loadTips(id) {
    const box = $("#tipsBox");
    box.innerHTML = '<span class="spinner"></span> Thinking…';
    try {
      const d = await api(`/api/customers/${id}/tips`);
      box.innerHTML = `<ul class="tips-list">${d.tips.map(t => `<li>${esc(t)}</li>`).join("")}</ul>
        <div class="muted">${d.ai_enabled ? "AI-generated" : "rule-based (add OpenAI key for richer tips)"}</div>`;
    } catch (e) { box.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
  }

  async function loadDraft(c) {
    const box = $("#draftBox");
    box.innerHTML = '<span class="spinner"></span> Drafting…';
    try {
      const d = await api(`/api/customers/${c.id}/draft`, {
        method: "POST", body: JSON.stringify({ goal: "check in and offer support" }),
      });
      box.innerHTML = `<div class="draft-box" style="margin-top:8px;"><b>✍️ ${esc(d.channel)}:</b>\n${esc(d.message)}</div>
        <button class="btn btn-xs btn-primary" style="margin-top:6px;" id="copyDraft">Copy</button>`;
      $("#copyDraft").addEventListener("click", () => { navigator.clipboard?.writeText(d.message); toast("Copied."); });
    } catch (e) { box.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
  }

  // ===================================================================
  // Modals (add/edit forms)
  // ===================================================================
  $("#modalClose").addEventListener("click", closeModal);
  $("#modalOverlay").addEventListener("click", (e) => { if (e.target.id === "modalOverlay") closeModal(); });
  function closeModal() { $("#modalOverlay").classList.add("hidden"); }
  function openModal(title, bodyHtml) {
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = bodyHtml;
    $("#modalOverlay").classList.remove("hidden");
  }

  function field(label, name, opts = {}) {
    const { type = "text", value = "", placeholder = "", options } = opts;
    if (options) {
      return `<label class="fld">${label}</label><select class="input" name="${name}">${
        options.map(o => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    }
    if (type === "textarea") {
      return `<label class="fld">${label}</label><textarea class="input" name="${name}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`;
    }
    return `<label class="fld">${label}</label><input class="input" name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" style="width:100%;" />`;
  }
  const formVals = (root) => {
    const o = {};
    $$("[name]", root).forEach(i => { o[i.name] = i.value.trim(); });
    return o;
  };

  function openAddCustomer() {
    openModal("Add customer", `
      ${field("Name *", "name", { placeholder: "Jane Doe" })}
      <div class="row2">${field("Phone", "phone")}${field("Email", "email")}</div>
      <div class="row2">${field("Company", "company")}${field("Stage", "stage", { options: ["prospect", "active", "vip", "inactive"] })}</div>
      ${field("Preferred channel", "preferred_channel", { options: ["text", "call", "email"] })}
      ${field("Tags (comma-separated)", "tags", { placeholder: "wellness, referral" })}
      ${field("Socials (e.g. instagram:@handle, facebook:name)", "socials", { placeholder: "instagram:@jane" })}
      ${field("Notes", "notes", { type: "textarea" })}
      <button class="btn btn-primary" id="saveCust" style="margin-top:12px;width:100%;">Save customer</button>`);
    $("#saveCust").addEventListener("click", async () => {
      const v = formVals($("#modalBody"));
      if (!v.name) { toast("Name is required."); return; }
      const payload = {
        name: v.name, phone: v.phone, email: v.email, company: v.company,
        stage: v.stage, preferred_channel: v.preferred_channel, notes: v.notes,
        tags: v.tags ? v.tags.split(",").map(s => s.trim()).filter(Boolean) : [],
        socials: parseSocials(v.socials),
      };
      try {
        const c = await api("/api/customers", { method: "POST", body: JSON.stringify(payload) });
        toast("Customer added."); closeModal(); await loadCustomers(); openCustomer(c.id);
      } catch (e) { toast("Error: " + e.message); }
    });
  }

  function openImportLeads() {
    openModal("📥 Import leads from social", `
      <label class="fld">Platform</label>
      <select class="input" id="ilPlatform" style="width:100%;">
        <option value="instagram">Instagram</option>
        <option value="facebook">Facebook</option>
        <option value="tiktok">TikTok</option>
        <option value="x">X (Twitter)</option>
        <option value="linkedin">LinkedIn</option>
      </select>
      <label class="fld">Paste handles, profile links, or a whole comment thread</label>
      <textarea class="input" id="ilText" style="min-height:120px;" placeholder="@jane_doe
instagram.com/mark.fit
Loved this! — @sara.wellness
..."></textarea>
      <button class="btn btn-primary full" id="ilPreviewBtn" style="margin-top:12px;width:100%;">Find leads</button>
      <div id="ilPreview" style="margin-top:12px;"></div>`);
    $("#ilPreviewBtn").addEventListener("click", previewLeads);
  }

  async function previewLeads() {
    const text = $("#ilText").value.trim();
    const platform = $("#ilPlatform").value;
    if (!text) { toast("Paste something first."); return; }
    const box = $("#ilPreview");
    box.innerHTML = '<span class="spinner"></span> Scanning…';
    try {
      const d = await api("/api/leads/import", {
        method: "POST", body: JSON.stringify({ text, platform, preview: true }),
      });
      if (!d.count) { box.innerHTML = `<p class="muted">No handles found. Make sure they include @ or a profile link.</p>`; return; }
      box.innerHTML = `
        <div class="muted" style="margin-bottom:8px;">Found ${d.count} lead(s) — uncheck any you don't want:</div>
        ${d.parsed.map((L, i) => `
          <label style="display:flex;gap:8px;align-items:center;padding:8px;border:1px solid var(--border);border-radius:10px;margin-bottom:6px;">
            <input type="checkbox" class="il-chk" data-i="${i}" checked />
            <div><b>${esc(L.name)}</b> <span class="muted">${esc(L.handle)}</span>${L.note ? `<div class="muted">${esc(L.note)}</div>` : ""}</div>
          </label>`).join("")}
        <button class="btn btn-primary full" id="ilCreateBtn" style="margin-top:8px;width:100%;">Import selected</button>`;
      $("#ilCreateBtn").addEventListener("click", () => createLeads(d.parsed, platform));
    } catch (e) { box.innerHTML = `<p class="muted">Error: ${esc(e.message)}</p>`; }
  }

  async function createLeads(parsed, platform) {
    const chosen = $$(".il-chk").filter(c => c.checked).map(c => parsed[+c.dataset.i]);
    if (!chosen.length) { toast("Select at least one lead."); return; }
    try {
      const d = await api("/api/leads/import", {
        method: "POST", body: JSON.stringify({ platform, leads: chosen, preview: false }),
      });
      toast(`Imported ${d.created_count} lead(s)${d.skipped ? `, skipped ${d.skipped} duplicate(s)` : ""}.`);
      closeModal(); loadCustomers();
    } catch (e) { toast("Error: " + e.message); }
  }

  function openEditCustomer(c) {
    const socialStr = Object.entries(c.socials || {}).map(([k, v]) => `${k}:${v}`).join(", ");
    openModal("Edit customer", `
      ${field("Name", "name", { value: c.name })}
      <div class="row2">${field("Phone", "phone", { value: c.phone || "" })}${field("Email", "email", { value: c.email || "" })}</div>
      <div class="row2">${field("Company", "company", { value: c.company || "" })}${field("Stage", "stage", { options: ["prospect", "active", "vip", "inactive"], value: c.stage })}</div>
      ${field("Preferred channel", "preferred_channel", { options: ["text", "call", "email"], value: c.preferred_channel })}
      ${field("Tags", "tags", { value: (c.tags || []).join(", ") })}
      ${field("Socials", "socials", { value: socialStr })}
      ${field("Notes", "notes", { type: "textarea", value: c.notes || "" })}
      <button class="btn btn-primary" id="saveEdit" style="margin-top:12px;width:100%;">Save changes</button>`);
    $("#saveEdit").addEventListener("click", async () => {
      const v = formVals($("#modalBody"));
      const payload = {
        name: v.name, phone: v.phone, email: v.email, company: v.company,
        stage: v.stage, preferred_channel: v.preferred_channel, notes: v.notes,
        tags: v.tags ? v.tags.split(",").map(s => s.trim()).filter(Boolean) : [],
        socials: parseSocials(v.socials),
      };
      try {
        await api(`/api/customers/${c.id}`, { method: "PATCH", body: JSON.stringify(payload) });
        toast("Saved."); closeModal(); openCustomer(c.id); loadCustomers();
      } catch (e) { toast("Error: " + e.message); }
    });
  }

  function parseSocials(str) {
    const out = {};
    (str || "").split(",").forEach(pair => {
      const idx = pair.indexOf(":");
      if (idx > 0) out[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
    });
    return out;
  }

  function openSubForm(customerId, kind) {
    const forms = {
      order: {
        title: "Add order", path: `/api/customers/${customerId}/orders`,
        html: `${field("Product *", "product")}
               <div class="row2">${field("Amount", "amount", { type: "number" })}${field("Status", "status", { options: ["paid", "pending", "shipped", "delivered", "refunded"] })}</div>
               ${field("Notes", "notes")}`,
        build: v => ({ product: v.product, amount: parseFloat(v.amount) || 0, status: v.status, notes: v.notes }),
        require: "product",
      },
      activity: {
        title: "Log social activity / comment", path: `/api/customers/${customerId}/activities`,
        html: `<div class="row2">${field("Platform", "platform", { options: ["instagram", "facebook", "tiktok", "x", "linkedin", "whatsapp", "other"] })}${field("Type", "kind", { options: ["comment", "post", "like", "dm", "mention", "story", "review"] })}</div>
               ${field("Content / comment", "content", { type: "textarea", placeholder: "What did they say?" })}
               <div class="row2">${field("Sentiment (auto if blank)", "sentiment", { options: ["", "positive", "neutral", "negative"] })}${field("Needs response?", "needs_response", { options: ["no", "yes"] })}</div>
               ${field("URL", "url")}`,
        build: v => ({ platform: v.platform, kind: v.kind, content: v.content, url: v.url,
                       sentiment: v.sentiment || null, needs_response: v.needs_response === "yes" }),
      },
      progress: {
        title: "Add milestone", path: `/api/customers/${customerId}/progress`,
        html: `${field("Title *", "title")}
               ${field("Status", "status", { options: ["planned", "in_progress", "done", "stalled"] })}
               ${field("Note", "note", { type: "textarea" })}`,
        build: v => ({ title: v.title, status: v.status, note: v.note }),
        require: "title",
      },
      interaction: {
        title: "Log catch-up", path: `/api/customers/${customerId}/interactions`,
        html: `${field("Channel", "channel", { options: ["call", "text", "email", "in_person", "social"] })}
               ${field("Summary", "summary", { type: "textarea", placeholder: "What did you discuss?" })}`,
        build: v => ({ channel: v.channel, summary: v.summary }),
      },
    };
    const f = forms[kind];
    openModal(f.title, f.html + `<button class="btn btn-primary" id="saveSub" style="margin-top:12px;width:100%;">Save</button>`);
    $("#saveSub").addEventListener("click", async () => {
      const v = formVals($("#modalBody"));
      if (f.require && !v[f.require]) { toast(`${f.require} is required.`); return; }
      try {
        await api(f.path, { method: "POST", body: JSON.stringify(f.build(v)) });
        toast("Saved."); closeModal(); openCustomer(customerId); loadCustomers();
      } catch (e) { toast("Error: " + e.message); }
    });
  }

  // ===================================================================
  // Reminders
  // ===================================================================
  $("#reminderFilter").addEventListener("change", loadReminders);

  async function loadReminders() {
    const status = $("#reminderFilter").value;
    try {
      const d = await api(`/api/reminders?status=${status}`);
      renderReminders(d.reminders);
    } catch (e) { toast("Failed to load reminders: " + e.message); }
  }

  function renderReminders(rems) {
    const wrap = $("#reminderList");
    if (!rems.length) { wrap.innerHTML = `<p class="muted">No reminders. Run the daily catch-up to generate some.</p>`; return; }
    wrap.innerHTML = "";
    rems.forEach(r => {
      const item = el(`
        <div class="rem-item">
          <div class="rem-top">
            <div>
              <span class="urg ${r.priority}">${esc(r.priority)}</span>
              <b style="margin-left:6px;cursor:pointer;" class="rem-name">${esc(r.customer_name)}</b>
              <span class="muted"> · ${esc(r.kind)}${r.source === "agent" ? " · 🤖 agent" : ""}</span>
            </div>
            <span class="health ${r.status === "open" ? "nurture" : "healthy"}">${esc(r.status)}</span>
          </div>
          <div class="reason">${esc(r.reason)}</div>
          ${r.draft_message ? `<div class="draft-box">${esc(r.draft_message)}</div>` : ""}
          <div class="focus-actions">
            ${r.draft_message ? `<button class="btn btn-xs btn-primary rem-copy">Copy</button>` : ""}
            ${r.status === "open" ? `<button class="btn btn-xs btn-good rem-done">✓ Done</button>
            <button class="btn btn-xs rem-dismiss">Dismiss</button>` : ""}
          </div>
        </div>`);
      item.querySelector(".rem-name").addEventListener("click", () => openCustomer(r.customer_id));
      item.querySelector(".rem-copy")?.addEventListener("click", () => { navigator.clipboard?.writeText(r.draft_message); toast("Copied."); });
      item.querySelector(".rem-done")?.addEventListener("click", () => setReminder(r.id, "done"));
      item.querySelector(".rem-dismiss")?.addEventListener("click", () => setReminder(r.id, "dismissed"));
      wrap.appendChild(item);
    });
  }

  async function setReminder(id, status) {
    try {
      await api(`/api/reminders/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      toast("Reminder " + status + "."); loadReminders(); refreshReminderBadge();
    } catch (e) { toast("Error: " + e.message); }
  }

  async function refreshReminderBadge() {
    try {
      const d = await api("/api/reminders?status=open");
      $("#remCount").textContent = d.count ? d.count : "";
    } catch (e) {}
  }

  // ===================================================================
  // Ask AI
  // ===================================================================
  $("#chatSendBtn").addEventListener("click", sendChat);
  $("#chatInput").addEventListener("keydown", e => { if (e.key === "Enter") sendChat(); });

  async function sendChat() {
    const input = $("#chatInput");
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    const log = $("#chatLog");
    log.appendChild(el(`<div class="chat-msg user">${esc(msg)}</div>`));
    const thinking = el(`<div class="chat-msg bot"><span class="spinner"></span></div>`);
    log.appendChild(thinking);
    log.scrollTop = log.scrollHeight;
    try {
      const d = await api("/api/agent/chat", { method: "POST", body: JSON.stringify({ message: msg }) });
      thinking.textContent = d.answer;
    } catch (e) {
      thinking.textContent = "Error: " + e.message;
    }
    log.scrollTop = log.scrollHeight;
  }

  // ===================================================================
  // init
  // ===================================================================
  async function init() {
    // Gate behind onboarding: if there's a session, confirm it's onboarded;
    // otherwise send the user to the Jarvis welcome flow.
    const tok = localStorage.getItem("CRM_TOKEN");
    if (!tok) { location.href = "./onboarding.html"; return; }
    try {
      const me = await api("/api/me");
      if (!me.onboarded) { location.href = "./onboarding.html"; return; }
      const greet = $("#aiBadge");
      if (me.name) document.querySelector(".brand p").textContent = "Welcome back, " + me.name.split(" ")[0];
    } catch (e) {
      localStorage.removeItem("CRM_TOKEN"); location.href = "./onboarding.html"; return;
    }
    try {
      const h = await api("/api/health");
      aiEnabled = h.ai_enabled;
      const badge = $("#aiBadge");
      badge.innerHTML = `<span class="dot ${aiEnabled ? "good" : ""}"></span> AI ${aiEnabled ? "enabled" : "offline mode"}`;
    } catch (e) {
      $("#aiBadge").innerHTML = `<span class="dot bad"></span> backend offline`;
    }
    loadCustomers();
    refreshReminderBadge();
    refreshApprovalBadge();
    refreshNotifications(true);
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
    loadBriefing(false);
  }
  init();
})();
