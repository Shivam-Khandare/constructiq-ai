import { useState, useEffect } from "react";
import {
  analyzeCracks,
  getAllCracks,
  getSites,
  getSiteImageUrl,
  validateImageFile,
} from "../services/api";
import ImageUploader from "../components/ImageUploader";
import ResultCard from "../components/ResultCard";
import Spinner from "../components/Spinner";

const SEVERITY_COLOR = {
  none: "#16a34a",
  hairline: "#16a34a",
  minor: "#16a34a",
  moderate: "#d97706",
  severe: "#ea580c",
  critical: "#b91c1c",
};

export default function CrackDetection({ backendStatus }) {
  const [activeTab, setActiveTab] = useState("detect"); // "detect" | "records"

  // ── Detection State ──
  const [file, setFile] = useState(null);
  const [context, setContext] = useState("");
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [savedSiteName, setSavedSiteName] = useState("");

  // ── Sites List & History State ──
  const [sites, setSites] = useState([]);
  const [recordsSiteFilter, setRecordsSiteFilter] = useState("");
  const [records, setRecords] = useState([]);
  const [loadingRecords, setLoadingRecords] = useState(false);

  // Load sites on mount
  useEffect(() => {
    loadSitesList();
  }, []);

  async function loadSitesList() {
    try {
      const data = await getSites();
      if (data?.sites) {
        setSites(data.sites);
      }
    } catch {
      // ignore
    }
  }

  // Load crack records when switching to records tab or changing filter
  async function loadRecords(siteId = recordsSiteFilter) {
    setLoadingRecords(true);
    try {
      const data = await getAllCracks(siteId);
      setRecords(data?.cracks || []);
    } catch (err) {
      console.warn("Failed to load crack records:", err);
    } finally {
      setLoadingRecords(false);
    }
  }

  function handleTabChange(tab) {
    setActiveTab(tab);
    if (tab === "records") {
      loadRecords(recordsSiteFilter);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const validationError = validateImageFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setSavedSiteName("");

    try {
      const data = await analyzeCracks(file, context, selectedSiteId);
      setResult(data);

      if (selectedSiteId) {
        const found = sites.find((s) => s.id === selectedSiteId);
        setSavedSiteName(found ? found.name : `Site (${selectedSiteId})`);
        loadSitesList(); // refresh site crack counters
      }
    } catch (err) {
      setError(err.message || "Analysis failed. Please try again.");
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
              <span>🔍</span> Structural Crack & Defect Inspection
            </h2>
            <p>
              AI-driven forensic structural inspection for reinforced concrete, masonry,
              and foundations. Automatically categorizes crack severity, structural hazard,
              and generates civil engineering repair actions. Inspections can be standalone
              or directly logged under any active monitored site.
            </p>
          </div>
        </div>
      </div>

      {/* ── Toolbar: Mode Switcher ── */}
      <div className="hub-toolbar">
        <div className="view-selector">
          <button
            className={`view-btn ${activeTab === "detect" ? "active" : ""}`}
            onClick={() => handleTabChange("detect")}
          >
            🔍 Scan & Analyze Cracks
          </button>
          <button
            className={`view-btn ${activeTab === "records" ? "active" : ""}`}
            onClick={() => handleTabChange("records")}
          >
            📜 Monitored Site Crack Records
          </button>
        </div>

        {activeTab === "records" && (
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <label style={{ fontSize: "12px", fontWeight: "600", color: "#3d5244" }}>
              Filter Site:
            </label>
            <select
              value={recordsSiteFilter}
              onChange={(e) => {
                const newFilter = e.target.value;
                setRecordsSiteFilter(newFilter);
                loadRecords(newFilter);
              }}
              style={{
                padding: "6px 12px",
                fontSize: "12px",
                borderRadius: "6px",
                border: "1px solid #d1dbd3",
              }}
            >
              <option value="">All Monitored Sites</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.crack_count || 0} cracks)
                </option>
              ))}
            </select>

            <button
              className="btn-card-action"
              onClick={() => loadRecords(recordsSiteFilter)}
              title="Refresh crack logs"
            >
              🔄 Refresh
            </button>
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
         TAB 1: DETECT & ANALYZE CRACKS
         ══════════════════════════════════════════════════════════════════════ */}
      {activeTab === "detect" && (
        <div className="feature-panel">
          <form onSubmit={handleSubmit} className="feature-form">
            {/* Optional Site Linking Selector */}
            <div className="form-group">
              <label style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Link to Monitored Site (Optional)</span>
                <span style={{ fontSize: "11px", fontWeight: "400", color: "#738078" }}>
                  Saves detection directly to that site's structural registry
                </span>
              </label>
              <select
                value={selectedSiteId}
                onChange={(e) => setSelectedSiteId(e.target.value)}
                style={{
                  padding: "9px 12px",
                  fontSize: "13px",
                  borderRadius: "8px",
                  border: "1px solid #d1dbd3",
                }}
              >
                <option value="">-- Standalone Single Photo Scan (No Site) --</option>
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    🏗️ {s.name} ({s.location || "Active"})
                  </option>
                ))}
              </select>
            </div>

            <ImageUploader
              file={file}
              onChange={(f) => {
                setFile(f);
                setResult(null);
                setError("");
                setSavedSiteName("");
              }}
            />

            <div className="form-group">
              <label>Structural context (optional)</label>
              <textarea
                placeholder="e.g. Beam B-12 shear diagonal crack, 10-year-old concrete structure, basement level"
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={2}
              />
            </div>

            <button
              className="btn-primary"
              type="submit"
              disabled={loading || !file || backendStatus !== "healthy"}
            >
              {loading ? <><Spinner /> Analyzing Structure...</> : "Detect & Analyze Cracks"}
            </button>
            {backendStatus !== "healthy" && (
              <p className="hint-text">Backend unavailable — cannot run analysis.</p>
            )}
          </form>

          {error && <div className="alert alert-error">{error}</div>}

          {savedSiteName && (
            <div
              style={{
                marginTop: "16px",
                padding: "12px 16px",
                background: "#f0fdf4",
                border: "1px solid #86efac",
                borderRadius: "8px",
                color: "#166534",
                fontSize: "13px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <span>✅</span>
              <span>
                <b>Recorded Under Monitored Site:</b> This crack diagnosis has been logged to{" "}
                <b>{savedSiteName}</b>. It is accessible in the site's structural album and history.
              </span>
            </div>
          )}

          {result && (
            <div className="results">
              {result.is_valid_crack_photo === false && (
                <div
                  style={{
                    padding: "12px 14px",
                    background: "#fef2f2",
                    border: "1px solid #fecdd3",
                    borderRadius: "8px",
                    marginBottom: "14px",
                    color: "#991b1b",
                    fontSize: "13px",
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                  }}
                >
                  <span style={{ fontSize: "20px" }}>ℹ️</span>
                  <div>
                    <b>No Structural Element Detected:</b> No concrete, masonry wall, or structural surfaces were detected in this photograph. If you intended to inspect site worker safety, scaffolding, or PPE compliance, please use the <b>🦺 Safety Monitoring</b> tool.
                  </div>
                </div>
              )}

              <div
                className="risk-banner"
                style={{
                  background:
                    result.overall_severity === "critical" || result.overall_severity === "severe"
                      ? "#fef2f2"
                      : result.overall_severity === "moderate"
                        ? "#fffbeb"
                        : "#f0fdf4",
                }}
              >
                <span
                  className="risk-badge"
                  style={{
                    background:
                      SEVERITY_COLOR[result.overall_severity] || "#16a34a",
                  }}
                >
                  {result.crack_detected
                    ? `${(result.overall_severity || "DETECTED").toUpperCase()} CRACK`
                    : "NO CRACKS DETECTED"}
                </span>
                <span className="ppe-badge">
                  Structural risk: {(result.structural_risk || "low").toUpperCase()}
                </span>
                {result.immediate_action_required && (
                  <span className="risk-badge" style={{ background: "#b91c1c" }}>
                    🚨 IMMEDIATE ACTION REQUIRED
                  </span>
                )}
              </div>

              <div className="plain-risk-box" style={{ marginTop: "14px", padding: "14px" }}>
                <div className="plain-risk-row">
                  <span className="plain-risk-label" style={{ fontSize: "13px" }}>
                    📝 Forensic Diagnosis:
                  </span>
                  <span className="plain-risk-desc" style={{ fontSize: "13px" }}>
                    {result.summary}
                  </span>
                </div>
                {result.cracks?.[0]?.recommended_action && (
                  <div className="plain-risk-row" style={{ marginTop: "8px" }}>
                    <span className="plain-risk-label" style={{ fontSize: "13px" }}>
                      ✅ Civil Engineer Fix:
                    </span>
                    <span className="plain-improve-desc" style={{ fontSize: "13px" }}>
                      {result.cracks[0].recommended_action}
                    </span>
                  </div>
                )}
              </div>

              {result.cracks?.length > 0 && (
                <ResultCard title={`🔍 Crack Inspection Breakdown (${result.cracks.length} defect${result.cracks.length > 1 ? "s" : ""})`}>
                  <div className="hazard-list">
                    {result.cracks.map((c, i) => (
                      <div key={i} className="hazard-item">
                        <div className="hazard-header">
                          <span className="hazard-type">{c.type || "Structural Crack"}</span>
                          <span
                            className="severity-tag"
                            style={{
                              color: SEVERITY_COLOR[c.severity] || "#16a34a",
                              fontWeight: "700",
                            }}
                          >
                            {(c.severity || "minor").toUpperCase()}
                          </span>
                        </div>
                        {c.location && <p className="hazard-location">📍 Member: {c.location}</p>}
                        {c.probable_cause && (
                          <p className="hazard-desc">🔎 Probable Cause: {c.probable_cause}</p>
                        )}
                        {c.recommended_action && (
                          <p className="hazard-rec">✅ Repair Action: {c.recommended_action}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </ResultCard>
              )}
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         TAB 2: MONITORED SITE CRACK RECORDS (Across Sites / Telegram)
         ══════════════════════════════════════════════════════════════════════ */}
      {activeTab === "records" && (
        <div className="feature-panel">
          {loadingRecords ? (
            <div style={{ textAlign: "center", padding: "40px" }}>
              <Spinner /> Loading structural crack records...
            </div>
          ) : records.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: "40px",
                background: "#ffffff",
                borderRadius: "10px",
                border: "1px solid #e5ebe6",
              }}
            >
              <div style={{ fontSize: "32px", marginBottom: "10px" }}>🔍</div>
              <p style={{ margin: "0 0 14px", color: "#738078", fontSize: "14px" }}>
                {recordsSiteFilter
                  ? "No structural cracks logged for this site yet."
                  : "No structural cracks logged across any monitored site yet."}
              </p>
              <p style={{ margin: "0 0 16px", color: "#9ca3af", fontSize: "12px" }}>
                Field workers can log cracks directly from Telegram by selecting the site and tapping{" "}
                <b>"🔍 Crack Detection"</b>, or civil engineers can scan photos above with a site selected.
              </p>
              <button
                className="btn-primary"
                onClick={() => setActiveTab("detect")}
              >
                Scan New Crack Photo
              </button>
            </div>
          ) : (
            <div className="album-feed">
              {records.map((item, idx) => {
                const diag = item.crack_analysis || {};
                const sev = (diag.overall_severity || "none").toLowerCase();
                const detected = diag.crack_detected;
                const sevClass =
                  sev === "critical" || sev === "severe"
                    ? "critical"
                    : sev === "moderate"
                      ? "high"
                      : sev === "minor"
                        ? "medium"
                        : "low";

                return (
                  <div key={item.id || idx} className="album-item">
                    <img
                      src={getSiteImageUrl(item.site_id, item.filename)}
                      alt={item.caption || "Crack Photo"}
                      className="album-item-thumb"
                    />
                    <div className="album-item-content">
                      <div className="album-item-header">
                        <div>
                          <span
                            style={{
                              background: "#e8f4ec",
                              color: "#18794e",
                              fontWeight: "700",
                              fontSize: "11px",
                              padding: "2px 8px",
                              borderRadius: "4px",
                              marginRight: "8px",
                            }}
                          >
                            🏗️ {item.site_name || item.site_id}
                          </span>
                          <span className="album-sender">{item.sender}</span>
                          <span className="album-time">
                            {" "}
                            • {new Date(item.timestamp).toLocaleString()}
                          </span>
                        </div>
                        <span className={`status-badge ${sevClass}`}>
                          {detected ? `${sev.toUpperCase()} CRACK` : "NO CRACKS"}
                        </span>
                      </div>

                      {item.caption && (
                        <p className="album-caption">"{item.caption}"</p>
                      )}

                      <div className="plain-risk-box">
                        <div className="plain-risk-row">
                          <span className="plain-risk-label">🏗️ Structural Risk:</span>
                          <span className="plain-risk-desc">
                            {(diag.structural_risk || "none").toUpperCase()}
                          </span>
                        </div>
                        <div className="plain-risk-row">
                          <span className="plain-risk-label">📝 Forensic Diagnosis:</span>
                          <span className="plain-risk-desc">{diag.summary}</span>
                        </div>
                        {diag.cracks?.[0]?.recommended_action && (
                          <div className="plain-risk-row">
                            <span className="plain-risk-label">✅ Civil Fix:</span>
                            <span className="plain-improve-desc">
                              {diag.cracks[0].recommended_action}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
