"""SQLAlchemy models — one module, grouped by domain, mirroring spec §10.

Enum-ish columns use VARCHAR + application-level validation (values documented
inline) so tuning doesn't require a Postgres enum migration.
"""
import uuid
from datetime import date, datetime

from sqlalchemy.sql import func

from sqlalchemy import (
    BigInteger,
    LargeBinary,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin

UUID_T = UUID(as_uuid=True)


# ===========================================================================
# 10.1 Identity
# ===========================================================================
class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # player | coach
    player_id: Mapped[str | None] = mapped_column(String(6), unique=True)  # P{YY}{NNN}
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    avatar_media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", use_alter=True))
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # blue tick
    onboarding_stage: Mapped[str] = mapped_column(String(16), default="profile", nullable=False)  # profile|sport|complete
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete, 30-day grace

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", uselist=False)
    coach_profile: Mapped["CoachProfile | None"] = relationship(back_populates="user", uselist=False)
    settings: Mapped["UserSettings | None"] = relationship(uselist=False)
    qo_score: Mapped["QoScore | None"] = relationship(uselist=False)
    avatar: Mapped["Media | None"] = relationship(foreign_keys=[avatar_media_id])

    __table_args__ = (
        Index("idx_users_role", "role"),
        Index("idx_users_player_id", "player_id"),
        CheckConstraint("role IN ('player','coach')", name="ck_users_role"),
    )


class UserProfile(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    dob: Mapped[date | None] = mapped_column(Date)          # NEVER exposed publicly (minors)
    sport: Mapped[str | None] = mapped_column(Text)          # 'Cricket'
    sub_role: Mapped[str | None] = mapped_column(Text)       # 'Batsman' / 'Batter' / ...
    age_group: Mapped[str | None] = mapped_column(String(8))  # 'U16' — public-safe, derived from dob
    team: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)       # city-level only
    school: Mapped[str | None] = mapped_column(Text)         # private field
    bio: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)

    user: Mapped[User] = relationship(back_populates="profile")


class CoachProfile(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "coach_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    role_title: Mapped[str | None] = mapped_column(Text)     # 'Head Coach'
    academy: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    sport: Mapped[str | None] = mapped_column(Text)
    certification: Mapped[str | None] = mapped_column(Text)  # display label, e.g. 'BCCI Level 3'
    experience_years: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coach_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    rank_scope: Mapped[str | None] = mapped_column(Text)     # 'Karnataka Coaches'
    bio: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="coach_profile")


class UserSettings(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_alerts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dark_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    private_profile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    location_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Session(UUIDPkMixin, TimestampMixin, Base):
    """Rotating refresh tokens with family reuse-detection."""
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID_T, nullable=False)
    device_id: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUID_T)

    __table_args__ = (Index("idx_sessions_user", "user_id"), Index("idx_sessions_family", "family_id"))


class OtpRequest(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "otp_requests"

    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="coach_verification", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (Index("idx_otp_phone_created", "phone", "created_at"),)


class Device(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fcm_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # android | ios | web
    app_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerIdSequence(Base):
    """Row-locked per-year allocator for public Player IDs (P{YY}{NNN})."""
    __tablename__ = "player_ids"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PasswordReset(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "password_resets"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "email_verifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ===========================================================================
# 10.2 Leagues & Matches
# ===========================================================================
CRICKET_TYPES = ("gully", "professional", "box", "tennis_ball", "hard_ball", "corporate", "beach")


class League(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "leagues"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)  # coach
    name: Mapped[str] = mapped_column(Text, nullable=False)
    league_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    cricket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False)  # mens | womens
    location: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(String(16))
    teams_count: Mapped[int] = mapped_column(Integer, nullable=False)
    logo_media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", use_alter=True))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # draft|active|completed|archived

    teams: Mapped[list["Team"]] = relationship(back_populates="league", order_by="Team.position")
    logo: Mapped["Media | None"] = relationship(foreign_keys=[logo_media_id])

    __table_args__ = (Index("idx_leagues_owner", "owner_id"),)


class Team(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    logo_media_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", use_alter=True))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    league: Mapped[League] = relationship(back_populates="teams")
    logo: Mapped["Media | None"] = relationship(foreign_keys=[logo_media_id])

    __table_args__ = (UniqueConstraint("league_id", "name", name="uq_team_name_per_league"),)


class LeagueMember(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "league_members"

    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="player", nullable=False)  # player | captain
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active|left|removed
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("league_id", "user_id", name="uq_league_member"),)


class Match(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "matches"

    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False)
    team_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    team_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    venue: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", nullable=False)  # scheduled|live|completed|abandoned
    result: Mapped[str | None] = mapped_column(String(16))  # team_a_won|team_b_won|draw|abandoned
    score_a: Mapped[dict | None] = mapped_column(JSONB)
    score_b: Mapped[dict | None] = mapped_column(JSONB)
    points_idempotency_key: Mapped[str | None] = mapped_column(Text)

    team_a: Mapped[Team] = relationship(foreign_keys=[team_a_id])
    team_b: Mapped[Team] = relationship(foreign_keys=[team_b_id])

    __table_args__ = (Index("idx_matches_league", "league_id"),)


class MatchParticipant(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "match_participants"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wickets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    catches: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_mom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    qo_points_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("match_id", "user_id", name="uq_match_participant"),)


class Standing(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "standings"

    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("league_id", "team_id", name="uq_standing"),)


# ===========================================================================
# 10.3 Scoring
# ===========================================================================
class CardTier(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "card_tiers"

    level: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)  # 1..8
    label: Mapped[str] = mapped_column(Text, nullable=False)                  # 'Purple Card' … 'Golden Pro'
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    hex: Mapped[str] = mapped_column(String(7), nullable=False)


class QoScore(UUIDPkMixin, TimestampMixin, Base):
    """Materialised SUM of the ledger — recompute, never mutate in place."""
    __tablename__ = "qo_scores"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    card_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    percentile: Mapped[float | None] = mapped_column(Numeric(5, 2))
    last_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QoScoreEvent(UUIDPkMixin, TimestampMixin, Base):
    """Immutable, append-only points ledger — the source of truth."""
    __tablename__ = "qo_score_events"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # match|recommendation|post|streak|milestone|correction
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID_T)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)

    __table_args__ = (Index("idx_qse_user_created", "user_id", "created_at"),)


