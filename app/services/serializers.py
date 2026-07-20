"""Response shaping helpers shared across routers.

Privacy rule (spec §3, security notes): most players are minors — `dob`,
`school`, and `phone` never appear in any public payload; only `age_group`
and city-level `location`.
"""
from sqlalchemy import inspect as sa_inspect

from app.db.models import CardTier, Media, Post, User
from app.services.providers import get_storage
from app.services.scoring import tier_slug


def media_url(media: Media | None) -> str | None:
    if media is None:
        return None
    if media.acl == "private":
        return get_storage().signed_url(media.storage_key)
    return media.url


def avatar_url(user: User) -> str | None:
    if not user.avatar_media_id:
        return None
    # Async SQLAlchemy cannot lazy-load mid-request (MissingGreenlet). If the
    # relationship wasn't eagerly loaded, degrade to no avatar instead of a 500.
    # Queries that need avatars must use .options(selectinload(User.avatar)).
    if "avatar" in sa_inspect(user).unloaded:
        return None
    return media_url(user.avatar)


def _video_thumbnail(url: str | None) -> str | None:
    """Cloudinary can serve a video's first frame as a jpg poster (so_0)."""
    if not url or "/video/upload/" not in url:
        return None
    base = url.replace("/video/upload/", "/video/upload/so_0,w_720/", 1)
    root, dot, _ext = base.rpartition(".")
    return (root + ".jpg") if dot else (base + ".jpg")


def serialize_media_item(media: Media, thumbnail: str | None = None) -> dict:
    url = media_url(media)
    is_video = (media.mime or "").startswith("video/")
    return {
        "id": str(media.id),
        "type": "video" if is_video else "image",
        "url": url,
        "thumbnail_url": thumbnail
        or (_video_thumbnail(url) if is_video else None)
        or url,
        "title": media.title,
        "subtitle": media.subtitle,
        "duration_ms": media.duration_ms,
        "width": media.width,
        "height": media.height,
        "status": media.status,
    }


def role_line(user: User) -> str:
    if user.role == "coach":
        cp = user.coach_profile
        bits = [b for b in [(cp.sport if cp else None) or "Cricket", cp.role_title if cp else None] if b]
        return " • ".join(bits)
    p = user.profile
    bits = [b for b in [(p.sport if p else None), (p.sub_role if p else None), (p.age_group if p else None)] if b]
    return " • ".join(bits) or "Player"


def serialize_post_author(user: User, qo_score: int | None = None, author_type: str | None = None) -> dict:
    return {
        "id": str(user.id),
        "name": user.full_name,
        "type": author_type or user.role,
        "verified": user.verified,
        "role_line": role_line(user),
        "avatar_url": avatar_url(user),
        "qo_score": qo_score if qo_score is not None else (user.qo_score.score if user.qo_score else 0),
    }


def serialize_post(post: Post, viewer_liked: bool, viewer_bookmarked: bool) -> dict:
    return {
        "id": str(post.id),
        "author": serialize_post_author(post.author, author_type=post.author_type),
        "content": post.content,
        "hashtags": post.hashtags or [],
        "category": post.category,
        "media": [serialize_media_item(link.media) for link in post.media_links],
        "qo_points_earned": post.qo_points_earned,
        "counts": {"likes": post.like_count, "comments": post.comment_count, "shares": post.share_count},
        "viewer": {"liked": viewer_liked, "bookmarked": viewer_bookmarked},
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


def serialize_tier(tier: CardTier, score: int, unlocked: bool) -> dict:
    return {
        "level": tier.level,
        "label": tier.label,
        "threshold": tier.threshold,
        "hex": tier.hex,
        "unlocked": unlocked,
    }


def card_payload(tier: CardTier, score: int, next_tier: CardTier | None) -> dict:
    return {
        "tier": tier_slug(tier.label),
        "level": tier.level,
        "label": tier.label,
        "hex": tier.hex,
        "threshold": tier.threshold,
        "next_tier": (
            {
                "label": next_tier.label,
                "threshold": next_tier.threshold,
                "points_needed": max(0, tier.threshold - score),
            }
            if next_tier
            else None
        ),
    }
