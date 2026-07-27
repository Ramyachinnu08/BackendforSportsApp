"""Qo Score engine.

Invariant (spec §10.3): `qo_score_events` is an immutable, append-only ledger;
`qo_scores.score` is a materialised SUM — recomputed, never mutated in place.
There is no direct score-write path anywhere in the codebase.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    CardTier,
    PlayerMilestone,
    QoScore,
    QoScoreEvent,
    Ranking,
    User,
    UserProfile,
)

DEFAULT_TIERS = [
    (1, "Purple Card", 1000, "#7B2FFF"),
    (2, "Green Card", 2500, "#00C853"),
    (3, "Yellow Card", 5000, "#FFEB3B"),
    (4, "Orange Card", 15000, "#FF9800"),
    (5, "Red Card", 30000, "#FF3B30"),
    (6, "Bronze Pro", 50000, "#CD7F32"),
    (7, "Silver Pro", 75000, "#9E9E9E"),
    (8, "Golden Pro", 100000, "#FFB300"),
]

MILESTONE_DEFS = [
    ("started_playing", "Started Playing", "Joined SportyQo"),
    ("first_league", "Joined First League", "Join a league with an invite code"),
    ("first_tournament", "First Tournament Win", "Win a tournament"),
    ("purple_card", "Reached Purple Card", "Earn your first Qo points"),
    ("green_card", "Reach Green Card", "2500 Qo Points needed"),
    ("coach_recommended", "Get Coach Recommended", "By a verified coach"),
]


def tier_slug(label: str) -> str:
    return label.lower().replace(" card", "").replace(" ", "_")


async def load_tiers(db: AsyncSession) -> list[CardTier]:
    tiers = (await db.execute(select(CardTier).order_by(CardTier.level))).scalars().all()
    return list(tiers)


async def seed_tiers(db: AsyncSession) -> None:
    if await load_tiers(db):
        return
    for level, label, threshold, hexv in DEFAULT_TIERS:
        db.add(CardTier(level=level, label=label, threshold=threshold, hex=hexv))
    await db.flush()


def resolve_tier(score: int, tiers: list[CardTier]) -> CardTier:
    """Current card = lowest tier whose threshold has not been passed yet.
    (Purple covers 0–999, Green 1000–2499, … Golden Pro from 75000 up.)"""
    for tier in tiers:
        if score < tier.threshold:
            return tier
    return tiers[-1]


def tier_unlocked(level: int, score: int, tiers: list[CardTier]) -> bool:
    prev_threshold = 0 if level <= 1 else tiers[level - 2].threshold
    return score >= prev_threshold


def next_tier_of(tier: CardTier, tiers: list[CardTier]) -> CardTier | None:
    idx = tier.level  # levels are 1-based and contiguous
    return tiers[idx] if idx < len(tiers) else None


def match_points(runs: int, wickets: int, catches: int, is_mom: bool, won: bool,
                 is_player_of_match: bool = False, is_best_bowler: bool = False,
                 is_best_batsman: bool = False, is_mvp: bool = False,
                 balls: int = 0, fours: int = 0, sixes: int = 0) -> int:
    """Official Qo Points formula — matches the published Qo scoring chart.

    Batting:  +20 participation | +0.5 per run | 30-49 bonus +10 | 50+ bonus +15
              (not cumulative) | +2 per four | +4 per six | Strike Rate
              100-120 +5 / 121-150 +10 / 151+ +15
    Bowling:  +15 per wicket | 2 wkts +5 | 3 wkts +10 | 5 wkts +20
              (highest tier only)
    Fielding: +5 per assist (catch, run-out, stumping)
    Bonuses:  Match Win +20, MoM +20, Best Bowler +20, Best Batsman +20,
              Runner-Up (PoM) +50, League Champion (MVP) +100"""
    pts = 20.0
    pts += runs * 0.5
    if runs >= 50:
        pts += 15
    elif runs >= 30:
        pts += 10
    pts += fours * 2
    pts += sixes * 4
    if balls > 0:
        sr = (runs / balls) * 100
        if sr >= 151:
            pts += 15
        elif sr >= 121:
            pts += 10
        elif sr >= 100:
            pts += 5

    pts += wickets * 15
    if wickets >= 5:
        pts += 20
    elif wickets >= 3:
        pts += 10
    elif wickets >= 2:
        pts += 5

    pts += catches * 5

    if won:
        pts += 20
    if is_mom:
        pts += 20
    if is_best_batsman:
        pts += 20
    if is_best_bowler:
        pts += 20
    if is_player_of_match:
        pts += 50
    if is_mvp:
        pts += 100

    return int(round(pts))


async def award_points(
    db: AsyncSession,
    user_id: uuid.UUID,
    source: str,
    points: int,
    reason: str,
    source_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> QoScoreEvent | None:
    """Append a ledger event and rematerialise the score.

    Returns the event, or None when the idempotency key already exists
    (points must never double-award — spec §5.8).
    """
    if idempotency_key:
        stmt = (
            pg_insert(QoScoreEvent)
            .values(
                id=uuid.uuid4(), user_id=user_id, source=source, source_id=source_id,
                points=points, reason=reason, idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(QoScoreEvent.id)
        )
        inserted = (await db.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            return None
        event = await db.get(QoScoreEvent, inserted)
    else:
        event = QoScoreEvent(user_id=user_id, source=source, source_id=source_id, points=points, reason=reason)
        db.add(event)
        await db.flush()

    await recompute_score(db, user_id)
    return event


async def recompute_score(db: AsyncSession, user_id: uuid.UUID) -> QoScore:
    total = (
        await db.execute(select(func.coalesce(func.sum(QoScoreEvent.points), 0)).where(QoScoreEvent.user_id == user_id))
    ).scalar_one()
    tiers = await load_tiers(db)
    tier = resolve_tier(total, tiers)

    qs = (await db.execute(select(QoScore).where(QoScore.user_id == user_id))).scalar_one_or_none()
    if qs is None:
        qs = QoScore(user_id=user_id)
        db.add(qs)
    previous_level = qs.card_level or 1
    qs.score = total
    qs.card_level = tier.level
    qs.last_calculated_at = datetime.now(timezone.utc)
    await db.flush()

    # tier-linked milestones
    if total > 0:
        await grant_milestone(db, user_id, "purple_card", subtitle=f"{total} Qo Points")
    if tier.level >= 2 or total >= tiers[0].threshold:
        await grant_milestone(db, user_id, "green_card", subtitle=f"{total} Qo Points")
    qs._tier_changed = tier.level > previous_level  # transient hint for callers
    return qs


async def grant_milestone(db: AsyncSession, user_id: uuid.UUID, key: str, subtitle: str | None = None) -> bool:
    defs = {k: (t, s) for k, t, s in MILESTONE_DEFS}
    title, default_sub = defs.get(key, (key.replace("_", " ").title(), None))
    stmt = (
        pg_insert(PlayerMilestone)
        .values(id=uuid.uuid4(), user_id=user_id, key=key, title=title,
                subtitle=subtitle or default_sub, achieved_at=datetime.now(timezone.utc))
        .on_conflict_do_nothing(index_elements=["user_id", "key"])
        .returning(PlayerMilestone.id)
    )
    inserted = (await db.execute(stmt)).scalar_one_or_none()
    return inserted is not None


def ranking_category(profile: UserProfile | None) -> str | None:
    if profile is None or not profile.sport:
        return None
    return f"{profile.age_group or 'Open'} {profile.sport}"


async def recompute_rankings(db: AsyncSession, category: str) -> None:
    """Dense-rank every player in a category by score. Called after score
    mutations and by the rankings cron (`scripts/recompute_rankings.py`)."""
    rows = (
        await db.execute(
            select(QoScore.user_id, QoScore.score)
            .join(User, User.id == QoScore.user_id)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(
                func.concat(func.coalesce(UserProfile.age_group, "Open"), " ", UserProfile.sport) == category,
                User.deleted_at.is_(None),
                User.role == "player",
            )
            .order_by(QoScore.score.desc())
        )
    ).all()
    total = len(rows)
    now = datetime.now(timezone.utc)
    for position, (user_id, score) in enumerate(rows, start=1):
        stmt = (
            pg_insert(Ranking)
            .values(id=uuid.uuid4(), user_id=user_id, category=category, rank=position,
                    total_players=total, computed_at=now)
            .on_conflict_do_update(
                index_elements=["user_id", "category"],
                set_={"rank": position, "total_players": total, "computed_at": now},
            )
        )
        await db.execute(stmt)
        percentile = max(1, math.ceil(position / total * 100)) if total else None
        qs = (await db.execute(select(QoScore).where(QoScore.user_id == user_id))).scalar_one_or_none()
        if qs:
            qs.rank = position
            qs.percentile = percentile
    await db.flush()


def percentile_label(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    return f"Top {int(percentile)}%"


async def score_deltas(db: AsyncSession, user_id: uuid.UUID) -> tuple[int, int]:
    """(delta_week, delta_month) from the ledger."""
    now = datetime.now(timezone.utc)
    week = (
        await db.execute(
            select(func.coalesce(func.sum(QoScoreEvent.points), 0)).where(
                QoScoreEvent.user_id == user_id,
                QoScoreEvent.created_at >= now - timedelta(days=7),
            )
        )
    ).scalar_one()
    month = (
        await db.execute(
            select(func.coalesce(func.sum(QoScoreEvent.points), 0)).where(
                QoScoreEvent.user_id == user_id,
                QoScoreEvent.created_at >= now - timedelta(days=30),
            )
        )
    ).scalar_one()
    return int(week), int(month)
