"""Dugout — feed, posts, interactions (spec §6.1–6.3)."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import Page, get_current_user
from app.core.errors import bad_request, not_found, unprocessable
from app.db.base import get_db
from app.db.models import (
    Follow,
    Post,
    PostBookmark,
    PostComment,
    PostLike,
    PostMedia,
    User,
    UserProfile,
)
from app.services import scoring
from app.services.media_ops import store_upload
from app.services.notify import create_notification, deliver_notification
from app.services.serializers import avatar_url, serialize_post

router = APIRouter(tags=["dugout"])

_POST_OPTIONS = (
    selectinload(Post.author).selectinload(User.profile),
    selectinload(Post.author).selectinload(User.coach_profile),
    selectinload(Post.author).selectinload(User.qo_score),
    selectinload(Post.author).selectinload(User.avatar),
    selectinload(Post.media_links).selectinload(PostMedia.media),
)


async def _post_or_404(db: AsyncSession, post_id: uuid.UUID) -> Post:
    post = (
        await db.execute(select(Post).options(*_POST_OPTIONS)
                         .where(Post.id == post_id, Post.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if post is None:
        raise not_found("POST_NOT_FOUND", "This post doesn't exist.")
    return post


def _viewer_flags(viewer_id: uuid.UUID):
    liked = exists(select(PostLike.id).where(PostLike.post_id == Post.id, PostLike.user_id == viewer_id))
    bookmarked = exists(select(PostBookmark.id).where(PostBookmark.post_id == Post.id,
                                                      PostBookmark.user_id == viewer_id))
    return liked.label("viewer_liked"), bookmarked.label("viewer_bookmarked")


@router.get("/feed")
async def feed(
    tab: str = Query("all", pattern="^(all|players|coaches|teams|following)$"),
    q: str | None = Query(default=None, max_length=80),
    page: Page = Depends(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    liked_col, bookmarked_col = _viewer_flags(user.id)
    query = (
        select(Post, liked_col, bookmarked_col)
        .join(User, User.id == Post.author_id)
        .options(*_POST_OPTIONS)
        .where(Post.deleted_at.is_(None), User.deleted_at.is_(None))
    )
    count_q = (
        select(func.count()).select_from(Post)
        .join(User, User.id == Post.author_id)
        .where(Post.deleted_at.is_(None), User.deleted_at.is_(None))
    )

    if tab in ("players", "coaches", "teams"):
        author_type = tab.rstrip("s")  # players→player, coaches→coach, teams→team
        query = query.where(Post.author_type == author_type)
        count_q = count_q.where(Post.author_type == author_type)
    elif tab == "following":
        followees = select(Follow.followee_id).where(Follow.follower_id == user.id)
        query = query.where(Post.author_id.in_(followees))
        count_q = count_q.where(Post.author_id.in_(followees))

    if q:
        needle = f"%{q.strip()}%"
        cond = or_(User.full_name.ilike(needle), Post.content.ilike(needle))
        query = query.where(cond)
        count_q = count_q.where(cond)

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(query.order_by(Post.created_at.desc()).offset(page.offset).limit(page.limit))
    ).all()
    items = [serialize_post(post, liked, bookmarked) for post, liked, bookmarked in rows]
    return page.envelope(items, total)


@router.post("/posts", status_code=201)
async def create_post(
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    content: str = Form("", max_length=2000),
    category: str | None = Form(default=None),
    hashtags: str | None = Form(default=None),
    media: list[UploadFile] = File(default=[]),
):
    if category is not None and category not in ("playing", "certificates", "team", "trophies"):
        raise bad_request("VALIDATION_ERROR",
                          "category must be playing|certificates|team|trophies.", field="category")
    if len(media) > settings.max_post_media:
        raise unprocessable("VALIDATION_ERROR", f"Max {settings.max_post_media} media per post.", field="media")
    if not content.strip() and not media:
        raise bad_request("MISSING_FIELD", "A post needs text or media.", field="content")

    tags = [h.strip().lstrip("#") for h in (hashtags or "").split(",") if h.strip()]
    # also lift #tags out of the content body
    tags += [w.lstrip("#") for w in content.split() if w.startswith("#") and len(w) > 1]
    tags = list(dict.fromkeys(tags))

    post = Post(author_id=user.id, author_type=user.role, content=content.strip(),
                hashtags=tags, category=category, qo_points_earned=settings.points_per_post)
    db.add(post)
    await db.flush()

    for i, upload in enumerate(media):
        mime = (upload.content_type or "").lower()
        purpose = "post_video" if mime.startswith("video/") else "post_image"
        m = await store_upload(db, user, upload, purpose=purpose)
        db.add(PostMedia(post_id=post.id, media_id=m.id, position=i))

    await scoring.award_points(db, user.id, source="post", source_id=post.id,
                               points=settings.points_per_post, reason="Shared a post in the Dugout",
                               idempotency_key=f"post:{post.id}")
    # keep leaderboards live: posting changes the score, so the player's
    # category ranking must be refreshed too (matches/recos already do this)
    if user.role == "player":
        profile = (await db.execute(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )).scalar_one_or_none()
        category = scoring.ranking_category(profile)
        if category:
            await scoring.recompute_rankings(db, category)
    await db.commit()

    post = await _post_or_404(db, post.id)
    return serialize_post(post, viewer_liked=False, viewer_bookmarked=False)


# --------------------------------------------------------------------------- interactions
async def _emit_counts(post_id: uuid.UUID, likes: int, comments: int):
    from app.realtime import manager
    await manager.emit(f"post:{post_id}:counts", "like", {"likes": likes, "comments": comments})


@router.post("/posts/{post_id}/like")
async def like(post_id: uuid.UUID, background: BackgroundTasks,
               db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    post = await _post_or_404(db, post_id)
    existing = (
        await db.execute(select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user.id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(PostLike(post_id=post_id, user_id=user.id))
        post.like_count += 1
        if post.author_id != user.id:
            n = await create_notification(db, post.author_id, "post_liked",
                                          f"{user.full_name} liked your post", deep_link_id=str(post_id))
            background.add_task(deliver_notification, n.id)
        await db.commit()
        background.add_task(_emit_counts, post_id, post.like_count, post.comment_count)
    return {"liked": True, "like_count": post.like_count}


@router.delete("/posts/{post_id}/like")
async def unlike(post_id: uuid.UUID, background: BackgroundTasks,
                 db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    post = await _post_or_404(db, post_id)
    existing = (
        await db.execute(select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        post.like_count = max(0, post.like_count - 1)
        await db.commit()
        background.add_task(_emit_counts, post_id, post.like_count, post.comment_count)
    return {"liked": False, "like_count": post.like_count}


@router.post("/posts/{post_id}/bookmark")
async def bookmark(post_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    await _post_or_404(db, post_id)
    existing = (
        await db.execute(select(PostBookmark).where(PostBookmark.post_id == post_id,
                                                    PostBookmark.user_id == user.id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(PostBookmark(post_id=post_id, user_id=user.id))
        await db.commit()
    return {"bookmarked": True}


@router.delete("/posts/{post_id}/bookmark")
async def unbookmark(post_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    existing = (
        await db.execute(select(PostBookmark).where(PostBookmark.post_id == post_id,
                                                    PostBookmark.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return {"bookmarked": False}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    parent_id: uuid.UUID | None = None


@router.get("/posts/{post_id}/comments")
async def comments(post_id: uuid.UUID, page: Page = Depends(),
                   db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _post_or_404(db, post_id)
    total = (
        await db.execute(select(func.count()).select_from(PostComment)
                         .where(PostComment.post_id == post_id))
    ).scalar_one()
    rows = (
        await db.execute(
            select(PostComment).options(selectinload(PostComment.author).selectinload(User.avatar))
            .where(PostComment.post_id == post_id)
            .order_by(PostComment.created_at).offset(page.offset).limit(page.limit)
        )
    ).scalars().all()
    items = [
        {
            "id": str(c.id),
            "body": c.body,
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "author": {"id": str(c.author.id), "name": c.author.full_name,
                       "verified": c.author.verified, "avatar_url": avatar_url(c.author)},
            "created_at": c.created_at.isoformat(),
        }
        for c in rows
    ]
    return page.envelope(items, total)


@router.post("/posts/{post_id}/comments", status_code=201)
async def add_comment(post_id: uuid.UUID, body: CommentIn, background: BackgroundTasks,
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    post = await _post_or_404(db, post_id)
    if body.parent_id is not None:
        parent = (
            await db.execute(select(PostComment.id).where(PostComment.id == body.parent_id,
                                                          PostComment.post_id == post_id))
        ).first()
        if parent is None:
            raise not_found("POST_NOT_FOUND", "Parent comment not found on this post.")
    comment = PostComment(post_id=post_id, author_id=user.id, body=body.body, parent_id=body.parent_id)
    db.add(comment)
    post.comment_count += 1
    if post.author_id != user.id:
        n = await create_notification(db, post.author_id, "post_commented",
                                      f"{user.full_name}: {body.body[:80]}", deep_link_id=str(post_id))
        background.add_task(deliver_notification, n.id)
    await db.commit()
    background.add_task(_emit_counts, post_id, post.like_count, post.comment_count)
    return {
        "id": str(comment.id),
        "body": comment.body,
        "author": {"id": str(user.id), "name": user.full_name, "avatar_url": avatar_url(user)},
        "created_at": comment.created_at.isoformat(),
    }


@router.post("/posts/{post_id}/share")
async def share(post_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                user: User = Depends(get_current_user)):
    post = await _post_or_404(db, post_id)
    post.share_count += 1
    await db.commit()
    return {"share_url": f"{settings.share_base_url}/post/{post_id}", "share_count": post.share_count}
