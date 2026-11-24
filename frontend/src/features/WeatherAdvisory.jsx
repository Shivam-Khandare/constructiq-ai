import { useEffect, useState } from "react";
import { getWeatherAdvisory, getSites } from "../services/api";
import ResultCard from "../components/ResultCard";
import Spinner from "../components/Spinner";

const RISK_COLOR = {
  low: "#16a34a",
  medium: "#d97706",
  high: "#ea580c",
  extreme: "#b91c1c",
};

const RISK_BG = {
  low: "#f0fdf4",
  medium: "#fffbeb",
  high: "#fff7ed",
  extreme: "#fef2f2",
};

const CITY_PRESETS = [
  { name: "Mumbai", label: "🏙️ Mumbai (Coastal Monsoon)", activities: "Concrete slab casting, Scaffolding, Drainage" },
  { name: "Delhi NCR", label: "🏛️ Delhi NCR (Summer Heat & Smog)", activities: "Steel fabrication, Brick masonry, Earthwork" },
  { name: "Bengaluru", label: "🌳 Bengaluru (Moderate / Thunderstorms)", activities: "Foundation excavation, Exterior painting" },
  { name: "Dubai", label: "🏜️ Dubai (Extreme Arid Heat >45°C)", activities: "Night shift pouring, Tower crane hoisting" },
  { name: "London / NY", label: "🗽 New York / London (Freeze-Thaw / Wind)", activities: "Roofing, Scaffolding inspection, Concrete curing" },
];

const HAZARD_ACTIVITIES = [
  { icon: "🏗️", label: "Tower Crane & Hoisting", threshold: "Wind limit: 20-30 mph" },
  { icon: "🧱", label: "Concrete Pouring & Curing", threshold: "Temp 5°C - 35°C, Rain halt" },
  { icon: "🪜", label: "Roofing & Height Scaffolding", threshold: "High wind & rain prohibition" },
  { icon: "🎨", label: "Exterior Painting & Plaster", threshold: "Humidity must be < 85%" },
  { icon: "🚜", label: "Trenching & Deep Excavation", threshold: "Severe collapse risk in heavy rain" },
  { icon: "⚡", label: "Outdoor Welding & Electrical", threshold: "Zero moisture / rain allowed" },
];

