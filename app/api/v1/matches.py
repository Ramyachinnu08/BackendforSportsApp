"""Match points submission — the only score-mutation path besides
recommendations (spec §5.8).  Idempotent via required Idempotency-Key."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_coach
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.db.base import get_db
from app.db.models import (
    League,
    LeagueMember,
    Match,
    MatchParticipant,
    QoScore,
    Standing,
    Team,
    User,
    UserProfile,
)
from app.core.config import settings
from app.services import scoring
from app.services.notify import create_notification, deliver_notification

router = APIRouter(prefix="/matches", tags=["matches"])


class PlayerStat(BaseModel):
    user_id: uuid.UUID
    runs: int = Field(0, ge=0, le=1000)
    balls: int = Field(0, ge=0, le=1000)
    wickets: int = Field(0, ge=0, le=10)
    catches: int = Field(0, ge=0, le=20)
    is_mom: bool = False


class PointsIn(BaseModel):
    result: str = Field(pattern="^(team_a_won|team_b_won|draw|abandoned)$")
    player_stats: list[PlayerStat] = Field(min_length=1, max_length=60)
    score_a: dict | None = None
    score_b: dict | None = None


@router.post("/{match_id}/points")
async def submit_points(
    match_id: uuid.UUID,
    body: PointsIn,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_coach),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise bad_request("MISSING_FIELD", "Idempotency-Key header is required.", field="Idempotency-Key")

    match = await db.get(Match, match_id)
    if match is None:
        raise not_found("LEAGUE_NOT_FOUND", "This match doesn't exist.")
    league = await db.get(League, match.league_id)
    if league.owner_id != user.id:
        raise forbidden("NOT_LEAGUE_OWNER", "Only the league owner can submit match points.")

    # Replay of the same submission → return the stored outcome (never double-award).
    if match.status == "completed":
        if match.points_idempotency_key == idempotency_key:
            participants = (
                await db.execute(select(MatchParticipant).where(MatchParticipant.match_id == match.id))
            ).scalars().all()
            return {"match_id": str(match.id), "status": match.status,
                    "qo_points_awarded": {str(p.user_id): p.qo_points_awarded for p in participants}}
        raise conflict("ALREADY_SUBMITTED", "Points were already submitted for this match.")

    member_ids = set(
        (await db.execute(select(LeagueMember.user_id).where(LeagueMember.league_id == league.id,
                                                             LeagueMember.status == "active"))).scalars().all()
    )
    team_of = {
        uid: tid
        for uid, tid in (
            await db.execute(select(LeagueMember.user_id, LeagueMember.team_id)
                             .where(LeagueMember.league_id == league.id,
                                    LeagueMember.status == "active"))
        ).all()
    }

    seen: set[uuid.UUID] = set()
    for stat in body.player_stats:
        if stat.user_id in seen:
            raise bad_request("VALIDATION_ERROR", "Duplicate player in player_stats.", field="player_stats")
        seen.add(stat.user_id)
        if stat.user_id not in member_ids:
            raise bad_request("VALIDATION_ERROR",
                              f"Player {stat.user_id} is not an active member of this league.",
                              field="player_stats")
        if team_of[stat.user_id] not in (match.team_a_id, match.team_b_id):
            raise bad_request("VALIDATION_ERROR",
                              f"Player {stat.user_id}'s team isn't playing in this match.",
                              field="player_stats")

    winning_team = {"team_a_won": match.team_a_id, "team_b_won": match.team_b_id}.get(body.result)

    # teams up-front so every ledger reason can carry real names
    team_a = await db.get(Team, match.team_a_id)
    team_b = await db.get(Team, match.team_b_id)
    team_name = {match.team_a_id: team_a.name, match.team_b_id: team_b.name}

    awarded: dict[str, int] = {}
    notif_ids: list[uuid.UUID] = []
    categories: set[str] = set()
    old_ranks: dict[uuid.UUID, int | None] = {}

    for stat in body.player_stats:
        player_team = team_of[stat.user_id]
        won = winning_team is not None and player_team == winning_team
        points = scoring.match_points(stat.runs, stat.wickets, stat.catches, stat.is_mom, won)

        db.add(MatchParticipant(match_id=match.id, user_id=stat.user_id, team_id=player_team,
                                runs=stat.runs, balls=stat.balls, wickets=stat.wickets,
                                catches=stat.catches, is_mom=stat.is_mom, qo_points_awarded=points))

        qs_before = (
            await db.execute(select(QoScore).where(QoScore.user_id == stat.user_id))
        ).scalar_one_or_none()
        old_ranks[stat.user_id] = qs_before.rank if qs_before else None

        opponent_name = team_name[match.team_b_id if player_team == match.team_a_id else match.team_a_id]
        parts = [f"vs {opponent_name}",
                 f"{stat.runs} runs, {stat.wickets} wkts, {stat.catches} catches"]
        if stat.is_mom:
            parts.append(f"MoM bonus +{settings.points_mom_bonus}")
        if won:
            parts.append(f"{team_name[player_team]} win bonus +{settings.points_win_bonus}")
        event = await scoring.award_points(
            db, stat.user_id, source="match", source_id=match.id, points=points,
            reason="Match " + " • ".join(parts),
            idempotency_key=f"match:{match.id}:{stat.user_id}:{idempotency_key}",
        )
        awarded[str(stat.user_id)] = points if event is not None else 0

        n = await create_notification(db, stat.user_id, "points_added",
                                      f"+{points} Qo points added to your profile")
        notif_ids.append(n.id)

        profile = (
            await db.execute(select(UserProfile).where(UserProfile.user_id == stat.user_id))
        ).scalar_one_or_none()
        category = scoring.ranking_category(profile)
        if category:
            categories.add(category)

    # standings (win 2 pts, draw 1 pt)
    match.status = "completed"
    match.result = body.result
    match.score_a = body.score_a
    match.score_b = body.score_b
    match.points_idempotency_key = idempotency_key

    for team_id in (match.team_a_id, match.team_b_id):
        standing = (
            await db.execute(select(Standing).where(Standing.league_id == league.id,
                                                    Standing.team_id == team_id))
        ).scalar_one_or_none()
        if standing is None:
            standing = Standing(league_id=league.id, team_id=team_id)
            db.add(standing)
            await db.flush()
        if body.result == "abandoned":
            continue
        standing.played += 1
        if winning_team is None:  # draw
            standing.points += 1
        elif team_id == winning_team:
            standing.won += 1
            standing.points += 2
        else:
            standing.lost += 1
    # refresh positions
    standings = (
        await db.execute(select(Standing).where(Standing.league_id == league.id)
                         .order_by(Standing.points.desc()))
    ).scalars().all()
    for pos, s in enumerate(standings, start=1):
        s.position = pos

    for category in categories:
        await scoring.recompute_rankings(db, category)

    # rank-improved notifications after recompute
    for stat in body.player_stats:
        qs_after = (
            await db.execute(select(QoScore).where(QoScore.user_id == stat.user_id))
        ).scalar_one_or_none()
        old = old_ranks.get(stat.user_id)
        if qs_after and qs_after.rank and old and qs_after.rank < old:
            n = await create_notification(db, stat.user_id, "rank_improved",
                                          f"You climbed to #{qs_after.rank} in your category")
            notif_ids.append(n.id)

    # match result to all league members
    result_text = {
        "team_a_won": f"{team_a.name} beat {team_b.name}",
        "team_b_won": f"{team_b.name} beat {team_a.name}",
        "draw": f"{team_a.name} vs {team_b.name} ended in a draw",
        "abandoned": f"{team_a.name} vs {team_b.name} was abandoned",
    }[body.result]
    for uid in member_ids:
        n = await create_notification(db, uid, "match_result", result_text, deep_link_id=str(match.id))
        notif_ids.append(n.id)

    await db.commit()
    for nid in notif_ids:
        background.add_task(deliver_notification, nid)

    from app.realtime import manager
    background.add_task(manager.emit, f"league:{league.id}:matches", "status_change",
                        {"match_id": str(match.id), "status": "completed", "result": body.result})
    for stat in body.player_stats:
        qs = (await db.execute(select(QoScore).where(QoScore.user_id == stat.user_id))).scalar_one_or_none()
        if qs:
            background.add_task(manager.emit, f"player:{stat.user_id}:qo_score", "updated",
                                {"score": qs.score, "delta": awarded[str(stat.user_id)]})

    return {"match_id": str(match.id), "status": "completed", "qo_points_awarded": awarded}
