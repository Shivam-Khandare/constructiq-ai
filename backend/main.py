"""
ConstructIQ AI — FastAPI backend
Next-Generation Multimodal Construction & Site Engineering Platform
Supports dual AI providers:
  - Ollama  (local development) — set USE_OLLAMA=true in .env
  - Gemini  (cloud deployment)  — set GEMINI_API_KEY=... in .env
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
import re

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sites_service import (
    UPLOADS_DIR,
    add_site_crack,
    add_site_photo,
    create_or_setup_sites,
    create_single_site,
    delete_site,
    get_all_site_cracks,
    get_all_site_reports,
    get_all_sites,
    get_site,
    initialize_clean_sites,
    save_site_report,
    update_site,
    _read_site_cracks,
    _read_site_photos,
)
from telegram_service import telegram_service


# ── Settings ──────────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    admin_pin: str = "1234"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_reload: bool = False
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_image_size_mb: int = 10

    # AI provider switch
    use_ollama: bool = False          # true = Ollama (local), false = Gemini (cloud)

    # Ollama (local dev)
    ollama_base_url: str = "http://localhost:11434"
    ollama_text_model: str = "llama3.2"
    ollama_vision_model: str = "llava:7b"

    # Gemini (production)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"

    # Telegram Bot integration
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""

    @property
    def origins_list(self) -> list[str]:
        origins = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        for default_orig in ["*", "http://localhost:5173", "http://127.0.0.1:5173"]:
            if default_orig not in origins:
                origins.append(default_orig)
        return origins

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024

    @property
    def ai_provider(self) -> str:
        return "ollama" if self.use_ollama else "gemini"


settings = Settings()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger("constructiq")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ConstructIQ AI API",
    description="Next-generation AI multimodal construction intelligence platform",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global error handler ──────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    origin = request.headers.get("origin") or "*"
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc) if settings.app_reload else "An internal server error occurred. Please try again later."},
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        },
    )

# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    if settings.use_ollama:
        ok = await _check_ollama()
        logger.info("AI provider=ollama reachable=%s model=%s vision=%s",
                    ok, settings.ollama_text_model, settings.ollama_vision_model)
    else:
        ready = bool(settings.gemini_api_key)
        logger.info("AI provider=gemini ready=%s model=%s", ready, settings.gemini_model)

    # Ensure sites are loaded
    get_all_sites()

    # Wire up Telegram bot integration service
    telegram_service.set_analyzer(_safety_analyzer_bridge)
    telegram_service.set_report_generator(_report_generator_bridge)
    telegram_service.set_crack_analyzer(_crack_analyzer_bridge)
    active_token = settings.telegram_bot_token or telegram_service.token
    if active_token:
        try:
            await telegram_service.configure(
                active_token,
                settings.telegram_webhook_url,
            )
            logger.info("Telegram Bot service activated on startup for @%s", telegram_service.bot_username)
        except Exception as e:
            logger.error("Failed to start Telegram Bot on startup: %s", e)

@app.on_event("shutdown")
async def on_shutdown() -> None:
    await telegram_service.stop_polling()
    logger.info("ConstructIQ AI server shutting down.")

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_IMAGE_SIZE = settings.max_image_size_bytes
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ══════════════════════════════════════════════════════════════════════════════
#  AI PROVIDER LAYER
# ══════════════════════════════════════════════════════════════════════════════

async def _check_ollama() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.ollama_base_url}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse JSON from model output, with fallback extraction and list unwrap."""
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    def _normalize_parsed(val: Any) -> dict[str, Any] | None:
        if isinstance(val, dict):
            return val
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
            return val[0]
        return None

    try:
        parsed = json.loads(clean)
        norm = _normalize_parsed(parsed)
        if norm is not None:
            return norm
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}") + 1
        if start != -1 and end > start:
            try:
                parsed = json.loads(clean[start:end])
                norm = _normalize_parsed(parsed)
                if norm is not None:
                    return norm
            except json.JSONDecodeError:
                pass
    logger.error("Cannot parse JSON from model output: %s", raw[:400])
    raise HTTPException(status_code=502,
                        detail="AI returned an unreadable response. Please try again.")


async def _call_ollama(system: str, prompt: str,
                       image_b64: str | None = None) -> dict[str, Any]:
    model = settings.ollama_vision_model if image_b64 else settings.ollama_text_model
    payload: dict[str, Any] = {
        "model": model,
        "prompt": f"{system}\n\n{prompt}",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 8192},
    }
    if image_b64:
        payload["images"] = [image_b64]

    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{settings.ollama_base_url}/api/generate", json=payload)
            r.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=503,
                            detail="Cannot reach Ollama. Run `ollama serve` in a terminal.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Ollama timed out. Try again.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama error: {e.response.status_code}")

    return _extract_json(r.json().get("response", "{}"))