export default function WeatherAdvisory({ backendStatus }) {
  const [sites, setSites] = useState([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [fields, setFields] = useState({
    location: "Mumbai, Maharashtra",
    activities: "Concrete pouring, Scaffolding, Exterior masonry",
    date_range: "next 7 days",
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  // Load sites on mount for direct link
  useEffect(() => {
    async function init() {
      try {
        const res = await getSites();
        if (res?.sites?.length > 0) {
          setSites(res.sites);
        }
      } catch {
        // silent
      }
    }
    init();
  }, []);

  function set(key, val) {
    setFields((f) => ({ ...f, [key]: val }));
  }

  function handleSiteSelect(siteId) {
    setSelectedSiteId(siteId);
    if (!siteId) return;
    const found = sites.find((s) => s.id === siteId);
    if (found) {
      setFields((f) => ({
        ...f,
        location: found.location || found.name,
      }));
    }
  }

  function applyPreset(preset) {
    setFields((f) => ({
      ...f,
      location: preset.name,
      activities: preset.activities,
    }));
  }

  function toggleActivity(actLabel) {
    const current = fields.activities ? fields.activities.split(",").map((s) => s.trim()) : [];
    if (current.includes(actLabel)) {
      set("activities", current.filter((x) => x !== actLabel).join(", "));
    } else {
      set("activities", [...current, actLabel].filter(Boolean).join(", "));
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!fields.location.trim()) {
      setError("Site location is required.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await getWeatherAdvisory(fields));
    } catch (err) {
      setError(err.message || "Failed to generate weather advisory.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="multi-site-hub">
      {/* ── Feature Header ── */}
      <div className="hub-banner">
        <div className="hub-banner-content">
          <div className="hub-title-area">
            <h2>
              <span>🌤️</span> Construction Weather Advisory & Safety Risk Radar
            </h2>
            <p>
              AI meteorological intelligence tailored specifically for civil engineering worksites.
              Identifies wind, rain, and temperature hazards for concrete curing, crane lifts,
              trench stability, and high-elevation scaffolding.
            </p>
          </div>
        </div>
      </div>

      <div className="feature-panel">
        {/* Quick Hub Presets */}
        <div style={{ marginBottom: "16px" }}>
          <label style={{ fontSize: "11px", fontWeight: "700", color: "#738078", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            ⚡ Quick Construction Hub Presets:
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "6px" }}>
            {CITY_PRESETS.map((p, idx) => (
              <button
                key={idx}
                type="button"
                className="btn-card-action"
                style={{ fontSize: "12px", padding: "5px 10px" }}
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="feature-form">
          <div className="form-row">
            {/* Monitored Site Link */}
            {sites.length > 0 && (
              <div className="form-group">
                <label>Link to Monitored Site</label>
                <select
                  value={selectedSiteId}
                  onChange={(e) => handleSiteSelect(e.target.value)}
                  style={{
                    padding: "9px 12px",
                    borderRadius: "8px",
                    border: "1px solid #d1dbd3",
                    fontSize: "13px",
                    background: "#ffffff",
                  }}
                >
                  <option value="">-- Custom Location --</option>
                  {sites.map((s) => (
                    <option key={s.id} value={s.id}>
                      🏗️ {s.name} ({s.location || "Active"})
                    </option>
                  ))}
                </select>
              </div>
            )}

            <div className="form-group">
              <label>Site Location / City *</label>
              <input
                type="text"
                placeholder="e.g. Mumbai, Maharashtra or London, UK"
                value={fields.location}
                onChange={(e) => set("location", e.target.value)}
                required
              />
            </div>

            <div className="form-group">
              <label>Advisory Forecast Period</label>
              <select
                value={fields.date_range}
                onChange={(e) => set("date_range", e.target.value)}
                style={{
                  padding: "9px 12px",
                  borderRadius: "8px",
                  border: "1px solid #d1dbd3",
                  fontSize: "13px",
                  background: "#ffffff",
                }}
              >
                <option>today (current shift)</option>
                <option>next 3 days</option>
                <option>next 7 days (recommended)</option>
                <option>next 2 weeks</option>
                <option>this month</option>
              </select>
            </div>
          </div>

          {/* Quick Activity Selector Chips */}
          <div className="form-group">
            <label style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Planned Construction Activities</span>
              <span style={{ fontSize: "11px", fontWeight: "400", color: "#738078" }}>
                Tap chips below to toggle activities
              </span>
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "8px" }}>
              {HAZARD_ACTIVITIES.map((act, i) => {
                const active = fields.activities.toLowerCase().includes(act.label.toLowerCase().slice(0, 10));
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => toggleActivity(act.label)}
                    style={{
                      border: active ? "1px solid #18794e" : "1px solid #d1dbd3",
                      background: active ? "#f0fdf4" : "#ffffff",
                      color: active ? "#166534" : "#3d5244",
                      padding: "4px 10px",
                      borderRadius: "6px",
                      fontSize: "12px",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "5px",
                    }}
                    title={act.threshold}
                  >
                    <span>{act.icon}</span>
                    <span>{act.label}</span>
                  </button>
                );
              })}
            </div>
            <textarea
              placeholder="e.g. Concrete slab casting on 5th floor, Scaffolding dismantling, Tower crane operations"
              value={fields.activities}
              onChange={(e) => set("activities", e.target.value)}
              rows={2}
            />
          </div>

          <button
            className="btn-primary"
            type="submit"
            disabled={loading || backendStatus !== "healthy"}
          >
            {loading ? <><Spinner /> Analyzing Meteorological Risks...</> : "Generate Construction Weather Advisory"}
          </button>
          {backendStatus !== "healthy" && (
            <p className="hint-text">Backend unavailable — cannot connect to weather service.</p>
          )}
        </form>

        {error && <div className="alert alert-error">{error}</div>}

        {result && (
          <div className="results" style={{ marginTop: "24px" }}>
            {/* Risk Banner */}
            <div
              className="risk-banner"
              style={{ background: RISK_BG[result.risk_level] || "#f0f6f1" }}
            >
              <span
                className="risk-badge"
                style={{ background: RISK_COLOR[result.risk_level] || "#64748b" }}
              >
                {(result.risk_level || "MEDIUM").toUpperCase()} WEATHER RISK
              </span>
              <span className="ppe-badge">📍 {result.location}</span>
              <span className="ppe-badge">🌤️ {result.current_conditions}</span>
            </div>

            <p className="result-summary" style={{ fontSize: "14px", lineHeight: "1.55" }}>
              {result.summary}
            </p>

            {/* Weather Impact KPI Row */}
            <div className="kpi-row" style={{ margin: "16px 0" }}>
              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#f0fdf4", color: "#166534" }}>🕐</div>
                <div className="kpi-data">
                  <span className="kpi-label">Optimal Work Window</span>
                  <span className="kpi-value" style={{ fontSize: "14px", color: "#166534" }}>
                    {result.work_safe_window}
                  </span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#fff7ed", color: "#c2410c" }}>⚠️</div>
                <div className="kpi-data">
                  <span className="kpi-label">Activities At Risk</span>
                  <span className="kpi-value" style={{ fontSize: "15px" }}>
                    {result.affected_activities?.length || 0} flagged
                  </span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#eff6ff", color: "#2563eb" }}>🛡️</div>
                <div className="kpi-data">
                  <span className="kpi-label">Site Precaution Actions</span>
                  <span className="kpi-value" style={{ fontSize: "15px" }}>
                    {result.recommendations?.length || 0} safety steps
                  </span>
                </div>
              </div>
            </div>

            {/* Activities At Risk */}
            {result.affected_activities?.length > 0 && (
              <ResultCard title="⚠️ High-Risk Construction Activities">
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {result.affected_activities.map((a, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "10px 14px",
                        background: "#fff7ed",
                        border: "1px solid #fed7aa",
                        borderRadius: "8px",
                        fontSize: "13px",
                        color: "#9a3412",
                        display: "flex",
                        alignItems: "center",
                        gap: "10px",
                      }}
                    >
                      <span style={{ fontSize: "16px" }}>🛑</span>
                      <span>{a}</span>
                    </div>
                  ))}
                </div>
              </ResultCard>
            )}

            {/* Recommendations */}
            {result.recommendations?.length > 0 && (
              <ResultCard title="✅ Mandatory Site Recommendations & Protective Actions">
                <ul className="action-list">
                  {result.recommendations.map((r, i) => (
                    <li key={i} style={{ fontSize: "13px", lineHeight: "1.5" }}>
                      {r}
                    </li>
                  ))}
                </ul>
              </ResultCard>
            )}

            {/* Threshold Reference Card */}
            <ResultCard title="📐 Civil Engineering Weather Threshold Reference (OSHA / IS Codes)">
              <div className="table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Operation</th>
                      <th>Threshold Limit</th>
                      <th>Hazard Impact</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td><b>Tower Cranes & Hoists</b></td>
                      <td>Wind &gt; 30 mph (48 km/h)</td>
                      <td>Boom buckling, load sway, tipping hazard</td>
                    </tr>
                    <tr>
                      <td><b>Concrete Pours</b></td>
                      <td>Temp &lt; 5°C or &gt; 35°C, or Heavy Rain</td>
                      <td>Slump loss, cement washout, plastic shrinkage cracking</td>
                    </tr>
                    <tr>
                      <td><b>Scaffolding & Roofing</b></td>
                      <td>Wind &gt; 25 mph or Wet Surfaces</td>
                      <td>Impaired footing, severe high-elevation fall danger</td>
                    </tr>
                    <tr>
                      <td><b>Deep Excavation / Trenching</b></td>
                      <td>Rainfall Accumulation &gt; 15mm</td>
                      <td>Hydrostatic pressure increase, sidewall cave-in</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </ResultCard>
          </div>
        )}
      </div>
    </div>
  );
}