class Ranking(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "rankings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)  # 'U16 Cricket'
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_players: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "category", name="uq_ranking_user_category"),)


class PlayerMilestone(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "player_milestones"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text)
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_milestone"),)


# ===========================================================================
# 10.4 Social
# ===========================================================================
class Post(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "posts"

    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)  # player|coach|team
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    category: Mapped[str | None] = mapped_column(String(16))  # playing|certificates|team|trophies
    qo_points_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    share_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User] = relationship()
    media_links: Mapped[list["PostMedia"]] = relationship(order_by="PostMedia.position")

    __table_args__ = (
        Index("idx_posts_author", "author_id"),
        Index("idx_posts_created_at", "created_at"),
        Index("idx_posts_hashtags", "hashtags", postgresql_using="gin"),
    )


class PostMedia(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "post_media"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    media: Mapped["Media"] = relationship()


class PostLike(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "post_likes"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_like"),)


class PostComment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "post_comments"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("post_comments.id", ondelete="CASCADE"))

    author: Mapped[User] = relationship()


class PostBookmark(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "post_bookmarks"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_bookmark"),)


class Follow(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "follows"

    follower_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    followee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_follow"),
        CheckConstraint("follower_id <> followee_id", name="ck_no_self_follow"),
    )


class FriendRequest(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "friend_requests"

    from_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending|accepted|declined

    __table_args__ = (CheckConstraint("from_id <> to_id", name="ck_no_self_friend"),)


class Friendship(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "friendships"

    user_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("user_a_id", "user_b_id", name="uq_friendship"),)


class Conversation(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "conversations"


class ConversationParticipant(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "conversation_participants"

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),)


class Message(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("idx_messages_conversation", "conversation_id", "created_at"),)


# ===========================================================================
# 10.5 Coaching
# ===========================================================================
class CoachPlayer(UUIDPkMixin, TimestampMixin, Base):
    """Coach roster — players added by SportyQo Player ID."""
    __tablename__ = "coach_players"

    coach_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active|inactive

    __table_args__ = (UniqueConstraint("coach_id", "user_id", name="uq_coach_player"),)


class Recommendation(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "recommendations"

    coach_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[float | None] = mapped_column(Numeric(2, 1))
    target: Mapped[str | None] = mapped_column(String(16))  # club|league|scout
    status: Mapped[str] = mapped_column(String(16), default="sent", nullable=False)  # sent|viewed|accepted

    coach: Mapped[User] = relationship(foreign_keys=[coach_id])

    __table_args__ = (Index("idx_reco_coach_player", "coach_id", "player_id", "created_at"),)


class Certification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "certifications"

    coach_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    certification_level: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_body: Mapped[str | None] = mapped_column(Text)
    issued_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="under_review", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list["CertificationDocument"]] = relationship()


class CertificationDocument(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "certification_documents"

    certification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("certifications.id", ondelete="CASCADE"), nullable=False)
    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False)

    media: Mapped["Media"] = relationship()


# ===========================================================================
# 10.6 Media & Notifications
# ===========================================================================
class Media(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "media"

    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ready", nullable=False)  # pending|processing|ready|failed
    acl: Mapped[str] = mapped_column(String(8), default="public", nullable=False)     # public|private
    title: Mapped[str | None] = mapped_column(Text)
    subtitle: Mapped[str | None] = mapped_column(Text)


class Notification(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(Text)
    accent: Mapped[str | None] = mapped_column(String(7))
    deep_link: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_notifications_user_unread", "user_id", postgresql_where=text("read_at IS NULL")),
    )


class AuditLog(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(Text, nullable=False)  # 'admin' or user id
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)


class MediaBlob(Base):
    """File bytes stored in Postgres — used by the 'db' storage provider so
    uploads survive restarts on hosts with ephemeral disks (e.g. Render
    free tier)."""
    __tablename__ = "media_blobs"

    storage_key: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    acl: Mapped[str] = mapped_column(String(16), default="public", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
