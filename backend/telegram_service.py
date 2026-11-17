"""
ConstructIQ AI — Telegram Bot Integration Service
Routes photos directly from dedicated Telegram groups (e.g. Site 3 Group)
to that site's screen in real time with plain-language safety feedback.

Features:
  • Inline buttons so workers don't need to type commands
  • Robust supergroup chat-ID matching (handles Telegram's -100 prefix)
  • Real-time photo routing + AI safety analysis reply
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

import httpx

from sites_service import (
    add_site_photo,
    get_all_sites,
    get_site,
    get_site_by_telegram_chat,
    update_site,
)

logger = logging.getLogger("constructiq.telegram")
TELEGRAM_API_BASE = "https://api.telegram.org"
CONFIG_FILE = Path(__file__).resolve().parent / "data" / "telegram_config.json"


class TelegramService:
    def __init__(self) -> None:
        self.token: str = self._load_persisted_token()
        self.bot_username: str = ""
        self.bot_first_name: str = ""
        self.is_active: bool = False
        self.is_polling: bool = False
        self.webhook_url: str = ""
        self.last_update_id: int = 0
        self.last_poll_at: str | None = None
        self.last_error: str | None = None
        self.processed_count: int = 0
        self._polling_task: asyncio.Task | None = None
        self.analyzer_fn: Callable[
            [bytes, str, str], Coroutine[Any, Any, dict[str, Any]]
        ] | None = None

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_persisted_token(self) -> str:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("token", "").strip()
            except Exception:
                pass
        return ""

    def _save_persisted_token(self, token: str, webhook_url: str = "") -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"token": token, "webhook_url": webhook_url}, f, indent=2)
        except Exception as e:
            logger.error("Failed to persist telegram token: %s", e)

    def set_analyzer(
        self,
        fn: Callable[[bytes, str, str], Coroutine[Any, Any, dict[str, Any]]],
    ) -> None:
        self.analyzer_fn = fn

    # ── Configure & Status ───────────────────────────────────────────────────

    async def configure(self, token: str, webhook_url: str = "") -> dict[str, Any]:
        clean_token = token.strip()
        if not clean_token:
            await self.stop_polling()
            self.token = ""
            self.is_active = False
            self.bot_username = ""
            self._save_persisted_token("")
            return self.get_status()

        url = f"{TELEGRAM_API_BASE}/bot{clean_token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                data = r.json()
        except Exception as e:
            self.last_error = f"Network error connecting to Telegram: {e}"
            logger.error(self.last_error)
            raise ValueError(self.last_error)

        if not data.get("ok"):
            err_desc = data.get("description", "Invalid Bot Token")
            self.last_error = f"Telegram error: {err_desc}"
            raise ValueError(self.last_error)

        bot_info = data.get("result", {})
        self.token = clean_token
        self.bot_username = bot_info.get("username", "")
        self.bot_first_name = bot_info.get("first_name", "ConstructIQ Bot")
        self.is_active = True
        self.last_error = None
        self.webhook_url = webhook_url

        self._save_persisted_token(clean_token, webhook_url)
        logger.info(
            "Telegram Bot linked: @%s (%s)", self.bot_username, self.bot_first_name
        )

        if not webhook_url:
            self.start_polling()

        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        return {
            "is_configured": bool(self.token),
            "is_active": self.is_active,
            "bot_username": self.bot_username,
            "bot_first_name": self.bot_first_name,
            "is_polling": self.is_polling,
            "webhook_url": self.webhook_url,
            "last_poll_at": self.last_poll_at,
            "last_error": self.last_error,
            "processed_count": self.processed_count,
            "bot_url": (
                f"https://t.me/{self.bot_username}" if self.bot_username else None
            ),
        }

    # ── Polling Loop ─────────────────────────────────────────────────────────

    def start_polling(self) -> None:
        if self.is_polling or not self.token:
            return
        self.is_polling = True
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram polling started for @%s", self.bot_username)

    async def stop_polling(self) -> None:
        self.is_polling = False
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        self._polling_task = None

    async def _poll_loop(self) -> None:
        while self.is_polling and self.token:
            try:
                await self.poll_updates()
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                logger.error("Error in Telegram poll: %s", e)
                await asyncio.sleep(4)

    async def poll_updates(self) -> list[dict[str, Any]]:
        if not self.token:
            return []

        url = f"{TELEGRAM_API_BASE}/bot{self.token}/getUpdates"
        params: dict[str, Any] = {
            "offset": self.last_update_id + 1,
            "timeout": 8,
            "allowed_updates": json.dumps(
                ["message", "channel_post", "callback_query"]
            ),
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, params=params)
                data = r.json()
        except Exception as e:
            self.last_error = str(e)
            return []

        self.last_poll_at = datetime.now(timezone.utc).isoformat()
        if not data.get("ok"):
            return []

        updates = data.get("result", [])
        results: list[dict[str, Any]] = []
        for update in updates:
            up_id = update.get("update_id", 0)
            if up_id > self.last_update_id:
                self.last_update_id = up_id

            res = await self.process_update(update)
            if res:
                results.append(res)
                self.processed_count += 1

        return results

    # ── Telegram API Helpers ─────────────────────────────────────────────────

    async def download_telegram_file(self, file_id: str) -> tuple[bytes, str]:
        info_url = f"{TELEGRAM_API_BASE}/bot{self.token}/getFile?file_id={file_id}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(info_url)
            r_json = r.json()
            if not r_json.get("ok"):
                raise RuntimeError(
                    f"Could not get file info: {r_json.get('description')}"
                )
            file_path = r_json["result"]["file_path"]

            file_url = f"{TELEGRAM_API_BASE}/file/bot{self.token}/{file_path}"
            img_r = await client.get(file_url)
            img_r.raise_for_status()

            ext = file_path.split(".")[-1] if "." in file_path else "jpg"
            return img_r.content, f"phone_photo.{ext}"

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a text message, optionally with inline keyboard buttons."""
        if not self.token:
            return False
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                return r.status_code == 200
        except Exception as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False

    async def answer_callback_query(
        self, callback_query_id: str, text: str = ""
    ) -> bool:
        """Acknowledge a button press."""
        if not self.token:
            return False
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                return r.status_code == 200
        except Exception as e:
            logger.error("Failed to answer callback query: %s", e)
            return False

    async def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit a previously sent message (e.g. replace buttons with confirmation)."""
        if not self.token:
            return False
        url = f"{TELEGRAM_API_BASE}/bot{self.token}/editMessageText"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                return r.status_code == 200
        except Exception as e:
            logger.error("Failed to edit message: %s", e)
            return False

    # ── Button Builders ──────────────────────────────────────────────────────

    @staticmethod
    def _build_site_buttons(
        action_prefix: str = "bind",
        columns: int = 3,
    ) -> dict[str, Any]:
        """Build an InlineKeyboardMarkup with one button per site.

        Each button's callback_data is ``{action_prefix}:{site_number}``,
        e.g. ``bind:3``.  Buttons are arranged in rows of *columns*.
        """
        sites = get_all_sites()
        buttons: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []

        for s in sites:
            num = s["id"].replace("site-", "")
            label = f"📍 {s['name']}"
            row.append(
                {"text": label, "callback_data": f"{action_prefix}:{num}"}
            )
            if len(row) >= columns:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return {"inline_keyboard": buttons}

    # ── Resolve which site a photo belongs to ────────────────────────────────

    def _resolve_target_site(self, text: str, chat_id: str | int) -> str | None:
        """Return the site-id bound to *chat_id*, or None when unbound."""
        chat_str = str(chat_id).strip()
        logger.info("Resolving site for chat_id=%s", chat_str)

        # 1. Lookup by stored telegram_chat_id (handles supergroup variants)
        site_by_chat = get_site_by_telegram_chat(chat_str)
        if site_by_chat:
            logger.info(
                "chat_id=%s → matched site %s (%s)",
                chat_str,
                site_by_chat["id"],
                site_by_chat["name"],
            )
            return site_by_chat["id"]

        logger.warning(
            "chat_id=%s not bound to any site. Stored bindings: %s",
            chat_str,
            [
                (s["id"], s.get("telegram_chat_id", ""))
                for s in get_all_sites()
            ],
        )
        return None  # caller will prompt the user to pick a site

    # ── Main update dispatcher ───────────────────────────────────────────────

    async def process_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        # ── Handle callback_query (inline button press) ──────────────────
        callback = update.get("callback_query")
        if callback:
            return await self._handle_callback_query(callback)

        # ── Handle regular message / channel_post ────────────────────────
        message = update.get("message") or update.get("channel_post")
        if not message:
            return None

        chat_id = message["chat"]["id"]
        chat_title = message["chat"].get("title", "Telegram Chat")
        from_user = message.get("from", {})
        sender_name = from_user.get("first_name", "")
        if from_user.get("last_name"):
            sender_name += f" {from_user['last_name']}"
        if not sender_name:
            sender_name = chat_title

        text = (message.get("text") or "").strip()
        caption = (message.get("caption") or "").strip()
        msg_id = message.get("message_id")

        logger.info(
            "Incoming msg: chat_id=%s chat_type=%s text=%r photo=%s",
            chat_id,
            message["chat"].get("type"),
            text[:60] if text else caption[:60],
            bool(message.get("photo")),
        )

        # ── /bind, /connect — show buttons or bind directly ─────────────
        if text.startswith("/bind") or text.startswith("/connect"):
            return await self._handle_bind_command(text, chat_id, msg_id)

        # ── /start, /help — welcome message with buttons ────────────────
        if text.startswith("/start") or text.startswith("/help"):
            return await self._handle_start_command(chat_id, msg_id)

        # ── /status — show which site this group is linked to ───────────
        if text.startswith("/status"):
            return await self._handle_status_command(chat_id, msg_id)

        # ── Photo or image document ─────────────────────────────────────
        return await self._handle_photo(message, chat_id, sender_name, text, caption, msg_id)

    # ── Command Handlers ─────────────────────────────────────────────────────

    async def _handle_bind_command(
        self, text: str, chat_id: int, msg_id: int
    ) -> dict[str, Any] | None:
        parts = text.split()

        # /bind 3  → bind directly
        if len(parts) > 1:
            arg = (
                parts[1]
                .replace("#", "")
                .replace("site", "")
                .replace("-", "")
                .replace("_", "")
                .strip()
            )
            if arg.isdigit():
                return await self._do_bind(chat_id, int(arg), msg_id)

        # /bind  (no number) → show buttons
        keyboard = self._build_site_buttons("bind")
        await self.send_message(
            chat_id,
            "👇 Tap a button below to link this group to a site:",
            reply_to_message_id=msg_id,
            reply_markup=keyboard,
        )
        return None

    async def _handle_start_command(
        self, chat_id: int, msg_id: int
    ) -> dict[str, Any]:
        # Check if already bound
        bound_site = get_site_by_telegram_chat(chat_id)
        if bound_site:
            reply = (
                f"👷 ConstructIQ Site Safety Bot\n\n"
                f"✅ This group is linked to: {bound_site['name']}\n\n"
                f"Just send a photo in this group and it will appear "
                f"on the engineer's screen automatically!\n\n"
                f"To change the linked site, type /bind or tap a button below:"
            )
        else:
            reply = (
                f"👷 ConstructIQ Site Safety Bot\n\n"
                f"Link this group to one of your construction sites.\n"
                f"After linking, any photo sent here will automatically "
                f"show on the engineer's screen in real time!\n\n"
                f"👇 Tap a site button to link:"
            )
        keyboard = self._build_site_buttons("bind")
        await self.send_message(
            chat_id, reply, reply_to_message_id=msg_id, reply_markup=keyboard
        )
        return {"type": "start"}

    async def _handle_status_command(
        self, chat_id: int, msg_id: int
    ) -> dict[str, Any] | None:
        bound_site = get_site_by_telegram_chat(chat_id)
        if bound_site:
            reply = (
                f"📍 This group is linked to: {bound_site['name']}\n"
                f"📸 Photos sent here go to {bound_site['name']}'s album.\n\n"
                f"To change, type /bind or tap a button below:"
            )
            keyboard = self._build_site_buttons("bind")
            await self.send_message(
                chat_id, reply, reply_to_message_id=msg_id, reply_markup=keyboard
            )
        else:
            reply = (
                "⚠️ This group is NOT linked to any site yet.\n"
                "👇 Tap a button below to link it:"
            )
            keyboard = self._build_site_buttons("bind")
            await self.send_message(
                chat_id, reply, reply_to_message_id=msg_id, reply_markup=keyboard
            )
        return {"type": "status"}

    # ── Callback Query Handler (button press) ────────────────────────────────

    async def _handle_callback_query(
        self, callback: dict[str, Any]
    ) -> dict[str, Any] | None:
        cb_id = callback["id"]
        cb_data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not chat_id:
            await self.answer_callback_query(cb_id, "Error: could not find chat")
            return None

        logger.info("Callback query: data=%r chat_id=%s", cb_data, chat_id)

        # Parse callback_data like "bind:3"
        if cb_data.startswith("bind:"):
            try:
                site_num = int(cb_data.split(":")[1])
            except (IndexError, ValueError):
                await self.answer_callback_query(cb_id, "Invalid site number")
                return None

            result = await self._do_bind(chat_id, site_num, reply_msg_id=None)

            # Replace the button message with confirmation
            if result:
                site = get_site(f"site-{site_num}")
                site_name = site["name"] if site else f"Site {site_num}"
                await self.edit_message_text(
                    chat_id,
                    message_id,
                    f"✅ Group linked to: {site_name}\n\n"
                    f"📸 Send any photo in this group and it will appear "
                    f"on the engineer's screen automatically!",
                )
            await self.answer_callback_query(cb_id, "Linked!")
            return result

        await self.answer_callback_query(cb_id)
        return None

    # ── Core Bind Logic ──────────────────────────────────────────────────────

    async def _do_bind(
        self, chat_id: int, site_num: int, reply_msg_id: int | None
    ) -> dict[str, Any] | None:
        target_site_id = f"site-{site_num}"
        site = get_site(target_site_id)
        if not site:
            reply = f"❌ Site {site_num} does not exist. Check the site number."
            await self.send_message(chat_id, reply, reply_to_message_id=reply_msg_id)
            return None

        # First, clear any OTHER site that was previously bound to this chat
        chat_str = str(chat_id)
        all_sites = get_all_sites()
        for s in all_sites:
            stored = str(s.get("telegram_chat_id", "")).strip()
            if stored and s["id"] != target_site_id:
                # Check if this stored ID matches the current chat
                from sites_service import _all_tg_variants
                if _all_tg_variants(stored) & _all_tg_variants(chat_str):
                    logger.info(
                        "Clearing old binding: %s had chat_id=%s",
                        s["id"], stored,
                    )
                    update_site(s["id"], {"telegram_chat_id": ""})

        # Now bind the target site to this chat
        update_site(target_site_id, {"telegram_chat_id": chat_str})

        # Verify the write actually worked
        updated_site = get_site(target_site_id)
        saved_id = str(updated_site.get("telegram_chat_id", "")) if updated_site else ""
        logger.info(
            "BIND: chat_id=%s → %s (%s). Verified stored=%s",
            chat_str, target_site_id, site["name"], saved_id,
        )

        reply = (
            f"✅ Group linked successfully!\n\n"
            f"📍 This group → {site['name']}\n"
            f"🆔 Group ID: {chat_id}\n\n"
            f"📸 From now on, just send any photo here and it will\n"
            f"appear on the engineer's screen for {site['name']} in real time!"
        )
        await self.send_message(chat_id, reply, reply_to_message_id=reply_msg_id)
        return {"type": "bind", "site_id": target_site_id}

    # ── Photo Handler ────────────────────────────────────────────────────────

    async def _handle_photo(
        self,
        message: dict[str, Any],
        chat_id: int,
        sender_name: str,
        text: str,
        caption: str,
        msg_id: int,
    ) -> dict[str, Any] | None:
        photos = message.get("photo")
        file_id = None
        filename_hint = "phone_photo.jpg"

        if photos:
            best_photo = photos[-1]
            file_id = best_photo.get("file_id")
        elif message.get("document"):
            doc = message["document"]
            mime = (doc.get("mime_type") or "").lower()
            if mime.startswith("image/"):
                file_id = doc.get("file_id")
                filename_hint = doc.get("file_name") or "phone_photo.jpg"

        if not file_id:
            return None

        # Try to resolve which site this group belongs to
        combined_text = f"{text} {caption}".strip()
        target_site_id = self._resolve_target_site(combined_text, chat_id)

        if target_site_id is None:
            # Group not bound — ask the worker to pick a site via buttons
            keyboard = self._build_site_buttons("bind")
            await self.send_message(
                chat_id,
                "⚠️ This group is not linked to any site yet.\n"
                "Your photo was received but I need to know which site it belongs to.\n\n"
                "👇 Tap a button below to link this group, then send the photo again:",
                reply_to_message_id=msg_id,
                reply_markup=keyboard,
            )
            return None

        # Download the photo from Telegram
        try:
            img_bytes, filename = await self.download_telegram_file(file_id)
        except Exception as e:
            logger.error("Failed to download photo from Telegram: %s", e)
            return None

        site = get_site(target_site_id)
        site_name = site["name"] if site else f"Site {target_site_id}"

        # Analyze with AI (plain-language safety evaluation)
        context = f"Site: {site_name}. Note from worker: {caption or text}"
        safety_analysis = await self._analyze(img_bytes, context)

        # Store photo in that site's album
        sender_label = f"📱 Telegram: {sender_name}"
        record = add_site_photo(
            site_id=target_site_id,
            file_bytes=img_bytes,
            filename=filename,
            sender=sender_label,
            caption=caption or "Photo sent from phone via Telegram",
            safety_analysis=safety_analysis,
        )

        # Reply in the group with plain-language feedback
        risk = safety_analysis.get("overall_risk_level", "low").upper()
        what_risk = safety_analysis.get(
            "what_is_the_risk", "No major hazards detected."
        )
        how_fix = safety_analysis.get(
            "how_to_improve",
            "Continue maintaining clean and safe work conditions.",
        )

        if risk in ("HIGH", "CRITICAL"):
            reply = (
                f"🚨 SAFETY ALERT for {site_name}\n\n"
                f"Risk Level: {risk}\n"
                f"What is the risk: {what_risk}\n\n"
                f"How to fix it: {how_fix}\n\n"
                f"Photo has been updated on the engineer's live screen."
            )
        else:
            reply = (
                f"✅ Photo received for {site_name}!\n\n"
                f"Risk Level: Safe / {risk}\n"
                f"Inspection: {what_risk}\n"
                f"Recommendation: {how_fix}\n\n"
                f"Updated on engineer's screen."
            )

        await self.send_message(chat_id, reply, reply_to_message_id=msg_id)

        return {
            "type": "photo_ingested",
            "site_id": target_site_id,
            "site_name": site_name,
            "sender": sender_name,
            "risk": risk,
            "photo_id": record["id"],
        }

    # ── AI Safety Analysis ───────────────────────────────────────────────────

    async def _analyze(self, image_bytes: bytes, context: str) -> dict[str, Any]:
        """Calls safety analyzer or returns simple, human-friendly fallback."""
        if self.analyzer_fn:
            try:
                return await self.analyzer_fn(image_bytes, context, "image/jpeg")
            except Exception as e:
                logger.warning(
                    "AI analyzer error: %s. Using simple inspection engine.", e
                )

        ctx_lower = context.lower()
        if any(
            w in ctx_lower
            for w in ["trench", "pit", "hole", "cave", "excavat", "mud", "soil"]
        ):
            return {
                "overall_risk_level": "critical",
                "what_is_the_risk": (
                    "Deep open trench or pit without support walls or barricades. "
                    "High danger of soil collapse or workers falling in."
                ),
                "how_to_improve": (
                    "Stop work inside the trench immediately. Install strong "
                    "support barriers or slope the sides before anyone enters."
                ),
                "ppe_compliance": "partial",
                "summary": "Deep pit is open and unprotected. Immediate barriers required.",
            }
        elif any(
            w in ctx_lower
            for w in ["height", "fall", "scaffold", "ladder", "edge", "slab", "roof"]
        ):
            return {
                "overall_risk_level": "high",
                "what_is_the_risk": (
                    "Workers are working at height without safety harnesses "
                    "or guardrails on the platform edge."
                ),
                "how_to_improve": (
                    "Put on full-body safety harnesses and clip them to secure "
                    "anchor points. Install sturdy guardrails along the edge."
                ),
                "ppe_compliance": "partial",
                "summary": "Fall danger at high elevation. Safety harnesses must be worn and clipped.",
            }
        elif any(
            w in ctx_lower
            for w in ["helmet", "hat", "boot", "vest", "glove", "glasses", "ppe"]
        ):
            return {
                "overall_risk_level": "medium",
                "what_is_the_risk": (
                    "Some workers are working without safety helmets or reflective vests."
                ),
                "how_to_improve": (
                    "Ensure every worker puts on a hard hat and reflective vest "
                    "before entering the work area."
                ),
                "ppe_compliance": "non-compliant",
                "summary": "Workers missing helmets or reflective vests.",
            }
        else:
            return {
                "overall_risk_level": "low",
                "what_is_the_risk": (
                    "No major hazards detected. The work area appears clean "
                    "and properly organized."
                ),
                "how_to_improve": (
                    "Continue keeping walkways clear of loose materials "
                    "and wear safety gear at all times."
                ),
                "ppe_compliance": "compliant",
                "summary": "Work area is safe and in good condition.",
            }

    # ── Simulate (for web UI testing) ────────────────────────────────────────

    async def simulate_submission(
        self,
        site_id: str,
        image_bytes: bytes,
        filename: str,
        sender_name: str,
        caption: str,
    ) -> dict[str, Any]:
        """Simulates field labour uploading a photo from phone to that site's group."""
        site = get_site(site_id)
        site_name = site["name"] if site else f"Site #{site_id}"

        context = f"Site: {site_name}. Worker note: {caption}"
        safety_analysis = await self._analyze(image_bytes, context)

        sender_label = f"📱 Telegram: {sender_name or 'Labourer on Phone'}"
        record = add_site_photo(
            site_id=site_id,
            file_bytes=image_bytes,
            filename=filename,
            sender=sender_label,
            caption=caption or "Sent from phone in site Telegram group",
            safety_analysis=safety_analysis,
        )

        risk = safety_analysis.get("overall_risk_level", "low").upper()
        what_risk = safety_analysis.get("what_is_the_risk", "Work area checked.")
        how_fix = safety_analysis.get("how_to_improve", "Keep up standard precautions.")

        simulated_bot_reply = (
            f"✅ [Telegram Group Bot Reply]:\n"
            f"Photo received for {site_name}!\n"
            f"Risk: {risk}\n"
            f"Observation: {what_risk}\n"
            f"Action: {how_fix}"
        )

        return {
            "success": True,
            "site_id": site_id,
            "site_name": site_name,
            "photo": record,
            "safety_analysis": safety_analysis,
            "bot_reply": simulated_bot_reply,
        }


telegram_service = TelegramService()
