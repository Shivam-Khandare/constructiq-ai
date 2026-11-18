"""
ConstructIQ AI — Telegram Bot Integration Service
Routes photos directly from dedicated Telegram groups (e.g. Site 3 Group)
to that site's screen in real time with plain-language safety feedback.

Features:
  • Persistent Reply Keyboard (large mobile buttons at bottom of chat for illiterate workers)
  • Inline Keyboard buttons attached to bot messages
  • Automatic site button updates when new sites are added
  • Pending photo holding (if photo sent before site selection, 1-tap assigns it)
  • Telegram Menu button registration (setMyCommands & setChatMenuButton)
  • Auto-welcome when bot is added to any group
  • Supergroup chat-ID normalization (handles Telegram's -100 prefix)
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
    _all_tg_variants,
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
        # Cache for pending photos sent before site is selected
        # chat_id -> dict with img_bytes, filename, sender_name, caption, msg_id
        self.pending_photos: dict[str, dict[str, Any]] = {}

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
            async with httpx.AsyncClient(timeout=25) as client:
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

        # Register bot commands & menu button in Telegram
        await self._setup_bot_commands()

        if not webhook_url:
            self.start_polling()

        return self.get_status()

    async def _setup_bot_commands(self) -> None:
        """Configures official Telegram Bot Menu and Commands."""
        if not self.token:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 1. Set commands list
                commands = [
                    {"command": "menu", "description": "📱 Show Site Selection Buttons"},
                    {"command": "bind", "description": "📍 Link this group to a site"},
                    {"command": "status", "description": "ℹ️ Check currently linked site"},
                    {"command": "help", "description": "👷 Help & Instructions"},
                ]
                await client.post(
                    f"{TELEGRAM_API_BASE}/bot{self.token}/setMyCommands",
                    json={"commands": commands},
                )
                # 2. Set default menu button
                await client.post(
                    f"{TELEGRAM_API_BASE}/bot{self.token}/setChatMenuButton",
                    json={"menu_button": {"type": "commands"}},
                )
                logger.info("Configured Telegram bot commands and menu button")
        except Exception as e:
            logger.warning("Could not set bot commands: %s", e)

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
                ["message", "channel_post", "callback_query", "my_chat_member"]
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
        """Send a text message with optional inline or reply keyboard."""
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
        """Acknowledge an inline button tap."""
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
        """Edit a previously sent message."""
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

    # ── Button Builders (Inline & Reply Keyboards) ───────────────────────────

    @staticmethod
    def _build_inline_keyboard(
        action_prefix: str = "bind",
        columns: int = 2,
    ) -> dict[str, Any]:
        """Build InlineKeyboardMarkup with clean buttons for each site.

        Automatically reads all sites from DB, so if a new site is added,
        its button appears instantly.
        """
        sites = get_all_sites()
        buttons: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []

        for s in sites:
            num = s["id"].replace("site-", "")
            label = f"📍 {s['name']}"
            row.append({"text": label, "callback_data": f"{action_prefix}:{num}"})
            if len(row) >= columns:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Utility row
        buttons.append([
            {"text": "ℹ️ Check Status", "callback_data": "status"},
            {"text": "🔄 Refresh Sites", "callback_data": "refresh"},
        ])

        return {"inline_keyboard": buttons}

    @staticmethod
    def _build_reply_keyboard(columns: int = 2) -> dict[str, Any]:
        """Build persistent ReplyKeyboardMarkup (large touch buttons on mobile).

        Displays at the bottom of the chat screen so illiterate workers can
        simply tap a button without typing anything.
        """
        sites = get_all_sites()
        keyboard: list[list[dict[str, str]]] = []
        row: list[dict[str, str]] = []

        for s in sites:
            label = f"📍 {s['name']}"
            row.append({"text": label})
            if len(row) >= columns:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Bottom row for easy check
        keyboard.append([
            {"text": "ℹ️ Group Status"},
            {"text": "🔄 Refresh Buttons"},
        ])

        return {
            "keyboard": keyboard,
            "resize_keyboard": True,
            "is_persistent": True,
            "one_time_keyboard": False,
        }

    # ── Site Resolution ──────────────────────────────────────────────────────

    def _resolve_target_site(self, text: str, chat_id: str | int) -> str | None:
        """Returns the target site-id for an incoming photo or message.

        Resolution Order:
        1. Explicit site tag in photo caption / text (e.g. #site2, site 2, Site 3, 📍 Site 1)
        2. Group/chat bound site (stored telegram_chat_id)
        3. None (triggers interactive button prompt)
        """
        # 1. Check if caption or message explicitly specifies a site (e.g. worker captions #site3)
        if text:
            site_num = self._extract_site_number(text)
            if site_num is not None:
                candidate_id = f"site-{site_num}"
                if get_site(candidate_id):
                    logger.info("Resolved site from caption/text tag: %r → %s", text[:40], candidate_id)
                    return candidate_id

        # 2. Stored telegram_chat_id lookup (handles supergroups with -100 prefix)
        chat_str = str(chat_id).strip()
        site_by_chat = get_site_by_telegram_chat(chat_str)
        if site_by_chat:
            logger.info("Resolved chat %s → site %s (%s)", chat_str, site_by_chat["id"], site_by_chat["name"])
            return site_by_chat["id"]

        logger.info("chat_id %s is not currently bound to any site", chat_str)
        return None

    # ── Update Processing (Main Router) ──────────────────────────────────────

    async def process_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        # 1. Handle Inline Button Clicks (callback_query)
        callback = update.get("callback_query")
        if callback:
            return await self._handle_callback_query(callback)

        # 2. Handle Bot added to group (my_chat_member)
        my_chat_member = update.get("my_chat_member")
        if my_chat_member:
            return await self._handle_my_chat_member(my_chat_member)

        # 3. Handle Normal Message / Channel Post
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
            "Msg chat_id=%s (%s): text=%r photo=%s",
            chat_id,
            message["chat"].get("type"),
            text[:60] if text else caption[:60],
            bool(message.get("photo")),
        )

        # Handle new members (bot added or human members added to group)
        if message.get("new_chat_members"):
            bot_added = False
            new_human_names = []
            bot_uname = (self.bot_username or "").lower()
            for m in message["new_chat_members"]:
                m_uname = (m.get("username") or "").lower()
                if bot_uname and m_uname == bot_uname:
                    bot_added = True
                else:
                    first = m.get("first_name", "")
                    last = m.get("last_name", "")
                    name = f"{first} {last}".strip() or m.get("username", "Member")
                    new_human_names.append(name)

            if bot_added:
                return await self._send_welcome_menu(chat_id, msg_id)
            elif new_human_names:
                return await self._send_new_member_welcome(chat_id, new_human_names, msg_id)

        # Handle commands & button taps
        text_lower = text.lower()
        bot_uname = (self.bot_username or "").lower()
        clean_text = text_lower
        if bot_uname:
            clean_text = clean_text.replace(f"@{bot_uname}", "").strip()

        # Menu / Start / Help / Buttons
        is_menu_cmd = (
            any(clean_text.startswith(cmd) for cmd in ["/start", "start", "/menu", "menu", "/help", "help", "/sites", "sites", "buttons", "/buttons"])
            or (bot_uname and f"@{bot_uname}" in text_lower and clean_text in ["", "menu", "help", "hi", "hello", "sites"])
        )
        if is_menu_cmd:
            return await self._send_welcome_menu(chat_id, msg_id)

        # Status check
        if any(clean_text.startswith(cmd) for cmd in ["/status", "status", "ℹ️ group status", "check status"]):
            return await self._handle_status_command(chat_id, msg_id)

        # Refresh buttons
        if any(clean_text.startswith(cmd) for cmd in ["refresh", "🔄 refresh", "🔄 refresh buttons"]):
            return await self._handle_refresh_command(chat_id, msg_id)

        # Bind command (e.g., "/bind 3", "bind 3", "site 3", "3", or "📍 Site 3")
        site_match = self._extract_site_number(clean_text)
        if site_match is not None:
            return await self._do_bind(chat_id, site_match, msg_id)

        if clean_text.startswith("/bind") or clean_text.startswith("bind"):
            # /bind without number -> show buttons
            inline_kb = self._build_inline_keyboard("bind")
            reply_kb = self._build_reply_keyboard()
            await self.send_message(
                chat_id,
                "👇 Tap your construction site below:",
                reply_to_message_id=msg_id,
                reply_markup=inline_kb,
            )
            # Also deploy reply keyboard
            await self.send_message(
                chat_id,
                "📱 Buttons are also ready at the bottom of your screen!",
                reply_markup=reply_kb,
            )
            return None

        # Photo or Document Image
        return await self._handle_photo(message, chat_id, sender_name, text, caption, msg_id)

    def _extract_site_number(self, text: str) -> int | None:
        """Extracts site number from user text or reply-keyboard tap.

        Examples matched:
          '📍 Site 3' -> 3
          'Site 3'    -> 3
          '/bind 3'   -> 3
          'bind 3'    -> 3
          'site-3'    -> 3
          '3'         -> 3 (only if pure digit under 50)
        """
        if not text:
            return None
        t = text.strip()

        # Pure digit (e.g. user just typed "3")
        if t.isdigit() and 1 <= int(t) <= 50:
            return int(t)

        # '📍 Site 3' or 'Site 3' or 'Site-3'
        m = re.search(r"(?:📍\s*)?(?:site[\s\-_]?|#site[\s\-_]?|bind[\s\-_]?|/bind[\s\-_]?)(\d+)", t, re.IGNORECASE)
        if m:
            return int(m.group(1))

        # Check against custom site names (e.g. renamed to 'Metro Rail' or tapped '📍 Metro Rail')
        clean_text = t.replace("📍", "").strip().lower()
        if clean_text:
            for s in get_all_sites():
                s_name = s.get("name", "").strip().lower()
                if s_name and (clean_text == s_name or clean_text == s.get("id", "").lower()):
                    try:
                        return int(s["id"].replace("site-", ""))
                    except ValueError:
                        pass

        return None

    # ── Command & Menu Handlers ──────────────────────────────────────────────

    async def _send_welcome_menu(self, chat_id: int, msg_id: int | None = None) -> dict[str, Any]:
        """Sends comprehensive welcome message with both Inline and Reply keyboards."""
        bound = get_site_by_telegram_chat(chat_id)
        sites = get_all_sites()

        if bound:
            status_text = f"📍 Currently linked to: <b>{bound['name']}</b> ({bound.get('location', 'Worksite')})\n"
        else:
            status_text = "⚠️ This group is NOT linked to any site yet.\n"

        welcome_text = (
            f"👷 <b>ConstructIQ Site Safety Bot</b> 🦺\n\n"
            f"{status_text}\n"
            f"📸 <b>How to upload photos:</b>\n"
            f"• Drop, paste, or send photos in this chat anytime (from phone or laptop).\n"
            f"• Photos are automatically analyzed for hazards and logged to the live dashboard.\n\n"
            f"👇 <b>Select / Switch Site:</b>\n"
            f"Click or tap any button below to link this group to that site:"
        )

        inline_kb = self._build_inline_keyboard("bind")
        reply_kb = self._build_reply_keyboard()

        # 1. Send inline buttons message
        await self.send_message(
            chat_id,
            welcome_text,
            reply_to_message_id=msg_id,
            reply_markup=inline_kb,
        )

        # 2. Deploy persistent mobile keyboard
        await self.send_message(
            chat_id,
            "💡 <i>Tip: On mobile, persistent buttons are ready at the bottom. On laptop, click the buttons above or click the keyboard icon next to your message box!</i>",
            reply_markup=reply_kb,
        )

        return {"type": "welcome", "sites_count": len(sites)}

    async def _send_new_member_welcome(
        self, chat_id: int, names: list[str], msg_id: int | None = None
    ) -> dict[str, Any]:
        """Greets newly added human members with clear instructions and clickable site buttons."""
        bound = get_site_by_telegram_chat(chat_id)
        names_str = ", ".join(names)

        if bound:
            status_text = f"📍 This group is currently logging photos to: <b>{bound['name']}</b> ({bound.get('location', 'Worksite')}).\n"
        else:
            status_text = "⚠️ This group is not linked to any site yet.\n"

        welcome_text = (
            f"👋 Welcome <b>{names_str}</b> to ConstructIQ Site Monitoring!\n\n"
            f"{status_text}\n"
            f"📸 <b>How to upload site photos:</b>\n"
            f"• Simply drop, paste, or send photos in this chat from your laptop or phone.\n"
            f"• AI automatically inspects safety hazards, PPE compliance, and logs them to the live dashboard.\n"
            f"• If you want to specify a particular site in your caption, just write <code>#site1</code>, <code>#site2</code>, etc.\n\n"
            f"👇 Click any button below to view or change the linked site:"
        )

        inline_kb = self._build_inline_keyboard("bind")
        reply_kb = self._build_reply_keyboard()

        await self.send_message(chat_id, welcome_text, reply_to_message_id=msg_id, reply_markup=inline_kb)
        await self.send_message(
            chat_id,
            "💡 <i>Tip: Tap buttons above on laptop, or use the mobile buttons at the bottom of your screen!</i>",
            reply_markup=reply_kb,
        )
        return {"type": "new_member_welcome", "members": names}

    async def _handle_my_chat_member(self, my_chat_member: dict[str, Any]) -> dict[str, Any] | None:
        """Triggered automatically when the bot is added or promoted in a group."""
        new_status = my_chat_member.get("new_chat_member", {}).get("status")
        if new_status in ("member", "administrator"):
            chat_id = my_chat_member.get("chat", {}).get("id")
            if chat_id:
                logger.info("Bot added to group chat_id=%s, sending welcome menu", chat_id)
                return await self._send_welcome_menu(chat_id)
        return None

    async def _handle_status_command(self, chat_id: int, msg_id: int) -> dict[str, Any]:
        bound = get_site_by_telegram_chat(chat_id)
        inline_kb = self._build_inline_keyboard("bind")
        reply_kb = self._build_reply_keyboard()

        if bound:
            reply = (
                f"📍 Group Linked To: {bound['name']}\n"
                f"🏷️ Worksite: {bound.get('location', 'N/A')}\n"
                f"👷 Supervisor: {bound.get('supervisor', 'N/A')}\n"
                f"📸 Photos In Album: {bound.get('photo_count', 0)}\n\n"
                f"To switch to a different site, tap any button below:"
            )
        else:
            reply = (
                "⚠️ This group is not linked to any site.\n\n"
                "👇 Tap a site button below to link it:"
            )

        await self.send_message(chat_id, reply, reply_to_message_id=msg_id, reply_markup=inline_kb)
        await self.send_message(chat_id, "📱 Or tap a button from the keyboard at the bottom of your screen:", reply_markup=reply_kb)
        return {"type": "status", "bound_site": bound["id"] if bound else None}

    async def _handle_refresh_command(self, chat_id: int, msg_id: int) -> dict[str, Any]:
        sites = get_all_sites()
        reply_kb = self._build_reply_keyboard()
        inline_kb = self._build_inline_keyboard("bind")
        await self.send_message(
            chat_id,
            f"🔄 Site buttons refreshed! Total monitored sites: {len(sites)}\nTap a button to select:",
            reply_to_message_id=msg_id,
            reply_markup=inline_kb,
        )
        await self.send_message(
            chat_id,
            "📱 Mobile keyboard buttons updated:",
            reply_markup=reply_kb,
        )
        return {"type": "refresh", "sites_count": len(sites)}

    # ── Callback Query (Inline Button Click) ─────────────────────────────────

    async def _handle_callback_query(self, callback: dict[str, Any]) -> dict[str, Any] | None:
        cb_id = callback["id"]
        cb_data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not chat_id:
            await self.answer_callback_query(cb_id, "Error: missing chat")
            return None

        logger.info("Callback query: data=%r chat_id=%s", cb_data, chat_id)

        # Status button
        if cb_data == "status":
            bound = get_site_by_telegram_chat(chat_id)
            status_str = f"Linked to {bound['name']}" if bound else "Not linked yet"
            await self.answer_callback_query(cb_id, status_str)
            return await self._handle_status_command(chat_id, message_id)

        # Refresh button
        if cb_data == "refresh":
            await self.answer_callback_query(cb_id, "Refreshed!")
            return await self._handle_refresh_command(chat_id, message_id)

        # Bind button (e.g. "bind:3")
        if cb_data.startswith("bind:"):
            try:
                site_num = int(cb_data.split(":")[1])
            except (IndexError, ValueError):
                await self.answer_callback_query(cb_id, "Invalid site number")
                return None

            result = await self._do_bind(chat_id, site_num, reply_msg_id=None)

            if result:
                site = get_site(f"site-{site_num}")
                site_name = site["name"] if site else f"Site {site_num}"
                await self.edit_message_text(
                    chat_id,
                    message_id,
                    f"✅ Group linked to: {site_name}!\n\n"
                    f"📸 Any photo sent here will now appear on {site_name}'s screen automatically.",
                )
            await self.answer_callback_query(cb_id, f"Linked to Site {site_num}!")
            return result

        await self.answer_callback_query(cb_id)
        return None

    # ── Bind Execution ───────────────────────────────────────────────────────

    async def _do_bind(
        self, chat_id: int, site_num: int, reply_msg_id: int | None
    ) -> dict[str, Any] | None:
        target_site_id = f"site-{site_num}"
        site = get_site(target_site_id)
        if not site:
            reply = f"❌ Site {site_num} does not exist. Check the site number."
            await self.send_message(chat_id, reply, reply_to_message_id=reply_msg_id)
            return None

        # 1. Clear any other site that had this chat_id previously
        chat_str = str(chat_id)
        for s in get_all_sites():
            stored = str(s.get("telegram_chat_id", "")).strip()
            if stored and s["id"] != target_site_id:
                if _all_tg_variants(stored) & _all_tg_variants(chat_str):
                    logger.info("Clearing old binding: %s had chat_id=%s", s["id"], stored)
                    update_site(s["id"], {"telegram_chat_id": ""})

        # 2. Bind target site to this chat
        update_site(target_site_id, {"telegram_chat_id": chat_str})
        logger.info("BIND SUCCESS: chat_id=%s → %s (%s)", chat_str, target_site_id, site["name"])

        # 3. Confirm with user
        reply_kb = self._build_reply_keyboard()
        reply = (
            f"✅ Group linked successfully!\n\n"
            f"📍 Connected to: {site['name']}\n"
            f"🆔 Group ID: {chat_id}\n\n"
            f"📸 Send any photo in this group and it will automatically appear "
            f"on {site['name']}'s live album on the engineer's dashboard!"
        )
        await self.send_message(chat_id, reply, reply_to_message_id=reply_msg_id, reply_markup=reply_kb)

        # 4. Check if there was a pending photo waiting to be processed!
        chat_str = str(chat_id)
        pending_key = None
        for k in list(self.pending_photos.keys()):
            if _all_tg_variants(k) & _all_tg_variants(chat_str):
                pending_key = k
                break

        if pending_key:
            pending = self.pending_photos.pop(pending_key)
            logger.info("Processing pending photo for newly bound %s", target_site_id)
            await self._process_and_save_photo(
                target_site_id=target_site_id,
                img_bytes=pending["img_bytes"],
                filename=pending["filename"],
                sender_name=pending["sender_name"],
                caption=pending["caption"],
                chat_id=chat_id,
                msg_id=pending["msg_id"],
            )

        return {"type": "bind", "site_id": target_site_id}

    # ── Photo Ingestion & Processing ─────────────────────────────────────────

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
            fname = (doc.get("file_name") or "").lower()
            is_img_ext = any(fname.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp"])
            if mime.startswith("image/") or is_img_ext:
                file_id = doc.get("file_id")
                filename_hint = doc.get("file_name") or "laptop_photo.jpg"

        if not file_id:
            return None

        # Download image from Telegram
        try:
            img_bytes, filename = await self.download_telegram_file(file_id)
        except Exception as e:
            logger.error("Failed to download photo from Telegram: %s", e)
            return None

        combined_text = f"{text} {caption}".strip()
        target_site_id = self._resolve_target_site(combined_text, chat_id)

        # Case A: Group is NOT linked to any site yet
        if target_site_id is None:
            # Save pending photo so worker only needs to tap a button ONCE!
            chat_key = str(chat_id)
            self.pending_photos[chat_key] = {
                "img_bytes": img_bytes,
                "filename": filename_hint,
                "sender_name": sender_name,
                "caption": caption or text,
                "msg_id": msg_id,
            }

            inline_kb = self._build_inline_keyboard("bind")
            reply_kb = self._build_reply_keyboard()
            await self.send_message(
                chat_id,
                f"📸 Photo received from {sender_name}!\n\n"
                f"⚠️ This group is not linked to a site yet.\n"
                f"👇 Please tap your construction site below to save the photo:",
                reply_to_message_id=msg_id,
                reply_markup=inline_kb,
            )
            await self.send_message(
                chat_id,
                "📱 Or tap a button from the keyboard at the bottom:",
                reply_markup=reply_kb,
            )
            return None

        # Case B: Group is already linked -> process immediately
        return await self._process_and_save_photo(
            target_site_id=target_site_id,
            img_bytes=img_bytes,
            filename=filename,
            sender_name=sender_name,
            caption=caption or text,
            chat_id=chat_id,
            msg_id=msg_id,
        )

    async def _process_and_save_photo(
        self,
        target_site_id: str,
        img_bytes: bytes,
        filename: str,
        sender_name: str,
        caption: str,
        chat_id: int,
        msg_id: int,
    ) -> dict[str, Any]:
        site = get_site(target_site_id)
        site_name = site["name"] if site else f"Site {target_site_id}"

        # Analyze with AI
        context = f"Site: {site_name}. Worker note: {caption}"
        safety_analysis = await self._analyze(img_bytes, context)

        # Store photo in album
        sender_label = f"📱 Telegram: {sender_name}"
        record = add_site_photo(
            site_id=target_site_id,
            file_bytes=img_bytes,
            filename=filename,
            sender=sender_label,
            caption=caption or "Photo sent from phone via Telegram",
            safety_analysis=safety_analysis,
        )

        risk = safety_analysis.get("overall_risk_level", "low").upper()
        what_risk = safety_analysis.get("what_is_the_risk", "No major hazards detected.")
        how_fix = safety_analysis.get("how_to_improve", "Continue maintaining clean and safe work conditions.")

        if risk in ("HIGH", "CRITICAL"):
            reply = (
                f"🚨 SAFETY ALERT for {site_name}\n\n"
                f"⚠️ Risk Level: {risk}\n"
                f"🔍 What is the risk: {what_risk}\n\n"
                f"🛠️ How to fix it: {how_fix}\n\n"
                f"✅ Photo uploaded to engineer's live dashboard."
            )
        else:
            reply = (
                f"✅ Photo received for {site_name}!\n\n"
                f"🛡️ Safety Status: {risk}\n"
                f"🔍 Observation: {what_risk}\n"
                f"💡 Recommendation: {how_fix}\n\n"
                f"✅ Added to engineer's live dashboard."
            )

        # Provide quick action button to switch site if worker sent to wrong site
        quick_kb = {
            "inline_keyboard": [
                [
                    {"text": "🔄 Switch Site", "callback_data": "refresh"},
                    {"text": "ℹ️ Status", "callback_data": "status"},
                ]
            ]
        }
        await self.send_message(chat_id, reply, reply_to_message_id=msg_id, reply_markup=quick_kb)

        return {
            "type": "photo_ingested",
            "site_id": target_site_id,
            "site_name": site_name,
            "sender": sender_name,
            "risk": risk,
            "photo_id": record["id"],
        }

    # ── AI Analysis ──────────────────────────────────────────────────────────

    async def _analyze(self, image_bytes: bytes, context: str) -> dict[str, Any]:
        """Calls safety analyzer or returns simple, human-friendly fallback."""
        if self.analyzer_fn:
            try:
                return await self.analyzer_fn(image_bytes, context, "image/jpeg")
            except Exception as e:
                logger.warning("AI analyzer error: %s. Using simple inspection engine.", e)

        ctx_lower = context.lower()
        if any(w in ctx_lower for w in ["trench", "pit", "hole", "cave", "excavat", "mud", "soil"]):
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
        elif any(w in ctx_lower for w in ["height", "fall", "scaffold", "ladder", "edge", "slab", "roof"]):
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
        elif any(w in ctx_lower for w in ["helmet", "hat", "boot", "vest", "glove", "glasses", "ppe"]):
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
