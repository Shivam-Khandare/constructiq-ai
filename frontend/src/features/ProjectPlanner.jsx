import { useState } from "react";
import { generatePlan } from "../services/api";
import ResultCard from "../components/ResultCard";
import Spinner from "../components/Spinner";

const BLUEPRINTS = [
  {
    label: "🏡 2-Story Residential Villa",
    type: "Residential Villa (G+1)",
    area: "3200",
    budget: "185000",
    location: "Mumbai Suburbs",
    desc: "RCC frame structure with brick masonry, modular kitchen, rainwater harvesting, solar panel rooftop, and perimeter boundary wall.",
  },
  {
    label: "🏢 5-Story Commercial Plaza",
    type: "Commercial Office Complex (G+4)",
    area: "24000",
    budget: "1450000",
    location: "Bengaluru Tech Corridor",
    desc: "Reinforced concrete commercial building with basement parking, dual passenger elevators, glass curtain facade, HVAC ducting, and advanced fire suppression.",
  },
  {
    label: "🏭 Pre-Engineered Steel Warehouse",
    type: "Industrial Warehouse Facility",
    area: "16000",
    budget: "520000",
    location: "Bhiwandi Logistics Hub",
    desc: "Clear-span PEB steel structural framing with insulated sandwich roof panels, heavy-duty laser screed industrial concrete flooring, and 4 loading dock bays.",
  },
  {
    label: "🌉 Concrete Flyover Overpass",
    type: "Civil Infrastructure Overpass Bridge",
    area: "45000",
    budget: "3100000",
    location: "Urban Highway Corridor",
    desc: "Prestressed concrete box girder flyover structure with reinforced concrete piers, pile foundations, crash barriers, expansion joints, and asphalt wearing course.",
  },
];

