import { useEffect, useRef, useState } from "react";
import {
  analyzeSafety,
  configureTelegram,
  createSite,
  deleteSite,
  getSiteCracks,
  getSiteImageUrl,
  getSitePhotos,
  getSites,
  getTelegramStatus,
  pollTelegramNow,
  setupSites,
  updateSite,
  uploadSiteCrack,
  uploadSitePhoto,
  validateImageFile,
} from "../services/api";
import ImageUploader from "../components/ImageUploader";
import Spinner from "../components/Spinner";

function getCompactPoints(text, maxLines = 2) {
  if (!text) return "";
  const lines = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length <= maxLines) return lines.join("\n");
  return lines.slice(0, maxLines).join("\n");
}

export default function SafetyMonitoring({ backendStatus: _backendStatus }) {
  // ── Multi-Site States ───────────────────────────────────────────────────────
  const [sites, setSites] = useState([]);
  const [loadingSites, setLoadingSites] = useState(true);
  const [activeView, setActiveView] = useState("screens"); // "screens" | "album" | "direct"
  const [filterRisk, setFilterRisk] = useState("all");
  const [telegramStatus, setTelegramStatus] = useState(null);

  // ── Access Control (Lead Civil Engineer Authorization) ─────────────────────
  const [isAdmin, setIsAdmin] = useState(
    () => sessionStorage.getItem("cq_lead_engineer") === "true"
  );
  const [isAdminModalOpen, setIsAdminModalOpen] = useState(false);
  const [adminPinInput, setAdminPinInput] = useState("");
  const [adminPinError, setAdminPinError] = useState("");
  const [pendingAction, setPendingAction] = useState(null);

  function requireAdmin(actionFn) {
    if (isAdmin) {
      actionFn();
    } else {
      setPendingAction(() => actionFn);
      setAdminPinInput("");
      setAdminPinError("");
      setIsAdminModalOpen(true);
    }
  }

  function handleUnlockAdmin(e) {
    e.preventDefault();
    const pin = adminPinInput.trim();
    if (!pin) {
      setAdminPinError("Please enter your Lead Engineer PIN.");
      return;
    }
    if (pin !== "1234") {
      setAdminPinError("Incorrect Lead Engineer PIN. Default is 1234.");
      return;
    }
    sessionStorage.setItem("cq_lead_engineer", "true");
    sessionStorage.setItem("cq_admin_pin", pin);
    setIsAdmin(true);
    setIsAdminModalOpen(false);
    setAdminPinError("");
    if (pendingAction) {
      const fn = pendingAction;
      setPendingAction(null);
      fn();
    }
  }

  function handleLockAdmin() {
    sessionStorage.removeItem("cq_lead_engineer");
    sessionStorage.removeItem("cq_admin_pin");
    setIsAdmin(false);
  }

  // ── Modals ──────────────────────────────────────────────────────────────────
  // 1. Setup site count modal
  const [isSetupOpen, setIsSetupOpen] = useState(false);
  const [targetSiteCount, setTargetSiteCount] = useState(5);

  // 2. Edit Site details modal
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [editingSite, setEditingSite] = useState(null);
  const [editForm, setEditForm] = useState({
    name: "",
    location: "",
    supervisor: "",
  });
  const [savingEdit, setSavingEdit] = useState(false);

  // 3. Add Site modal
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [addForm, setAddForm] = useState({
    name: "",
    location: "",
    supervisor: "",
  });
  const [addingSite, setAddingSite] = useState(false);

  // 4. Telegram setup & instructions modal
  const [isTelegramOpen, setIsTelegramOpen] = useState(false);
  const [telegramTokenInput, setTelegramTokenInput] = useState("");
  const [telegramMsg, setTelegramMsg] = useState("");

  // 5. Direct Site upload modal
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [uploadSiteId, setUploadSiteId] = useState("");
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadCaption, setUploadCaption] = useState("");
  const [uploading, setUploading] = useState(false);

  // 7. Album view
  const [activeSiteId, setActiveSiteId] = useState("");
  const [albumTab, setAlbumTab] = useState("safety"); // "safety" | "cracks"
  const [sitePhotos, setSitePhotos] = useState([]);
  const [siteCracks, setSiteCracks] = useState([]);
  const [loadingPhotos, setLoadingPhotos] = useState(false);
  const [loadingCracks, setLoadingCracks] = useState(false);
  const [isCrackUploadOpen, setIsCrackUploadOpen] = useState(false);
  const [crackUploadFile, setCrackUploadFile] = useState(null);
  const [crackUploadCaption, setCrackUploadCaption] = useState("");
  const [crackUploading, setCrackUploading] = useState(false);

  // 8. Standalone direct single photo scan
  const [standaloneFile, setStandaloneFile] = useState(null);
  const [standaloneContext, setStandaloneContext] = useState("");
  const [standaloneLoading, setStandaloneLoading] = useState(false);
  const [standaloneResult, setStandaloneResult] = useState(null);
  const [standaloneError, setStandaloneError] = useState("");

  const pollIntervalRef = useRef(null);

  // ── Load Sites & Live Polling ───────────────────────────────────────────────
  async function loadData() {
    try {
      const [sitesData, tgData] = await Promise.all([
        getSites(),
        getTelegramStatus().catch(() => null),
      ]);
      if (sitesData?.sites) {
        setSites(sitesData.sites);
        if (!activeSiteId && sitesData.sites.length > 0) {
          setActiveSiteId(sitesData.sites[0].id);
        }
      }
      if (tgData) setTelegramStatus(tgData);
    } catch (err) {
      console.warn("Failed to load sites:", err);
    } finally {
      setLoadingSites(false);
    }
  }

  useEffect(() => {
    loadData();

    // Auto-poll every 3.5 seconds so photos sent from phone via Telegram appear live!
    pollIntervalRef.current = setInterval(async () => {
      try {
        const sitesData = await getSites();
        if (sitesData?.sites) {
          setSites(sitesData.sites);
        }
      } catch {
        // silent background poll
      }
    }, 3500);

    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, []);

  // ── Site Count Setup ────────────────────────────────────────────────────────
  async function handleApplySiteCount(e) {
    e.preventDefault();
    setLoadingSites(true);
    try {
      const res = await setupSites(parseInt(targetSiteCount, 10));
      if (res?.sites) {
        setSites(res.sites);
        setIsSetupOpen(false);
      }
    } catch (err) {
      alert("Failed to setup sites: " + err.message);
    } finally {
      setLoadingSites(false);
    }
  }

  // ── Edit Site ───────────────────────────────────────────────────────────────
  function openEditModal(site) {
    setEditingSite(site);
    setEditForm({
      name: site.name || "",
      location: site.location || "",
      supervisor: site.supervisor || "",
    });
    setIsEditOpen(true);
  }

  async function handleSaveEdit(e) {
    e.preventDefault();
    if (!editingSite) return;
    setSavingEdit(true);
    try {
      await updateSite(editingSite.id, editForm);
      setIsEditOpen(false);
      await loadData();
    } catch (err) {
      alert("Failed to update site: " + err.message);
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteSite(siteId) {
    if (!confirm("Are you sure you want to remove this site?")) return;
    try {
      await deleteSite(siteId);
      setIsEditOpen(false);
      await loadData();
    } catch (err) {
      alert("Failed to delete site: " + err.message);
    }
  }

  // ── Add New Site ────────────────────────────────────────────────────────────
  async function handleCreateSite(e) {
    e.preventDefault();
    if (!addForm.name.trim()) {
      alert("Please enter a site name.");
      return;
    }
    setAddingSite(true);
    try {
      await createSite(addForm);
      setIsAddOpen(false);
      setAddForm({ name: "", location: "", supervisor: "", telegram_chat_id: "" });
      await loadData();
    } catch (err) {
      alert("Failed to create site: " + err.message);
    } finally {
      setAddingSite(false);
    }
  }

  // ── Album View ──────────────────────────────────────────────────────────────
  async function handleOpenAlbum(siteId, initialTab = "safety") {
    setActiveSiteId(siteId);
    setActiveView("album");
    setAlbumTab(initialTab);
    setLoadingPhotos(true);
    setLoadingCracks(true);
    try {
      const [photosRes, cracksRes] = await Promise.all([
        getSitePhotos(siteId).catch(() => ({ photos: [] })),
        getSiteCracks(siteId).catch(() => ({ cracks: [] })),
      ]);
      setSitePhotos(photosRes?.photos || []);
      setSiteCracks(cracksRes?.cracks || []);
    } catch (err) {
      alert("Failed to load album: " + err.message);
    } finally {
      setLoadingPhotos(false);
      setLoadingCracks(false);
    }
  }

  async function handleDirectCrackUpload(e) {
    e.preventDefault();
    if (!crackUploadFile || !activeSiteId) return;
    setCrackUploading(true);
    try {
      await uploadSiteCrack(
        activeSiteId,
        crackUploadFile,
        crackUploadCaption,
        "Civil Engineer",
        ""
      );
      setIsCrackUploadOpen(false);
      setCrackUploadFile(null);
      setCrackUploadCaption("");
      await handleOpenAlbum(activeSiteId, "cracks");
      await loadData();
    } catch (err) {
      alert("Failed to upload crack photo: " + err.message);
    } finally {
      setCrackUploading(false);
    }
  }

  // ── Telegram Configuration ──────────────────────────────────────────────────
  async function handleSaveTelegramToken(e) {
    e.preventDefault();
    setTelegramMsg("Connecting to Telegram...");
    try {
      const res = await configureTelegram(telegramTokenInput);
      setTelegramStatus(res);
      setTelegramMsg(
        res.is_active
          ? `✅ Connected to Telegram Bot: @${res.bot_username}!`
          : "Token updated."
      );
    } catch (err) {
      setTelegramMsg(`❌ ${err.message}`);
    }
  }

  // ── Direct Site Photo Upload ────────────────────────────────────────────────
  async function handleDirectUpload(e) {
    e.preventDefault();
    const valErr = validateImageFile(uploadFile);
    if (valErr) {
      alert(valErr);
      return;
    }
    setUploading(true);
    try {
      await uploadSitePhoto(
        uploadSiteId,
        uploadFile,
        uploadCaption,
        "Civil Engineer (Direct Upload)"
      );
      setIsUploadOpen(false);
      setUploadFile(null);
      setUploadCaption("");
      await loadData();
    } catch (err) {
      alert("Upload failed: " + err.message);
    } finally {
      setUploading(false);
    }
  }

  // ── Standalone Photo Scan ───────────────────────────────────────────────────
  async function handleStandaloneSubmit(e) {
    e.preventDefault();
    const valErr = validateImageFile(standaloneFile);
    if (valErr) {
      setStandaloneError(valErr);
      return;
    }
    setStandaloneLoading(true);
    setStandaloneError("");
    setStandaloneResult(null);
    try {
      const res = await analyzeSafety(standaloneFile, standaloneContext);
      setStandaloneResult(res);
    } catch (err) {
      setStandaloneError(err.message || "Analysis failed.");
    } finally {
      setStandaloneLoading(false);
    }
  }

  // ── Filtered Sites ──────────────────────────────────────────────────────────
  const filteredSites = sites.filter((s) => {
    if (filterRisk === "alert")
      return s.overall_risk === "critical" || s.overall_risk === "high";
    if (filterRisk === "safe")
      return s.overall_risk === "low" || s.overall_risk === "safe";
    return true;
  });

  const alertSitesCount = sites.filter(
    (s) => s.overall_risk === "critical" || s.overall_risk === "high"
  ).length;

  const activeSite = sites.find((s) => s.id === activeSiteId) || sites[0] || null;

  return (
    <div className="multi-site-hub">
      {/* ── Feature Header ── */}
      <div className="hub-banner">
        <div className="hub-banner-content">
          <div className="hub-title-area">
            <h2>
              <span>🦺</span> Multi-Site Safety & Telegram Monitoring
            </h2>
            <p>
              Manage all your construction sites in one place. Field workers can
              send photos to their site's dedicated <b>Telegram group</b> right from
              their phone — images are received in real time and safety risks are
              explained in plain, simple English.
            </p>
          </div>

          <div className="hub-banner-actions">
            {/* Lead Civil Engineer Authorization Mode Status */}
            <button
              className={`btn-lead-mode ${isAdmin ? "unlocked" : "locked"}`}
              onClick={isAdmin ? handleLockAdmin : () => requireAdmin(() => {})}
              title={
                isAdmin
                  ? "Lead Civil Engineer Mode is Active (Modifications Allowed). Click to Lock."
                  : "Field Worker Mode (Protected). Click to Enter Lead Engineer PIN."
              }
            >
              {isAdmin ? "🟢 Lead Engineer Mode [🔒 Lock]" : "🔒 Field Worker Mode [Unlock Admin]"}
            </button>

            <button
              className="btn-engineer-action"
              onClick={() => {
                requireAdmin(() => {
                  setTargetSiteCount(sites.length || 5);
                  setIsSetupOpen(true);
                });
              }}
              title="Set how many sites are under you (Requires Lead Engineer Access)"
            >
              <span>⚙️</span> Sites: {sites.length} Active
            </button>

            <button
              className="btn-engineer-action"
              onClick={() => requireAdmin(() => setIsAddOpen(true))}
              title="Add a new site (Requires Lead Engineer Access)"
            >
              <span>➕</span> Add Site
            </button>

            <button
              className="btn-engineer-action"
              onClick={() => setIsTelegramOpen(true)}
              title="Connect Telegram Bot and view phone instructions"
            >
              <span>📱</span> Telegram Setup
              {telegramStatus?.is_active && (
                <span style={{ color: "#16a34a", fontSize: "11px", fontWeight: "700" }}>
                  ● Connected
                </span>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* ── Toolbar: View Switcher & Risk Filters ── */}
      <div className="hub-toolbar">
        <div className="view-selector">
          <button
            className={`view-btn ${activeView === "screens" ? "active" : ""}`}
            onClick={() => setActiveView("screens")}
          >
            🖥️ Site Screens ({sites.length})
          </button>
          <button
            className={`view-btn ${activeView === "album" ? "active" : ""}`}
            onClick={() => handleOpenAlbum(sites[0]?.id || "site-1")}
          >
            📸 Site Photo Album
          </button>
          <button
            className={`view-btn ${activeView === "direct" ? "active" : ""}`}
            onClick={() => setActiveView("direct")}
          >
            ⚡ Quick Single Photo Check
          </button>
        </div>

        {activeView === "screens" && (
          <div className="filter-group">
            <span style={{ fontSize: "11px", fontWeight: "600", color: "#738078" }}>
              Filter:
            </span>
            <button
              className={`filter-pill ${filterRisk === "all" ? "active" : ""}`}
              onClick={() => setFilterRisk("all")}
            >
              All Sites ({sites.length})
            </button>
            <button
              className={`filter-pill alert ${filterRisk === "alert" ? "active" : ""}`}
              onClick={() => setFilterRisk("alert")}
            >
              ⚠️ Hazards ({alertSitesCount})
            </button>
            <button
              className={`filter-pill ${filterRisk === "safe" ? "active" : ""}`}
              onClick={() => setFilterRisk("safe")}
            >
              ✅ Safe ({sites.length - alertSitesCount})
            </button>
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════
         VIEW 1: SITE SCREENS (Plain Language + Editable Cards)
         ══════════════════════════════════════════════════════════════════════ */}
      {activeView === "screens" && (
        <div className="screens-grid">
          {filteredSites.map((site) => {
            const risk = site.overall_risk || "none";
            const isAlert = risk === "critical" || risk === "high";
            const latest = site.latest_photo;
            const photoUrl = latest
              ? getSiteImageUrl(site.id, latest.filename)
              : "";
            const analysis = latest?.safety_analysis || {};
            const whatRisk =
              analysis.what_is_the_risk ||
              (analysis.hazards?.[0]
                ? `${analysis.hazards[0].type}: ${analysis.hazards[0].description}`
                : "No hazards spotted.");
            const howToImprove =
              analysis.how_to_improve ||
              (analysis.hazards?.[0]
                ? analysis.hazards[0].recommendation
                : "Keep maintaining standard safety precautions.");

            return (
              <div
                key={site.id}
                className={`screen-card ${risk}`}
              >
                {/* Card Header with Site Name and Edit Button */}
                <div className="screen-card-header">
                  <div className="screen-title-area">
                    <h3 className="screen-title" title={site.name}>
                      {site.name}
                    </h3>
                    <p className="screen-location" title={site.location}>
                      📍 {site.location || "Location not set"} • 👤{" "}
                      {site.supervisor || "In-charge not set"}
                    </p>
                  </div>
                  <button
                    className="btn-edit-site"
                    onClick={() => requireAdmin(() => openEditModal(site))}
                    title="Edit site details (Requires Lead Civil Engineer PIN)"
                  >
                    ✏️ Edit
                  </button>
                </div>

                {/* Photo Viewport */}
                <div
                  className="screen-feed-viewport"
                  onClick={() => handleOpenAlbum(site.id, "safety")}
                  title="Click to view photo album"
                >
                  {photoUrl ? (
                    <>
                      <img
                        src={photoUrl}
                        alt={site.name}
                        className="screen-feed-img"
                        onError={(e) => {
                          e.target.style.display = "none";
                        }}
                      />
                      <div className="feed-watermark">
                        <span className="live-dot"></span> LIVE
                      </div>
                      <div className="feed-sender-pill">
                        <span>{latest.sender || "Site Photo"}</span>
                        <span>
                          {new Date(latest.timestamp).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </span>
                      </div>
                    </>
                  ) : (
                    <div className="empty-feed-placeholder">
                      <div className="empty-feed-icon">📷</div>
                      <div className="empty-feed-text">No photos yet</div>
                      <div className="empty-feed-hint">
                        Send a photo to this site's Telegram group or upload below.
                      </div>
                    </div>
                  )}
                </div>

                {/* Card Body: Simple, Plain-Language Safety Assessment */}
                <div className="screen-card-body">
                  <div className="risk-level-row">
                    <span className={`status-badge ${risk}`}>
                      {risk === "none"
                        ? "Awaiting Photo"
                        : risk === "low"
                          ? "🟢 Low Risk"
                          : risk === "medium"
                            ? "🟡 Medium Risk"
                            : risk === "high"
                              ? "🟠 High Risk"
                              : "🚨 Critical Risk"}
                    </span>
                    <span style={{ fontSize: "11px", color: "#738078" }}>
                      PPE: <b>{(site.ppe_compliance || "pending").toUpperCase()}</b>
                    </span>
                  </div>

                  {latest ? (
                    <div className={`plain-risk-box ${isAlert ? "alert" : ""}`}>
                      <div className="plain-risk-row">
                        <span className="plain-risk-label">⚠️ Risk:</span>
                        <span className="plain-risk-desc">
                          {getCompactPoints(whatRisk, 2)}
                        </span>
                      </div>
                      <div className="plain-risk-row">
                        <span className="plain-risk-label">✅ Fix:</span>
                        <span className="plain-improve-desc">
                          {getCompactPoints(howToImprove, 2)}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div
                      style={{
                        background: "#f9fbf9",
                        padding: "10px",
                        borderRadius: "8px",
                        fontSize: "11px",
                        color: "#738078",
                        textAlign: "center",
                      }}
                    >
                      Awaiting first photo upload to run safety inspection.
                    </div>
                  )}
                </div>

                {/* Card Footer Actions: Photos, Cracks, and Upload */}
                <div className="screen-card-footer">
                  <button
                    className="btn-card-action primary"
                    onClick={() => handleOpenAlbum(site.id, "safety")}
                    title="Open safety inspection photo album"
                  >
                    🦺 Photos ({site.photo_count || 0})
                  </button>
                  <button
                    className="btn-card-action"
                    onClick={() => handleOpenAlbum(site.id, "cracks")}
                    title="Open structural crack diagnostics for this site"
                  >
                    🔍 Cracks ({site.crack_count || 0})
                  </button>
                  <button
                    className="btn-card-action"
                    onClick={() => {
                      setUploadSiteId(site.id);
                      setIsUploadOpen(true);
                    }}
                    title="Upload a photo from your computer"
                  >
                    📤 Upload
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         VIEW 2: DEDICATED SITE PHOTO ALBUM
         ══════════════════════════════════════════════════════════════════════ */}
      {activeView === "album" && (
        <div className="feature-panel">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "12px",
              padding: "14px 18px",
              background: "#f9fbf9",
              borderRadius: "10px",
              border: "1px solid #e5ebe6",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <label style={{ fontSize: "12px", fontWeight: "700", color: "#3d5244" }}>
                Select Site:
              </label>
              <select
                value={activeSiteId}
                onChange={(e) => handleOpenAlbum(e.target.value)}
                style={{
                  padding: "7px 12px",
                  fontSize: "13px",
                  borderRadius: "8px",
                  border: "1px solid #d1dbd3",
                }}
              >
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ display: "flex", gap: "10px" }}>
              {albumTab === "safety" ? (
                <button
                  className="btn-engineer-action primary"
                  onClick={() => {
                    setUploadSiteId(activeSiteId);
                    setIsUploadOpen(true);
                  }}
                >
                  📤 Upload Safety Photo
                </button>
              ) : (
                <button
                  className="btn-engineer-action primary"
                  onClick={() => {
                    setIsCrackUploadOpen(true);
                  }}
                >
                  🔍 Upload Crack Photo
                </button>
              )}
            </div>
          </div>

          {/* Sub-tabs for Album: Safety Inspections vs Structural Cracks */}
          <div style={{ display: "flex", gap: "8px", margin: "14px 0" }}>
            <button
              className={`view-btn ${albumTab === "safety" ? "active" : ""}`}
              onClick={() => setAlbumTab("safety")}
            >
              🦺 Safety Photos ({sitePhotos.length})
            </button>
            <button
              className={`view-btn ${albumTab === "cracks" ? "active" : ""}`}
              onClick={() => setAlbumTab("cracks")}
            >
              🔍 Structural Cracks ({siteCracks.length})
            </button>
          </div>

          {albumTab === "safety" ? (
            loadingPhotos ? (
              <div style={{ textAlign: "center", padding: "40px" }}>
                <Spinner /> Loading site photo album...
              </div>
            ) : sitePhotos.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: "40px",
                  background: "#ffffff",
                  borderRadius: "10px",
                  border: "1px solid #e5ebe6",
                }}
              >
                <p style={{ margin: "0 0 12px", color: "#738078" }}>
                  No safety photos uploaded for this site yet.
                </p>
                <button
                  className="btn-primary"
                  onClick={() => {
                    setUploadSiteId(activeSiteId);
                    setIsUploadOpen(true);
                  }}
                >
                  Upload First Photo
                </button>
              </div>
            ) : (
              <div className="album-feed">
                {sitePhotos
                  .slice()
                  .reverse()
                  .map((photo, idx) => {
                    const analysis = photo.safety_analysis || {};
                    const risk = analysis.overall_risk_level || "low";
                    const whatRisk =
                      analysis.what_is_the_risk ||
                      analysis.hazards?.[0]?.description ||
                      analysis.summary;
                    const howToImprove =
                      analysis.how_to_improve ||
                      analysis.hazards?.[0]?.recommendation ||
                      analysis.immediate_actions?.[0];

                    return (
                      <div key={photo.id || idx} className="album-item">
                        <img
                          src={getSiteImageUrl(photo.site_id, photo.filename)}
                          alt={photo.caption}
                          className="album-item-thumb"
                        />
                        <div className="album-item-content">
                          <div className="album-item-header">
                            <div>
                              <span className="album-sender">{photo.sender}</span>
                              <span className="album-time">
                                {" "}
                                • {new Date(photo.timestamp).toLocaleString()}
                              </span>
                            </div>
                            <span className={`status-badge ${risk}`}>
                              {risk === "critical"
                                ? "🚨 CRITICAL RISK"
                                : risk === "high"
                                  ? "⚠️ HIGH RISK"
                                  : risk === "medium"
                                    ? "🟡 MEDIUM RISK"
                                    : "🟢 LOW RISK"}
                            </span>
                          </div>

                          <p className="album-caption">"{photo.caption}"</p>

                          {/* Full detailed unabridged points in Album View */}
                          <div className="plain-risk-box">
                            <div className="plain-risk-row">
                              <span className="plain-risk-label">⚠️ What is the risk:</span>
                              <span className="plain-risk-desc">{whatRisk}</span>
                            </div>
                            <div className="plain-risk-row">
                              <span className="plain-risk-label">✅ How to improve:</span>
                              <span className="plain-improve-desc">{howToImprove}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
              </div>
            )
          ) : (
            /* Crack Inspections Tab */
            loadingCracks ? (
              <div style={{ textAlign: "center", padding: "40px" }}>
                <Spinner /> Loading structural crack inspection records...
              </div>
            ) : siteCracks.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: "40px",
                  background: "#ffffff",
                  borderRadius: "10px",
                  border: "1px solid #e5ebe6",
                }}
              >
                <div style={{ fontSize: "28px", marginBottom: "8px" }}>🔍</div>
                <p style={{ margin: "0 0 12px", color: "#738078" }}>
                  No structural cracks logged for this site yet.
                </p>
                <button
                  className="btn-primary"
                  onClick={() => setIsCrackUploadOpen(true)}
                >
                  Inspect & Log New Crack
                </button>
              </div>
            ) : (
              <div className="album-feed">
                {siteCracks.map((crack, idx) => {
                  const diag = crack.crack_analysis || {};
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
                    <div key={crack.id || idx} className="album-item">
                      <img
                        src={getSiteImageUrl(crack.site_id, crack.filename)}
                        alt={crack.caption}
                        className="album-item-thumb"
                      />
                      <div className="album-item-content">
                        <div className="album-item-header">
                          <div>
                            <span className="album-sender">{crack.sender}</span>
                            <span className="album-time">
                              {" "}
                              • {new Date(crack.timestamp).toLocaleString()}
                            </span>
                          </div>
                          <span className={`status-badge ${sevClass}`}>
                            {detected ? `${sev.toUpperCase()} CRACK` : "NO CRACKS"}
                          </span>
                        </div>

                        <p className="album-caption">"{crack.caption}"</p>

                        <div className="plain-risk-box">
                          <div className="plain-risk-row">
                            <span className="plain-risk-label">🏗️ Structural Risk:</span>
                            <span className="plain-risk-desc">
                              {(diag.structural_risk || "none").toUpperCase()}
                            </span>
                          </div>
                          <div className="plain-risk-row">
                            <span className="plain-risk-label">📝 Diagnosis:</span>
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
            )
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         VIEW 3: QUICK SINGLE PHOTO CHECK (Original Direct Mode)
         ══════════════════════════════════════════════════════════════════════ */}
      {activeView === "direct" && (
        <div className="feature-panel">
          <form onSubmit={handleStandaloneSubmit} className="feature-form">
            <p style={{ margin: "0 0 10px", fontSize: "13px", color: "#738078" }}>
              Upload any single site photograph for an immediate safety and PPE check.
            </p>
            <ImageUploader
              file={standaloneFile}
              onChange={(f) => {
                setStandaloneFile(f);
                setStandaloneResult(null);
                setStandaloneError("");
              }}
            />

            <div className="form-group">
              <label>Additional context (optional)</label>
              <textarea
                placeholder="e.g. Scaffolding inspection on 4th floor, 10 workers"
                value={standaloneContext}
                onChange={(e) => setStandaloneContext(e.target.value)}
                rows={2}
              />
            </div>

            <button
              className="btn-primary"
              type="submit"
              disabled={standaloneLoading || !standaloneFile}
            >
              {standaloneLoading ? <><Spinner /> Analyzing...</> : "Analyze for Hazards"}
            </button>
          </form>

          {standaloneError && (
            <div className="alert alert-error">{standaloneError}</div>
          )}

          {standaloneResult && (
            <div className="results">
              <div
                className="risk-banner"
                style={{
                  background:
                    standaloneResult.overall_risk_level === "low"
                      ? "#f0fdf4"
                      : standaloneResult.overall_risk_level === "medium"
                        ? "#fffbeb"
                        : "#fff1f2",
                }}
              >
                <span
                  className="risk-badge"
                  style={{
                    background:
                      standaloneResult.overall_risk_level === "low"
                        ? "#16a34a"
                        : standaloneResult.overall_risk_level === "medium"
                          ? "#d97706"
                          : "#dc2626",
                  }}
                >
                  {standaloneResult.overall_risk_level?.toUpperCase()} RISK
                </span>
                <span className="ppe-badge">
                  PPE: {standaloneResult.ppe_compliance}
                </span>
              </div>

              <div className="plain-risk-box" style={{ marginTop: "14px", padding: "14px" }}>
                <div className="plain-risk-row">
                  <span className="plain-risk-label" style={{ fontSize: "13px" }}>
                    ⚠️ What is the risk:
                  </span>
                  <span className="plain-risk-desc" style={{ fontSize: "13px" }}>
                    {standaloneResult.what_is_the_risk || standaloneResult.summary}
                  </span>
                </div>
                <div className="plain-risk-row" style={{ marginTop: "8px" }}>
                  <span className="plain-risk-label" style={{ fontSize: "13px" }}>
                    ✅ How to fix / improve:
                  </span>
                  <span className="plain-improve-desc" style={{ fontSize: "13px" }}>
                    {standaloneResult.how_to_improve ||
                      standaloneResult.immediate_actions?.[0]}
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 1: EDIT SITE DETAILS (Name, Location, Supervisor, Telegram Group)
         ══════════════════════════════════════════════════════════════════════ */}
      {isEditOpen && editingSite && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>✏️ Edit Site: {editingSite.name}</h3>
              <button
                className="modal-close-btn"
                onClick={() => setIsEditOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleSaveEdit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                <div className="form-group">
                  <label>Site Name *</label>
                  <input
                    type="text"
                    value={editForm.name}
                    onChange={(e) =>
                      setEditForm((f) => ({ ...f, name: e.target.value }))
                    }
                    placeholder="e.g. Tower B - 5th Floor Slab"
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Location / Sector</label>
                  <input
                    type="text"
                    value={editForm.location}
                    onChange={(e) =>
                      setEditForm((f) => ({ ...f, location: e.target.value }))
                    }
                    placeholder="e.g. North Wing, Sector 62"
                  />
                </div>

                <div className="form-group">
                  <label>Site Supervisor / In-Charge</label>
                  <input
                    type="text"
                    value={editForm.supervisor}
                    onChange={(e) =>
                      setEditForm((f) => ({ ...f, supervisor: e.target.value }))
                    }
                    placeholder="e.g. Rajesh Kumar (Foreman)"
                  />
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginTop: "8px",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => requireAdmin(() => handleDeleteSite(editingSite.id))}
                    style={{
                      background: "transparent",
                      border: "1px solid #fecdd3",
                      color: "#dc2626",
                      padding: "8px 12px",
                      borderRadius: "6px",
                      fontSize: "12px",
                      cursor: "pointer",
                    }}
                  >
                    🗑️ Delete Site
                  </button>

                  <div style={{ display: "flex", gap: "10px" }}>
                    <button
                      type="button"
                      className="btn-card-action"
                      onClick={() => setIsEditOpen(false)}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="btn-primary"
                      disabled={savingEdit}
                    >
                      {savingEdit ? <Spinner /> : "Save Changes"}
                    </button>
                  </div>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 2: ADD A NEW SITE
         ══════════════════════════════════════════════════════════════════════ */}
      {isAddOpen && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>➕ Add New Construction Site</h3>
              <button
                className="modal-close-btn"
                onClick={() => setIsAddOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleCreateSite} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                <div className="form-group">
                  <label>Site Name *</label>
                  <input
                    type="text"
                    value={addForm.name}
                    onChange={(e) =>
                      setAddForm((f) => ({ ...f, name: e.target.value }))
                    }
                    placeholder="e.g. Metro Pier 14, Bridge Foundation, etc."
                    required
                  />
                </div>

                <div className="form-group">
                  <label>Location</label>
                  <input
                    type="text"
                    value={addForm.location}
                    onChange={(e) =>
                      setAddForm((f) => ({ ...f, location: e.target.value }))
                    }
                    placeholder="e.g. Sector 44 Highway Bypass"
                  />
                </div>

                <div className="form-group">
                  <label>Supervisor / In-Charge</label>
                  <input
                    type="text"
                    value={addForm.supervisor}
                    onChange={(e) =>
                      setAddForm((f) => ({ ...f, supervisor: e.target.value }))
                    }
                    placeholder="e.g. Suresh Patel"
                  />
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={() => setIsAddOpen(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={addingSite}
                  >
                    {addingSite ? <Spinner /> : "Create Site"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 3: SET HOW MANY SITES ARE UNDER SUPERVISION
         ══════════════════════════════════════════════════════════════════════ */}
      {isSetupOpen && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>⚙️ Configure Monitored Sites</h3>
              <button
                className="modal-close-btn"
                onClick={() => setIsSetupOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <p style={{ margin: 0, fontSize: "13px", color: "#55695e" }}>
                How many construction sites are currently under your supervision?
                Applying will initialize a fresh setup (resetting previous site photo records) while keeping your Telegram connection active.
              </p>

              <form onSubmit={handleApplySiteCount} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div className="form-group">
                  <label>Number of Sites (1 to 15):</label>
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <input
                      type="range"
                      min="1"
                      max="15"
                      value={targetSiteCount}
                      onChange={(e) => setTargetSiteCount(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <span
                      style={{
                        fontSize: "20px",
                        fontWeight: "700",
                        width: "36px",
                        textAlign: "center",
                        color: "#18794e",
                      }}
                    >
                      {targetSiteCount}
                    </span>
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={() => setIsSetupOpen(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={loadingSites}
                  >
                    {loadingSites ? <Spinner /> : `Apply ${targetSiteCount} Sites`}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 4: TELEGRAM BOT SETUP & HOW TO UPLOAD FROM PHONE
         ══════════════════════════════════════════════════════════════════════ */}
      {isTelegramOpen && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>📱 How to Upload from Phone via Telegram</h3>
              <button
                className="modal-close-btn"
                onClick={() => setIsTelegramOpen(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <div className="guide-step-box">
                <b>⚡ Step-by-Step: Uploading Photos for a Particular Site</b>
                <ol>
                  <li>
                    Open <b>Telegram</b> on your phone and create a group for the site (e.g. <i>"Site 3 - Scaffolding"</i>).
                  </li>
                  <li>
                    Add your ConstructIQ Bot to that group.
                  </li>
                  <li>
                    In that group, send the message:{" "}
                    <code className="code-pill">/bind 3</code> (or whichever site number it is).
                  </li>
                  <li>
                    <b>That's it!</b> Now, whenever anyone takes a picture on their phone and sends it into that group, ConstructIQ AI immediately fetches it, updates Site 3's screen, and sends back simple safety feedback!
                  </li>
                </ol>
              </div>

              <form onSubmit={handleSaveTelegramToken} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div className="form-group">
                  <label>Telegram Bot Token (from @BotFather)</label>
                  <input
                    type="text"
                    placeholder="Paste bot token here e.g. 123456:ABC-DEF..."
                    value={telegramTokenInput}
                    onChange={(e) => setTelegramTokenInput(e.target.value)}
                  />
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={async () => {
                      const res = await pollTelegramNow();
                      setTelegramMsg(`Polled: ${res.updates_processed} new photos received.`);
                      await loadData();
                    }}
                  >
                    🔄 Check for Telegram Photos Now
                  </button>
                  <button type="submit" className="btn-primary">
                    Save Bot Token
                  </button>
                </div>

                {telegramMsg && (
                  <div style={{ fontSize: "12px", color: "#18794e", marginTop: "4px" }}>
                    {telegramMsg}
                  </div>
                )}
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 5: DIRECT PHOTO UPLOAD
         ══════════════════════════════════════════════════════════════════════ */}
      {isUploadOpen && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>📤 Upload Site Photo</h3>
              <button
                className="modal-close-btn"
                onClick={() => {
                  setIsUploadOpen(false);
                  setUploadFile(null);
                }}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleDirectUpload} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                <div className="form-group">
                  <label>Destination Site</label>
                  <select
                    value={uploadSiteId}
                    onChange={(e) => setUploadSiteId(e.target.value)}
                  >
                    {sites.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label>Inspection Photo</label>
                  <ImageUploader
                    file={uploadFile}
                    onChange={(f) => setUploadFile(f)}
                  />
                </div>

                <div className="form-group">
                  <label>Notes / Caption</label>
                  <input
                    type="text"
                    value={uploadCaption}
                    onChange={(e) => setUploadCaption(e.target.value)}
                    placeholder="e.g. Scaffolding check before morning shift"
                  />
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={() => setIsUploadOpen(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={uploading || !uploadFile}
                  >
                    {uploading ? <><Spinner /> Uploading...</> : "Upload & Analyze"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 6: LEAD CIVIL ENGINEER PIN AUTHORIZATION
         ══════════════════════════════════════════════════════════════════════ */}
      {isAdminModalOpen && (
        <div className="modal-backdrop" onClick={() => setIsAdminModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "440px" }}>
            <div className="modal-header">
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "20px" }}>🛡️</span>
                <div>
                  <h3 style={{ margin: 0, fontSize: "15px", color: "#17251f" }}>
                    Lead Civil Engineer Authorization
                  </h3>
                  <p style={{ margin: "2px 0 0", fontSize: "11px", color: "#738078" }}>
                    Only 1 authorized person can edit or delete monitored sites.
                  </p>
                </div>
              </div>
              <button
                className="modal-close-btn"
                onClick={() => setIsAdminModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="modal-body">
              <form onSubmit={handleUnlockAdmin} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                <div
                  style={{
                    background: "#f0fdf4",
                    border: "1px solid #bbf7d0",
                    borderRadius: "8px",
                    padding: "10px 12px",
                    fontSize: "12px",
                    color: "#166534",
                    lineHeight: "1.45",
                  }}
                >
                  💡 <b>Open Access:</b> Quick Single Photo Check remains 100% accessible to all workers and visitors without any password. Modifying monitored sites or active counts requires authorization.
                </div>

                <div className="form-group">
                  <label style={{ fontSize: "12px", fontWeight: "700", color: "#17251f" }}>
                    Lead Engineer Master PIN *
                  </label>
                  <input
                    type="password"
                    placeholder="Enter PIN (Default: 1234)"
                    value={adminPinInput}
                    onChange={(e) => {
                      setAdminPinInput(e.target.value);
                      setAdminPinError("");
                    }}
                    autoFocus
                    required
                    style={{
                      padding: "10px 12px",
                      fontSize: "14px",
                      letterSpacing: "3px",
                      borderRadius: "8px",
                      border: adminPinError ? "1px solid #ef4444" : "1px solid #d1dbd3",
                    }}
                  />
                  <p style={{ margin: "4px 0 0", fontSize: "11px", color: "#738078" }}>
                    Default Passcode: <code>1234</code>
                  </p>
                </div>

                {adminPinError && (
                  <div
                    style={{
                      padding: "8px 12px",
                      background: "#fef2f2",
                      border: "1px solid #fecdd3",
                      borderRadius: "6px",
                      color: "#b91c1c",
                      fontSize: "12px",
                    }}
                  >
                    ⚠️ {adminPinError}
                  </div>
                )}

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "4px" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={() => setIsAdminModalOpen(false)}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn-primary" style={{ minWidth: "130px" }}>
                    🔓 Unlock Access
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════
         MODAL 7: UPLOAD CRACK PHOTO FOR CURRENT SITE
         ══════════════════════════════════════════════════════════════════════ */}
      {isCrackUploadOpen && (
        <div className="modal-backdrop" onClick={() => setIsCrackUploadOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: "520px" }}>
            <div className="modal-header">
              <h3>🔍 Log Structural Crack: {activeSite?.name || "Current Site"}</h3>
              <button
                className="modal-close-btn"
                onClick={() => {
                  setIsCrackUploadOpen(false);
                  setCrackUploadFile(null);
                  setCrackUploadCaption("");
                }}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleDirectCrackUpload} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                <p style={{ margin: "0", fontSize: "12px", color: "#738078" }}>
                  Upload a photo of a crack, structural fissure, spalling, or foundation shift. Antigravity AI will inspect crack width, geometry, structural hazard, and suggest civil repair methods.
                </p>

                <div className="form-group">
                  <label>Crack / Structural Defect Photo *</label>
                  <ImageUploader
                    file={crackUploadFile}
                    onChange={(f) => setCrackUploadFile(f)}
                  />
                </div>

                <div className="form-group">
                  <label>Structural Location / Member Notes</label>
                  <input
                    type="text"
                    value={crackUploadCaption}
                    onChange={(e) => setCrackUploadCaption(e.target.value)}
                    placeholder="e.g. Beam B-12 shear diagonal crack, 2nd floor east wing"
                  />
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    type="button"
                    className="btn-card-action"
                    onClick={() => {
                      setIsCrackUploadOpen(false);
                      setCrackUploadFile(null);
                      setCrackUploadCaption("");
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn-primary"
                    disabled={crackUploading || !crackUploadFile}
                  >
                    {crackUploading ? <><Spinner /> Analyzing Crack...</> : "Inspect & Save to Site"}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
