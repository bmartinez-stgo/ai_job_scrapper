import logging
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

logger = logging.getLogger(__name__)


def _gmail_password() -> str:
    if settings.gmail_app_password:
        return settings.gmail_app_password
    try:
        from app.database import SessionLocal
        from app.models import AppSetting
        from app.services.crypto import decrypt
        db = SessionLocal()
        row = db.query(AppSetting).filter(AppSetting.key == "gmail_app_password_enc").first()
        db.close()
        if row:
            return decrypt(row.value)
    except Exception:
        pass
    return ""


async def send_email(subject: str, body: str) -> None:
    password = _gmail_password()
    if not password or not settings.gmail_from:
        logger.debug("Email not configured, skipping notification")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.gmail_from
    msg["To"] = settings.gmail_to
    msg.attach(MIMEText(body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.gmail_smtp_host,
            port=settings.gmail_smtp_port,
            username=settings.gmail_from,
            password=password,
            start_tls=True,
        )
    except Exception as e:
        logger.warning("Email send failed: %s", e)


async def notify_new_matches(matches: list[dict]) -> None:
    if not matches:
        return
    rows = "".join(
        f"<tr><td>{m['score']}</td><td>{m['title']}</td><td>{m['company']}</td><td>{m['location']}</td></tr>"
        for m in matches
    )
    body = f"""
    <h2>New job matches found</h2>
    <table border="1" cellpadding="6" style="border-collapse:collapse">
      <tr><th>Score</th><th>Title</th><th>Company</th><th>Location</th></tr>
      {rows}
    </table>
    <p>Log in to review and draft applications.</p>
    """
    await send_email(f"Job Scraper: {len(matches)} new matches", body)


async def notify_ghosted(applications: list[dict]) -> None:
    if not applications:
        return
    rows = "".join(
        f"<tr><td>{a['title']}</td><td>{a['company']}</td><td>{a['submitted_at']}</td></tr>"
        for a in applications
    )
    body = f"""
    <h2>Applications with no response</h2>
    <table border="1" cellpadding="6" style="border-collapse:collapse">
      <tr><th>Title</th><th>Company</th><th>Applied</th></tr>
      {rows}
    </table>
    <p>These have been marked as ghosted.</p>
    """
    await send_email(f"Job Scraper: {len(applications)} applications ghosted", body)
