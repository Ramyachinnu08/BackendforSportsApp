"""User profile, settings, sport selection (Player ID minting), public
profiles and follow/track (spec §3.5–3.6, §4.9, §6.4, §8.4)."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import get_current_user, require_player
from app.core.errors import bad_request, conflict, not_found
from app.core.security import utcnow
from app.db.base import get_db
from app.db.models import (
    Follow,
    FriendRequest,
    Friendship,
    Media,
    Post,
    PostMedia,
    QoScore,
    Session,
    User,
    UserProfile,
    UserSettings,
)
from app.services import scoring
from app.services.identifiers import allocate_player_id, derive_age_group
from app.services.media_ops import store_upload
from app.services.notify import create_notification, deliver_notification
from app.services.serializers import avatar_url, media_url, serialize_media_item

router = APIRouter(tags=["users"])


async def _get_or_create_profile(db: AsyncSession, user: User) -> UserProfile:
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        await db.flush()
    return profile


def _me_payload(user: User, profile: UserProfile | None) -> dict:
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "player_id": user.player_id,
        "phone": user.phone,
        "avatar_url": avatar_url(user),
        "verified": user.verified,
        "email_verified": user.email_verified_at is not None,
        "onboarding_stage": user.onboarding_stage,
        "profile": {
            "dob": profile.dob.isoformat() if profile and profile.dob else None,
            "sport": profile.sport if profile else None,
            "sub_role": profile.sub_role if profile else None,
            "age_group": profile.age_group if profile else None,
            "team": profile.team if profile else None,
            "location": profile.location if profile else None,
            "school": profile.school if profile else None,
            "bio": profile.bio if profile else None,
            "hashtags": (profile.hashtags if profile else None) or [],
        } if user.role == "player" else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users/me")
async def get_me(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    return _me_payload(user, profile)


@router.patch("/users/me")
async def update_me(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    full_name: str | None = Form(default=None),
    dob: str | None = Form(default=None),
    role_position: str | None = Form(default=None),
    team: str | None = Form(default=None),
    location: str | None = Form(default=None),
    school: str | None = Form(default=None),
    bio: str | None = Form(default=None),
    hashtags: str | None = Form(default=None),  # comma separated
    avatar: UploadFile | None = File(default=None),
):
    """Player profile update — multipart when an avatar is attached, form/JSON
    fields otherwise (spec §3.5). Coaches use PATCH /coaches/me."""
    profile = await _get_or_create_profile(db, user)

    if full_name is not None:
        user.full_name = full_name.strip()
    if dob is not None:
        try:
            profile.dob = date.fromisoformat(dob)
        except ValueError:
            raise bad_request("VALIDATION_ERROR", "dob must be YYYY-MM-DD.", field="dob")
        profile.age_group = derive_age_group(profile.dob)
    if role_position is not None:
        profile.sub_role = role_position
    if team is not None:
        profile.team = team
    if location is not None:
        profile.location = location
    if school is not None:
        profile.school = school
    if bio is not None:
        profile.bio = bio
    if hashtags is not None:
        profile.hashtags = [h.strip().lstrip("#") for h in hashtags.split(",") if h.strip()]

    if avatar is not None:
        media = await store_upload(db, user, avatar, purpose="avatar")
        user.avatar_media_id = media.id

    if user.role == "player" and user.onboarding_stage == "profile":
        user.onboarding_stage = "sport"

    await db.commit()
    await db.refresh(user, ["avatar"])
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "avatar_url": avatar_url(user),
        "dob": profile.dob.isoformat() if profile.dob else None,
        "age_group": profile.age_group,
        "location": profile.location,
        "updated_at": utcnow().isoformat(),
    }


class SportIn(BaseModel):
    sport: str = Field(min_length=1)
    sub_role: str | None = None
    age_group: str | None = None


@router.post("/users/me/sport", status_code=201)
async def select_sport(body: SportIn, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require_player)):
    """Sport selection → server-side Player ID allocation (idempotent —
    replaces the client's `millisecondsSinceEpoch % 999` hack, Appendix A #1)."""
    profile = await _get_or_create_profile(db, user)
    profile.sport = body.sport
    if body.sub_role:
        profile.sub_role = body.sub_role
    profile.age_group = derive_age_group(profile.dob) if profile.dob else (body.age_group or profile.age_group)

    if user.player_id is None:
        user.player_id = await allocate_player_id(db)

    await scoring.award_points(db, user.id, source="signup",
                               points=settings.points_signup_bonus,
                               reason="Welcome bonus — joined SportyQo",
                               idempotency_key=f"signup:{user.id}")
    qs = await scoring.recompute_score(db, user.id)
    await scoring.grant_milestone(db, user.id, "started_playing", subtitle="Joined SportyQo")
    user.onboarding_stage = "complete"
    await db.commit()

    tiers = await scoring.load_tiers(db)
    tier = scoring.resolve_tier(qs.score, tiers)
    return {
        "player_id": user.player_id,
        "sport": profile.sport,
        "sub_role": profile.sub_role,
        "age_group": profile.age_group,
        "qo_score": qs.score,
        "card_tier": scoring.tier_slug(tier.label),
        "issued_at": utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- settings
class SettingsPatch(BaseModel):
    notifications_enabled: bool | None = None
    email_alerts: bool | None = None
    dark_mode: bool | None = None
    private_profile: bool | None = None
    location_access: bool | None = None


def _settings_payload(s: UserSettings) -> dict:
    return {
        "notifications_enabled": s.notifications_enabled,
        "email_alerts": s.email_alerts,
        "dark_mode": s.dark_mode,
        "private_profile": s.private_profile,
        "location_access": s.location_access,
    }


async def _get_or_create_settings(db: AsyncSession, user: User) -> UserSettings:
    s = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if s is None:
        s = UserSettings(user_id=user.id)
        db.add(s)
        await db.flush()
    return s


@router.get("/users/me/settings")
async def get_settings_route(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return _settings_payload(await _get_or_create_settings(db, user))


@router.patch("/users/me/settings")
async def patch_settings(body: SettingsPatch, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    s = await _get_or_create_settings(db, user)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(s, field, value)
    await db.commit()
    return _settings_payload(s)


# --------------------------------------------------------------------------- account deletion (DPDP)
@router.delete("/users/me", status_code=202)
async def delete_me(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Soft delete with a 30-day grace period (spec §4 checklist; DPDP deletion
    rights). A purge job permanently erases data after the grace window."""
    user.deleted_at = utcnow()
    sessions = (await db.execute(select(Session).where(Session.user_id == user.id,
                                                       Session.revoked_at.is_(None)))).scalars().all()
    for s in sessions:
        s.revoked_at = utcnow()
    await db.commit()
    return {"deleted": True, "grace_days": settings.account_deletion_grace_days,
            "purge_after": user.deleted_at.isoformat()}


# --------------------------------------------------------------------------- public profile
@router.get("/users/{user_id}/profile")
async def public_profile(user_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         viewer: User = Depends(get_current_user)):
    target = (
        await db.execute(
            select(User).options(selectinload(User.profile), selectinload(User.coach_profile),
                                 selectinload(User.qo_score), selectinload(User.avatar))
            .where(User.id == user_id, User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if target is None:
        raise not_found("PLAYER_NOT_FOUND", "This profile doesn't exist.")

    posts_count = (
        await db.execute(select(func.count()).select_from(Post).where(Post.author_id == target.id,
                                                                      Post.deleted_at.is_(None)))
    ).scalar_one()
    followers = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.followee_id == target.id))
    ).scalar_one()
    following = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.follower_id == target.id))
    ).scalar_one()
    viewer_following = (
        await db.execute(select(Follow.id).where(Follow.follower_id == viewer.id, Follow.followee_id == target.id))
    ).first() is not None
    is_friend = (
        await db.execute(
            select(Friendship.id).where(
                ((Friendship.user_a_id == viewer.id) & (Friendship.user_b_id == target.id))
                | ((Friendship.user_a_id == target.id) & (Friendship.user_b_id == viewer.id))
            )
        )
    ).first() is not None

    target_settings = (
        await db.execute(select(UserSettings).where(UserSettings.user_id == target.id))
    ).scalar_one_or_none()
    is_private = bool(target_settings and target_settings.private_profile)
    can_see_tabs = (viewer.id == target.id) or viewer_following or not is_private

    tabs: dict = {}
    tab_keys = ["playing", "certificate", "teams", "trophies", "update"]
    category_map = {"playing": "playing", "certificate": "certificates", "teams": "team", "trophies": "trophies"}
    for key in tab_keys:
        if not can_see_tabs:
            tabs[key] = {"count": 0, "items": []}
            continue
        if key == "update":
            recent = (
                await db.execute(
                    select(Post).where(Post.author_id == target.id, Post.deleted_at.is_(None))
                    .order_by(Post.created_at.desc()).limit(12)
                )
            ).scalars().all()
            tabs[key] = {"count": posts_count,
                         "items": [{"id": str(p.id), "type": "post", "content": p.content[:140],
                                    "created_at": p.created_at.isoformat()} for p in recent]}
        else:
            media_rows = (
                await db.execute(
                    select(Media)
                    .join(PostMedia, PostMedia.media_id == Media.id)
                    .join(Post, Post.id == PostMedia.post_id)
                    .where(Post.author_id == target.id, Post.category == category_map[key],
                           Post.deleted_at.is_(None), Media.acl == "public")
                    .order_by(Media.created_at.desc()).limit(12)
                )
            ).scalars().all()
            count = (
                await db.execute(
                    select(func.count()).select_from(PostMedia)
                    .join(Post, Post.id == PostMedia.post_id)
                    .where(Post.author_id == target.id, Post.category == category_map[key],
                           Post.deleted_at.is_(None))
                )
            ).scalar_one()
            tabs[key] = {"count": count, "items": [serialize_media_item(m) for m in media_rows]}

    if target.role == "coach":
        cp = target.coach_profile
        sport_line = cp.role_title if cp and cp.role_title else "Coach"
        location = (cp.location if cp else None) or ""
        bio = (cp.bio if cp else None) or ""
        hashtags: list[str] = []
    else:
        p = target.profile
        sport_line = f"{p.sport} Player" if p and p.sport else "Player"
        location = (p.location if p else None) or ""   # city-level only; dob/school/phone never exposed
        bio = (p.bio if p else None) or ""
        hashtags = (p.hashtags if p else None) or []

    if target_settings is not None and not target_settings.location_access:
        location = ""   # Location Access toggle OFF — hide from public profile

    return {
        "id": str(target.id),
        "name": target.full_name,
        "type": target.role,
        "verified": target.verified,
        "sport_line": sport_line,
        "location": location,
        "avatar_url": avatar_url(target),
        "bio": bio,
        "hashtags": hashtags,
        "qo_score": target.qo_score.score if target.qo_score else 0,
        "private": is_private and not can_see_tabs,
        "counts": {"posts": posts_count, "followers": followers, "following": following},
        "viewer": {"following": viewer_following, "is_friend": is_friend, "can_message": True},
        "tabs": tabs,
    }


# --------------------------------------------------------------------------- track / untrack
@router.post("/users/{user_id}/track")
async def track(user_id: uuid.UUID, background: BackgroundTasks,
                db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    if user_id == viewer.id:
        raise bad_request("VALIDATION_ERROR", "You can't follow yourself.")
    target = await db.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise not_found("PLAYER_NOT_FOUND", "This profile doesn't exist.")
    existing = (
        await db.execute(select(Follow).where(Follow.follower_id == viewer.id, Follow.followee_id == user_id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(Follow(follower_id=viewer.id, followee_id=user_id))
        notif = await create_notification(db, user_id, "new_follower",
                                          f"{viewer.full_name} started following you",
                                          deep_link_id=str(viewer.id))
        await db.commit()
        background.add_task(deliver_notification, notif.id)
    count = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.followee_id == user_id))
    ).scalar_one()
    return {"tracking": True, "follower_count": count}


@router.delete("/users/{user_id}/track")
async def untrack(user_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                  viewer: User = Depends(get_current_user)):
    existing = (
        await db.execute(select(Follow).where(Follow.follower_id == viewer.id, Follow.followee_id == user_id))
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
        await db.commit()
    count = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.followee_id == user_id))
    ).scalar_one()
    return {"tracking": False, "follower_count": count}
                      # ── Connections (followers / following / teams lists) ──────────────────
@router.get("/users/me/connections")
async def my_connections(kind: str = "followers",
                         db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Instagram-style lists behind the Playbook stats row.
    kind = followers | following | teams"""
    from app.db.models import League, LeagueMember, Team

    if kind == "teams":
        rows = (
            await db.execute(
                select(Team, League)
                .join(LeagueMember, LeagueMember.team_id == Team.id)
                .join(League, Team.league_id == League.id)
                .where(LeagueMember.user_id == user.id,
                       LeagueMember.status == "active")
                .order_by(LeagueMember.created_at.desc())
            )
        ).all()
        return {"items": [
            {"id": str(t.id), "name": t.name,
             "league_name": lg.name,
             "league_code": lg.league_code}
            for t, lg in rows
        ]}

    if kind == "following":
        cond = Follow.follower_id == user.id
        other_col = Follow.followee_id
    else:  # followers (default)
        cond = Follow.followee_id == user.id
        other_col = Follow.follower_id

    rows = (
        await db.execute(
            select(User)
            .join(Follow, other_col == User.id)
            .where(cond, User.deleted_at.is_(None))
            .order_by(Follow.created_at.desc())
        )
    ).scalars().all()

    ids = [u.id for u in rows]
    i_follow: set = set()
    if ids:
        i_follow = {
            fid for (fid,) in (
                await db.execute(
                    select(Follow.followee_id)
                    .where(Follow.follower_id == user.id,
                           Follow.followee_id.in_(ids))
                )
            ).all()
        }

    return {"items": [
        {"id": str(u.id),
         "full_name": u.full_name,
         "player_id": u.player_id,
         "role": u.role,
         "verified": u.verified,
         "avatar_url": avatar_url(u),
         "viewer_following": u.id in i_follow}
        for u in rows
    ]}
