"""
dispatchers/telegram_bot.py - Bi-Directional Telegram Bot Interface.
Pushes formatted Markdown alert cards and listens for inbound commands via long-polling.
"""

from __future__ import annotations
import logging
import asyncio
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Any, Union
from core.models import AlertEvent, AlertSeverity, DeepDiveAnalysis

logger = logging.getLogger(__name__)


class TelegramBotDispatcher:
    """
    Handles bi-directional communication with Telegram:
    - Outbound: Formats and sends P0/P1/P2 Markdown alert cards.
    - Inbound: Polls Telegram updates and routes user commands (/status, /stops, etc.).
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        allowed_user_ids: Optional[List[Union[int, str]]] = None,
        is_mock: bool = False
    ):
        self.bot_token = bot_token or ""
        self.chat_id = str(chat_id) if chat_id else ""
        self.allowed_user_ids = [str(uid) for uid in (allowed_user_ids or [])]
        if self.chat_id and self.chat_id not in self.allowed_user_ids:
            self.allowed_user_ids.append(self.chat_id)
        
        self.is_mock = is_mock or not bool(self.bot_token)
        self.command_handler: Optional[Callable[[str, str], str]] = None
        self.sent_messages: List[Dict[str, Any]] = []
        self._polling_active = False

    def set_command_handler(self, handler: Callable[[str, str], str]) -> None:
        """Sets the callback for handling incoming Telegram commands."""
        self.command_handler = handler

    def format_alert_card(self, alert: AlertEvent) -> str:
        """Formats an AlertEvent into a high-visibility Telegram Markdown card."""
        sym_str = f"`{alert.symbol}`" if alert.symbol else "GENERAL"
        time_str = alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

        if alert.severity == AlertSeverity.P2:
            return (
                f"🚨🚨 **[P2 EMERGENCY DIRECTIVE]** 🚨🚨\n\n"
                f"**Target:** {sym_str} | **Source:** `{alert.source}`\n"
                f"**Directive:** **{alert.title}**\n\n"
                f"{alert.message}\n\n"
                f"⚠️ *Bypass DND / Execute immediately in broker terminal!*\n"
                f"🕒 `{time_str}`"
            )
        elif alert.severity == AlertSeverity.P1:
            return (
                f"⚠️ **[P1 ACTIONABLE ALERT]** ⚠️\n\n"
                f"**Target:** {sym_str} | **Source:** `{alert.source}`\n"
                f"**Order:** **{alert.title}**\n\n"
                f"{alert.message}\n\n"
                f"📋 *Actionable order triggered. Review and execute limits.*\n"
                f"🕒 `{time_str}`"
            )
        else:
            return (
                f"ℹ️ **[P0 MANDATE INTELLIGENCE]**\n\n"
                f"**Subject:** **{alert.title}**\n\n"
                f"{alert.message}\n\n"
                f"🕒 `{time_str}`"
            )

    def send_alert(self, alert: AlertEvent) -> bool:
        """Formats and sends an alert card."""
        text = self.format_alert_card(alert)
        return self.send_raw_message(text)

    def send_raw_message(self, text: str) -> bool:
        """Sends a message to the configured Telegram chat."""
        if self.is_mock:
            logger.info(f"[MOCK TELEGRAM DISPATCH] To {self.chat_id or 'Console'}:\n{text}")
            self.sent_messages.append({"text": text, "timestamp": datetime.now(timezone.utc)})
            return True

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram credentials not configured. Skipping dispatch.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                self.sent_messages.append({"text": text, "timestamp": datetime.now(timezone.utc)})
                return True
            elif resp.status_code == 400:
                # Markdown entity parse failure fallback: send as plain text
                logger.warning("Telegram parse_mode=Markdown failed (400). Retrying as plain text...")
                fallback_payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True
                }
                fb_resp = requests.post(url, json=fallback_payload, timeout=10)
                if fb_resp.status_code == 200:
                    self.sent_messages.append({"text": text, "timestamp": datetime.now(timezone.utc)})
                    return True
                logger.error(f"Telegram plain text fallback failed {fb_resp.status_code}: {fb_resp.text}")
                return False
            else:
                logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def handle_incoming_text(self, text: str, user_id: str) -> str:
        """Processes an incoming command after security verification."""
        # Security authorization check
        if self.allowed_user_ids and str(user_id) not in self.allowed_user_ids:
            logger.warning(f"Unauthorized command attempt from user ID: {user_id}")
            return "⛔ Access Denied: Unauthorized sender."

        if not self.command_handler:
            return "⚠️ Sentinel daemon is running, but command router is not attached."

        return self.command_handler(text.strip(), str(user_id))

    async def poll_updates_loop(self, poll_interval_sec: int = 2) -> None:
        """
        Asynchronous long-polling loop for receiving Telegram commands from phone.
        """
        if self.is_mock:
            logger.info("Mock Telegram polling mode active (no live HTTP polling).")
            return

        self._polling_active = True
        offset = 0
        logger.info("Starting Telegram inbound long-polling listener...")

        while self._polling_active:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={offset}&timeout=25"
                # Run synchronous requests in worker thread to prevent blocking event loop
                resp = await asyncio.to_thread(requests.get, url, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        from_user = str(msg.get("from", {}).get("id", ""))
                        text = msg.get("text", "")

                        if text:
                            logger.info(f"Received inbound Telegram command: '{text}' from {from_user}")
                            reply = self.handle_incoming_text(text, from_user)
                            # Reply back to user
                            chat_id = msg.get("chat", {}).get("id", self.chat_id)
                            reply_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                            reply_resp = await asyncio.to_thread(
                                requests.post,
                                reply_url,
                                json={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"},
                                timeout=10
                            )
                            if getattr(reply_resp, "status_code", 200) == 400:
                                await asyncio.to_thread(
                                    requests.post,
                                    reply_url,
                                    json={"chat_id": chat_id, "text": reply},
                                    timeout=10
                                )
            except Exception as e:
                logger.debug(f"Polling update cycle exception: {e}")

            await asyncio.sleep(poll_interval_sec)

    def stop_polling(self) -> None:
        self._polling_active = False
