const RAW_API_URL =
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.VITE_API_URL ||
  "http://127.0.0.1:8000";
const API_BASE_URL = RAW_API_URL.replace(/\/+$/, "");

// ── Core fetch wrapper ────────────────────────────────────────────────────────

async function request(endpoint, options = {}) {
  let response;
  const headers = { ...(options.headers || {}) };
  const adminPin =
    sessionStorage.getItem("cq_admin_pin") ||
    (sessionStorage.getItem("cq_lead_engineer") === "true" ? "1234" : null);
  if (adminPin && !headers["X-Admin-PIN"] && !headers["x-admin-pin"]) {
    headers["X-Admin-PIN"] = adminPin;
  }
  const fetchOptions = { ...options, headers };

  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, fetchOptions);
  } catch {
    throw new Error(
      "Cannot reach the backend. Make sure the FastAPI server is running on port 8000."
    );
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}.`;
    try {
      const body = await response.json();
      if (body?.detail) {
        detail = typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
      }
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

// ── Image validation helper ───────────────────────────────────────────────────

const MAX_SIZE = 10 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];

export function validateImageFile(file) {
  if (!file) return "Please select an image.";
  if (!ALLOWED_TYPES.includes(file.type))
    return "Unsupported file type. Upload a JPG, PNG, or WEBP image.";
  if (file.size > MAX_SIZE)
    return "Image exceeds the 10 MB limit. Please choose a smaller file.";
  return null;
}

// ── Health & features ─────────────────────────────────────────────────────────

export const checkBackendHealth = () => request("/health");
export const getFeatures = () => request("/api/features");

// ── 1. Safety Monitoring & Multi-Site Hub ─────────────────────────────────────

export function analyzeSafety(file, context = "") {
  const err = validateImageFile(file);
  if (err) return Promise.reject(new Error(err));
  const form = new FormData();
  form.append("file", file);
  form.append("context", context);
  return request("/api/safety/analyze", { method: "POST", body: form });
}

export const getSites = () => request("/api/sites");

export const setupSites = (count, names = null) =>
  request("/api/sites/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count, names }),
  });

export const createSite = (data) =>
  request("/api/sites", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const updateSite = (siteId, data) =>
  request(`/api/sites/${siteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

export const deleteSite = (siteId) =>
  request(`/api/sites/${siteId}`, {
    method: "DELETE",
  });

export const getSitePhotos = (siteId) => request(`/api/sites/${siteId}/photos`);

export function uploadSitePhoto(siteId, file, caption = "", sender = "Civil Engineer (On-Site)", context = "") {
  const err = validateImageFile(file);
  if (err) return Promise.reject(new Error(err));
  const form = new FormData();
  form.append("file", file);
  form.append("caption", caption);
  form.append("sender", sender);
  form.append("context", context);
  return request(`/api/sites/${siteId}/photos/upload`, { method: "POST", body: form });
}

export function simulateTelegramUpload(siteId, file, senderName = "Ramesh (Foreman)", caption = "") {
  const err = validateImageFile(file);
  if (err) return Promise.reject(new Error(err));
  const form = new FormData();
  form.append("file", file);
  form.append("sender_name", senderName);
  form.append("caption", caption);
  return request(`/api/sites/${siteId}/photos/simulate-telegram`, { method: "POST", body: form });
}

export const getTelegramStatus = () => request("/api/telegram/status");

export const configureTelegram = (token, webhookUrl = "") =>
  request("/api/telegram/configure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, webhook_url: webhookUrl }),
  });

export const pollTelegramNow = () => request("/api/telegram/poll-now", { method: "POST" });

export function getSiteImageUrl(siteId, filename) {
  if (!filename) return "";
  if (filename.startsWith("http://") || filename.startsWith("https://")) return filename;
  return `${API_BASE_URL}/api/sites/${siteId}/images/${filename}`;
}

// ── 2. Crack Detection & Per-Site Structural Log ─────────────────────────────

export function analyzeCracks(file, context = "", siteId = "") {
  const err = validateImageFile(file);
  if (err) return Promise.reject(new Error(err));
  const form = new FormData();
  form.append("file", file);
  form.append("context", context);
  if (siteId) form.append("site_id", siteId);
  return request("/api/cracks/analyze", { method: "POST", body: form });
}

export const getSiteCracks = (siteId) => request(`/api/sites/${siteId}/cracks`);

export const getAllCracks = (siteId = "") =>
  request(
    siteId
      ? `/api/cracks/history?site_id=${encodeURIComponent(siteId)}`
      : "/api/cracks/history"
  );

export function uploadSiteCrack(
  siteId,
  file,
  caption = "",
  sender = "Civil Engineer",
  context = ""
) {
  const err = validateImageFile(file);
  if (err) return Promise.reject(new Error(err));
  const form = new FormData();
  form.append("file", file);
  form.append("caption", caption);
  form.append("sender", sender);
  form.append("context", context);
  return request(`/api/sites/${siteId}/cracks/upload`, {
    method: "POST",
    body: form,
  });
}

// ── 3. Project Planner ────────────────────────────────────────────────────────

export function generatePlan(fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return request("/api/planning/generate", { method: "POST", body: form });
}

// ── 4. Property Valuation ─────────────────────────────────────────────────────

export function estimateValuation(fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return request("/api/valuation/estimate", { method: "POST", body: form });
}

// ── 5. Weather Advisory ───────────────────────────────────────────────────────

export function getWeatherAdvisory(fields) {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  return request("/api/weather/advisory", { method: "POST", body: form });
}

// ── 6. Daily Site Reports ─────────────────────────────────────────────────────

export function generateReport(fields, imageFile = null) {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => {
    if (v !== undefined && v !== null) form.append(k, v);
  });
  if (imageFile) form.append("file", imageFile);
  return request("/api/reports/generate", { method: "POST", body: form });
}

export const getSiteReports = (siteId = "") =>
  request(
    siteId
      ? `/api/reports/history?site_id=${encodeURIComponent(siteId)}`
      : "/api/reports/history"
  );

export const broadcastReportToTelegram = (siteId, report) =>
  request("/api/reports/broadcast-telegram", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site_id: siteId, report }),
  });

// ── 7. AI Construction Assistant ──────────────────────────────────────────────

export function askAssistant(question, context = "") {
  const form = new FormData();
  form.append("question", question);
  form.append("context", context);
  return request("/api/assistant/ask", { method: "POST", body: form });
}
