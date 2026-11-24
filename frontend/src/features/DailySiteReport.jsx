import { useEffect, useState } from "react";
import {
  broadcastReportToTelegram,
  generateReport,
  getSiteReports,
  getSites,
} from "../services/api";
import ImageUploader from "../components/ImageUploader";
import ResultCard from "../components/ResultCard";
import Spinner from "../components/Spinner";

const PROGRESS_COLOR = {
  "behind-schedule": "#dc2626",
  "on-schedule": "#d97706",
  "ahead-of-schedule": "#16a34a",
};

export default function DailySiteReport({ backendStatus }) {
  const [activeTab, setActiveTab] = useState("create"); // "create" | "history"
  const [sites, setSites] = useState([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");

  const [fields, setFields] = useState({
    site_id: "",
    site_name: "",
    date: new Date().toISOString().split("T")[0],
    activities_completed: "",
    workers_present: "",
    equipment_used: "",
    issues: "",
    notes: "",
  });
  const [imageFile, setImageFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  // History & Broadcast
  const [historyReports, setHistoryReports] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historySiteFilter, setHistorySiteFilter] = useState("all");
  const [broadcastingId, setBroadcastingId] = useState(null);
  const [broadcastFeedback, setBroadcastFeedback] = useState("");

  function set(key, val) {
    setFields((f) => ({ ...f, [key]: val }));
  }

  // Load sites on mount
  useEffect(() => {
    async function init() {
      try {
        const res = await getSites();
        if (res?.sites?.length > 0) {
          setSites(res.sites);
          // Default to first site
          const first = res.sites[0];
          setSelectedSiteId(first.id);
          setFields((f) => ({
            ...f,
            site_id: first.id,
            site_name: first.name,
          }));
        }
      } catch (e) {
        console.warn("Could not fetch sites:", e);
      }
    }
    init();
  }, []);

  // Load reports history
  async function loadHistory(siteId = "") {
    setLoadingHistory(true);
    try {
      const res = await getSiteReports(siteId === "all" ? "" : siteId);
      setHistoryReports(res?.reports || []);
    } catch (err) {
      console.warn("Failed to load reports history:", err);
    } finally {
      setLoadingHistory(false);
    }
  }

  useEffect(() => {
    if (activeTab === "history") {
      loadHistory(historySiteFilter);
    }
  }, [activeTab, historySiteFilter]);

  function handleSiteSelect(siteId) {
    setSelectedSiteId(siteId);
    if (!siteId) {
      setFields((f) => ({ ...f, site_id: "", site_name: "" }));
      return;
    }
    const matched = sites.find((s) => s.id === siteId);
    if (matched) {
      setFields((f) => ({
        ...f,
        site_id: matched.id,
        site_name: matched.name,
      }));
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!fields.site_name || !fields.date) {
      setError("Site name and date are required.");
      return;
    }
    const workers = parseInt(fields.workers_present, 10);
    if (!fields.workers_present || isNaN(workers) || workers <= 0) {
      setError("⚠️ Missing required detail: Number of workers present on site is mandatory. Please enter a valid worker count (e.g. 15).");
      return;
    }
    if (!fields.activities_completed || fields.activities_completed.trim().length < 8) {
      setError("⚠️ Missing required detail: Key activities completed today is required. Please provide a description of the tasks executed.");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    setBroadcastFeedback("");
    try {
      const data = await generateReport(fields, imageFile);
      setResult(data);
    } catch (err) {
      setError(err.message || "Failed to generate report.");
    } finally {
      setLoading(false);
    }
  }

  async function handleBroadcast(report, repId = null) {
    const targetSiteId = report.site_id || selectedSiteId;
    if (!targetSiteId) {
      alert("Please select a monitored site to broadcast to its Telegram channel.");
      return;
    }
    setBroadcastingId(repId || "current");
    setBroadcastFeedback("");
    try {
      const res = await broadcastReportToTelegram(targetSiteId, report);
      if (res?.ok) {
        setBroadcastFeedback("✅ Official Daily Report broadcasted to Telegram group successfully!");
      } else {
        setBroadcastFeedback(res?.detail || "Could not broadcast report to Telegram.");
      }
    } catch (err) {
      setBroadcastFeedback("⚠️ " + (err.message || "Failed to broadcast report."));
    } finally {
      setBroadcastingId(null);
    }
  }

  return (
    <div className="feature-panel">
      {/* ── Tab Switcher & Banner ── */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
          marginBottom: "16px",
          paddingBottom: "14px",
          borderBottom: "1px solid #e5ebe6",
        }}
      >
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            className={`view-btn ${activeTab === "create" ? "active" : ""}`}
            onClick={() => setActiveTab("create")}
          >
            📝 New Daily Report
          </button>
          <button
            className={`view-btn ${activeTab === "history" ? "active" : ""}`}
            onClick={() => {
              setActiveTab("history");
              loadHistory(historySiteFilter);
            }}
          >
            📜 Reports Archive & Telegram Feed ({historyReports.length})
          </button>
        </div>

        <div style={{ fontSize: "12px", color: "#738078" }}>
          <span>👷 Telegram format: </span>
          <code style={{ background: "#eef2ef", padding: "2px 6px", borderRadius: "4px" }}>
            /report [labor, progress, notes]
          </code>
        </div>
      </div>

      {/* ── TAB 1: CREATE DAILY REPORT ── */}
      {activeTab === "create" && (
        <>
          <div
            style={{
              background: "#f0fdf4",
              border: "1px solid #bbf7d0",
              borderRadius: "8px",
              padding: "10px 14px",
              marginBottom: "16px",
              fontSize: "12px",
              color: "#166534",
              display: "flex",
              alignItems: "center",
              gap: "10px",
            }}
          >
            <span style={{ fontSize: "18px" }}>📋</span>
            <div>
              <b>Professional Multi-Site Reporting:</b> Foremen on site can submit reports directly from phone via Telegram using <code>/report</code>, or the Civil Engineer can compile and broadcast formal reports below.
            </div>
          </div>

          <form onSubmit={handleSubmit} className="feature-form">
            <div className="form-row">
              <div className="form-group">
                <label>Select Monitored Site</label>
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
                  <option value="">-- Custom / Other Site --</option>
                  {sites.map((s) => (
                    <option key={s.id} value={s.id}>
                      📍 {s.name} ({s.location || "Worksite"})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label>Site Name *</label>
                <input
                  type="text"
                  placeholder="e.g. Tower A - Andheri Project"
                  value={fields.site_name}
                  onChange={(e) => set("site_name", e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Date *</label>
                <input
                  type="date"
                  value={fields.date}
                  onChange={(e) => set("date", e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="form-group">
              <label>Activities Completed * (Mandatory)</label>
              <textarea
                placeholder="Describe work completed today (e.g. 4th floor slab concrete pour completed, curing on 3rd floor)..."
                value={fields.activities_completed}
                onChange={(e) => set("activities_completed", e.target.value)}
                rows={3}
                required
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>Workers Present * (Mandatory Headcount)</label>
                <input
                  type="number"
                  placeholder="e.g. 24"
                  value={fields.workers_present}
                  onChange={(e) => set("workers_present", e.target.value)}
                  min={1}
                  required
                />
              </div>
              <div className="form-group">
                <label>Equipment Used</label>
                <input
                  type="text"
                  placeholder="e.g. Tower Crane, Transit Concrete Mixer, Scaffolding"
                  value={fields.equipment_used}
                  onChange={(e) => set("equipment_used", e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label>Issues / Safety Concerns</label>
              <textarea
                placeholder="Any problems, delays, material shortages, or safety flags..."
                value={fields.issues}
                onChange={(e) => set("issues", e.target.value)}
                rows={2}
              />
            </div>

            <div className="form-group">
              <label>Additional Notes / Tomorrow's Plan</label>
              <textarea
                placeholder="Next steps, inspections scheduled, or weather notes..."
                value={fields.notes}
                onChange={(e) => set("notes", e.target.value)}
                rows={2}
              />
            </div>

            <div className="form-group">
              <label>Site Inspection Photo (optional)</label>
              <ImageUploader
                file={imageFile}
                onChange={(f) => {
                  setImageFile(f);
                  setResult(null);
                }}
              />
            </div>

            <button
              className="btn-primary"
              type="submit"
              disabled={loading || backendStatus !== "healthy"}
            >
              {loading ? (
                <>
                  <Spinner /> Generating Formal Report...
                </>
              ) : (
                "Generate Daily Site Report"
              )}
            </button>
            {backendStatus !== "healthy" && (
              <p className="hint-text">Backend unavailable.</p>
            )}
          </form>

          {error && <div className="alert alert-error">{error}</div>}

          {result && (
            <div className="results" style={{ marginTop: "24px" }}>
              <div
                className="risk-banner"
                style={{
                  background: "#f0fdf4",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: "10px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <span
                    className="risk-badge"
                    style={{
                      background: PROGRESS_COLOR[result.overall_progress] || "#64748b",
                    }}
                  >
                    {result.overall_progress?.replace(/-/g, " ").toUpperCase()}
                  </span>
                  <span className="ppe-badge">📋 {result.site_name}</span>
                  <span className="ppe-badge">📅 {result.date}</span>
                  <span className="ppe-badge">👤 {result.submitted_by || "Civil Engineer"}</span>
                </div>

                <button
                  type="button"
                  className="btn-engineer-action primary"
                  disabled={broadcastingId === "current"}
                  onClick={() => handleBroadcast(result, "current")}
                  style={{ padding: "6px 14px", fontSize: "12px" }}
                >
                  {broadcastingId === "current" ? <Spinner /> : "📤 Broadcast to Site Telegram"}
                </button>
              </div>

              {broadcastFeedback && (
                <div
                  style={{
                    padding: "8px 14px",
                    background: broadcastFeedback.startsWith("✅") ? "#f0fdf4" : "#fef2f2",
                    border: `1px solid ${broadcastFeedback.startsWith("✅") ? "#bbf7d0" : "#fecdd3"}`,
                    borderRadius: "6px",
                    fontSize: "12px",
                    color: broadcastFeedback.startsWith("✅") ? "#166534" : "#b91c1c",
                    fontWeight: "600",
                  }}
                >
                  {broadcastFeedback}
                </div>
              )}

              <p className="result-summary">{result.summary}</p>

              {result.sections?.map((s, i) => (
                <ResultCard key={i} title={s.section}>
                  <p style={{ margin: 0, whiteSpace: "pre-line" }}>{s.content}</p>
                </ResultCard>
              ))}

              {result.issues_flagged?.length > 0 && (
                <ResultCard title="🚩 Issues Flagged">
                  <ul className="action-list">
                    {result.issues_flagged.map((issue, i) => (
                      <li key={i}>{issue}</li>
                    ))}
                  </ul>
                </ResultCard>
              )}

              <ResultCard title="📌 Next Day Plan">
                <p style={{ margin: 0 }}>{result.next_day_plan}</p>
              </ResultCard>
            </div>
          )}
        </>
      )}

      {/* ── TAB 2: REPORTS ARCHIVE & TELEGRAM FEED ── */}
      {activeTab === "history" && (
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "10px",
              marginBottom: "16px",
              padding: "12px 16px",
              background: "#f9fbf9",
              borderRadius: "8px",
              border: "1px solid #e5ebe6",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <label style={{ fontSize: "12px", fontWeight: "700", color: "#3d5244" }}>
                Filter by Site:
              </label>
              <select
                value={historySiteFilter}
                onChange={(e) => setHistorySiteFilter(e.target.value)}
                style={{
                  padding: "6px 12px",
                  borderRadius: "6px",
                  border: "1px solid #d1dbd3",
                  fontSize: "12px",
                }}
              >
                <option value="all">🌐 All Monitored Sites</option>
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-card-action"
              onClick={() => loadHistory(historySiteFilter)}
              disabled={loadingHistory}
            >
              🔄 Refresh Reports
            </button>
          </div>

          {broadcastFeedback && (
            <div
              style={{
                padding: "8px 14px",
                marginBottom: "12px",
                background: broadcastFeedback.startsWith("✅") ? "#f0fdf4" : "#fef2f2",
                border: `1px solid ${broadcastFeedback.startsWith("✅") ? "#bbf7d0" : "#fecdd3"}`,
                borderRadius: "6px",
                fontSize: "12px",
                color: broadcastFeedback.startsWith("✅") ? "#166534" : "#b91c1c",
                fontWeight: "600",
              }}
            >
              {broadcastFeedback}
            </div>
          )}

          {loadingHistory ? (
            <div style={{ textAlign: "center", padding: "40px" }}>
              <Spinner /> Loading site reports history...
            </div>
          ) : historyReports.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: "40px",
                background: "#ffffff",
                borderRadius: "10px",
                border: "1px solid #e5ebe6",
              }}
            >
              <div style={{ fontSize: "28px", marginBottom: "8px" }}>📋</div>
              <p style={{ margin: "0 0 6px", fontWeight: "600", color: "#3d5244" }}>
                No daily reports recorded yet.
              </p>
              <p style={{ margin: 0, fontSize: "12px", color: "#738078" }}>
                Reports submitted via Telegram (<code>/report</code>) or generated on the web will appear here.
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {historyReports.map((rep, idx) => {
                const repKey = rep.id || idx;
                const status = rep.overall_progress || "on-schedule";
                const isTg = (rep.submitted_by || "").includes("Telegram");

                return (
                  <div
                    key={repKey}
                    style={{
                      background: "#ffffff",
                      border: "1px solid #e5ebe6",
                      borderRadius: "10px",
                      padding: "16px",
                      boxShadow: "0 1px 3px rgba(0,0,0,0.03)",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        flexWrap: "wrap",
                        gap: "8px",
                        marginBottom: "10px",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                        <span
                          className="risk-badge"
                          style={{
                            background: PROGRESS_COLOR[status] || "#64748b",
                            fontSize: "11px",
                            padding: "3px 8px",
                          }}
                        >
                          {status.replace(/-/g, " ").toUpperCase()}
                        </span>
                        <strong style={{ fontSize: "14px", color: "#17251f" }}>
                          {rep.site_name}
                        </strong>
                        <span style={{ fontSize: "12px", color: "#738078" }}>
                          📅 {rep.date}
                        </span>
                        <span
                          style={{
                            background: isTg ? "#e0f2fe" : "#f1f5f9",
                            color: isTg ? "#0369a1" : "#475569",
                            padding: "2px 8px",
                            borderRadius: "12px",
                            fontSize: "11px",
                            fontWeight: "600",
                          }}
                        >
                          {rep.submitted_by || "Civil Engineer"}
                        </span>
                      </div>

                      <button
                        className="btn-card-action"
                        disabled={broadcastingId === repKey}
                        onClick={() => handleBroadcast(rep, repKey)}
                        style={{ fontSize: "11px", padding: "4px 10px" }}
                      >
                        {broadcastingId === repKey ? <Spinner /> : "📤 Broadcast to Telegram"}
                      </button>
                    </div>

                    <p style={{ margin: "0 0 10px", fontSize: "13px", color: "#3d5244", lineHeight: "1.45" }}>
                      {rep.summary}
                    </p>

                    {rep.sections?.length > 0 && (
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                          gap: "8px",
                          background: "#f9fbf9",
                          padding: "10px",
                          borderRadius: "8px",
                          marginBottom: "10px",
                        }}
                      >
                        {rep.sections.map((sec, sIdx) => (
                          <div key={sIdx} style={{ fontSize: "12px" }}>
                            <b style={{ color: "#17251f" }}>▫️ {sec.section}: </b>
                            <span style={{ color: "#526359" }}>{sec.content}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {rep.next_day_plan && (
                      <div style={{ fontSize: "12px", color: "#15803d" }}>
                        <b>🎯 Tomorrow's Plan: </b>
                        <span>{rep.next_day_plan}</span>
                      </div>
                    )}
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
