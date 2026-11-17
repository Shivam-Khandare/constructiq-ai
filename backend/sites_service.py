"""
ConstructIQ AI — Multi-Site Safety & Civil Engineering Storage Service
Manages construction sites, customizable names, photo albums, and Telegram group bindings.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("constructiq.sites")

DATA_DIR = Path(__file__).resolve().parent / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
SITES_FILE = DATA_DIR / "sites.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _read_sites_db() -> dict[str, Any]:
    if not SITES_FILE.exists():
        return {"sites": [], "last_updated": datetime.now(timezone.utc).isoformat()}
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Error reading sites.json: %s", e)
        return {"sites": [], "last_updated": datetime.now(timezone.utc).isoformat()}


def _write_sites_db(db: dict[str, Any]) -> None:
    db["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def get_site_photos_file(site_id: str) -> Path:
    return DATA_DIR / f"photos_{site_id}.json"


def _read_site_photos(site_id: str) -> list[dict[str, Any]]:
    p = get_site_photos_file(site_id)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Error reading photos for %s: %s", site_id, e)
        return []


def _write_site_photos(site_id: str, photos: list[dict[str, Any]]) -> None:
    p = get_site_photos_file(site_id)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(photos, f, indent=2, ensure_ascii=False)


def initialize_clean_sites(count: int = 5) -> list[dict[str, Any]]:
    """Initializes clean sites with customizable names. Only called when no sites exist at all."""
    effective_count = max(1, min(count, 30))
    sites = []

    for idx in range(effective_count):
        num = idx + 1
        site_id = f"site-{num}"
        site_record = {
            "id": site_id,
            "name": f"Site {num}",
            "location": f"Sector {num} Worksite",
            "supervisor": f"Site Supervisor {num}",
            "telegram_chat_id": "",  # Empty until connected to a Telegram group
            "telegram_tag": f"site{num}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "overall_risk": "none",
            "ppe_compliance": "pending",
            "photo_count": 0,
            "latest_photo": None,
        }
        # Only reset photo file if it doesn't already exist
        photos_file = get_site_photos_file(site_id)
        if not photos_file.exists():
            _write_site_photos(site_id, [])
        sites.append(site_record)

    _write_sites_db({"sites": sites})
    logger.info("Initialized %d clean sites", len(sites))
    return sites


def get_all_sites() -> list[dict[str, Any]]:
    db = _read_sites_db()
    sites = db.get("sites", [])
    if not sites:
        sites = initialize_clean_sites(5)
    return sites


def get_site(site_id: str) -> dict[str, Any] | None:
    sites = get_all_sites()
    for s in sites:
        if s["id"] == site_id:
            return s
    return None


def _all_tg_variants(cid: Any) -> set[str]:
    """Returns all possible representations of a Telegram chat ID for matching.
    Handles: plain int, negative group, -100 supergroup prefix variants."""
    s = str(cid).strip()
    # Strip all leading dashes and -100 prefix to get the bare number
    bare = s.lstrip("-")
    if bare.startswith("100"):
        bare = bare[3:]
    variants = {
        s,                   # exact as-is
        bare,                # bare number e.g. 5569776630
        f"-{bare}",          # e.g. -5569776630
        f"-100{bare}",       # e.g. -1005569776630 (supergroup)
    }
    return variants


def get_site_by_telegram_chat(chat_id: str | int) -> dict[str, Any] | None:
    """Finds the site linked to a Telegram group chat ID.
    Handles supergroup ID conversion (Telegram adds -100 prefix when groups upgrade)."""
    incoming_variants = _all_tg_variants(chat_id)
    sites = get_all_sites()
    for s in sites:
        stored = str(s.get("telegram_chat_id", "")).strip()
        if not stored:
            continue
        stored_variants = _all_tg_variants(stored)
        if incoming_variants & stored_variants:  # any overlap = match
            logger.debug(
                "chat_id %s matched site %s (stored=%s)",
                chat_id, s["id"], stored,
            )
            return s
    return None


def create_or_setup_sites(count: int, site_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Configures the exact number of sites requested by the civil engineer.
    Preserves existing telegram_chat_id bindings and photo data."""
    effective_count = max(1, min(count, 30))

    # Load existing sites so we can preserve telegram bindings
    existing_db = _read_sites_db()
    existing_map: dict[str, dict[str, Any]] = {
        s["id"]: s for s in existing_db.get("sites", [])
    }

    sites = []
    for idx in range(effective_count):
        num = idx + 1
        site_id = f"site-{num}"
        custom_name = (
            site_names[idx].strip()
            if (site_names and idx < len(site_names) and site_names[idx].strip())
            else existing_map.get(site_id, {}).get("name", f"Site {num}")
        )

        # Preserve telegram binding if already set
        existing = existing_map.get(site_id, {})
        existing_chat_id = existing.get("telegram_chat_id", "")

        # Check existing photos
        existing_photos = _read_site_photos(site_id)
        latest = existing_photos[-1] if existing_photos else None
        analysis = latest.get("safety_analysis", {}) if latest else {}

        site_record = {
            "id": site_id,
            "name": custom_name,
            "location": existing.get("location", f"Zone {num}"),
            "supervisor": existing.get("supervisor", f"Site In-Charge {num}"),
            "telegram_chat_id": existing_chat_id,  # ← PRESERVED
            "telegram_tag": f"site{num}",
            "created_at": existing.get("created_at", datetime.now(timezone.utc).isoformat()),
            "status": existing.get("status", "active"),
            "overall_risk": analysis.get("overall_risk_level", existing.get("overall_risk", "none")),
            "ppe_compliance": analysis.get("ppe_compliance", existing.get("ppe_compliance", "pending")),
            "photo_count": len(existing_photos),
            "latest_photo": latest,
        }
        sites.append(site_record)

    _write_sites_db({"sites": sites})
    return sites


