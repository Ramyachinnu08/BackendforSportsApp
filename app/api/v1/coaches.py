"""Coach-facing endpoints (spec §3.7, §5)."""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import require_coach
from app.core.errors import bad_request, conflict, not_found, unprocessable
from app.core.security import utcnow
from app.db.base import get_db
from app.db.models import (
    Certification,
    CertificationDocument,
    CoachPlayer,
    CoachProfile,
    Follow,
    League,
    LeagueMember,
    Match,
    MatchParticipant,
    Media,
    Notification,
    Post,
    PostMedia,
    QoScore,
    Recommendation,
    Team,
    User,
    UserProfile,
)
from app.services import scoring
from app.services.media_ops import store_upload
from app.services.notify import create_notification, deliver_notification
from app.services.serializers import avatar_url, serialize_media_item

router = APIRouter(tags=["coaches"])


async def _get_or_create_coach_profile(db: AsyncSession, user: User) -> CoachProfile:
    cp = (await db.execute(select(CoachProfile).where(CoachProfile.user_id == user.id))).scalar_one_or_none()
    if cp is None:
        cp = CoachProfile(user_id=user.id)
        db.add(cp)
        await db.flush()
    return cp


@router.patch("/coaches/me")
async def update_coach(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_coach),
    full_name: str | None = Form(default=None),
    role_title: str | None = Form(default=None),
    academy: str | None = Form(default=None),
    location: str | None = Form(default=None),
    certification: str | None = Form(default=None),
    experience_years: int | None = Form(default=None),
    sport: str | None = Form(default=None),
    bio: str | None = Form(default=None),
    avatar: UploadFile | None = File(default=None),
):
    cp = await _get_or_create_coach_profile(db, user)
    if full_name is not None:
        user.full_name = full_name.strip()
    for field, value in [("role_title", role_title), ("academy", academy), ("location", location),
                         ("certification", certification), ("sport", sport), ("bio", bio)]:
        if value is not None:
            setattr(cp, field, value)
    if experience_years is not None:
        cp.experience_years = experience_years
    if avatar is not None:
        media = await store_upload(db, user, avatar, purpose="avatar")
        user.avatar_media_id = media.id
    if user.onboarding_stage != "complete":
        user.onboarding_stage = "complete"
    await db.commit()
    await db.refresh(user, ["avatar"])
    return {
        "coach_id": str(user.id),
        "full_name": user.full_name,
        "role_title": cp.role_title,
        "academy": cp.academy,
        "location": cp.location,
        "certification": cp.certification,
        "experience_years": cp.experience_years,
        "sport": cp.sport,
        "bio": cp.bio,
        "avatar_url": avatar_url(user),
        "verified": user.verified,
        "coach_score": cp.coach_score,
    }


