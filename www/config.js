// www/config.js
// Configure the backend URL for mobile builds.
// Option 1: hardcode your production backend here.
// Option 2: override at runtime by setting localStorage.AI_COACH_API_BASE (useful for QA).

(function () {
  const DEFAULT_API_BASE = "https://aicoach-backend-hdwj.onrender.com";

  function getApiBase() {
    const ls = (localStorage.getItem("AI_COACH_API_BASE") || "").trim();
    if (ls) return ls.replace(/\/$/, "");
    return DEFAULT_API_BASE.replace(/\/$/, "");
  }

  // Join base + path safely.
  function apiUrl(path) {
    if (!path) return getApiBase();
    if (/^https?:\/\//i.test(path)) return path;
    const base = getApiBase();
    if (!path.startsWith("/")) path = "/" + path;
    return base + path;
  }

  window.AI_COACH_CONFIG = { getApiBase, apiUrl };
})();
