"""Third-party provider abstractions.
Development defaults are dependency-free (console/local-disk).  Production
implementations (MSG91/Twilio, SES/SMTP, FCM, S3) plug in behind the same
interfaces — selected via settings, wired in `get_*_provider()`.
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
        log.info("[SMS → %s] %s", phone, message)
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
        log.info("[EMAIL → %s] %s\n%s", to, subject, body)
class SmtpEmail(EmailProvider):
    async def send(self, to: str, subject: str, body: str) -> None:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.email_from, to, subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
def get_email_provider() -> EmailProvider:
    if settings.email_provider == "smtp" and settings.smtp_host:
        return SmtpEmail()
    return ConsoleEmail()
# --- Push (FCM/APNs) ----------------------------------------------------------
class PushProvider:
    async def send(self, tokens: list[str], title: str, body: str, data: dict | None = None) -> None:
        raise NotImplementedError
class ConsolePush(PushProvider):
    async def send(self, tokens: list[str], title: str, body: str, data: dict | None = None) -> None:
        if tokens:
            log.info("[PUSH → %d device(s)] %s — %s %s", len(tokens), title, body, data or "")
def get_push_provider() -> PushProvider:
    # FCM implementation slots in here once a service-account JSON is configured.
    return ConsolePush()
# --- Storage -------------------------------------------------------------------
class StorageResult:
    def __init__(self, storage_key: str, url: str | None):
        self.storage_key = storage_key
        self.url = url
class StorageProvider:
    async def save(self, data: bytes, filename: str, mime: str, acl: str = "public") -> StorageResult: ...
    async def open_path(self, storage_key: str) -> Path: ...
    def public_url(self, storage_key: str) -> str: ...
    def signed_url(self, storage_key: str) -> str: ...
    async def delete(self, storage_key: str) -> None: ...
class LocalStorage(StorageProvider):
    """Local-disk storage. Public files are served from /static; private files
    only via HMAC-signed URLs (see /v1/files/signed)."""
    def __init__(self) -> None:
        self.root = Path(settings.storage_dir)
        (self.root / "public").mkdir(parents=True, exist_ok=True)
        (self.root / "private").mkdir(parents=True, exist_ok=True)
    async def save(self, data: bytes, filename: str, mime: str, acl: str = "public") -> StorageResult:
        safe = filename.replace("/", "_").replace("\\", "_")
        key = f"{acl}/{uuid.uuid4().hex}_{safe}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        url = self.public_url(key) if acl == "public" else None
        return StorageResult(key, url)
    async def open_path(self, storage_key: str) -> Path:
        return self.root / storage_key
    def public_url(self, storage_key: str) -> str:
        return f"{settings.base_url}/static/{storage_key.removeprefix('public/')}"
    def signed_url(self, storage_key: str) -> str:
        exp, sig = sign_storage_key(storage_key)
        return f"{settings.base_url}/v1/files/signed?key={storage_key}&exp={exp}&sig={sig}"
    async def delete(self, storage_key: str) -> None:
        path = self.root / storage_key
        if path.exists():
            path.unlink()
class DbStorage(StorageProvider):
    """Stores file bytes in Postgres (media_blobs). Survives restarts on
    hosts with ephemeral disks (Render free tier). Select with
    STORAGE_PROVIDER=db. Files are served from /v1/files/{key}."""
    async def save(self, data: bytes, filename: str, mime: str, acl: str = "public") -> StorageResult:
        from app.db.base import SessionLocal
        from app.db.models import MediaBlob
        safe = filename.replace("/", "_").replace("\\", "_")
        key = f"{acl}/{uuid.uuid4().hex}_{safe}"
        async with SessionLocal() as session:
            session.add(MediaBlob(storage_key=key, data=data, mime=mime, acl=acl))
            await session.commit()
        url = self.public_url(key) if acl == "public" else None
        return StorageResult(key, url)
    async def read(self, storage_key: str):
        from sqlalchemy import select
        from app.db.base import SessionLocal
        from app.db.models import MediaBlob
        async with SessionLocal() as session:
            return (
                await session.execute(select(MediaBlob).where(MediaBlob.storage_key == storage_key))
            ).scalar_one_or_none()
    async def open_path(self, storage_key: str) -> Path:  # not disk-backed
        raise FileNotFoundError(storage_key)
    def public_url(self, storage_key: str) -> str:
        from urllib.parse import quote
        return f"{settings.base_url}/v1/files/{quote(storage_key, safe='')}"
    def signed_url(self, storage_key: str) -> str:
        exp, sig = sign_storage_key(storage_key)
        return f"{settings.base_url}/v1/files/signed?key={storage_key}&exp={exp}&sig={sig}"
    async def delete(self, storage_key: str) -> None:
        from sqlalchemy import delete as sa_delete
        from app.db.base import SessionLocal
        from app.db.models import MediaBlob
        async with SessionLocal() as session:
            await session.execute(sa_delete(MediaBlob).where(MediaBlob.storage_key == storage_key))
            await session.commit()
class CloudinaryStorage(StorageProvider):
    """Cloudinary-backed storage. Files survive redeploys and are served
    from Cloudinary's CDN. Select with STORAGE_PROVIDER=cloudinary."""

    def __init__(self) -> None:
        import cloudinary
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )

    @staticmethod
    def _resource_type(mime: str) -> str:
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
        return "raw"  # pdfs, docs

    @staticmethod
    def _parse_key(storage_key: str) -> tuple[str, str, str, str]:
        """storage_key format: '{acl}/{public_id}::{resource_type}::{format}'"""
        acl, rest = storage_key.split("/", 1)
        public_id, rtype, fmt = rest.rsplit("::", 2)
        return acl, public_id, rtype, fmt

    async def save(self, data: bytes, filename: str, mime: str,
                   acl: str = "public") -> StorageResult:
        import asyncio

        import cloudinary.uploader

        safe = filename.replace("/", "_").replace("\\", "_")
        stem = safe.rsplit(".", 1)[0][:80]
        public_id = f"sportyqo/{acl}/{uuid.uuid4().hex}_{stem}"
        rtype = self._resource_type(mime)
        delivery = "private" if acl == "private" else "upload"

        def _upload():
            return cloudinary.uploader.upload(
                io.BytesIO(data),
                public_id=public_id,
                resource_type=rtype,
                type=delivery,
                overwrite=False,
            )

        result = await asyncio.to_thread(_upload)
        key = f"{acl}/{result['public_id']}::{rtype}::{result.get('format') or ''}"
        url = result["secure_url"] if acl == "public" else None
        return StorageResult(key, url)

    async def open_path(self, storage_key: str) -> Path:  # not disk-backed
        raise FileNotFoundError(storage_key)

    def public_url(self, storage_key: str) -> str:
        from cloudinary.utils import cloudinary_url

        _, public_id, rtype, fmt = self._parse_key(storage_key)
        url, _ = cloudinary_url(public_id, resource_type=rtype,
                                format=fmt or None, secure=True)
        return url

    def signed_url(self, storage_key: str) -> str:
        import time as _time

        from cloudinary.utils import private_download_url

        _, public_id, rtype, fmt = self._parse_key(storage_key)
        return private_download_url(
            public_id, fmt or "pdf",
            resource_type=rtype, type="private",
            expires_at=int(_time.time()) + 900,
        )

    async def delete(self, storage_key: str) -> None:
        import asyncio

        import cloudinary.uploader

        acl, public_id, rtype, _ = self._parse_key(storage_key)
        delivery = "private" if acl == "private" else "upload"

        def _destroy():
            cloudinary.uploader.destroy(public_id, resource_type=rtype, type=delivery)

        await asyncio.to_thread(_destroy)