export default function ProjectPlanner({ backendStatus }) {
  const [fields, setFields] = useState({
    project_type: "Residential Villa (G+1)",
    area_sqft: "3200",
    location: "Mumbai, Maharashtra",
    budget_usd: "185000",
    description: "RCC frame structure with brick masonry, modular kitchen, and rainwater harvesting.",
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  function set(key, val) {
    setFields((f) => ({ ...f, [key]: val }));
  }

  function applyBlueprint(bp) {
    setFields({
      project_type: bp.type,
      area_sqft: bp.area,
      location: bp.location,
      budget_usd: bp.budget,
      description: bp.desc,
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!fields.project_type || !fields.area_sqft) {
      setError("Project type and area (sq ft) are required.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await generatePlan(fields));
    } catch (err) {
      setError(err.message || "Failed to generate project plan.");
    } finally {
      setLoading(false);
    }
  }

  // Calculate timeline milestone statistics
  const totalWeeks = result?.estimated_duration_weeks || 1;
  const phases = result?.phases || [];

  return (
    <div className="multi-site-hub">
      {/* ── Feature Header ── */}
      <div className="hub-banner">
        <div className="hub-banner-content">
          <div className="hub-title-area">
            <h2>
              <span>📐</span> AI Civil Project Planner & Estimation Engine
            </h2>
            <p>
              Autonomous architectural and structural project scheduling. Computes multi-phase
              critical path durations, civil engineering material takeoffs, budget breakdown,
              and OSHA construction safety milestones.
            </p>
          </div>
        </div>
      </div>

      <div className="feature-panel">
        {/* Quick Blueprints */}
        <div style={{ marginBottom: "16px" }}>
          <label style={{ fontSize: "11px", fontWeight: "700", color: "#738078", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            ⚡ Instant Project Blueprints:
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "6px" }}>
            {BLUEPRINTS.map((bp, i) => (
              <button
                key={i}
                type="button"
                className="btn-card-action"
                style={{ fontSize: "12px", padding: "5px 11px" }}
                onClick={() => applyBlueprint(bp)}
              >
                {bp.label}
              </button>
            ))}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="feature-form">
          <div className="form-row">
            <div className="form-group">
              <label>Project Classification / Type *</label>
              <input
                type="text"
                placeholder="e.g. Commercial Office Plaza, Residential Villa"
                value={fields.project_type}
                onChange={(e) => set("project_type", e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label>Total Built-Up Area (sq ft) *</label>
              <input
                type="number"
                placeholder="e.g. 3500"
                value={fields.area_sqft}
                onChange={(e) => set("area_sqft", e.target.value)}
                required
                min={1}
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Location & Regulatory Jurisdiction</label>
              <input
                type="text"
                placeholder="e.g. Mumbai, Maharashtra or Austin, TX"
                value={fields.location}
                onChange={(e) => set("location", e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Target Budget (USD $)</label>
              <input
                type="number"
                placeholder="e.g. 250000"
                value={fields.budget_usd}
                onChange={(e) => set("budget_usd", e.target.value)}
                min={0}
              />
            </div>
          </div>

          <div className="form-group">
            <label>Project Scope & Engineering Specifications</label>
            <textarea
              placeholder="Describe site conditions, foundation type (raft/pile), finishing standards, HVAC, solar, or LEED requirements..."
              value={fields.description}
              onChange={(e) => set("description", e.target.value)}
              rows={3}
            />
          </div>

          <button
            className="btn-primary"
            type="submit"
            disabled={loading || backendStatus !== "healthy"}
          >
            {loading ? <><Spinner /> Generating Civil Engineering Plan...</> : "Generate Comprehensive Project Plan"}
          </button>
          {backendStatus !== "healthy" && (
            <p className="hint-text">Backend unavailable — cannot run planner.</p>
          )}
        </form>

        {error && <div className="alert alert-error">{error}</div>}

        {result && (
          <div className="results" style={{ marginTop: "24px" }}>
            {/* Project Hero Header */}
            <div className="risk-banner" style={{ background: "#f0fdf4" }}>
              <span className="risk-badge" style={{ background: "#18794e" }}>
                🏗️ {result.project_type || fields.project_type}
              </span>
              <span className="ppe-badge">⏱️ {result.estimated_duration_weeks} Weeks Duration</span>
              <span className="ppe-badge">💰 Est. Cost: ${result.total_estimated_cost_usd?.toLocaleString()}</span>
              {fields.budget_usd && (
                <span className="ppe-badge" style={{ background: "#edf2ee", color: "#3d5244" }}>
                  Budget: ${Number(fields.budget_usd).toLocaleString()}
                </span>
              )}
            </div>

            <p className="result-summary" style={{ fontSize: "14px", lineHeight: "1.55" }}>
              {result.summary}
            </p>

            {/* KPI Summary Cards */}
            <div className="kpi-row" style={{ margin: "16px 0" }}>
              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#f0fdf4", color: "#166534" }}>⏱️</div>
                <div className="kpi-data">
                  <span className="kpi-label">Total Duration</span>
                  <span className="kpi-value">
                    {result.estimated_duration_weeks} Weeks
                  </span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#eff6ff", color: "#2563eb" }}>🧱</div>
                <div className="kpi-data">
                  <span className="kpi-label">Major Materials</span>
                  <span className="kpi-value">{result.materials?.length || 0} Line Items</span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#fef3c7", color: "#b45309" }}>📅</div>
                <div className="kpi-data">
                  <span className="kpi-label">Phased Milestones</span>
                  <span className="kpi-value">{phases.length} Execution Phases</span>
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-icon" style={{ background: "#fdf2f8", color: "#be185d" }}>⚠</div>
                <div className="kpi-data">
                  <span className="kpi-label">Identified Hazards</span>
                  <span className="kpi-value">{result.key_risks?.length || 0} Safety Risks</span>
                </div>
              </div>
            </div>

            {/* Visual Gantt Phase Timeline */}
            {phases.length > 0 && (
              <ResultCard title={`📅 Phased Execution Timeline & Gantt Schedule (${phases.length} Phases)`}>
                <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  {phases.map((p, i) => {
                    const phaseWeeks = p.duration_weeks || 1;
                    const percent = Math.min(100, Math.max(10, Math.round((phaseWeeks / totalWeeks) * 100)));

                    return (
                      <div
                        key={i}
                        style={{
                          background: "#f9fbf9",
                          border: "1px solid #e5ebe6",
                          borderRadius: "8px",
                          padding: "14px",
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <span
                              style={{
                                background: "#18794e",
                                color: "#ffffff",
                                fontSize: "11px",
                                fontWeight: "700",
                                padding: "2px 8px",
                                borderRadius: "4px",
                              }}
                            >
                              Phase {i + 1}
                            </span>
                            <span style={{ fontWeight: "700", fontSize: "14px", color: "#17251f" }}>
                              {p.phase}
                            </span>
                          </div>
                          <span style={{ fontSize: "12px", fontWeight: "700", color: "#18794e" }}>
                            ⏱️ {phaseWeeks} Weeks ({percent}% of project)
                          </span>
                        </div>

                        {/* Progress Bar */}
                        <div style={{ width: "100%", height: "8px", background: "#e5ebe6", borderRadius: "4px", overflow: "hidden", marginBottom: "10px" }}>
                          <div
                            style={{
                              width: `${percent}%`,
                              height: "100%",
                              background: i % 2 === 0 ? "#18794e" : "#2563eb",
                              borderRadius: "4px",
                              transition: "width 0.3s ease",
                            }}
                          />
                        </div>

                        {p.activities?.length > 0 && (
                          <div style={{ marginTop: "8px" }}>
                            <span style={{ fontSize: "11px", fontWeight: "700", color: "#738078", textTransform: "uppercase" }}>
                              Milestone Deliverables:
                            </span>
                            <ul className="activity-list" style={{ marginTop: "4px" }}>
                              {p.activities.map((a, j) => (
                                <li key={j} style={{ fontSize: "12.5px" }}>{a}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </ResultCard>
            )}

            {/* Civil Materials Estimation Table */}
            {result.materials?.length > 0 && (
              <ResultCard title={`🧱 Civil Engineering Material Takeoff & Bill of Quantities (${result.materials.length} Materials)`}>
                <div className="table-wrapper">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Material Item</th>
                        <th>Estimated Quantity</th>
                        <th>Unit</th>
                        <th>Est. Cost (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.materials.map((m, i) => (
                        <tr key={i}>
                          <td style={{ fontWeight: "600" }}>{m.material}</td>
                          <td>{m.quantity}</td>
                          <td>{m.unit}</td>
                          <td style={{ fontWeight: "700", color: "#18794e" }}>
                            ${Number(m.estimated_cost_usd || 0).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ResultCard>
            )}

            {/* Key Project Risks */}
            {result.key_risks?.length > 0 && (
              <ResultCard title="⚠️ Key Structural, Geotechnical & Safety Risks">
                <ul className="action-list">
                  {result.key_risks.map((r, i) => (
                    <li key={i} style={{ fontSize: "13px", lineHeight: "1.5" }}>{r}</li>
                  ))}
                </ul>
              </ResultCard>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
