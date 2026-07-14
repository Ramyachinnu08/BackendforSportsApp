"""Leagues, teams, membership and matches (spec §4.2–4.4, §5.2–5.7)."""
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import get_current_user, require_coach
from app.core.errors import (
    bad_request,
    conflict,
    forbidden,
    gone,
    not_found,
    unprocessable,
)
from app.core.security import utcnow
from app.db.base import get_db
from app.db.models import (
    CRICKET_TYPES,
    League,
    LeagueMember,
    Match,
    QoScore,
    Standing,
    Team,
    User,
    UserProfile,
)
from app.services import scoring
from app.services.identifiers import mint_league_code
from app.services.media_ops import store_upload
from app.services.notify import create_notification, deliver_notification
from app.services.serializers import avatar_url, media_url

router = APIRouter(tags=["leagues"])

_GENDER_MAP = {"men's": "mens", "mens": "mens", "women's": "womens", "womens": "womens"}
_TYPE_LABELS = {
    "gully": "Gully Cricket", "professional": "Professional Cricket", "box": "Box Cricket",
    "tennis_ball": "Tennis Ball Cricket", "hard_ball": "Hard Ball Cricket",
    "corporate": "Corporate Cricket", "beach": "Beach Cricket",
}


def _normalize_cricket_type(value: str) -> str:
    v = value.strip().lower().replace(" cricket", "").replace(" ", "_")
    if v not in CRICKET_TYPES:
        raise bad_request("VALIDATION_ERROR",
                          f"cricket_type must be one of: {', '.join(CRICKET_TYPES)}.", field="cricket_type")
    return v


async def _league_or_404(db: AsyncSession, league_id: uuid.UUID) -> League:
    league = (
        await db.execute(select(League).options(selectinload(League.teams), selectinload(League.logo))
                         .where(League.id == league_id))
    ).scalar_one_or_none()
    if league is None:
        raise not_found("LEAGUE_NOT_FOUND", "This league doesn't exist.")
    return league


