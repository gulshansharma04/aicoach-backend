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
    const res = await fetch(API.apiUrl(path), {
      headers: { "Content-Type": "application/json" }, ...opts,
    });
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
  }));

  // ===================================================================
  // Dashboard / digest
  // ===================================================================
  $("#runDigestBtn").addEventListener("click", runDigest);

  async function runDigest() {
    const btn = $("#runDigestBtn");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Catching up…';
    try {
      const d = await api("/api/agent/digest");
      $("#briefingText").textContent = d.briefing;
      $("#briefingTime").textContent = "updated " + new Date().toLocaleTimeString();
      renderTiles(d.totals);
      renderFocus(d.focus);
      if (d.totals.reminders_created) toast(`Filed ${d.totals.reminders_created} new reminder(s).`);
      refreshReminderBadge();
    } catch (e) {
      toast("Catch-up failed: " + e.message);
    } finally {
      btn.disabled = false; btn.innerHTML = "⚡ Run daily catch-up";
    }
  }

  function renderTiles(t) {
    const tiles = [
      { n: t.customers, l: "Customers", cls: "" },
      { n: t.needs_attention, l: "Need attention", cls: "warn" },
      { n: t.urgent, l: "Urgent", cls: "bad" },
      { n: t.healthy, l: "Healthy", cls: "good" },
    ];
    $("#statTiles").innerHTML = tiles.map(x =>
      `<div class="tile ${x.cls}"><div class="n">${x.n}</div><div class="l">${x.l}</div></div>`).join("");
  }

  function renderFocus(focus) {
    const wrap = $("#focusList");
    if (!focus.length) {
      wrap.innerHTML = `<p class="muted">🎉 All clear — no customers need urgent attention right now.</p>`;
      return;
    }
    wrap.innerHTML = "";
    focus.forEach(f => {
      const h = f.health;
      const item = el(`
        <div class="focus-item">
          <div class="focus-top">
            <div>
              <span class="focus-name" data-id="${f.customer_id}">${esc(f.name)}</span>
              <span class="urg ${h.suggested_urgency}">${h.suggested_urgency}</span>
            </div>
            <span class="health ${h.status}">${h.score}/100 · ${h.status.replace("_", " ")}</span>
          </div>
          <div class="reason">${esc(h.support_reason)}</div>
          <div class="draft-box"><b>✍️ Suggested ${esc(f.suggested_channel)}:</b>\n${esc(f.draft_message)}</div>
          <div class="focus-actions">
            <button class="btn btn-xs btn-primary act-copy">Copy message</button>
            <button class="btn btn-xs act-log" data-ch="${esc(f.suggested_channel)}">✓ Mark ${esc(f.suggested_channel)}ed</button>
            <button class="btn btn-xs act-open">Open profile</button>
          </div>
        </div>`);
      item.querySelector(".focus-name").addEventListener("click", () => openCustomer(f.customer_id));
      item.querySelector(".act-open").addEventListener("click", () => openCustomer(f.customer_id));
      item.querySelector(".act-copy").addEventListener("click", () => {
        navigator.clipboard?.writeText(f.draft_message); toast("Message copied.");
      });
      item.querySelector(".act-log").addEventListener("click", async (e) => {
        const ch = e.target.dataset.ch === "call" ? "call" : "text";
        await api(`/api/customers/${f.customer_id}/interactions`, {
          method: "POST",
          body: JSON.stringify({ channel: ch, summary: `Reached out (${ch}) re: ${h.support_reason}` }),
        });
        toast(`Logged ${ch} with ${f.name}.`);
        item.style.opacity = ".5";
        e.target.disabled = true;
      });
      wrap.appendChild(item);
    });
  }

  // ===================================================================
  // Customers
  // ===================================================================
  $("#addCustomerBtn").addEventListener("click", openAddCustomer);
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
    runDigest();
  }
  init();
})();
