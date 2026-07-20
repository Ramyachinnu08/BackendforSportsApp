"""Upload ingestion shared by every multipart endpoint (spec §7).

Pipeline: validate purpose → MIME/extension check → size cap → virus scan →
EXIF strip (images) → store → `media` row.  Certification documents are
private-ACL; everything else public.
"""
import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import bad_request, too_large, unsupported_media
from app.db.models import Media, User
from app.services.providers import get_storage, strip_image_metadata, virus_scan

# purpose → (max bytes, allowed mime prefixes/full types, acl)
PURPOSES: dict[str, tuple[int, tuple[str, ...], str]] = {
    "avatar": (settings.max_avatar_bytes, ("image/jpeg", "image/png", "image/webp"), "public"),
    "league_logo": (settings.max_logo_bytes, ("image/png", "image/jpeg"), "public"),
    "team_logo": (settings.max_logo_bytes, ("image/png", "image/jpeg", "image/webp"), "public"),
    "team_logo": (settings.max_logo_bytes, ("image/png", "image/jpeg"), "public"),
    "post_image": (settings.max_image_bytes, ("image/",), "public"),
    "post_video": (settings.max_video_bytes, ("video/mp4", "video/quicktime", "video/webm"), "public"),
    "certification_doc": (settings.max_cert_doc_bytes, ("image/", "application/pdf"), "private"),
}

_EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime", ".pdf": "application/pdf",
}


def _mime_allowed(mime: str, allowed: tuple[str, ...]) -> bool:
    return any(mime == a or (a.endswith("/") and mime.startswith(a)) for a in allowed)


async def store_upload(db: AsyncSession, user: User, upload: UploadFile, purpose: str,
                       title: str | None = None, subtitle: str | None = None) -> Media:
    if purpose not in PURPOSES:
        raise bad_request("VALIDATION_ERROR", f"Unknown upload purpose '{purpose}'.", field="purpose")
    max_bytes, allowed, acl = PURPOSES[purpose]

    mime = (upload.content_type or "").lower()
    filename = upload.filename or "upload.bin"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if not _mime_allowed(mime, allowed):
        raise unsupported_media(f"'{mime or 'unknown'}' is not allowed for {purpose}.")
    # reject MIME/extension mismatch (spec §7.3)
    if ext in _EXT_MIME and not _mime_allowed(_EXT_MIME[ext], allowed):
        raise unsupported_media("File extension doesn't match an allowed type.")

    data = await upload.read()
    if len(data) > max_bytes:
        raise too_large(f"Max size for {purpose} is {max_bytes // (1024 * 1024)} MB.")
    if len(data) == 0:
        raise bad_request("VALIDATION_ERROR", "Empty file.", field="file")

    if not virus_scan(data):
        raise unsupported_media("File failed the security scan.")

    width = height = None
    if mime.startswith("image/"):
        data, width, height = strip_image_metadata(data, mime)

    stored = await get_storage().save(data, filename, mime, acl=acl)
    media = Media(
        id=uuid.uuid4(),
        owner_id=user.id,
        purpose=purpose,
        storage_key=stored.storage_key,
        url=stored.url,
        mime=mime,
        bytes=len(data),
        width=width,
        height=height,
        status="ready",
        acl=acl,
        title=title,
        subtitle=subtitle,
    )
    db.add(media)
    await db.flush()
    return media