_storage: StorageProvider | None = None
def get_storage() -> StorageProvider:
    global _storage
    if _storage is None:
        # S3Storage (boto3 presigned) slots in here when settings.storage_provider == "s3".
        if settings.storage_provider == "cloudinary" and settings.cloudinary_cloud_name:
            _storage = CloudinaryStorage()
        elif settings.storage_provider == "db":
            _storage = DbStorage()
        else:
            _storage = LocalStorage()
    return _storage
# --- image hygiene ----------------------------------------------------------------
MAX_IMAGE_DIMENSION = 1920  # px — plenty for phone screens, keeps feeds smooth
def strip_image_metadata(data: bytes, mime: str) -> tuple[bytes, int | None, int | None]:
    """Normalize uploaded images:
    1. Apply the EXIF Orientation tag BEFORE stripping it, so portrait
       photos aren't saved sideways (bug: photos rotated 90°).
    2. Downscale anything larger than MAX_IMAGE_DIMENSION — multi-MP
       camera photos were huge and made feed scrolling stutter.
    3. Re-encode without EXIF (drops GPS and other metadata).
    Returns (bytes, width, height)."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        # bake the rotation in, then EXIF can be safely discarded
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                          Image.LANCZOS)
        width, height = img.size
        out = io.BytesIO()
        fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}.get(mime, img.format or "PNG")
        if fmt == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        save_kwargs: dict = {"exif": b""} if fmt in ("JPEG", "WEBP") else {}
        if fmt == "JPEG":
            save_kwargs["quality"] = 88
        img.save(out, format=fmt, **save_kwargs)
        return out.getvalue(), width, height
    except Exception:  # non-image or unreadable — return as-is
        return data, None, None
def virus_scan(data: bytes) -> bool:
    """Hook for ClamAV / VirusTotal in production. Always clean in dev."""
    return True
