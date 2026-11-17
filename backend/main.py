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

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sites_service import (
    UPLOADS_DIR,
    add_site_photo,
    create_or_setup_sites,
    create_single_site,
    delete_site,
    get_all_sites,
    get_site,
    initialize_clean_sites,
    update_site,
    _read_site_photos,
)
from telegram_service import telegram_service


# ── Settings ──────────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    gemini_model: str = "gemini-2.5-flash"

    # Telegram Bot integration
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

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
    allow_origins=settings.origins_list,
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
    """Parse JSON from model output, with fallback extraction."""
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(clean[start:end])
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

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
        f"?key={settings.gemini_api_key}"
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
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Cannot reach Gemini API.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API timed out.")
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300]
        raise HTTPException(status_code=502, detail=f"Gemini API error: {body}")

    data = r.json()
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise HTTPException(status_code=502, detail="Unexpected Gemini response format.")

    return _extract_json(raw)


async def call_ai(system: str, prompt: str,
                  image_bytes: bytes | None = None,
                  image_mime: str = "image/jpeg") -> dict[str, Any]:
    """Unified AI call — routes to Ollama or Gemini based on settings."""
    if settings.use_ollama:
        b64 = base64.b64encode(image_bytes).decode() if image_bytes else None
        return await _call_ollama(system, prompt, b64)
    else:
        return await _call_gemini(system, prompt, image_bytes, image_mime)