@router.get("/coaches/me/dashboard")
async def coach_dashboard(db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    cp = await _get_or_create_coach_profile(db, user)

    players = (
        await db.execute(select(func.count()).select_from(CoachPlayer)
                         .where(CoachPlayer.coach_id == user.id, CoachPlayer.status == "active"))
    ).scalar_one()
    league_ids = (await db.execute(select(League.id).where(League.owner_id == user.id))).scalars().all()
    matches = 0
    win_rate = 0.0
    if league_ids:
        matches = (
            await db.execute(select(func.count()).select_from(Match).where(Match.league_id.in_(league_ids)))
        ).scalar_one()
        # win rate: fraction of roster players' completed-match participations on the winning side
        roster_ids = (
            await db.execute(select(CoachPlayer.user_id).where(CoachPlayer.coach_id == user.id))
        ).scalars().all()
        if roster_ids:
            rows = (
                await db.execute(
                    select(MatchParticipant.team_id, Match.result, Match.team_a_id, Match.team_b_id)
                    .join(Match, Match.id == MatchParticipant.match_id)
                    .where(MatchParticipant.user_id.in_(roster_ids), Match.status == "completed")
                )
            ).all()
            if rows:
                wins = sum(
                    1 for team_id, result, a, b in rows
                    if (result == "team_a_won" and team_id == a) or (result == "team_b_won" and team_id == b)
                )
                win_rate = round(wins / len(rows), 2)

    cert = (
        await db.execute(select(Certification).where(Certification.coach_id == user.id)
                         .order_by(Certification.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    unread = (
        await db.execute(select(func.count()).select_from(Notification)
                         .where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    ).scalar_one()

    return {
        "coach": {
            "full_name": user.full_name,
            "role_title": cp.role_title,
            "academy": cp.academy,
            "verified": user.verified,
            "avatar_url": avatar_url(user),
        },
        "quick_stats": {"players": players, "matches": matches, "win_rate": win_rate},
        "league_count": len(league_ids),
        "certification_status": cert.status if cert else "not_submitted",
        "unread_notifications": unread,
    }


# --------------------------------------------------------------------------- roster
def _roster_item(user_row: User, profile: UserProfile | None, qs: QoScore | None,
                 tiers, status: str) -> dict:
    tier = scoring.resolve_tier(qs.score if qs else 0, tiers)
    return {
        "user_id": str(user_row.id),
        "player_id": user_row.player_id,
        "name": user_row.full_name,
        "sub_role": profile.sub_role if profile else None,
        "team": profile.team if profile else None,
        "qo_score": qs.score if qs else 0,
        "card_tier": scoring.tier_slug(tier.label),
        "avatar_url": avatar_url(user_row),
        "last_active_at": user_row.updated_at.isoformat() if user_row.updated_at else None,
        "status": status,
    }


@router.get("/coaches/me/players")
async def roster(status: str = Query("active", pattern="^(active|inactive|all)$"),
                 db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    q = (
        select(CoachPlayer, User, UserProfile, QoScore)
        .join(User, User.id == CoachPlayer.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .outerjoin(QoScore, QoScore.user_id == User.id)
        .options(selectinload(User.avatar))
        .where(CoachPlayer.coach_id == user.id, User.deleted_at.is_(None))
        .order_by(CoachPlayer.created_at.desc())
    )
    if status != "all":
        q = q.where(CoachPlayer.status == status)
    rows = (await db.execute(q)).all()
    tiers = await scoring.load_tiers(db)
    return {"items": [_roster_item(u, p, qs, tiers, cp.status) for cp, u, p, qs in rows]}


class AddPlayerIn(BaseModel):
    player_id: str = Field(min_length=4, max_length=8)


@router.get("/players/directory")
async def player_directory(q: str | None = Query(None, max_length=80),
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(require_coach)):
    """All registered (onboarded) students, so coaches can browse the
    full registry before typing a name — with roster membership flags."""
    query = (
        select(User, UserProfile, QoScore)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .outerjoin(QoScore, QoScore.user_id == User.id)
        .options(selectinload(User.avatar))
        .where(User.role == "player",
               User.deleted_at.is_(None),
               User.player_id.is_not(None))
        .order_by(User.full_name.asc())
        .limit(200)
    )
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(User.full_name.ilike(like),
                                User.player_id.ilike(like)))
    rows = (await db.execute(query)).all()
    on_roster = set(
        (await db.execute(
            select(CoachPlayer.user_id)
            .where(CoachPlayer.coach_id == user.id,
                   CoachPlayer.status == "active")
        )).scalars().all()
    )
    tiers = await scoring.load_tiers(db)
    items = []
    for u, p, qs in rows:
        item = _roster_item(u, p, qs, tiers, "active")
        item["on_roster"] = u.id in on_roster
        item.pop("status", None)
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/coaches/me/players", status_code=201)
async def add_player(body: AddPlayerIn, background: BackgroundTasks,
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    player = (
        await db.execute(
            select(User).options(selectinload(User.avatar))
            .where(User.player_id == body.player_id.upper().strip(), User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if player is None:
        raise not_found("PLAYER_NOT_FOUND", f"No player found with ID {body.player_id}.")
    existing = (
        await db.execute(select(CoachPlayer).where(CoachPlayer.coach_id == user.id,
                                                   CoachPlayer.user_id == player.id))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == "active":
            raise conflict("ALREADY_ADDED", "This player is already on your roster.")
        existing.status = "active"
    else:
        db.add(CoachPlayer(coach_id=user.id, user_id=player.id))

    notif = await create_notification(db, player.id, "league_update",
                                      f"Coach {user.full_name} added you to their roster",
                                      title="Added by a Coach")
    await db.commit()
    background.add_task(deliver_notification, notif.id)

    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == player.id))).scalar_one_or_none()
    qs = (await db.execute(select(QoScore).where(QoScore.user_id == player.id))).scalar_one_or_none()
    tiers = await scoring.load_tiers(db)
    return _roster_item(player, profile, qs, tiers, "active")


@router.delete("/coaches/me/players/{player_user_id}")
async def remove_player(player_user_id: uuid.UUID, background: BackgroundTasks,
                        db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    """Kick a player from the coach's squad."""
    cp = (
        await db.execute(select(CoachPlayer).where(CoachPlayer.coach_id == user.id,
                                                   CoachPlayer.user_id == player_user_id))
    ).scalar_one_or_none()
    if cp is None:
        raise not_found("PLAYER_NOT_ON_ROSTER", "This player is not on your roster.")
    await db.delete(cp)
    notif = await create_notification(db, player_user_id, "league_update",
                                      f"Coach {user.full_name} removed you from their roster",
                                      title="Roster Update")
    await db.commit()
    background.add_task(deliver_notification, notif.id)
    return {"removed": True, "player_user_id": str(player_user_id)}


# --------------------------------------------------------------------------- recommendations
class RecommendIn(BaseModel):
    player_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=1000)
    rating: float | None = Field(default=None, ge=0, le=5)
    target: str | None = Field(default=None, pattern="^(club|league|scout)$")


@router.post("/recommendations", status_code=201)
async def recommend(body: RecommendIn, background: BackgroundTasks,
                    db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    cooldown_start = utcnow() - timedelta(days=settings.recommendation_cooldown_days)
    created, notif_ids = [], []
    for pid in body.player_ids:
        player = await db.get(User, pid)
        if player is None or player.role != "player" or player.deleted_at is not None:
            raise not_found("PLAYER_NOT_FOUND", f"Player {pid} not found.")
        recent = (
            await db.execute(
                select(Recommendation.id).where(Recommendation.coach_id == user.id,
                                                Recommendation.player_id == pid,
                                                Recommendation.created_at >= cooldown_start)
            )
        ).first()
        if recent:
            raise conflict("ALREADY_RECOMMENDED",
                           f"You already recommended {player.full_name} in the last "
                           f"{settings.recommendation_cooldown_days} days.")

        reco = Recommendation(coach_id=user.id, player_id=pid, note=body.note,
                              rating=body.rating, target=body.target)
        db.add(reco)
        await db.flush()

        await scoring.award_points(
            db, pid, source="recommendation", source_id=reco.id,
            points=settings.points_per_recommendation,
            reason=f"Recommended by Coach {user.full_name}",
            idempotency_key=f"reco:{reco.id}",
        )
        await scoring.grant_milestone(db, pid, "coach_recommended",
                                      subtitle=f"By Coach {user.full_name}")
        notif = await create_notification(
            db, pid, "coach_recommended",
            f"Coach {user.full_name} recommended you (+{settings.points_per_recommendation} Qo points)",
        )
        notif_ids.append(notif.id)
        created.append({"id": str(reco.id), "player_id": str(pid), "status": reco.status,
                        "created_at": reco.created_at.isoformat() if reco.created_at else None})

        profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == pid))).scalar_one_or_none()
        category = scoring.ranking_category(profile)
        if category:
            await scoring.recompute_rankings(db, category)

    await db.commit()
    for nid in notif_ids:
        background.add_task(deliver_notification, nid)
    return {"recommendations": created}


# --------------------------------------------------------------------------- certification
@router.get("/coaches/me/certification")
async def get_certification(db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    cert = (
        await db.execute(
            select(Certification).options(selectinload(Certification.documents)
                                          .selectinload(CertificationDocument.media))
            .where(Certification.coach_id == user.id)
            .order_by(Certification.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if cert is None:
        return {"status": "not_submitted"}
    return {
        "certification_id": str(cert.id),
        "certification_level": cert.certification_level,
        "issuing_body": cert.issuing_body,
        "issued_on": cert.issued_on.isoformat() if cert.issued_on else None,
        "status": cert.status,
        "rejection_reason": cert.rejection_reason,
        "submitted_at": cert.created_at.isoformat() if cert.created_at else None,
        "documents": [serialize_media_item(d.media) for d in cert.documents],
    }


@router.post("/coaches/me/certification", status_code=202)
async def submit_certification(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_coach),
    certification_level: str = Form(...),
    issuing_body: str | None = Form(default=None),
    issued_on: str | None = Form(default=None),
    documents: list[UploadFile] = File(default=[]),
):
    if len(documents) > settings.max_cert_documents:
        raise unprocessable("VALIDATION_ERROR",
                            f"Max {settings.max_cert_documents} documents.", field="documents")
    pending = (
        await db.execute(select(Certification.id).where(Certification.coach_id == user.id,
                                                        Certification.status == "under_review"))
    ).first()
    if pending:
        raise conflict("ALREADY_SUBMITTED", "You already have a certification under review.")

    issued = None
    if issued_on:
        try:
            issued = date.fromisoformat(issued_on)
        except ValueError:
            raise bad_request("VALIDATION_ERROR", "issued_on must be YYYY-MM-DD.", field="issued_on")

    cert = Certification(coach_id=user.id, certification_level=certification_level,
                         issuing_body=issuing_body, issued_on=issued, status="under_review")
    db.add(cert)
    await db.flush()
    for doc in documents:
        media = await store_upload(db, user, doc, purpose="certification_doc")
        db.add(CertificationDocument(certification_id=cert.id, media_id=media.id))

    cp = await _get_or_create_coach_profile(db, user)
    cp.certification = certification_level
    await db.commit()
    return {"certification_id": str(cert.id), "status": "under_review",
            "submitted_at": cert.created_at.isoformat(), "eta_days": 5}


# --------------------------------------------------------------------------- coach playbook
@router.get("/coaches/me/playbook")
async def coach_playbook(tab: str | None = Query(default=None),
                         db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    cp = await _get_or_create_coach_profile(db, user)
    followers = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.followee_id == user.id))
    ).scalar_one()
    following = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.follower_id == user.id))
    ).scalar_one()
    players_trained = (
        await db.execute(select(func.count()).select_from(CoachPlayer)
                         .where(CoachPlayer.coach_id == user.id))
    ).scalar_one()
    tournaments = (
        await db.execute(select(func.count()).select_from(League).where(League.owner_id == user.id))
    ).scalar_one()

    tab_keys = ["coaching", "certificates", "teams", "trophies"]
    category_map = {"coaching": "playing", "certificates": "certificates", "teams": "team", "trophies": "trophies"}
    wanted = [tab] if tab in tab_keys else tab_keys
    tabs = {}
    for key in wanted:
        media_rows = (
            await db.execute(
                select(Media, PostMedia.post_id)
                .join(PostMedia, PostMedia.media_id == Media.id)
                .join(Post, Post.id == PostMedia.post_id)
                .where(Post.author_id == user.id, Post.category == category_map[key],
                       Post.deleted_at.is_(None))
                .order_by(Media.created_at.desc()).limit(24)
            )
        ).all()
        tabs[key] = [serialize_media_item(m) | {"date": m.created_at.date().isoformat(),
                                                "post_id": str(pid)} for m, pid in media_rows]

    return {
        "profile": {
            "full_name": user.full_name,
            "role_title": cp.role_title,
            "academy": cp.academy,
            "location": cp.location,
            "certification": cp.certification,
            "avatar_url": avatar_url(user),
            "verified": user.verified,
            "about": cp.bio,
        },
        "coach_score": {"current": cp.coach_score, "rank": cp.rank, "rank_scope": cp.rank_scope},
        "stats": {"players_trained": players_trained, "tournaments": tournaments,
                  "followers": followers, "following": following,
                  "experience_years": cp.experience_years},
        "tabs": tabs,
    }