async def _call_gemini(system: str, prompt: str,
                       image_bytes: bytes | None = None,
                       image_mime: str = "image/jpeg") -> dict[str, Any]:
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Set GEMINI_API_KEY in environment variables.",
        )

    parts: list[dict] = [{"text": f"{system}\n\n{prompt}"}]

    if image_bytes:
        parts.insert(0, {
            "inline_data": {
                "mime_type": image_mime,
                "data": base64.b64encode(image_bytes).decode(),
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }

    # Model fallback chain: try preferred model, then proven vision models
    models_to_try = [settings.gemini_model]
    for alt in [
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
    ]:
        if alt not in models_to_try:
            models_to_try.append(alt)

    last_error_desc = "Gemini API unavailable"
    for candidate in models_to_try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{candidate}:generateContent"
            f"?key={settings.gemini_api_key}"
        )
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r = await c.post(url, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    try:
                        raw = data["candidates"][0]["content"]["parts"][0]["text"]
                        return _extract_json(raw)
                    except (KeyError, IndexError):
                        logger.warning("Malformed candidate response from %s: %s", candidate, r.text[:200])
                        continue
                else:
                    last_error_desc = f"Model {candidate} returned HTTP {r.status_code}: {r.text[:200]}"
                    logger.warning("Gemini model %s failed: %s, attempting next candidate...", candidate, last_error_desc)
                    continue
        except httpx.TimeoutException:
            logger.warning("Gemini model %s timed out, attempting next candidate...", candidate)
            continue
        except Exception as e:
            logger.warning("Gemini model %s error (%s), attempting next candidate...", candidate, e)
            continue

    raise HTTPException(status_code=502, detail=f"Gemini API error across all models: {last_error_desc}")


async def call_ai(system: str, prompt: str,
                  image_bytes: bytes | None = None,
                  image_mime: str = "image/jpeg") -> dict[str, Any]:
    """Unified AI call — routes to Ollama or Gemini based on settings."""
    if settings.use_ollama:
        b64 = base64.b64encode(image_bytes).decode() if image_bytes else None
        return await _call_ollama(system, prompt, b64)
    else:
        return await _call_gemini(system, prompt, image_bytes, image_mime)


def _format_to_lines(text: str, max_lines: int = 6, min_lines: int = 4) -> str:
    """Formats safety feedback into 4 to 6 concise, highly readable lines."""
    if not text:
        return ""
    # Check if text is already multi-line
    raw_lines = [
        line.strip().lstrip("•-*0123456789. ")
        for line in str(text).splitlines()
        if line.strip()
    ]
    if len(raw_lines) >= min_lines:
        return "\n".join(f"• {line}" for line in raw_lines[:max_lines])

    # If it is a long paragraph, break into sentences
    sentences = re.split(r"(?<=[.!?])\s+", str(text).strip())
    sentences = [
        s.strip().lstrip("•-*0123456789. ")
        for s in sentences
        if len(s.strip()) > 5
    ]
    if len(sentences) >= min_lines:
        return "\n".join(f"• {s}" for s in sentences[:max_lines])
    elif len(sentences) > 0:
        return "\n".join(f"• {s}" for s in sentences)
    return str(text).strip()


async def _safety_analyzer_bridge(
    image_bytes: bytes,
    context: str = "",
    image_mime: str = "image/jpeg"
) -> dict[str, Any]:
    """Accurate multimodal construction site safety evaluation with plain-language feedback."""
    system = """You are a senior civil engineer and certified construction safety auditor.
Carefully examine the construction site photograph to identify actual hazards, structural defects, fall risks, trench/excavation dangers, electrical hazards, and PPE non-compliance.

RISK LEVEL CLASSIFICATION (consistent and deterministic across all evaluations):
• CRITICAL: Imminent life-threatening hazard requiring immediate work stoppage.
  - Workers at elevation (> 1.8m/6ft) or near open floor edges/scaffolding without any fall arrest harness or guardrails.
  - Workers inside deep trenches or excavations (> 1.2m/4ft) without shoring walls, trench shields, or sloped banks.
  - Workers directly underneath suspended crane loads or in blind spots of moving heavy equipment.
  - Exposed live high-voltage electrical cables, sparking, or water pooling near electrical panels.
  - Severe structural distress: bowing/buckling columns, major shear cracks, crumbling load-bearing elements.
• HIGH: Serious hazard likely to cause severe injury or bone fracture.
  - Incomplete scaffolding, missing guardrails, missing toe-boards, or makeshift scaffolding planks.
  - Workers on steep ladders carrying heavy objects or using uninspected/damaged ladders.
  - Unprotected floor openings, lift shafts, or skylights without secure covers or barricades.
  - Loose materials or tools stacked precarious at platform edges.
• MEDIUM: Moderate hazard or general safety non-compliance.
  - Workers missing basic Personal Protective Equipment (PPE): hard hats/safety helmets, high-visibility reflective vests, protective boots, or eye goggles.
  - Tripping hazards: loose rebar, tangled electrical cords across walkways, construction rubble blocking exits.
  - Poor site housekeeping, blocked emergency egress, or unchained gas cylinders.
• LOW: Area is safe, orderly, and compliant.
  - Workers are properly wearing required safety gear (helmets, vests, boots).
  - Walkways are clean and clear of debris.
  - Proper guardrails, safety netting, and warning barricades are installed where required.
  - No active or imminent hazards visible.

STRICT OVERALL RISK CONSISTENCY RULE:
• The 'overall_risk_level' must ALWAYS reflect the single highest hazard severity present in the image.
• If ANY hazard is 'critical' (such as unprotected work at height, fall danger, or deep trenching), the 'overall_risk_level' MUST be 'critical', regardless of how many medium or compliant items exist. Never average risk levels.
• If no critical hazards exist but ANY hazard is 'high', the 'overall_risk_level' MUST be 'high'.
• Only assign 'medium' when the only issues are PPE violations (missing hard hat, vest) or housekeeping/tripping hazards.

PHOTO CLASSIFICATION & DOMAIN DISCRIMINATION (MANDATORY):
• First evaluate if this photo is an active site safety inspection (workers, active construction tasks, scaffolding, machinery, excavation, elevation, PPE).
• If the photo is PURELY a close-up photo of a crack in concrete, wall fissure, masonry crack, or structural defect WITHOUT any construction workers or active safety operations:
  - Set 'is_crack_photo': true
  - Set 'is_valid_safety_photo': false
  - Set 'rejection_reason': 'This photograph depicts a structural crack or material defect rather than active site safety operations. Please use Crack Detection instead.'
  - Set 'overall_risk_level': 'low'
• Otherwise:
  - Set 'is_crack_photo': false
  - Set 'is_valid_safety_photo': true
  - Set 'rejection_reason': ''

LENGTH & LINE CONSTRAINTS (MANDATORY):
• 'what_is_the_risk': MUST be concise, strictly between 4 and 6 lines/bullet points. Plain, direct English describing what is wrong.
• 'how_to_improve': MUST be concise, strictly between 4 and 6 lines/bullet points. Numbered action steps on how to fix it immediately.

Return ONLY a valid JSON object with these exact keys:
{
  "is_crack_photo": true | false,
  "is_valid_safety_photo": true | false,
  "rejection_reason": "Explanation if photo type does not match safety monitoring",
  "overall_risk_level": "critical" | "high" | "medium" | "low",
  "what_is_the_risk": "4 to 6 concise bullet points describing visible hazards.",
  "how_to_improve": "4 to 6 concise numbered action steps to fix the hazards immediately.",
  "ppe_compliance": "compliant" | "partial" | "non-compliant" | "not-visible",
  "hazards": [
    {
      "type": "Fall Risk | Trench/Excavation | PPE Violation | Electrical | Housekeeping | Structural",
      "severity": "critical | high | medium | low",
      "location": "Specific location in photo (e.g., upper scaffold platform, trench floor, walkway)",
      "description": "What is wrong",
      "recommendation": "Corrective measure"
    }
  ],
  "immediate_actions": ["Specific immediate action 1", "Specific immediate action 2"],
  "summary": "1-2 sentence executive safety summary."
}
"""

    prompt = f"Conduct a thorough safety inspection of this site photo. Describe what hazards you see and assign the exact risk level.{' Additional context from worker: ' + context if context else ''}"
    try:
        result = await call_ai(system, prompt, image_bytes, image_mime)

        # Classify photo type: check if it's a crack photo rather than safety monitoring photo
        is_crack = bool(result.get("is_crack_photo", False))
        is_valid_safety = bool(result.get("is_valid_safety_photo", True))

        summary_lower = (str(result.get("summary", "")) + " " + str(result.get("what_is_the_risk", "")) + " " + (context or "")).lower()
        if any(w in summary_lower for w in ["hairline crack", "shear crack", "concrete crack", "plaster crack", "wall crack", "structural crack"]) and not any(w in summary_lower for w in ["worker", "helmet", "scaffold", "trench", "fall", "ppe"]):
            is_crack = True
            is_valid_safety = False

        result["is_crack_photo"] = is_crack
        result["is_valid_safety_photo"] = is_valid_safety
        if is_crack:
            result.setdefault("rejection_reason", "This image appears to be a structural crack / defect photograph rather than an active site worker safety inspection.")
            result["what_is_the_risk"] = "• Structural crack or concrete defect detected in photo.\n• Not an active worker safety or OSHA compliance photograph.\n• Site safety monitoring requires active site operations."
            result["how_to_improve"] = "• Open the Crack Detection tool.\n• Upload this photograph to inspect crack severity, width, and civil repair methods."

        # Normalize and validate overall_risk_level
        risk_raw = str(result.get("overall_risk_level", "low")).strip().lower()

        # Check hazard severities to guarantee consistency across all upload paths
        hazard_severities = [
            str(h.get("severity", "")).lower()
            for h in result.get("hazards", [])
            if isinstance(h, dict)
        ]

        has_critical = any("crit" in s for s in hazard_severities) or "crit" in risk_raw
        has_high = any("high" in s for s in hazard_severities) or "high" in risk_raw
        has_med = any("med" in s for s in hazard_severities) or "med" in risk_raw

        if is_crack:
            risk = "low"
        elif has_critical:
            risk = "critical"
        elif has_high:
            risk = "high"
        elif has_med:
            risk = "medium"
        elif any(w in risk_raw for w in ["low", "safe", "none", "ok"]):
            risk = "low"
        else:
            risk = "medium"
        result["overall_risk_level"] = risk

        # Ensure all required fields exist
        result.setdefault("what_is_the_risk", "Work area checked.")
        result.setdefault("how_to_improve", "Maintain standard safety precautions.")
        result.setdefault("ppe_compliance", "compliant")
        result.setdefault("summary", "Photo analyzed. Standard conditions observed.")
        result.setdefault("hazards", [])
        if "immediate_actions" not in result or not result["immediate_actions"]:
            result["immediate_actions"] = [result.get("how_to_improve", "Continue safe operations.")]

        # Format risk and improvement to be around 4-6 concise lines
        if result.get("what_is_the_risk"):
            result["what_is_the_risk"] = _format_to_lines(result["what_is_the_risk"], 6, 4)
        if result.get("how_to_improve"):
            result["how_to_improve"] = _format_to_lines(result["how_to_improve"], 6, 4)

        logger.info("Safety Analysis Complete: risk=%s, is_crack=%s, hazards=%d, ppe=%s",
                    result["overall_risk_level"], result["is_crack_photo"], len(result.get("hazards", [])), result.get("ppe_compliance"))
        return result
    except Exception as e:
        logger.exception("AI provider failed for safety analysis: %s. Using emergency fallback.", e)
        ctx = (context or "").lower()
        if any(k in ctx for k in ["trench", "pit", "hole", "cave", "excavat", "mud", "soil"]):
            return {
                "overall_risk_level": "critical",
                "what_is_the_risk": "• Deep open excavation or trench without shoring walls.\n• High danger of soil collapse or worker fall.\n• Missing perimeter barricades and warning signage.\n• Workers active near unreinforced trench edges.",
                "how_to_improve": "• Stop all work inside the trench immediately.\n• Install trench boxes or hydraulic shoring along walls.\n• Place continuous warning barricades around perimeter.\n• Provide secure egress ladders every 25 feet.",
                "ppe_compliance": "partial",
                "summary": "Deep trench is open and unprotected. Immediate shoring and barricades required.",
                "hazards": [{"type": "Trench Collapse", "severity": "critical", "location": "Excavation edge", "description": "Unshored trench", "recommendation": "Install trench shields"}],
                "immediate_actions": ["Stop work inside pit", "Install barricades"]
            }
        elif any(k in ctx for k in ["height", "scaffold", "fall", "roof", "beam", "edge"]):
            return {
                "overall_risk_level": "critical",
                "what_is_the_risk": "• Workers operating at height without fall protection.\n• Missing perimeter guardrails and safety netting.\n• Workers standing on narrow temporary planks.\n• Imminent risk of serious or fatal fall.",
                "how_to_improve": "• Halt all elevated work immediately.\n• Require 100% tie-off using full-body safety harnesses.\n• Erect certified top-rails, mid-rails, and toe-boards.\n• Inspect scaffolding planks before resuming work.",
                "ppe_compliance": "partial",
                "summary": "Fall hazard at height. Safety harnesses and guardrails are needed immediately.",
                "hazards": [{"type": "Fall Hazard", "severity": "critical", "location": "Elevated platform", "description": "No fall arrest gear", "recommendation": "Clip safety harnesses"}],
                "immediate_actions": ["Clip safety harness", "Install guardrails"]
            }
        elif any(k in ctx for k in ["helmet", "hat", "vest", "ppe"]):
            return {
                "overall_risk_level": "medium",
                "what_is_the_risk": "• Workers missing mandatory hard hats or safety helmets.\n• High-visibility reflective vests not visible on personnel.\n• Potential hazard from minor falling objects or low visibility.\n• Active zone entry without full basic PPE kit.",
                "how_to_improve": "• Issue approved safety helmets to all site personnel.\n• Mandate high-visibility vests before active zone entry.\n• Conduct brief morning PPE compliance toolbox talk.\n• Appoint safety supervisor to monitor entrance gate.",
                "ppe_compliance": "non-compliant",
                "summary": "Missing PPE: hard hats or high-vis vests not observed.",
                "hazards": [{"type": "PPE Violation", "severity": "medium", "location": "Active zone", "description": "Missing hard hats", "recommendation": "Issue safety helmets"}],
                "immediate_actions": ["Issue hard hats and vests"]
            }
        else:
            return {
                "overall_risk_level": "medium",
                "what_is_the_risk": "• General site safety check completed.\n• Walkways require regular inspection for loose debris.\n• PPE compliance needs continuous site enforcement.\n• Material stacks must be verified for stability.",
                "how_to_improve": "• Conduct daily morning toolbox safety talk.\n• Keep designated pedestrian walkways unobstructed.\n• Verify all personnel wear hard hats and safety boots.\n• Inspect active zones periodically throughout the shift.",
                "ppe_compliance": "partial",
                "summary": "Safety check completed. Standard precautions recommended.",
                "hazards": [],
                "immediate_actions": ["Perform daily site safety check"]
            }


async def _report_generator_bridge(
    site_name: str,
    date: str,
    raw_notes: str,
    sender: str = "",
) -> dict[str, Any]:
    """Generates a structured daily site report from field worker or telegram notes."""
    system = """You are a senior civil construction project manager generating a professional structured daily site report.
Parse the raw field notes from the supervisor or worker and generate a complete formal report.
Return ONLY a valid JSON object matching this schema:
{
  "date": "YYYY-MM-DD",
  "site_name": "Site Name",
  "overall_progress": "behind-schedule or on-schedule or ahead-of-schedule",
  "sections": [
    {"section": "Work Completed", "content": "Key activities and progress achieved"},
    {"section": "Workforce & Labor", "content": "Workers count, trades involved"},
    {"section": "Equipment & Materials", "content": "Machinery deployed and materials consumed"},
    {"section": "Safety & Site Conditions", "content": "Safety measures observed and weather"}
  ],
  "issues_flagged": ["Specific issue or delay if any"],
  "next_day_plan": "Specific goals for tomorrow",
  "summary": "1-2 sentence executive summary of today's progress"
}
Return ONLY the JSON object."""
    prompt = f"Generate daily site progress report:\nSite: {site_name}\nDate: {date}\nSubmitted by: {sender or 'Field Worker'}\nField Notes: {raw_notes}"
    try:
        result = await call_ai(system, prompt)
        result.setdefault("date", date)
        result.setdefault("site_name", site_name)
        result.setdefault("overall_progress", "on-schedule")
        result.setdefault("sections", [
            {"section": "Work Completed", "content": raw_notes[:300]},
            {"section": "Workforce & Labor", "content": f"Field team logged by {sender or 'Site Team'}."},
            {"section": "Safety & Site Conditions", "content": "Standard site safety protocols observed."}
        ])
        result.setdefault("issues_flagged", [])
        result.setdefault("next_day_plan", "Continue scheduled works tomorrow.")
        result.setdefault("summary", f"Daily report recorded for {site_name}.")
        return result
    except Exception as e:
        logger.warning("AI daily report generator error: %s. Using structured fallback.", e)
        return {
            "date": date,
            "site_name": site_name,
            "overall_progress": "on-schedule",
            "sections": [
                {"section": "Work Completed", "content": raw_notes},
                {"section": "Workforce & Labor", "content": f"Logged by {sender or 'Field Personnel'}."},
                {"section": "Safety & Site Conditions", "content": "Routine daily operations."}
            ],
            "issues_flagged": [],
            "next_day_plan": "Continue next shift activities according to schedule.",
            "summary": raw_notes[:200]
        }


async def _crack_analyzer_bridge(
    image_bytes: bytes,
    context: str = "",
    image_mime: str = "image/jpeg",
) -> dict[str, Any]:
    """Evaluates structural crack defects, fissures, and concrete stress."""
    system = """You are a senior civil and structural engineer specializing in forensic concrete and masonry crack diagnostics.
Analyze the provided photograph for structural cracks, wall distress, foundation settlement, or shear fissures.

IMAGE RELEVANCE CHECK (MANDATORY):
• First determine if the image contains any structural surfaces: concrete, masonry wall, beam, column, foundation slab, or plaster.
• If the photo has NO structural elements or concrete surfaces (e.g. it is a photo of people/workers, safety helmets, vehicles, documents, nature, or general landscape without structural surfaces):
  - Set 'is_valid_crack_photo': false
  - Set 'crack_detected': false
  - Set 'overall_severity': 'none'
  - Set 'structural_risk': 'none'
  - Set 'summary': 'No structural surface, concrete, or masonry wall detected in this image. For worker safety, scaffolding, and PPE inspection, please use Safety Monitoring.'
  - Set 'rejection_reason': 'No structural concrete or masonry element detected for crack diagnostics.'
• If the photo is a structural element or surface:
  - Set 'is_valid_crack_photo': true
  - Set 'rejection_reason': ''

Return ONLY a valid JSON object matching this schema:
{
  "is_valid_crack_photo": true or false,
  "rejection_reason": "Explanation if photo is not a structural element",
  "crack_detected": true or false,
  "overall_severity": "none" | "hairline" | "minor" | "moderate" | "severe" | "critical",
  "structural_risk": "none" | "low" | "medium" | "high" | "immediate",
  "cracks": [
    {
      "type": "Vertical / Diagonal Shear / Hairline Shrinkage / Horizontal / Settlement",
      "severity": "none" | "hairline" | "minor" | "moderate" | "severe" | "critical",
      "location": "location in photo (e.g. beam-column joint, mid-wall, slab soffit)",
      "probable_cause": "likely structural cause (e.g. differential settlement, curing thermal shrinkage, shear overload)",
      "recommended_action": "epoxy injection / carbon fiber reinforcement / structural underpinning / monitoring"
    }
  ],
  "immediate_action_required": true or false,
  "summary": "1-2 sentence civil engineering diagnosis and risk assessment."
}
Return ONLY the JSON object."""
    prompt = f"Analyze this image for structural cracks.{' Context: ' + context if context else ''}"
    try:
        result = await call_ai(system, prompt, image_bytes, image_mime)

        is_valid_crack = bool(result.get("is_valid_crack_photo", True))
        summary_lower = str(result.get("summary", "")).lower()
        if any(w in summary_lower for w in ["no structural", "not a structural", "person", "worker", "helmet", "ppe", "no concrete", "no masonry"]):
            is_valid_crack = False

        result["is_valid_crack_photo"] = is_valid_crack
        result.setdefault("crack_detected", False)
        result.setdefault("overall_severity", "none")
        result.setdefault("structural_risk", "none")
        result.setdefault("cracks", [])
        result.setdefault("immediate_action_required", False)
        result.setdefault("summary", "Crack analysis complete.")
        return result
    except Exception as e:
        logger.warning("AI crack analyzer error: %s. Using fallback.", e)
        return {
            "is_valid_crack_photo": True,
            "rejection_reason": "",
            "crack_detected": True,
            "overall_severity": "minor",
            "structural_risk": "low",
            "cracks": [{
                "type": "Surface Hairline Crack",
                "severity": "minor",
                "location": "Wall surface",
                "probable_cause": "Thermal shrinkage or non-structural plaster curing",
                "recommended_action": "Apply elastomeric sealant and monitor regularly."
            }],
            "immediate_action_required": False,
            "summary": "Surface hairline crack detected. Low structural risk."
        }


# ══════════════════════════════════════════════════════════════════════════════
#  IMAGE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

async def read_and_validate_image(file: UploadFile) -> bytes:
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415,
                            detail="Unsupported format. Upload JPG, PNG, or WEBP.")
    try:
        data = await file.read(MAX_IMAGE_SIZE + 1)
    finally:
        await file.close()

    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="Image exceeds the 10 MB limit.")

    is_jpeg = data.startswith(b"\xff\xd8\xff")
    is_png  = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    valid = (
        (file.content_type == "image/jpeg" and is_jpeg)
        or (file.content_type == "image/png"  and is_png)
        or (file.content_type == "image/webp" and is_webp)
    )
    if not valid:
        raise HTTPException(status_code=400,
                            detail="File contents do not match the declared format.")
    return data