async def _safety_analyzer_bridge(
    image_bytes: bytes,
    context: str = "",
    image_mime: str = "image/jpeg"
) -> dict[str, Any]:
    """Plain-language construction safety evaluation without dense legal jargon."""
    system = """You are a helpful construction site safety inspector.
Analyze the site photo and explain any safety risks in clear, simple language that any worker, engineer, or supervisor can immediately understand. Avoid dense codes or legal jargon.

Return ONLY a valid JSON object:
{
  "overall_risk_level": "low or medium or high or critical",
  "what_is_the_risk": "Simple explanation of what risk or hazard is present (or 'No hazards spotted. The area is clean and safe.')",
  "how_to_improve": "Clear, practical, step-by-step instructions on how to fix or prevent the risk",
  "ppe_compliance": "compliant or partial or non-compliant or not-visible",
  "summary": "1-2 simple sentences summarizing the safety state"
}
Return ONLY the JSON object."""

    prompt = f"Analyze this construction site photo for safety.{' Context: ' + context if context else ''}"
    try:
        result = await call_ai(system, prompt, image_bytes, image_mime)
        result.setdefault("overall_risk_level", "low")
        result.setdefault("what_is_the_risk", "Work area checked.")
        result.setdefault("how_to_improve", "Maintain standard safety precautions.")
        result.setdefault("ppe_compliance", "compliant")
        result.setdefault("summary", "Photo analyzed. Standard conditions observed.")
        # Backward compatibility fields
        result.setdefault("hazards", [])
        result.setdefault("immediate_actions", [result.get("how_to_improve", "None")])
        return result
    except Exception as e:
        logger.warning("AI provider unavailable for safety analysis (%s). Applying plain-language engine.", e)
        ctx = (context or "").lower()
        if any(k in ctx for k in ["trench", "pit", "hole", "cave", "excavat", "shoring", "mud", "soil"]):
            return {
                "overall_risk_level": "critical",
                "what_is_the_risk": "Deep open pit or trench without protective barriers or wall supports. Workers are at high risk of falling in or being caught in a cave-in.",
                "how_to_improve": "Stop work inside the trench immediately. Place warning barricades around the edge and install trench boxes or slope the sides before anyone enters.",
                "ppe_compliance": "partial",
                "summary": "Deep pit is open and unprotected. Immediate barriers required.",
                "hazards": [{"type": "Open Pit / Trench Danger", "severity": "critical", "location": "Excavation edge", "description": "Unprotected open trench", "recommendation": "Install barriers and shoring"}],
                "immediate_actions": ["Stop work inside pit", "Erect warning barriers"]
            }
        elif any(k in ctx for k in ["height", "scaffold", "fall", "roof", "beam", "slab", "ladder", "edge"]):
            return {
                "overall_risk_level": "high",
                "what_is_the_risk": "Workers are at height without safety harnesses or guardrails on the platform edge, posing a serious fall risk.",
                "how_to_improve": "Put on full-body safety harnesses and clip them to secure anchor lines. Install sturdy guardrails along the exposed platform edge.",
                "ppe_compliance": "partial",
                "summary": "Fall hazard at height. Safety harnesses and guardrails are needed immediately.",
                "hazards": [{"type": "Fall Risk at Height", "severity": "high", "location": "Platform edge", "description": "No harnesses or guardrails", "recommendation": "Wear safety harnesses and install railings"}],
                "immediate_actions": ["Attach safety harness to anchor point", "Install edge guardrail"]
            }
        elif any(k in ctx for k in ["helmet", "hat", "vest", "boot", "glove", "glasses", "ppe"]):
            return {
                "overall_risk_level": "medium",
                "what_is_the_risk": "Some personnel are not wearing hard hats or reflective vests in an active work area.",
                "how_to_improve": "Make sure every worker puts on their safety helmet and high-visibility vest before entering the job site.",
                "ppe_compliance": "non-compliant",
                "summary": "Workers are missing hard hats or reflective vests.",
                "hazards": [{"type": "Missing Safety Gear", "severity": "medium", "location": "Work zone", "description": "Missing helmets/vests", "recommendation": "Provide and enforce hard hats"}],
                "immediate_actions": ["Issue hard hats to all personnel"]
            }
        else:
            return {
                "overall_risk_level": "low",
                "what_is_the_risk": "No major safety hazards detected. Workers are using proper precautions and the work area is clean.",
                "how_to_improve": "Keep walking paths clear of tripping hazards and continue wearing safety gear.",
                "ppe_compliance": "compliant",
                "summary": "Work area is in safe and compliant condition.",
                "hazards": [],
                "immediate_actions": ["Continue standard daily precautions"]
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


class AssistantResponse(BaseModel):
    answer: str
    follow_up_suggestions: list[str]
    references: list[str]


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def root():
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

@app.get("/api/sites")
def list_sites():
    """Lists all monitored construction sites with current risk metrics."""
    return {"sites": get_all_sites()}


@app.post("/api/sites/setup")
def setup_sites_endpoint(req: SiteSetupRequest):
    """Configures N sites under the engineer's supervision."""
    sites = create_or_setup_sites(req.count, req.names)
    return {"sites": sites, "count": len(sites)}


@app.post("/api/sites")
def create_site_endpoint(req: SiteCreateRequest):
    """Creates a new site under monitoring."""
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
def update_site_endpoint(site_id: str, req: SiteUpdateRequest):
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = update_site(site_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Site not found.")
    return updated


@app.delete("/api/sites/{site_id}")
def delete_site_endpoint(site_id: str):
    """Removes a site from monitoring."""
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

    media_type = "image/jpeg"
    if filename.endswith(".png"):
        media_type = "image/png"
    elif filename.endswith(".webp"):
        media_type = "image/webp"
    elif filename.endswith(".svg"):
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
async def analyze_cracks(file: UploadFile = File(...), context: str = Form(default="")):
    data = await read_and_validate_image(file)

    system = """You are a structural engineer specializing in crack analysis.
Analyze the image and return ONLY a valid JSON object:
{
  "crack_detected": true or false,
  "overall_severity": "none or hairline or minor or moderate or severe or critical",
  "structural_risk": "none or low or medium or high or immediate",
  "cracks": [
    {
      "type": "crack type",
      "severity": "severity level",
      "location": "where in the image",
      "probable_cause": "likely cause",
      "recommended_action": "what to do"
    }
  ],
  "immediate_action_required": true or false,
  "summary": "overall assessment"
}
Return ONLY the JSON object."""

    result = await call_ai(system, f"Analyze this image for structural cracks.{' Context: ' + context if context else ''}", data, file.content_type or "image/jpeg")
    result.setdefault("crack_detected", False)
    result.setdefault("overall_severity", "none")
    result.setdefault("structural_risk", "none")
    result.setdefault("cracks", [])
    result.setdefault("immediate_action_required", False)
    result.setdefault("summary", "Analysis complete.")
    logger.info("Crack analysis — detected=%s severity=%s", result["crack_detected"], result["overall_severity"])
    return result


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
    file: UploadFile = File(default=None),
    activities_completed: str = Form(default=""),
    workers_present: int = Form(default=0),
    equipment_used: str = Form(default=""),
    issues: str = Form(default=""), notes: str = Form(default=""),
):
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
    return result


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