def create_single_site(name: str, location: str = "", supervisor: str = "", telegram_chat_id: str = "") -> dict[str, Any]:
    """Adds a new site to the project."""
    db = _read_sites_db()
    existing = db.get("sites", [])
    num = len(existing) + 1
    site_id = f"site-{num}"
    
    # Ensure unique id
    while any(s["id"] == site_id for s in existing):
        num += 1
        site_id = f"site-{num}"

    new_site = {
        "id": site_id,
        "name": name or f"Site {num}",
        "location": location or f"Worksite {num}",
        "supervisor": supervisor or "Site Supervisor",
        "telegram_chat_id": telegram_chat_id,
        "telegram_tag": f"site{num}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "overall_risk": "none",
        "ppe_compliance": "pending",
        "photo_count": 0,
        "latest_photo": None,
    }
    existing.append(new_site)
    _write_sites_db(db)
    _write_site_photos(site_id, [])
    return new_site


def update_site(site_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Updates site metadata (name, location, supervisor, telegram_chat_id)."""
    db = _read_sites_db()
    for i, s in enumerate(db.get("sites", [])):
        if s["id"] == site_id:
            for k in ["name", "location", "supervisor", "telegram_chat_id", "telegram_tag", "status"]:
                if k in data and data[k] is not None:
                    s[k] = str(data[k]).strip()
            db["sites"][i] = s
            _write_sites_db(db)
            return s
    return None


def delete_site(site_id: str) -> bool:
    """Removes a site and archives its records."""
    db = _read_sites_db()
    original_len = len(db.get("sites", []))
    db["sites"] = [s for s in db.get("sites", []) if s["id"] != site_id]
    if len(db["sites"]) < original_len:
        _write_sites_db(db)
        return True
    return False


def add_site_photo(
    site_id: str,
    file_bytes: bytes,
    filename: str,
    sender: str,
    caption: str,
    safety_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Adds an uploaded or Telegram-received photo to a site's album."""
    site = get_site(site_id)
    if not site:
        site = create_single_site(name=f"Site #{site_id.replace('site-', '')}")

    ext = Path(filename).suffix.lower() or ".jpg"
    unique_name = f"{site_id}_{uuid.uuid4().hex[:10]}{ext}"
    dest_path = UPLOADS_DIR / unique_name
    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    photo_record = {
        "id": f"photo-{uuid.uuid4().hex[:8]}",
        "site_id": site_id,
        "filename": unique_name,
        "url": f"/api/sites/{site_id}/images/{unique_name}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender": sender or "Telegram Photo",
        "caption": caption or "Site inspection photo",
        "safety_analysis": safety_analysis,
    }

    # Append to photos
    photos = _read_site_photos(site_id)
    photos.append(photo_record)
    _write_site_photos(site_id, photos)

    # Update site metrics
    risk = safety_analysis.get("overall_risk_level", "low")
    ppe = safety_analysis.get("ppe_compliance", "compliant")
    
    site["overall_risk"] = risk
    site["ppe_compliance"] = ppe
    site["photo_count"] = len(photos)
    site["latest_photo"] = photo_record

    db = _read_sites_db()
    for i, s in enumerate(db.get("sites", [])):
        if s["id"] == site_id:
            db["sites"][i] = site
            break
    _write_sites_db(db)

    logger.info("Added photo to %s (%s). Risk=%s", site_id, site["name"], risk)
    return photo_record
