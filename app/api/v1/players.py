"""Player-facing aggregates (spec §4)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import Page, require_player
from app.db.base import get_db
from app.db.models import (
    Follow,
    League,
    LeagueMember,
    Match,
    MatchParticipant,
    Media,
    Notification,
    PlayerMilestone,
    Post,
    PostMedia,
    QoScore,
    QoScoreEvent,
    Ranking,
    Recommendation,
    Team,
    User,
    UserProfile,
)
from app.services import scoring
from app.services.serializers import avatar_url, card_payload, media_url, serialize_media_item, serialize_tier

router = APIRouter(prefix="/players/me", tags=["players"])


async def _score_row(db: AsyncSession, user_id: uuid.UUID) -> QoScore:
    qs = (await db.execute(select(QoScore).where(QoScore.user_id == user_id))).scalar_one_or_none()
    if qs is None:
        qs = await scoring.recompute_score(db, user_id)
        await db.commit()
    return qs


def _trend(values: list[int], points: int = 8) -> list[float]:
    """Normalise the last N cumulative scores into 0..1 sparkline values."""
    if not values:
        return [0.0] * points
    values = values[-points:]
    values = [values[0]] * (points - len(values)) + values
    peak = max(values) or 1
    return [round(v / peak, 2) for v in values]


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db), user: User = Depends(require_player)):
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    qs = await _score_row(db, user.id)
    _, delta_month = await scoring.score_deltas(db, user.id)
    tiers = await scoring.load_tiers(db)
    tier = scoring.resolve_tier(qs.score, tiers)

    # cumulative trend from ledger
    events = (
        await db.execute(select(QoScoreEvent.points).where(QoScoreEvent.user_id == user.id)
                         .order_by(QoScoreEvent.created_at))
    ).scalars().all()
    cumulative, running = [], 0
    for p in events:
        running += p
        cumulative.append(running)

    membership = (
        await db.execute(
            select(LeagueMember, League, Team)
            .join(League, League.id == LeagueMember.league_id)
            .join(Team, Team.id == LeagueMember.team_id)
            .where(LeagueMember.user_id == user.id, LeagueMember.status == "active",
                   League.status.in_(("draft", "active")))
            .order_by(LeagueMember.created_at.desc()).limit(1)
        )
    ).first()

    active_league = None
    upcoming_match = None
    if membership:
        member, league, team = membership
        active_league = {"id": str(league.id), "team_name": team.name, "league_name": league.name,
                         "status": league.status}
        nxt = (
            await db.execute(
                select(Match).options(selectinload(Match.team_a).selectinload(Team.league),
                                      selectinload(Match.team_b))
                .where(Match.league_id == league.id, Match.status == "scheduled",
                       Match.starts_at >= datetime.now(timezone.utc),
                       or_(Match.team_a_id == member.team_id,
                           Match.team_b_id == member.team_id))
                .order_by(Match.starts_at).limit(1)
            )
        ).scalar_one_or_none()
        if nxt:
            upcoming_match = {
                "id": str(nxt.id),
                "team_a": {"id": str(nxt.team_a.id), "name": nxt.team_a.name, "logo_url": None},
                "team_b": {"id": str(nxt.team_b.id), "name": nxt.team_b.name, "logo_url": None},
                "starts_at": nxt.starts_at.isoformat(),
                "venue": nxt.venue,
            }

    unread = (
        await db.execute(select(func.count()).select_from(Notification)
                         .where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    ).scalar_one()

    first_name = user.full_name.split(" ")[0] if user.full_name else ""
    return {
        "player": {
            "first_name": first_name,
            "full_name": user.full_name,
            "player_id": user.player_id,
            "age_group": profile.age_group if profile else None,
            "sub_role": profile.sub_role if profile else None,
            "avatar_url": avatar_url(user),
        },
        "qo_score": {
            "current": qs.score,
            "card_tier": scoring.tier_slug(tier.label),
            "delta_month": delta_month,
            "trend": _trend(cumulative),
        },
        "active_league": active_league,
        "upcoming_match": upcoming_match,
        "unread_notifications": unread,
    }


@router.get("/qo-score")
async def qo_score_card(db: AsyncSession = Depends(get_db), user: User = Depends(require_player)):
    qs = await _score_row(db, user.id)
    tiers = await scoring.load_tiers(db)
    tier = scoring.resolve_tier(qs.score, tiers)
    nxt = scoring.next_tier_of(tier, tiers)

    # breakdown by ledger source
    rows = (
        await db.execute(
            select(QoScoreEvent.source, func.coalesce(func.sum(QoScoreEvent.points), 0))
            .where(QoScoreEvent.user_id == user.id).group_by(QoScoreEvent.source)
        )
    ).all()
    by_source = {source: int(points) for source, points in rows}
    breakdown = [
        {"category": "Match Performance", "points": by_source.get("match", 0), "max": 400},
        {"category": "Consistency", "points": by_source.get("streak", 0) + by_source.get("milestone", 0), "max": 200},
        {"category": "Coach Rating", "points": by_source.get("recommendation", 0), "max": 200},
        {"category": "Community", "points": by_source.get("post", 0), "max": 200},
    ]

    tips = []
    if by_source.get("match", 0) < 100:
        tips.append("Play 2 more matches this month")
    if by_source.get("recommendation", 0) == 0:
        tips.append("Get recommended by a verified coach")
    if by_source.get("post", 0) < 50:
        tips.append("Share your training highlights in the Dugout")
    if not tips:
        tips.append(f"Keep going — {max(0, tier.threshold - qs.score)} points to your next card")

    return {
        "score": qs.score,
        "card": card_payload(tier, qs.score, nxt),
        "breakdown": breakdown,
        "improve_tips": tips[:3],
        "all_tiers": [serialize_tier(t, qs.score, scoring.tier_unlocked(t.level, qs.score, tiers)) for t in tiers],
    }


@router.get("/performance")
async def performance(period: str = Query("this_season", pattern="^(this_season|last_season|all_time)$"),
                      db: AsyncSession = Depends(get_db), user: User = Depends(require_player)):
    qs = await _score_row(db, user.id)
    tiers = await scoring.load_tiers(db)
    tier = scoring.resolve_tier(qs.score, tiers)
    nxt = scoring.next_tier_of(tier, tiers)
    delta_week, _ = await scoring.score_deltas(db, user.id)

    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    category = scoring.ranking_category(profile)
    ranking_row = None
    if category:
        ranking_row = (
            await db.execute(select(Ranking).where(Ranking.user_id == user.id, Ranking.category == category))
        ).scalar_one_or_none()

    # monthly journey graph (last 6 calendar months, cumulative)
    now = datetime.now(timezone.utc)
    month_rows = (
        await db.execute(
            select(func.date_trunc("month", QoScoreEvent.created_at).label("m"),
                   func.sum(QoScoreEvent.points))
            .where(QoScoreEvent.user_id == user.id)
            .group_by("m").order_by("m")
        )
    ).all()
    labels, values, running = [], [], 0
    per_month = {m.strftime("%Y-%m"): int(p) for m, p in month_rows}
    months = []
    y, mo = now.year, now.month
    for _ in range(6):
        months.append((y, mo))
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
    months.reverse()
    # points earned before the window still count toward the running total
    window_keys = {f"{yy:04d}-{mm:02d}" for yy, mm in months}
    running = sum(p for k, p in per_month.items() if k not in window_keys and k < min(window_keys))
    for yy, mm in months:
        running += per_month.get(f"{yy:04d}-{mm:02d}", 0)
        labels.append(datetime(yy, mm, 1).strftime("%b"))
        values.append(running)

    milestone_rows = (
        await db.execute(select(PlayerMilestone).where(PlayerMilestone.user_id == user.id))
    ).scalars().all()
    achieved = {m.key: m for m in milestone_rows}
    journey = []
    for key, title, default_sub in scoring.MILESTONE_DEFS:
        m = achieved.get(key)
        journey.append({
            "key": key,
            "title": title,
            "subtitle": (m.subtitle if m and m.subtitle else default_sub),
            "done": m is not None and m.achieved_at is not None,
        })

    participations = (
        await db.execute(
            select(MatchParticipant, Match)
            .join(Match, Match.id == MatchParticipant.match_id)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(MatchParticipant.user_id == user.id, Match.status == "completed")
            .order_by(Match.starts_at.desc()).limit(5)
        )
    ).all()
    recent_matches = []
    for part, match in participations:
        my_team_won = (
            (match.result == "team_a_won" and part.team_id == match.team_a_id)
            or (match.result == "team_b_won" and part.team_id == match.team_b_id)
        )
        opponent = match.team_b if part.team_id == match.team_a_id else match.team_a
        badges = []
        if my_team_won:
            badges.append("Won Match")
        if part.is_mom:
            badges.append("MOM")
        recent_matches.append({
            "id": str(match.id),
            "opponent": opponent.name,
            "played_at": match.starts_at.date().isoformat(),
            "result": "won" if my_team_won else ("draw" if match.result == "draw" else "lost"),
            "stats": {"runs": part.runs, "balls": part.balls, "wickets": part.wickets, "catches": part.catches},
            "badges": badges,
            "qo_points": part.qo_points_awarded,
        })

    return {
        "qo_score": {"current": qs.score, "card_tier": scoring.tier_slug(tier.label),
                     "delta_week": delta_week, "label": "Elite Performer" if (qs.percentile or 100) <= 10 else "Rising Star"},
        "card_progress": {
            "current": qs.score,
            "target": tier.threshold,
            "next_tier": nxt.label if nxt else None,
            "points_needed": max(0, tier.threshold - qs.score),
        },
        "ranking": {
            "rank": ranking_row.rank if ranking_row else qs.rank,
            "total_players": ranking_row.total_players if ranking_row else None,
            "percentile": scoring.percentile_label(float(qs.percentile) if qs.percentile is not None else None),
            "category": category,
        },
        "journey_graph": {"period": period, "labels": labels, "values": values},
        "journey_milestones": journey,
        "recent_matches": recent_matches,
    }


@router.get("/matches")
async def my_matches(page: Page = Depends(), db: AsyncSession = Depends(get_db),
                     user: User = Depends(require_player)):
    base = (
        select(MatchParticipant, Match)
        .join(Match, Match.id == MatchParticipant.match_id)
        .where(MatchParticipant.user_id == user.id)
    )
    total = (
        await db.execute(select(func.count()).select_from(MatchParticipant)
                         .where(MatchParticipant.user_id == user.id))
    ).scalar_one()
    rows = (
        await db.execute(
            base.options(selectinload(Match.team_a), selectinload(Match.team_b))
            .order_by(Match.starts_at.desc()).offset(page.offset).limit(page.limit)
        )
    ).all()
    items = []
    for part, match in rows:
        my_team_won = (
            (match.result == "team_a_won" and part.team_id == match.team_a_id)
            or (match.result == "team_b_won" and part.team_id == match.team_b_id)
        )
        items.append({
            "id": str(match.id),
            "league_id": str(match.league_id),
            "team_a": {"id": str(match.team_a.id), "name": match.team_a.name},
            "team_b": {"id": str(match.team_b.id), "name": match.team_b.name},
            "starts_at": match.starts_at.isoformat(),
            "venue": match.venue,
            "status": match.status,
            "result": match.result,
            "my_result": ("won" if my_team_won else ("draw" if match.result == "draw" else "lost"))
            if match.status == "completed" else "upcoming",
            "my_stats": {"runs": part.runs, "balls": part.balls, "wickets": part.wickets,
                         "catches": part.catches, "is_mom": part.is_mom},
            "qo_points": part.qo_points_awarded,
        })
    return page.envelope(items, total)


@router.get("/playbook")
async def playbook(tab: str | None = Query(default=None),
                   db: AsyncSession = Depends(get_db), user: User = Depends(require_player)):
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))).scalar_one_or_none()
    qs = await _score_row(db, user.id)

    followers = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.followee_id == user.id))
    ).scalar_one()
    following = (
        await db.execute(select(func.count()).select_from(Follow).where(Follow.follower_id == user.id))
    ).scalar_one()
    teams_count = (
        await db.execute(select(func.count()).select_from(LeagueMember)
                         .where(LeagueMember.user_id == user.id))
    ).scalar_one()
    tournaments = (
        await db.execute(select(func.count(func.distinct(Match.league_id)))
                         .select_from(MatchParticipant)
                         .join(Match, Match.id == MatchParticipant.match_id)
                         .where(MatchParticipant.user_id == user.id))
    ).scalar_one()

    recos = (
        await db.execute(
            select(Recommendation).options(selectinload(Recommendation.coach)
                                           .selectinload(User.coach_profile),
                                           selectinload(Recommendation.coach).selectinload(User.avatar))
            .where(Recommendation.player_id == user.id)
            .order_by(Recommendation.created_at.desc()).limit(10)
        )
    ).scalars().all()
    coach_recommendations = []
    for r in recos:
        cp = r.coach.coach_profile
        title_bits = [b for b in [(cp.role_title if cp else None), (cp.academy if cp else None)] if b]
        coach_recommendations.append({
            "id": str(r.id),
            "coach_id": str(r.coach_id),
            "name": r.coach.full_name,
            "title": " • ".join(title_bits) or "Coach",
            "verified": r.coach.verified,
            "rating": float(r.rating) if r.rating is not None else None,
            "quote": r.note,
            "recommended_at": r.created_at.date().isoformat(),
        })

    category = scoring.ranking_category(profile)
    tab_keys = ["playing", "certificates", "team", "trophies"]
    wanted = [tab] if tab in tab_keys else tab_keys
    tabs = {}
    for key in wanted:
        media_rows = (
            await db.execute(
                select(Media, PostMedia.post_id)
                .join(PostMedia, PostMedia.media_id == Media.id)
                .join(Post, Post.id == PostMedia.post_id)
                .where(Post.author_id == user.id, Post.category == key, Post.deleted_at.is_(None))
                .order_by(Media.created_at.desc()).limit(24)
            )
        ).all()
        tabs[key] = [serialize_media_item(m) | {"date": m.created_at.date().isoformat(),
                                                "post_id": str(pid)} for m, pid in media_rows]

    return {
        "profile": {
            "full_name": user.full_name,
            "player_id": user.player_id,
            "sport": profile.sport if profile else None,
            "sub_role": profile.sub_role if profile else None,
            "age_group": profile.age_group if profile else None,
            "location": profile.location if profile else None,
            "avatar_url": avatar_url(user),
            "verified": user.verified,
            "about": profile.bio if profile else None,
        },
        "qo_score": {"current": qs.score, "rank": qs.rank, "category": category},
        "stats": {"followers": followers, "following": following,
                  "teams": teams_count, "tournaments": tournaments},
        "coach_recommendations": coach_recommendations,
        "tabs": tabs,
    }