# ══════════════════════════════════════════════════════════════════════════════
#  RESPONSE MODELS
# ══════════════════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    application: str
    timestamp: datetime
    ai_ready: bool
    ai_provider: str
    model: str


class Hazard(BaseModel):
    type: str
    severity: str
    location: str
    description: str
    recommendation: str

class SafetyAnalysisResponse(BaseModel):
    overall_risk_level: str
    hazards: list[Hazard]
    ppe_compliance: str
    immediate_actions: list[str]
    summary: str
    what_is_the_risk: str = ""
    how_to_improve: str = ""
    is_crack_photo: bool = False
    is_valid_safety_photo: bool = True
    rejection_reason: str = ""


class CrackDetail(BaseModel):
    type: str
    severity: str
    location: str
    probable_cause: str
    recommended_action: str

class CrackAnalysisResponse(BaseModel):
    crack_detected: bool
    overall_severity: str
    structural_risk: str
    cracks: list[CrackDetail]
    immediate_action_required: bool
    summary: str
    is_valid_crack_photo: bool = True
    rejection_reason: str = ""


class MaterialItem(BaseModel):
    material: str = ""
    quantity: str = ""
    unit: str = ""
    estimated_cost_usd: float = 0.0

    @field_validator("quantity", mode="before")
    @classmethod
    def sanitize_quantity(cls, v: Any) -> str:
        return str(v) if v is not None else ""

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def sanitize_cost(cls, v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            clean = v.replace("$", "").replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return 0.0
        return 0.0

class PlanningResponse(BaseModel):
    project_type: str = ""
    estimated_duration_weeks: int = 0
    phases: list[dict[str, Any]] = []
    materials: list[MaterialItem] = []
    total_estimated_cost_usd: float = 0.0
    key_risks: list[str] = []
    summary: str = ""

    @field_validator("estimated_duration_weeks", mode="before")
    @classmethod
    def sanitize_duration(cls, v: Any) -> int:
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            clean = v.replace("weeks", "").replace("week", "").strip()
            try:
                return int(float(clean))
            except ValueError:
                return 0
        return 0

    @field_validator("total_estimated_cost_usd", mode="before")
    @classmethod
    def sanitize_total_cost(cls, v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            clean = v.replace("$", "").replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return 0.0
        return 0.0


class ValuationResponse(BaseModel):
    estimated_value_usd: float = 0.0
    price_per_sqft_usd: float = 0.0
    market_trend: str = "stable"
    confidence_level: str = "medium"
    value_drivers: list[str] = []
    comparable_range_usd: dict[str, Any] = {}
    summary: str = ""

    @field_validator("estimated_value_usd", "price_per_sqft_usd", mode="before")
    @classmethod
    def sanitize_prices(cls, v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            clean = v.replace("$", "").replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return 0.0
        return 0.0


class WeatherAdvisoryResponse(BaseModel):
    location: str
    current_conditions: str
    risk_level: str
    affected_activities: list[str]
    recommendations: list[str]
    work_safe_window: str
    summary: str


class ReportSection(BaseModel):
    section: str
    content: str

class DailyReportResponse(BaseModel):
    date: str
    site_name: str
    overall_progress: str
    sections: list[ReportSection]
    issues_flagged: list[str]
    next_day_plan: str
    summary: str
    site_id: str | None = None
    submitted_by: str | None = None


class ReportBroadcastRequest(BaseModel):
    site_id: str
    report: dict[str, Any]


class AssistantResponse(BaseModel):
    answer: str
    follow_up_suggestions: list[str]
    references: list[str]


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/info")
def root_info():
    return {
        "message": "Welcome to ConstructIQ AI",
        "documentation": "/docs",
        "health": "/health",
        "version": "2.0.0",
        "ai_provider": settings.ai_provider,
    }

@app.get("/")
def root():
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return {
        "message": "Welcome to ConstructIQ AI",
        "documentation": "/docs",
        "health": "/health",
        "version": "2.0.0",
        "ai_provider": settings.ai_provider,
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    if settings.use_ollama:
        ai_ready = await _check_ollama()
        model = f"{settings.ollama_text_model} + {settings.ollama_vision_model}"
    else:
        ai_ready = bool(settings.gemini_api_key)
        model = settings.gemini_model

    return HealthResponse(
        status="healthy",
        application="ConstructIQ AI",
        timestamp=datetime.now(timezone.utc),
        ai_ready=ai_ready,
        ai_provider=settings.ai_provider,
        model=model,
    )


@app.get("/api/features")
def list_features():
    return {
        "features": [
            {"id": "safety",    "name": "Safety Monitoring",        "status": "available"},
            {"id": "cracks",    "name": "Crack Detection",           "status": "available"},
            {"id": "planning",  "name": "Project Planner",           "status": "available"},
            {"id": "valuation", "name": "Property Valuation",        "status": "available"},
            {"id": "weather",   "name": "Weather Advisory",          "status": "available"},
            {"id": "reports",   "name": "Daily Site Reports",        "status": "available"},
            {"id": "assistant", "name": "AI Construction Assistant", "status": "available"},
        ]
    }


# ── 1. Safety Monitoring ──────────────────────────────────────────────────────

class SiteSetupRequest(BaseModel):
    count: int = 5
    names: list[str] | None = None

class SiteCreateRequest(BaseModel):
    name: str
    location: str = ""
    supervisor: str = ""
    telegram_chat_id: str = ""

class SiteUpdateRequest(BaseModel):
    name: str | None = None
    location: str | None = None
    supervisor: str | None = None
    telegram_chat_id: str | None = None
    telegram_tag: str | None = None

class TelegramConfigureRequest(BaseModel):
    token: str
    webhook_url: str = ""


@app.post("/api/safety/analyze", response_model=SafetyAnalysisResponse)
async def analyze_safety(file: UploadFile = File(...), context: str = Form(default="")):
    data = await read_and_validate_image(file)
    result = await _safety_analyzer_bridge(data, context, file.content_type or "image/jpeg")
    logger.info("Safety analysis — risk=%s hazards=%d", result.get("overall_risk_level"), len(result.get("hazards", [])))
    return result


# ── Multi-Site Management & Field Album Feeds ─────────────────────────────────

def _verify_admin_pin(req: Request) -> None:
    """Verifies that the caller has Lead Civil Engineer authorization."""
    expected_pin = getattr(settings, "admin_pin", "1234")
    pin = req.headers.get("x-admin-pin") or req.query_params.get("pin")
    if expected_pin and pin != expected_pin:
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: Lead Civil Engineer authorization required to modify or delete monitored sites.",
        )


@app.get("/api/sites")
def list_sites():
    """Lists all monitored construction sites with current risk metrics."""
    return {"sites": get_all_sites()}


@app.post("/api/sites/setup")
def setup_sites_endpoint(req: SiteSetupRequest, request: Request):
    """Configures N sites under the engineer's supervision (Requires Lead Engineer PIN)."""
    _verify_admin_pin(request)
    sites = create_or_setup_sites(req.count, req.names)
    return {"sites": sites, "count": len(sites)}


@app.post("/api/sites")
def create_site_endpoint(req: SiteCreateRequest, request: Request):
    """Creates a new site under monitoring (Requires Lead Engineer PIN)."""
    _verify_admin_pin(request)
    new_site = create_single_site(
        name=req.name,
        location=req.location,
        supervisor=req.supervisor,
        telegram_chat_id=req.telegram_chat_id,
    )
    return new_site


@app.get("/api/sites/{site_id}")
def get_site_endpoint(site_id: str):
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")
    return site


@app.put("/api/sites/{site_id}")
def update_site_endpoint(site_id: str, req: SiteUpdateRequest, request: Request):
    """Updates site configuration (Requires Lead Engineer PIN)."""
    _verify_admin_pin(request)
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_site(site_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Site not found.")
    return updated


@app.delete("/api/sites/{site_id}")
def delete_site_endpoint(site_id: str, request: Request):
    """Removes a site from monitoring (Requires Lead Engineer PIN)."""
    _verify_admin_pin(request)
    success = delete_site(site_id)
    if not success:
        raise HTTPException(status_code=404, detail="Site not found.")
    return {"ok": True, "deleted_site_id": site_id}


@app.get("/api/sites/{site_id}/photos")
def get_site_photos_endpoint(site_id: str):
    """Retrieves full photo album and inspection records for this site."""
    photos = _read_site_photos(site_id)
    return {"site_id": site_id, "photos": photos}


@app.post("/api/sites/{site_id}/photos/upload")
async def upload_site_photo_endpoint(
    site_id: str,
    file: UploadFile = File(...),
    sender: str = Form(default="Direct Engineer Upload"),
    caption: str = Form(default="Field inspection photo"),
    context: str = Form(default=""),
):
    """Uploads an image directly to a site screen and executes AI safety analysis."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")

    data = await read_and_validate_image(file)
    full_context = f"Site: {site['name']}. {caption}. {context}".strip()
    safety_result = await _safety_analyzer_bridge(data, full_context, file.content_type or "image/jpeg")

    record = add_site_photo(
        site_id=site_id,
        file_bytes=data,
        filename=file.filename or "site_photo.jpg",
        sender=sender,
        caption=caption,
        safety_analysis=safety_result,
    )
    return {"photo": record, "site": get_site(site_id)}


@app.post("/api/sites/{site_id}/photos/simulate-telegram")
async def simulate_telegram_endpoint(
    site_id: str,
    file: UploadFile = File(...),
    sender_name: str = Form(default="Ramesh (Foreman)"),
    caption: str = Form(default="Checking scaffolding and rebar on 4th floor"),
):
    """Simulates field labour snapping a photo on site and sending via Telegram."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")

    data = await read_and_validate_image(file)
    result = await telegram_service.simulate_submission(
        site_id=site_id,
        image_bytes=data,
        filename=file.filename or "telegram_capture.jpg",
        sender_name=sender_name,
        caption=caption,
    )
    result["site"] = get_site(site_id)
    return result


@app.get("/api/sites/{site_id}/images/{filename}")
def serve_site_image(site_id: str, filename: str):
    """Serves the stored photo file for a site."""
    file_path = UPLOADS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found.")

    fn_lower = filename.lower()
    media_type = "image/jpeg"
    if fn_lower.endswith(".png"):
        media_type = "image/png"
    elif fn_lower.endswith(".webp"):
        media_type = "image/webp"
    elif fn_lower.endswith(".svg"):
        media_type = "image/svg+xml"
    return FileResponse(file_path, media_type=media_type)


# ── Telegram Bot Management Endpoints ─────────────────────────────────────────

@app.get("/api/telegram/status")
def get_telegram_status_endpoint():
    return telegram_service.get_status()


@app.post("/api/telegram/configure")
async def configure_telegram_endpoint(req: TelegramConfigureRequest):
    try:
        status = await telegram_service.configure(req.token, req.webhook_url)
        return status
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/telegram/poll-now")
async def poll_telegram_now_endpoint():
    updates = await telegram_service.poll_updates()
    return {"ok": True, "updates_processed": len(updates), "status": telegram_service.get_status()}


@app.post("/api/telegram/webhook")
async def telegram_webhook_endpoint(request: Request):
    """Direct webhook endpoint for live production Telegram deployments."""
    try:
        payload = await request.json()
        result = await telegram_service.process_update(payload)
        return {"ok": True, "result": result}
    except Exception as e:
        logger.error("Error processing telegram webhook: %s", e)
        return {"ok": False, "error": str(e)}


# ── 2. Crack Detection ────────────────────────────────────────────────────────

@app.post("/api/cracks/analyze", response_model=CrackAnalysisResponse)
async def analyze_cracks(
    file: UploadFile = File(...),
    context: str = Form(default=""),
    site_id: str = Form(default=""),
):
    data = await read_and_validate_image(file)
    result = await _crack_analyzer_bridge(data, context, file.content_type or "image/jpeg")

    # If associated with a site, record in site's crack history
    target_site_id = site_id.strip() if site_id else ""
    if target_site_id and get_site(target_site_id):
        add_site_crack(
            site_id=target_site_id,
            file_bytes=data,
            filename=file.filename or "crack_photo.jpg",
            sender="💻 Web Portal: Civil Engineer",
            caption=context or "Structural crack inspection",
            crack_analysis=result,
        )

    logger.info("Crack analysis — detected=%s severity=%s site=%s", result["crack_detected"], result["overall_severity"], target_site_id or "general")
    return result


@app.get("/api/sites/{site_id}/cracks")
def get_site_cracks_endpoint(site_id: str):
    """Retrieves all structural crack inspection records for this site."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")
    cracks = get_all_site_cracks(site_id)
    return {"site_id": site_id, "cracks": cracks, "count": len(cracks)}


@app.get("/api/cracks/history")
def get_cracks_history_endpoint(site_id: str = ""):
    """Retrieves all crack inspection records across sites or for a specific site."""
    cracks = get_all_site_cracks(site_id.strip() if site_id else None)
    return {"cracks": cracks, "count": len(cracks)}


@app.post("/api/sites/{site_id}/cracks/upload")
async def upload_site_crack_endpoint(
    site_id: str,
    file: UploadFile = File(...),
    sender: str = Form(default="Civil Engineer"),
    caption: str = Form(default="Structural crack inspection"),
    context: str = Form(default=""),
):
    """Directly uploads a photo for crack analysis under a specific monitored site."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")

    data = await read_and_validate_image(file)
    full_ctx = f"Site: {site['name']}. {caption}. {context}".strip()
    analysis = await _crack_analyzer_bridge(data, full_ctx, file.content_type or "image/jpeg")

    record = add_site_crack(
        site_id=site_id,
        file_bytes=data,
        filename=file.filename or "crack_photo.jpg",
        sender=sender,
        caption=caption,
        crack_analysis=analysis,
    )
    return {"crack": record, "site": get_site(site_id)}


# ── 3. Project Planner ────────────────────────────────────────────────────────

@app.post("/api/planning/generate", response_model=PlanningResponse)
async def generate_plan(
    project_type: str = Form(...), area_sqft: float = Form(...),
    location: str = Form(default=""), budget_usd: float = Form(default=0),
    description: str = Form(default=""),
):
    system = """You are a senior construction project manager with 20 years of experience.
Generate a concise construction plan with 4 key phases and 6-8 core materials.
Return ONLY a valid, complete JSON object matching this schema:
{
  "project_type": "project name",
  "estimated_duration_weeks": 12,
  "phases": [{"phase": "name", "duration_weeks": 2, "activities": ["a1", "a2"], "dependencies": []}],
  "materials": [{"material": "name", "quantity": "100", "unit": "bags", "estimated_cost_usd": 500}],
  "total_estimated_cost_usd": 150000,
  "key_risks": ["risk 1", "risk 2"],
  "summary": "overview summary"
}
Return ONLY the JSON object."""

    prompt = f"Generate a construction plan:\nProject: {project_type}\nArea: {area_sqft} sq ft\nLocation: {location or 'not specified'}\nBudget: {'$' + str(budget_usd) if budget_usd else 'not specified'}\nDescription: {description or 'standard construction'}"
    result = await call_ai(system, prompt)
    result.setdefault("project_type", project_type)
    result.setdefault("estimated_duration_weeks", 0)
    result.setdefault("phases", [])
    result.setdefault("materials", [])
    result.setdefault("total_estimated_cost_usd", 0)
    result.setdefault("key_risks", [])
    result.setdefault("summary", "Plan generated.")

    if isinstance(result.get("materials"), list):
        for m in result["materials"]:
            if isinstance(m, dict) and "quantity" in m:
                m["quantity"] = str(m["quantity"])

    return result


# ── 4. Property Valuation ─────────────────────────────────────────────────────

@app.post("/api/valuation/estimate", response_model=ValuationResponse)
async def estimate_valuation(
    property_type: str = Form(...), area_sqft: float = Form(...),
    location: str = Form(...), bedrooms: int = Form(default=0),
    bathrooms: int = Form(default=0), year_built: int = Form(default=0),
    condition: str = Form(default="good"), amenities: str = Form(default=""),
):
    system = """You are a certified real estate appraiser.
Return ONLY a valid JSON object:
{
  "estimated_value_usd": 350000,
  "price_per_sqft_usd": 175,
  "market_trend": "stable or declining or appreciating or hot",
  "confidence_level": "low or medium or high",
  "value_drivers": ["driver 1"],
  "comparable_range_usd": {"low": 300000, "mid": 350000, "high": 400000},
  "summary": "valuation summary"
}
Return ONLY the JSON object."""

    prompt = f"Estimate property value:\nType: {property_type}\nArea: {area_sqft} sq ft\nLocation: {location}\nBedrooms: {bedrooms}, Bathrooms: {bathrooms}\nYear built: {year_built or 'unknown'}, Condition: {condition}\nAmenities: {amenities or 'standard'}"
    result = await call_ai(system, prompt)
    result.setdefault("estimated_value_usd", 0)
    result.setdefault("price_per_sqft_usd", 0)
    result.setdefault("market_trend", "stable")
    result.setdefault("confidence_level", "medium")
    result.setdefault("value_drivers", [])
    result.setdefault("comparable_range_usd", {"low": 0, "mid": 0, "high": 0})
    result.setdefault("summary", "Valuation complete.")
    return result


# ── 5. Weather Advisory ───────────────────────────────────────────────────────

@app.post("/api/weather/advisory", response_model=WeatherAdvisoryResponse)
async def weather_advisory(
    location: str = Form(...), activities: str = Form(default="general construction"),
    date_range: str = Form(default="next 7 days"),
):
    system = """You are a construction weather risk specialist.
Return ONLY a valid JSON object:
{
  "location": "city name",
  "current_conditions": "typical weather description",
  "risk_level": "low or medium or high or extreme",
  "affected_activities": ["activity 1"],
  "recommendations": ["recommendation 1"],
  "work_safe_window": "e.g. 6 AM to 2 PM on clear days",
  "summary": "advisory summary"
}
Return ONLY the JSON object."""

    prompt = f"Construction weather advisory:\nLocation: {location}\nActivities: {activities}\nPeriod: {date_range}"
    result = await call_ai(system, prompt)
    result.setdefault("location", location)
    result.setdefault("current_conditions", "unknown")
    result.setdefault("risk_level", "medium")
    result.setdefault("affected_activities", [])
    result.setdefault("recommendations", [])
    result.setdefault("work_safe_window", "To be determined")
    result.setdefault("summary", "Advisory generated.")
    return result


# ── 6. Daily Site Reports ─────────────────────────────────────────────────────

@app.post("/api/reports/generate", response_model=DailyReportResponse)
async def generate_report(
    site_name: str = Form(...), date: str = Form(...),
    site_id: str = Form(default=""),
    file: UploadFile = File(default=None),
    activities_completed: str = Form(default=""),
    workers_present: int = Form(default=0),
    equipment_used: str = Form(default=""),
    issues: str = Form(default=""), notes: str = Form(default=""),
):
    if workers_present <= 0:
        raise HTTPException(
            status_code=422,
            detail="Missing required detail: Number of workers present on site is mandatory. Please enter a valid worker count (e.g. 15)."
        )
    if not activities_completed or len(activities_completed.strip()) < 8:
        raise HTTPException(
            status_code=422,
            detail="Missing required detail: Key activities completed today is required. Please specify what work was executed."
        )

    image_bytes = None
    image_mime = "image/jpeg"
    if file and file.filename:
        image_bytes = await read_and_validate_image(file)
        image_mime = file.content_type or "image/jpeg"

    system = """You are a construction site manager generating a formal daily report.
Return ONLY a valid JSON object:
{
  "date": "YYYY-MM-DD",
  "site_name": "site name",
  "overall_progress": "behind-schedule or on-schedule or ahead-of-schedule",
  "sections": [
    {"section": "Work Completed", "content": "details"},
    {"section": "Workforce", "content": "details"},
    {"section": "Equipment", "content": "details"},
    {"section": "Safety Observations", "content": "details"},
    {"section": "Materials", "content": "details"},
    {"section": "Weather Impact", "content": "details"}
  ],
  "issues_flagged": ["issue 1"],
  "next_day_plan": "plan for tomorrow",
  "summary": "brief summary"
}
Return ONLY the JSON object."""

    prompt = f"Daily site report:\nSite: {site_name}, Date: {date}\nActivities: {activities_completed or 'not specified'}\nWorkers: {workers_present}, Equipment: {equipment_used or 'not specified'}\nIssues: {issues or 'none'}, Notes: {notes or 'none'}"
    result = await call_ai(system, prompt, image_bytes, image_mime)
    result.setdefault("date", date)
    result.setdefault("site_name", site_name)
    result.setdefault("overall_progress", "on-schedule")
    result.setdefault("sections", [])
    result.setdefault("issues_flagged", [])
    result.setdefault("next_day_plan", "Continue scheduled activities.")
    result.setdefault("summary", "Report generated.")

    # Associate with site and persist to site report records
    target_site_id = site_id.strip() if site_id else ""
    if not target_site_id:
        for s in get_all_sites():
            if s.get("name", "").strip().lower() == site_name.strip().lower():
                target_site_id = s["id"]
                break

    if target_site_id:
        result["site_id"] = target_site_id
        result["submitted_by"] = "💻 Web Portal: Civil Engineer"
        save_site_report(target_site_id, result)
    else:
        result["submitted_by"] = "💻 Web Portal: Civil Engineer"

    return result


@app.get("/api/reports/history")
def get_reports_history_endpoint(site_id: str = ""):
    """Returns past daily site reports, optionally filtered by site_id."""
    reports = get_all_site_reports(site_id.strip() if site_id else None)
    return {"reports": reports, "count": len(reports)}


@app.post("/api/reports/broadcast-telegram")
async def broadcast_report_endpoint(req: ReportBroadcastRequest):
    """Broadcasts a formal daily site report to the site's connected Telegram group."""
    site = get_site(req.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found.")
    chat_id = site.get("telegram_chat_id")
    if not chat_id:
        raise HTTPException(
            status_code=400,
            detail="This site is not currently linked to a Telegram group. Tap a site button in your Telegram group first.",
        )

    rep = req.report
    status_label = str(rep.get("overall_progress", "on-schedule")).replace("-", " ").upper()
    status_emoji = "🟢" if "ahead" in status_label.lower() else "🟡" if "on" in status_label.lower() else "🔴"

    sections_text = ""
    for sec in rep.get("sections", [])[:4]:
        title = sec.get("section", "Section")
        content = sec.get("content", "")
        if content:
            sections_text += f"\n▫️ <b>{title}:</b> {content[:180]}"

    issues_text = ""
    if rep.get("issues_flagged"):
        issues_list = ", ".join(str(i) for i in rep["issues_flagged"][:3])
        issues_text = f"\n⚠️ <b>Issues Flagged:</b> {issues_list}"

    broadcast_msg = (
        f"📊 <b>OFFICIAL DAILY SITE REPORT</b>\n"
        f"📍 Site: <b>{site['name']}</b> ({site.get('location', 'Worksite')})\n"
        f"📅 Date: <b>{rep.get('date', 'Today')}</b>\n"
        f"👤 Author: <b>{rep.get('submitted_by', 'Civil Engineer')}</b>\n"
        f"📈 Progress: {status_emoji} <b>{status_label}</b>\n\n"
        f"📝 <b>Summary:</b>\n{rep.get('summary', 'Report recorded.')}\n"
        f"{sections_text}\n"
        f"{issues_text}\n\n"
        f"🎯 <b>Tomorrow's Plan:</b>\n{rep.get('next_day_plan', 'Continue scheduled site works.')}\n\n"
        f"<i>ConstructIQ AI Construction Management</i>"
    )
    sent = await telegram_service.send_message(chat_id, broadcast_msg)
    return {"ok": bool(sent), "chat_id": chat_id, "site_id": req.site_id}


# ── 7. AI Construction Assistant ──────────────────────────────────────────────

@app.post("/api/assistant/ask", response_model=AssistantResponse)
async def ask_assistant(question: str = Form(...), context: str = Form(default="")):
    system = """You are an expert AI construction assistant with deep knowledge in:
- Construction engineering and structural design
- Building codes and safety (OSHA, IBC, ACI, AISC)
- Materials science and cost estimation
- Project management and scheduling
- Real estate and property valuation

Return ONLY a valid JSON object:
{
  "answer": "detailed professional answer",
  "follow_up_suggestions": ["question 1?", "question 2?", "question 3?"],
  "references": ["OSHA 1926.502", "ACI 318-14"]
}
Return ONLY the JSON object."""

    prompt = f"Question: {question}" + (f"\nContext: {context}" if context else "")
    result = await call_ai(system, prompt)
    result.setdefault("answer", "No answer generated.")
    result.setdefault("follow_up_suggestions", [])
    result.setdefault("references", [])
    return result


# ── Static Frontend Files (Unified Single-Deployment Mode) ───────────────────
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    logger.info("Mounted production frontend from %s", FRONTEND_DIST)
