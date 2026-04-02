"""
Notifier — logs events and optionally sends email notifications.
Email is a stub: configure SMTP in .env to activate.
Notification failures NEVER crash the pipeline.
"""
from __future__ import annotations

import logging

from packages.core.src.result import Result

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        from packages.core.src.config import get_settings
        settings = get_settings()
        self._enabled: bool = getattr(settings, "notifications_enabled", False)
        self._notify_email: str = getattr(settings, "notify_email", "")

    def notify_sale(self, sku: str, sold_price: float, platform: str) -> None:
        msg = f"SALE: {sku} sold for ${sold_price:.2f} on {platform}"
        logger.info(msg)
        if self._enabled and self._notify_email:
            self.send_email(f"Item Sold: {sku}", msg)

    def notify_stale(self, items) -> None:
        if not items:
            return
        count = len(items)
        msg = f"STALE LISTINGS: {count} items need price adjustment"
        logger.warning(msg)
        if self._enabled and self._notify_email:
            lines = "\n".join(
                f"  - {i.sku}: {i.title_final or i.title_raw or ''}"
                for i in items[:20]
            )
            self.send_email("Stale Listings Alert", f"{msg}\n\n{lines}")

    def notify_review_queue(self, count: int) -> None:
        msg = f"REVIEW QUEUE: {count} items waiting for review"
        logger.info(msg)
        if self._enabled and count > 0 and self._notify_email:
            self.send_email(f"Review Queue: {count} items pending", msg)

    def send_email(self, subject: str, body: str) -> Result[bool]:
        """
        Send email via SMTP. Configure in .env:
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL
        Returns failure if not configured — never crashes.
        """
        try:
            from packages.core.src.config import get_settings
            settings = get_settings()

            smtp_host = getattr(settings, "smtp_host", "")
            smtp_port = getattr(settings, "smtp_port", 587)
            smtp_user = getattr(settings, "smtp_user", "")
            smtp_password = getattr(settings, "smtp_password", "")
            notify_email = getattr(settings, "notify_email", "")

            if not all([smtp_host, smtp_user, smtp_password, notify_email]):
                return Result.failure("email_not_configured")

            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = f"[Resale AI] {subject}"
            msg["From"] = smtp_user
            msg["To"] = notify_email

            with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

            logger.info("Email sent: %s → %s", subject, notify_email)
            return Result.success(True)
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return Result.failure(str(e))
