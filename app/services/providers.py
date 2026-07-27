"""Third-party provider abstractions.
Development defaults are dependency-free (console/local-disk).  Production
implementations (MSG91/Twilio, SES/SMTP, FCM, S3) plug in behind the same
interfaces â€” selected via settings, wired in `get_*_provider()`.
"""
import io
import logging
import shutil
import uuid
from pathlib import Path
from app.core.config import settings
from app.core.security import sign_storage_key
log = logging.getLogger("sportyqo.providers")
# --- SMS -------------------------------------------------------------------
class SmsProvider:
    async def send(self, phone: str, message: str) -> None:
        raise NotImplementedError
class ConsoleSms(SmsProvider):
    async def send(self, phone: str, message: str) -> None:
        log.info("[SMS â†’ %s] %s", phone, message)
class Msg91Sms(SmsProvider):
    """MSG91 (India). Requires MSG91_AUTH_KEY; HTTP call left as a thin wrapper."""
    async def send(self, phone: str, message: str) -> None:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "https://control.msg91.com/api/v5/flow/",
                headers={"authkey": settings.msg91_auth_key},
                json={"recipients": [{"mobiles": phone.lstrip("+"), "message": message}]},
            )
def get_sms_provider() -> SmsProvider:
    if settings.sms_provider == "msg91" and settings.msg91_auth_key:
        return Msg91Sms()
    return ConsoleSms()
# --- Email -------------------------------------------------------------------
class EmailProvider:
    async def send(self, to: str, subject: str, body: str) -> None:
        raise NotImplementedError
class ConsoleEmail(EmailProvider):
    async def send(self, to: str, subject: str, body: str) -> None:
        log.info("[EMAIL â†’ %s] %s\n%s", to, subject, body)
class SmtpEmail(EmailProvider):
    async def send(self, to: str, subject: str, body: str) -> None:
        import smtplib
        import ssl
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.email_from, to, subject
        msg.set_content(body)
        # Port 465 = SSL, 587 = STARTTLS. GoDaddy cPanel uses 465 SSL.
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, 465,
                                  context=ssl.create_default_context()) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
def get_email_provider() -> EmailProvider:
    if settings.email_provider == "smtp" and settings.smtp_host:
        return SmtpEmail()
    return ConsoleEmail()
