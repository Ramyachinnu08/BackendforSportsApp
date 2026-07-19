"""Notifications: DB row + realtime emit + push fan-out.

The 15-type catalogue (spec §8.5) supplies icon / accent / deep-link defaults
so callers only pass what's unique.  Push delivery happens off the request
path (FastAPI background task); swap in Celery/queue for scale without
changing call sites.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import SessionLocal
from app.db.models import Device, Notification, UserSettings
from app.realtime import manager
from app.services.providers import get_push_provider

log = logging.getLogger("sportyqo.notify")

# type → (title default, icon, accent hex, deep_link template)
CATALOGUE: dict[str, tuple[str, str, str, str]] = {
    "points_added":         ("Points Added!",          "emoji_events",     "#FFB300", "sportyqo://performance"),
    "rank_improved":        ("Rank Improved!",         "trending_up",      "#00C853", "sportyqo://performance"),
    "achievement_unlocked": ("Achievement Unlocked!",  "military_tech",    "#7B2FFF", "sportyqo://qo-score"),
    "new_follower":         ("New Follower",           "person_add",       "#2196F3", "sportyqo://profile/{id}"),
    "post_liked":           ("Post Liked",             "favorite",         "#FF3B30", "sportyqo://post/{id}"),
    "post_commented":       ("New Comment",            "chat_bubble",      "#2196F3", "sportyqo://post/{id}"),
    "coach_recommended":    ("Coach Recommended You",  "thumb_up",         "#00C853", "sportyqo://playbook"),
    "league_update":        ("League Update",          "emoji_events",     "#FF9800", "sportyqo://league/{id}"),
    "league_created":       ("League Created",         "emoji_events",     "#7B2FFF", "sportyqo://league/{id}"),
    "match_scheduled":      ("Match Scheduled",        "sports_cricket",   "#2196F3", "sportyqo://match/{id}"),
    "match_result":         ("Match Result",           "scoreboard",       "#FF9800", "sportyqo://match/{id}"),
    "player_joined":        ("New Player Joined!",     "group_add",        "#00C853", "sportyqo://league/{id}"),
    "certification_update": ("Certification Update",   "verified",         "#7B2FFF", "sportyqo://certification"),
    "performance_report":   ("Weekly Performance",     "insights",         "#2196F3", "sportyqo://performance"),
    "new_message":          ("New Message",            "mail",             "#2196F3", "sportyqo://chat/{id}"),
}


async def create_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    type_: str,
    body: str,
    title: str | None = None,
    deep_link_id: str | None = None,
    payload: dict | None = None,
) -> Notification:
    default_title, icon, accent, link_tpl = CATALOGUE.get(type_, (type_.replace("_", " ").title(), "notifications", "#2196F3", "sportyqo://home"))
    notif = Notification(
        user_id=user_id,
        type=type_,
        title=title or default_title,
        body=body,
        icon=icon,
        accent=accent,
        deep_link=link_tpl.format(id=deep_link_id) if "{id}" in link_tpl else link_tpl,
        payload=payload,
    )
    prefs = (
        await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id))
    ).scalar_one_or_none()
    if prefs is not None and not prefs.notifications_enabled:
        # Notifications toggle is OFF — return an unsaved row so callers
        # can still read .id; nothing lands in the bell and delivery no-ops.
        return notif
    db.add(notif)
    await db.flush()
    return notif


def serialize_notification(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "icon": n.icon,
        "accent": n.accent,
        "read": n.read_at is not None,
        "deep_link": n.deep_link,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


async def deliver_notification(notification_id: uuid.UUID) -> None:
    """Background: WS emit + push fan-out. Opens its own session."""
    try:
        async with SessionLocal() as db:
            notif = await db.get(Notification, notification_id)
            if notif is None:
                return
            await manager.emit(f"user:{notif.user_id}:notifications", "created", serialize_notification(notif))

            prefs = (
                await db.execute(select(UserSettings).where(UserSettings.user_id == notif.user_id))
            ).scalar_one_or_none()
            if prefs and not prefs.notifications_enabled:
                return
            tokens = (
                await db.execute(select(Device.fcm_token).where(Device.user_id == notif.user_id))
            ).scalars().all()
            if tokens:
                await get_push_provider().send(
                    list(tokens), notif.title, notif.body,
                    data={"type": notif.type, "deep_link": notif.deep_link},
                )
    except Exception:  # background task — never crash the loop
        log.exception("notification delivery failed")
