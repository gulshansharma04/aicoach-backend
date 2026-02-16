// www/food.js
(() => {
  // ---------- Config ----------
  const API_FOOD = (window.AI_COACH_CONFIG?.apiUrl
    ? window.AI_COACH_CONFIG.apiUrl("/api/food")
    : "http://localhost:8000/api/food"
  );

  // ---------- Elements ----------
  const takePhotoBtn = document.getElementById("takePhotoBtn");
  const camFacingSel = document.getElementById("foodCamFacing");

  const imgPreview = document.getElementById("foodPreview");
  const previewEmpty = document.getElementById("foodPreviewEmpty");
  const foodStatus = document.getElementById("foodStatus"); // optional from updated food.html

  // Results (v2 UI)
  const resultClassification = document.getElementById("resultClassification");
  const resultConfidence = document.getElementById("resultConfidence");
  const resultItems = document.getElementById("resultItems");

  const resultNutritionBreakdown = document.getElementById("resultNutritionBreakdown");
  const resultQualitySignals = document.getElementById("resultQualitySignals");
  const resultCaloriesDetails = document.getElementById("resultCaloriesDetails");

  const resultBehaviorCoaching = document.getElementById("resultBehaviorCoaching");
  const resultNotes = document.getElementById("resultNotes");
  const resultHelpfulTips = document.getElementById("resultHelpfulTips");

  // Debug
  const debugEl = document.getElementById("debug");
  const toggleDebugBtn = document.getElementById("toggleDebugBtn");

  // ---------- State ----------
  let lastFoodDataUrl = null;

  // ---------- Debug helpers ----------
  function logDebug(obj) {
    if (!debugEl) return;
    const ts = new Date().toLocaleTimeString();
    const msg = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
    debugEl.textContent = `[${ts}] ${msg}\n\n` + debugEl.textContent;
  }

  if (toggleDebugBtn && debugEl) {
    debugEl.style.display = "none";
    toggleDebugBtn.textContent = "Show";
    toggleDebugBtn.onclick = (e) => {
      e.preventDefault();
      const hidden = debugEl.style.display === "none";
      debugEl.style.display = hidden ? "block" : "none";
      toggleDebugBtn.textContent = hidden ? "Hide" : "Show";
    };
  }

  // ---------- Small utilities ----------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    }[m]));
  }

  function ulHtml(arr) {
    const a = Array.isArray(arr) ? arr : [];
    const clean = a.map(x => String(x || "").trim()).filter(Boolean);
    if (!clean.length) return "—";
    return `<ul class="ul" style="margin:0 0 0 16px;">${clean.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul>`;
  }

  function asText(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "string") return v.trim();
    if (Array.isArray(v)) return v.filter(Boolean).join(", ");
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function setDash(el) {
    if (!el) return;
    el.textContent = "—";
  }

  function setStatus(text, show = true) {
    if (!foodStatus) return;
    if (!show) {
      foodStatus.style.display = "none";
      return;
    }
    foodStatus.textContent = text || "";
    foodStatus.style.display = "block";
  }

  // ---------- Camera capture (Capacitor native with fallback) ----------
  async function takeNativePhoto() {
    const Camera = window.Capacitor?.Plugins?.Camera;
    if (!Camera) throw new Error("Capacitor Camera plugin not available.");

    const facing = (camFacingSel?.value || "environment");
    const direction = (facing === "user") ? "FRONT" : "REAR";

    logDebug({ type: "camera_request", facing, direction });

    const photo = await Camera.getPhoto({
      quality: 85,
      allowEditing: false,
      resultType: "dataUrl",
      source: "CAMERA",
      direction
    });

    if (!photo?.dataUrl || !photo.dataUrl.startsWith("data:image")) {
      throw new Error("Camera returned no dataUrl.");
    }
    return photo.dataUrl;
  }

  async function takeFilePhotoFallback() {
    return new Promise((resolve, reject) => {
      const inp = document.createElement("input");
      inp.type = "file";
      inp.accept = "image/*";
      inp.capture = "environment";
      inp.onchange = async () => {
        const f = inp.files?.[0];
        if (!f) return reject(new Error("No file selected"));
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = (e) => reject(e);
        reader.readAsDataURL(f);
      };
      inp.click();
    });
  }

  async function capturePhoto() {
    if (window.Capacitor?.Plugins?.Camera) return await takeNativePhoto();
    return await takeFilePhotoFallback();
  }

  // ---------- API call ----------
  async function callAnalyzeApi(dataUrl) {
    const payload = { image_data_url: dataUrl };
    logDebug({ type: "food_request", url: API_FOOD, bytes: dataUrl?.length || 0 });

    const res = await fetch(API_FOOD, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`API ${res.status}: ${txt || "Unknown error"}`);
    }

    const j = await res.json();
    logDebug({ type: "food_response", j });
    return j;
  }

  // ---------- Render results (v2) ----------
  function renderResultsV2(d) {
    // Core
    const classification = asText(d.classification) || asText(d.rating) || "Mixed";
    const confidence = asText(d.confidence) || "—";
    const items = Array.isArray(d.items) ? d.items : (Array.isArray(d.detected_items) ? d.detected_items : []);
    const itemsText = items.length ? items.join(", ") : (asText(d.food_name) || "—");

    // Breakdown
    const proteinRange = asText(d.protein_grams_range) || "—";
    const fiberRange = asText(d.fiber_grams_range) || "—";
    const calorieDensity = asText(d.calorie_density) || "—";
    const satiety = (d.satiety_score !== undefined && d.satiety_score !== null) ? String(d.satiety_score) : "—";

    // Quality signals
    const totalCals = asText(d.total_calories_range) || asText(d.calories_range) || asText(d.calories) || "—";
    const sugar = asText(d.added_sugar_risk) || "—";
    const fatQuality = asText(d.fat_quality) || "—";
    const bloodSugar = asText(d.blood_sugar_impact) || "—";

    // Calorie details
    const portionAssumption = asText(d.portion_assumption) || "—";
    const caloriesByItem = Array.isArray(d.calories_by_item) ? d.calories_by_item : [];

    // Coaching
    const portionRisk = asText(d.portion_risk) || "—";
    const timingAdvice = asText(d.timing_advice) || "—";

    // Notes + tips
    const notes = asText(d.notes) || "—";
    const tips = Array.isArray(d.tips) ? d.tips : [];

    // General Nutrition Facts
    if (resultClassification) resultClassification.textContent = classification;
    if (resultConfidence) resultConfidence.textContent = confidence;
    if (resultItems) resultItems.textContent = itemsText;

    // Nutrition Breakdown
    if (resultNutritionBreakdown) {
      resultNutritionBreakdown.innerHTML = `
        <div class="row" style="gap:10px; margin-top:6px;">
          <div class="chip"><span>Protein</span><strong style="margin-left:6px;">${escapeHtml(proteinRange)}</strong></div>
          <div class="chip"><span>Fiber</span><strong style="margin-left:6px;">${escapeHtml(fiberRange)}</strong></div>
        </div>
        <div class="row" style="gap:10px; margin-top:10px;">
          <div class="chip"><span>Calorie density</span><strong style="margin-left:6px;">${escapeHtml(calorieDensity)}</strong></div>
          <div class="chip"><span>Satiety</span><strong style="margin-left:6px;">${escapeHtml(satiety)}/5</strong></div>
        </div>
      `;
    }

    // Quality Signals
    if (resultQualitySignals) {
      resultQualitySignals.innerHTML = `
        <div class="row" style="gap:10px; margin-top:6px;">
          <div class="chip"><span>Est calories</span><strong style="margin-left:6px;">${escapeHtml(totalCals)}</strong></div>
        </div>
        <div class="row" style="gap:10px; margin-top:10px;">
          <div class="chip"><span>Sugar</span><strong style="margin-left:6px;">${escapeHtml(sugar)}</strong></div>
          <div class="chip"><span>Fat quality</span><strong style="margin-left:6px;">${escapeHtml(fatQuality)}</strong></div>
        </div>
        <div class="row" style="gap:10px; margin-top:10px;">
          <div class="chip"><span>Blood sugar impact</span><strong style="margin-left:6px;">${escapeHtml(bloodSugar)}</strong></div>
        </div>
      `;
    }

    // Calorie Details
    if (resultCaloriesDetails) {
      const rows = caloriesByItem.length
        ? `<ul class="ul" style="margin:8px 0 0 16px;">
            ${caloriesByItem.map(x => {
              const nm = asText(x.item) || "Item";
              const cr = asText(x.calories_range) || "—";
              return `<li><b>${escapeHtml(nm)}</b>: ${escapeHtml(cr)}</li>`;
            }).join("")}
          </ul>`
        : `<div style="color:var(--muted); font-size:13px;">—</div>`;

      resultCaloriesDetails.innerHTML = `
        <div class="chip"><span>Total</span><strong style="margin-left:6px;">${escapeHtml(totalCals)}</strong></div>

        <div style="margin-top:10px; color:var(--muted); font-size:12px;">By item (portion-based estimate)</div>
        ${rows}

        <div style="margin-top:10px; color:var(--muted); font-size:12px;">Portion assumption</div>
        <div style="margin-top:6px;">${escapeHtml(portionAssumption)}</div>
      `;
    }

    // Behavior Coaching
    if (resultBehaviorCoaching) {
      resultBehaviorCoaching.innerHTML = `
        <div class="row" style="gap:10px; margin-top:6px;">
          <div class="chip"><span>Portion risk</span><strong style="margin-left:6px;">${escapeHtml(portionRisk)}</strong></div>
          <div class="chip"><span>Timing</span><strong style="margin-left:6px;">${escapeHtml(timingAdvice)}</strong></div>
        </div>

        <div style="margin-top:10px; color:var(--muted); font-size:13px;">
          Practical coaching: if portion risk is Medium/High, consider a smaller portion, add fiber/protein, or pair with vegetables.
        </div>
      `;
    }

    // Notes
    if (resultNotes) resultNotes.textContent = notes;

    // Helpful tips
    if (resultHelpfulTips) resultHelpfulTips.innerHTML = ulHtml(tips);
  }

  function clearResults() {
    setDash(resultClassification);
    setDash(resultConfidence);
    setDash(resultItems);
    if (resultNutritionBreakdown) resultNutritionBreakdown.textContent = "—";
    if (resultQualitySignals) resultQualitySignals.textContent = "—";
    if (resultCaloriesDetails) resultCaloriesDetails.textContent = "—";
    if (resultBehaviorCoaching) resultBehaviorCoaching.textContent = "—";
    setDash(resultNotes);
    if (resultHelpfulTips) resultHelpfulTips.textContent = "—";
  }

  // ---------- Analyze immediately after capture ----------
  async function analyzeCurrentPhoto() {
    if (!lastFoodDataUrl) return;

    try {
      setStatus("Analyzing…", true);
      if (resultNotes) resultNotes.textContent = "Analyzing…";

      const data = await callAnalyzeApi(lastFoodDataUrl);
      renderResultsV2(data);

      setStatus("", false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      logDebug({ type: "analyze_error", error: String(e) });
      setStatus("Analysis failed. Open Debug.", true);
      if (resultNotes) resultNotes.textContent = "Analysis failed. Open Debug.";
    }
  }

  // ---------- UI wiring ----------
  takePhotoBtn?.addEventListener("click", async () => {
    try {
      takePhotoBtn.disabled = true;
      setStatus("", false);

      if (imgPreview) {
        imgPreview.removeAttribute("src");
        imgPreview.style.display = "none";
      }
      if (previewEmpty) previewEmpty.style.display = "block";

      clearResults();

      const dataUrl = await capturePhoto();
      lastFoodDataUrl = dataUrl;

      if (imgPreview) {
        imgPreview.src = dataUrl;
        imgPreview.style.display = "block";
      }
      if (previewEmpty) previewEmpty.style.display = "none";

      logDebug({ type: "photo_captured", length: dataUrl.length });

      // 🚀 Auto-analyze immediately after capture
      await analyzeCurrentPhoto();
    } catch (e) {
      logDebug({ type: "capture_error", error: String(e) });
      setStatus("Capture failed. Open Debug.", true);
      if (resultNotes) resultNotes.textContent = "Could not capture photo. Open Debug.";
      if (previewEmpty) previewEmpty.textContent = "Capture failed. Open Debug.";
    } finally {
      takePhotoBtn.disabled = false;
    }
  });

  // ---------- init ----------
  clearResults();
  setStatus("", false);
  logDebug("food.js initialized (v2, auto-analyze)");
})();