# --------------------------------------------------------------------------- create
@router.post("/leagues", status_code=201)
async def create_league(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_coach),
    name: str = Form(...),
    cricket_type: str = Form(...),
    location: str | None = Form(default=None),
    gender: str = Form("Men's"),
    teams_count: int = Form(...),
    team_names: str = Form(...),  # JSON array or comma-separated
    season: str | None = Form(default=None),
    logo: UploadFile | None = File(default=None),
):
    ctype = _normalize_cricket_type(cricket_type)
    g = _GENDER_MAP.get(gender.strip().lower())
    if g is None:
        raise bad_request("VALIDATION_ERROR", "gender must be Men's or Women's.", field="gender")

    try:
        names = json.loads(team_names)
        if not isinstance(names, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        names = [n.strip() for n in team_names.split(",")]
    names = [str(n).strip() for n in names if str(n).strip()]

    if teams_count < 2:
        raise unprocessable("TEAMS_COUNT_MISMATCH", "A league needs at least 2 teams.", field="teams_count")
    if len(names) != teams_count:
        raise unprocessable("TEAMS_COUNT_MISMATCH",
                            f"teams_count is {teams_count} but {len(names)} team names were given.",
                            field="team_names")
    if len(set(n.lower() for n in names)) != len(names):
        raise unprocessable("TEAMS_COUNT_MISMATCH", "Team names must be unique within a league.",
                            field="team_names")

    code = await mint_league_code(db, name)
    league = League(owner_id=user.id, name=name.strip(), league_code=code, cricket_type=ctype,
                    gender=g, location=location, season=season, teams_count=teams_count, status="active")
    db.add(league)
    await db.flush()

    if logo is not None:
        media = await store_upload(db, user, logo, purpose="league_logo")
        league.logo_media_id = media.id

    teams = []
    for i, team_name in enumerate(names):
        team = Team(league_id=league.id, name=team_name, position=i)
        db.add(team)
        teams.append(team)
    await db.flush()
    for team in teams:
        db.add(Standing(league_id=league.id, team_id=team.id))

    notif = await create_notification(db, user.id, "league_created",
                                      f"'{league.name}' is live — share code {code} with your players",
                                      deep_link_id=str(league.id))
    # Announce to registered players so they can join: every onboarded
    # player gets a notification about the new league.
    player_ids = (
        await db.execute(
            select(User.id).where(User.role == "player",
                                  User.deleted_at.is_(None),
                                  User.player_id.is_not(None))
        )
    ).scalars().all()
    player_notifs = []
    for pid in player_ids:
        player_notifs.append(await create_notification(
            db, pid, "league_created",
            f"New league '{league.name}' by Coach {user.full_name} is open — join with code {code}",
            title="New League Created", deep_link_id=str(league.id)))
    await db.commit()
    background.add_task(deliver_notification, notif.id)
    for pn in player_notifs:
        background.add_task(deliver_notification, pn.id)

    await db.refresh(league, ["logo"])
    return {
        "id": str(league.id),
        "name": league.name,
        "league_code": league.league_code,
        "cricket_type": league.cricket_type,
        "gender": "Men's" if league.gender == "mens" else "Women's",
        "location": league.location,
        "logo_url": media_url(league.logo),
        "teams": [{"id": str(t.id), "name": t.name} for t in teams],
        "status": league.status,
        "created_at": league.created_at.isoformat(),
    }


# --------------------------------------------------------------------------- coach's leagues
@router.get("/coaches/me/leagues")
async def my_leagues(status: str | None = Query(default=None),
                     db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    q = (
        select(League).options(selectinload(League.logo))
        .where(League.owner_id == user.id).order_by(League.created_at.desc())
    )
    if status:
        q = q.where(League.status == status)
    leagues = (await db.execute(q)).scalars().all()
    items = []
    for lg in leagues:
        players_count = (
            await db.execute(select(func.count()).select_from(LeagueMember)
                             .where(LeagueMember.league_id == lg.id, LeagueMember.status == "active"))
        ).scalar_one()
        items.append({
            "id": str(lg.id),
            "name": lg.name,
            "league_code": lg.league_code,
            "logo_url": media_url(lg.logo),
            "cricket_type": lg.cricket_type,
            "teams_count": lg.teams_count,
            "players_count": players_count,
            "status": lg.status,
            "created_at": lg.created_at.isoformat(),
        })
    return {"items": items}


# --------------------------------------------------------------------------- join / exit
class JoinIn(BaseModel):
    league_code: str = Field(min_length=4, max_length=16)
    team_id: uuid.UUID


@router.post("/leagues/join")
async def join_league(body: JoinIn, background: BackgroundTasks,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    league = (
        await db.execute(select(League).where(League.league_code == body.league_code.upper().strip()))
    ).scalar_one_or_none()
    if league is None:
        raise not_found("INVALID_CODE", "That league code doesn't exist.")
    if league.status in ("completed", "archived"):
        raise gone("LEAGUE_CLOSED", "This league is no longer accepting players.")

    team = (
        await db.execute(select(Team).where(Team.id == body.team_id, Team.league_id == league.id))
    ).scalar_one_or_none()
    if team is None:
        raise not_found("LEAGUE_NOT_FOUND", "That team doesn't belong to this league.")

    existing = (
        await db.execute(select(LeagueMember).where(LeagueMember.league_id == league.id,
                                                    LeagueMember.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None and existing.status == "active":
        raise conflict("ALREADY_MEMBER", "You're already in this league.")

    team_size = (
        await db.execute(select(func.count()).select_from(LeagueMember)
                         .where(LeagueMember.team_id == team.id, LeagueMember.status == "active"))
    ).scalar_one()
    if team_size >= settings.max_players_per_team:
        raise forbidden("LEAGUE_FULL", "This team is full.")

    now = utcnow()
    if existing is not None:  # re-join after leaving
        existing.team_id = team.id
        existing.status = "active"
        existing.joined_at = now
        existing.left_at = None
    else:
        db.add(LeagueMember(league_id=league.id, team_id=team.id, user_id=user.id, joined_at=now))

    await scoring.grant_milestone(db, user.id, "first_league", subtitle=f"{team.name} • {now.strftime('%b %Y')}")
    notif = await create_notification(db, league.owner_id, "player_joined",
                                      f"{user.full_name} joined {team.name}",
                                      deep_link_id=str(league.id))
    await db.commit()
    background.add_task(deliver_notification, notif.id)

    return {
        "league": {"id": str(league.id), "name": league.name,
                   "cricket_type": _TYPE_LABELS.get(league.cricket_type, league.cricket_type)},
        "team": {"id": str(team.id), "name": team.name},
        "joined_at": now.isoformat(),
        "membership_status": "active",
    }


@router.delete("/leagues/{league_id}/membership", status_code=204)
async def exit_league(league_id: uuid.UUID, background: BackgroundTasks,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    member = (
        await db.execute(select(LeagueMember).where(LeagueMember.league_id == league_id,
                                                    LeagueMember.user_id == user.id,
                                                    LeagueMember.status == "active"))
    ).scalar_one_or_none()
    if member is None:
        raise not_found("LEAGUE_NOT_FOUND", "You're not a member of this league.")
    league = await _league_or_404(db, league_id)
    team = await db.get(Team, member.team_id)

    member.status = "left"
    member.left_at = utcnow()
    notif = await create_notification(db, league.owner_id, "league_update",
                                      f"{user.full_name} left {team.name if team else 'their team'}",
                                      title="Player Left", deep_link_id=str(league.id))
    await db.commit()
    background.add_task(deliver_notification, notif.id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- detail / code / players
@router.get("/leagues/by-code/{code}")
async def league_by_code(code: str, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Preview a league from its invite code (join screen's team picker).

    Deliberately excludes member identities — only per-team counts — since
    the caller hasn't joined yet and members may be minors.
    """
    league = (
        await db.execute(
            select(League).options(selectinload(League.teams), selectinload(League.logo))
            .where(League.league_code == code.upper().strip())
        )
    ).scalar_one_or_none()
    if league is None:
        raise not_found("INVALID_CODE", "That league code doesn't exist.")

    counts = {
        team_id: count
        for team_id, count in (
            await db.execute(
                select(LeagueMember.team_id, func.count())
                .where(LeagueMember.league_id == league.id, LeagueMember.status == "active")
                .group_by(LeagueMember.team_id)
            )
        ).all()
    }
    return {
        "id": str(league.id),
        "name": league.name,
        "league_code": league.league_code,
        "cricket_type": _TYPE_LABELS.get(league.cricket_type, league.cricket_type),
        "gender": "Men's" if league.gender == "mens" else "Women's",
        "location": league.location,
        "status": league.status,
        "logo_url": media_url(league.logo),
        "teams": [
            {"id": str(t.id), "name": t.name, "player_count": counts.get(t.id, 0)}
            for t in league.teams
        ],
    }


@router.get("/leagues/{league_id}")
async def league_detail(league_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    league = await _league_or_404(db, league_id)

    matches_count = (
        await db.execute(select(func.count()).select_from(Match).where(Match.league_id == league.id))
    ).scalar_one()
    standings_rows = (
        await db.execute(
            select(Standing, Team).join(Team, Team.id == Standing.team_id)
            .where(Standing.league_id == league.id)
            .order_by(Standing.points.desc(), Team.name)
        )
    ).all()

    my_member = (
        await db.execute(select(LeagueMember).where(LeagueMember.league_id == league.id,
                                                    LeagueMember.user_id == user.id,
                                                    LeagueMember.status == "active"))
    ).scalar_one_or_none()
    my_team_id = my_member.team_id if my_member else None

    standings, my_rank, my_points = [], None, None
    for position, (standing, team) in enumerate(standings_rows, start=1):
        is_me = my_team_id == team.id
        if is_me:
            my_rank, my_points = position, standing.points
        standings.append({
            "position": position,
            "team_id": str(team.id),
            "team_name": team.name,
            "played": standing.played,
            "won": standing.won,
            "lost": standing.lost,
            "points": standing.points,
            "is_me": is_me,
        })

    my_team = None
    if my_team_id:
        team = await db.get(Team, my_team_id)
        player_count = (
            await db.execute(select(func.count()).select_from(LeagueMember)
                             .where(LeagueMember.team_id == my_team_id, LeagueMember.status == "active"))
        ).scalar_one()
        my_team = {"id": str(team.id), "name": team.name, "player_count": player_count}

    return {
        "id": str(league.id),
        "name": league.name,
        "season": league.season,
        "cricket_type": _TYPE_LABELS.get(league.cricket_type, league.cricket_type),
        "gender": "Men's" if league.gender == "mens" else "Women's",
        "location": league.location,
        "status": league.status,
        "logo_url": media_url(league.logo),
        "is_owner": league.owner_id == user.id,
        "stats": {"teams": len(league.teams), "matches": matches_count,
                  "my_rank": my_rank, "my_points": my_points},
        "my_team": my_team,
        "teams": [{"id": str(t.id), "name": t.name} for t in league.teams],
        "standings": standings,
    }


@router.get("/leagues/{league_id}/code")
async def league_code(league_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    league = await _league_or_404(db, league_id)
    if league.owner_id != user.id:
        raise forbidden("NOT_LEAGUE_OWNER", "Only the league owner can view the invite code.")
    return {
        "league_code": league.league_code,
        "share_url": f"{settings.share_base_url}/join/{league.league_code}",
        "qr_url": None,  # QR is rendered client-side from share_url
    }


@router.get("/leagues/{league_id}/players")
async def league_players(league_id: uuid.UUID, team_id: uuid.UUID | None = Query(default=None),
                         db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _league_or_404(db, league_id)
    q = (
        select(LeagueMember, User, UserProfile, QoScore)
        .join(User, User.id == LeagueMember.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .outerjoin(QoScore, QoScore.user_id == User.id)
        .options(selectinload(User.avatar))
        .where(LeagueMember.league_id == league_id, LeagueMember.status == "active",
               User.deleted_at.is_(None))
        .order_by(User.full_name)
    )
    if team_id:
        q = q.where(LeagueMember.team_id == team_id)
    rows = (await db.execute(q)).all()
    return {
        "items": [
            {
                "id": str(u.id),
                "player_id": u.player_id,
                "name": u.full_name,
                "sub_role": p.sub_role if p else None,
                "team_id": str(m.team_id),
                "qo_score": qs.score if qs else 0,
                "avatar_url": avatar_url(u),
                "selected": False,
            }
            for m, u, p, qs in rows
        ]
    }


# --------------------------------------------------------------------------- matches
@router.get("/leagues/{league_id}/matches")
async def league_matches(league_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    league = await _league_or_404(db, league_id)
    matches = (
        await db.execute(
            select(Match).options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.league_id == league_id).order_by(Match.starts_at)
        )
    ).scalars().all()
    return {
        "league": {"id": str(league.id), "name": league.name},
        "items": [
            {
                "id": str(m.id),
                "team_a": {"id": str(m.team_a.id), "name": m.team_a.name},
                "team_b": {"id": str(m.team_b.id), "name": m.team_b.name},
                "starts_at": m.starts_at.isoformat(),
                "venue": m.venue,
                "status": m.status,
                "result": m.result,
                "score": {"a": m.score_a, "b": m.score_b} if (m.score_a or m.score_b) else None,
            }
            for m in matches
        ],
    }


class MatchIn(BaseModel):
    team_a_id: uuid.UUID
    team_b_id: uuid.UUID
    starts_at: datetime
    venue: str | None = None


@router.post("/leagues/{league_id}/matches", status_code=201)
async def create_match(league_id: uuid.UUID, body: MatchIn, background: BackgroundTasks,
                       db: AsyncSession = Depends(get_db), user: User = Depends(require_coach)):
    league = await _league_or_404(db, league_id)
    if league.owner_id != user.id:
        raise forbidden("NOT_LEAGUE_OWNER", "Only the league owner can schedule matches.")
    if body.team_a_id == body.team_b_id:
        raise bad_request("VALIDATION_ERROR", "A team can't play itself.", field="team_b_id")
    team_ids = {t.id for t in league.teams}
    if body.team_a_id not in team_ids or body.team_b_id not in team_ids:
        raise bad_request("VALIDATION_ERROR", "Both teams must belong to this league.", field="team_a_id")

    match = Match(league_id=league.id, team_a_id=body.team_a_id, team_b_id=body.team_b_id,
                  starts_at=body.starts_at, venue=body.venue)
    db.add(match)
    await db.flush()

    team_a = await db.get(Team, body.team_a_id)
    team_b = await db.get(Team, body.team_b_id)

    member_ids = (
        await db.execute(select(LeagueMember.user_id).where(LeagueMember.league_id == league.id,
                                                            LeagueMember.status == "active"))
    ).scalars().all()
    notif_ids = []
    for uid in member_ids:
        n = await create_notification(
            db, uid, "match_scheduled",
            f"{team_a.name} vs {team_b.name} • {body.starts_at.strftime('%d %b %Y %I:%M %p')}",
            deep_link_id=str(match.id),
        )
        notif_ids.append(n.id)
    await db.commit()
    for nid in notif_ids:
        background.add_task(deliver_notification, nid)

    from app.realtime import manager
    background.add_task(manager.emit, f"league:{league.id}:matches", "status_change",
                        {"match_id": str(match.id), "status": "scheduled"})

    return {
        "id": str(match.id),
        "league_id": str(league.id),
        "team_a": {"id": str(team_a.id), "name": team_a.name},
        "team_b": {"id": str(team_b.id), "name": team_b.name},
        "starts_at": match.starts_at.isoformat(),
        "venue": match.venue,
        "status": match.status,
    }
