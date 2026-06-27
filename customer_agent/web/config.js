// Resolve the API base URL.
// By default the UI is served by the same FastAPI server, so relative paths work.
// Override for split deploys via localStorage.CRM_API_BASE.
(function () {
  function getApiBase() {
    const ls = (localStorage.getItem("CRM_API_BASE") || "").trim();
    return ls ? ls.replace(/\/$/, "") : ""; // "" => same-origin
  }
  function apiUrl(path) {
    if (!path.startsWith("/")) path = "/" + path;
    return getApiBase() + path;
  }
  window.CRM_CONFIG = { getApiBase, apiUrl };
})();
