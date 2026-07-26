"""Auth & onboarding (spec §3)."""
import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import get_current_user, get_optional_user
from app.core.errors import (
    bad_request,
    conflict,
    forbidden,
    gone,
    rate_limited,
    unauthorized,
    unprocessable,
)
from app.core.security import (
    create_access_token,
    hash_opaque_token,
    hash_password,
    hash_refresh_token,
    mask_phone,
    new_opaque_token,
    new_refresh_token,
    refresh_expiry,
    utcnow,
    validate_password_strength,
    verify_password,
)
from app.db.base import get_db
from app.db.models import (
    Device,
    EmailVerification,
    OtpRequest,
    PasswordReset,
    Session,
    User,
    UserSettings,
)
from app.services.providers import get_email_provider, get_sms_provider

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------------------------------------------------------- helpers
def _user_snippet(user: User) -> dict:
    from app.services.serializers import avatar_url

    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "player_id": user.player_id,
        "avatar_url": avatar_url(user),
        "verified": user.verified,
        "onboarding_stage": user.onboarding_stage,
        "onboarding_complete": user.onboarding_stage == "complete",
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


async def _issue_tokens(db: AsyncSession, user: User, device_id: str | None = None,
                        family_id: uuid.UUID | None = None) -> dict:
    refresh = new_refresh_token()
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh),
        family_id=family_id or uuid.uuid4(),
        device_id=device_id,
        expires_at=refresh_expiry(),
    )
    db.add(session)
    await db.flush()
    return {"access_token": create_access_token(user.id, user.role), "refresh_token": refresh, "_session": session}


# --------------------------------------------------------------------------- register / login
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str
    role: str = Field(pattern="^(player|coach)$")


@router.post("/register", status_code=201)
async def register(body: RegisterIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    if not validate_password_strength(body.password):
        raise unprocessable("WEAK_PASSWORD",
                            f"Password must be at least {settings.password_min_length} characters and include a letter and a number.",
                            field="password")
    # Only reject if a NON-deleted user has this email. Soft-deleted
    # accounts free up their email + name so the person (or someone new)
    # can create a fresh account. Two different real users with the
    # same name (e.g. "Rahul") is fine — we don't check names.
    existing = (await db.execute(
        select(User).where(User.email == body.email, User.deleted_at.is_(None))
    )).scalar_one_or_none()
    if existing:
        raise conflict("EMAIL_TAKEN", "An account with this email already exists.", field="email")

    # If a soft-deleted account with this email still exists, rename its
    # email (email column has a UNIQUE constraint at the DB level, so
    # without this rewrite the fresh insert would still fail).
    deleted_hits = (await db.execute(
        select(User).where(User.email == body.email, User.deleted_at.is_not(None))
    )).scalars().all()
    for old in deleted_hits:
        # Simple, deterministic suffix so we don't collide with itself
        # again if the same person deletes+signs-up multiple times.
        old.email = f"deleted+{old.id}@sportyqo.local"
    if deleted_hits:
        await db.flush()

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name.strip(),
        role=body.role,
        onboarding_stage="profile",
    )
    db.add(user)
    await db.flush()
    db.add(UserSettings(user_id=user.id))

    # email verification (industry-mandatory even though no UI yet — spec §3)
    token = new_opaque_token()
    db.add(EmailVerification(user_id=user.id, token_hash=hash_opaque_token(token),
                             expires_at=utcnow() + timedelta(days=2)))
    tokens = await _issue_tokens(db, user)
    await db.commit()

    background.add_task(
        get_email_provider().send, user.email, "Verify your SportyQo email",
        f"Welcome to SportyQo! Verify your email with this token: {token}",
    )
    return {"user": _user_snippet(user), "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    role: str = Field(pattern="^(player|coach)$")


@router.post("/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(
        select(User).where(User.email == body.email).options(selectinload(User.avatar))
    )).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise unauthorized("INVALID_CREDENTIALS", "Email or password is incorrect.")
    if user.deleted_at is not None:
        raise forbidden("ACCOUNT_DELETED", "This account is scheduled for deletion. Contact support to restore it.")
    if user.role != body.role:
        raise forbidden("ROLE_MISMATCH", f"This account is registered as a {user.role}. Switch the toggle and try again.")

    tokens = await _issue_tokens(db, user)
    await db.commit()
    return {"user": _user_snippet(user), "access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}


# --------------------------------------------------------------------------- refresh / logout
class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/refresh")
async def refresh(body: RefreshIn, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)
    session = (
        await db.execute(select(Session).where(Session.refresh_token_hash == token_hash))
    ).scalar_one_or_none()
    if session is None:
        raise unauthorized("INVALID_TOKEN", "Refresh token not recognised.")

    if session.revoked_at is not None or session.replaced_by is not None:
        # Reuse detected → revoke the whole family (spec §3: reuse-detection revokes family).
        family = (await db.execute(select(Session).where(Session.family_id == session.family_id))).scalars().all()
        now = utcnow()
        for s in family:
            if s.revoked_at is None:
                s.revoked_at = now
        await db.commit()
        raise unauthorized("TOKEN_REUSED", "Refresh token reuse detected. Please log in again.")

    if session.expires_at < utcnow():
        raise unauthorized("TOKEN_EXPIRED", "Refresh token expired. Please log in again.")

    user = await db.get(User, session.user_id)
    if user is None or user.deleted_at is not None:
        raise unauthorized("INVALID_TOKEN", "Account not found.")

    tokens = await _issue_tokens(db, user, device_id=session.device_id, family_id=session.family_id)
    session.revoked_at = utcnow()
    session.replaced_by = tokens["_session"].id
    await db.commit()
    return {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}


class LogoutIn(BaseModel):
    refresh_token: str
    fcm_token: str | None = None


@router.post("/logout", status_code=204)
async def logout(body: LogoutIn, db: AsyncSession = Depends(get_db),
                 user: User = Depends(get_current_user)):
    session = (
        await db.execute(select(Session).where(Session.refresh_token_hash == hash_refresh_token(body.refresh_token),
                                               Session.user_id == user.id))
    ).scalar_one_or_none()
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
    if body.fcm_token:
        device = (await db.execute(select(Device).where(Device.fcm_token == body.fcm_token,
                                                        Device.user_id == user.id))).scalar_one_or_none()
        if device:
            await db.delete(device)
    await db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- OTP (coach onboarding)
class OtpSendIn(BaseModel):
    phone: str = Field(pattern=r"^\+\d{8,15}$")
    country_code: str | None = None
    purpose: str = "coach_verification"


@router.post("/otp/send")
async def otp_send(body: OtpSendIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db),
                   user: User | None = Depends(get_optional_user)):
    window_start = utcnow() - timedelta(minutes=settings.otp_send_window_minutes)
    recent = (
        await db.execute(
            select(func.count()).select_from(OtpRequest).where(OtpRequest.phone == body.phone,
                                                               OtpRequest.created_at >= window_start)
        )
    ).scalar_one()
    if recent >= settings.otp_send_limit:
        raise rate_limited("Too many codes requested for this number. Try again in a few minutes.",
                           retry_after=settings.otp_send_window_minutes * 60)

    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = OtpRequest(
        phone=body.phone,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        purpose=body.purpose,
        expires_at=utcnow() + timedelta(seconds=settings.otp_ttl_seconds),
        user_id=user.id if user else None,
    )
    db.add(otp)
    await db.commit()

    background.add_task(get_sms_provider().send, body.phone, f"Your SportyQo verification code is {code}")
    payload = {"request_id": f"otp_{otp.id.hex}", "expires_in": settings.otp_ttl_seconds,
               "masked_phone": mask_phone(body.phone)}
    if settings.otp_dev_echo and not settings.is_production:
        payload["dev_code"] = code  # development convenience only; disabled in production
    return payload


class OtpVerifyIn(BaseModel):
    request_id: str
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/otp/verify")
async def otp_verify(body: OtpVerifyIn, db: AsyncSession = Depends(get_db),
                     user: User | None = Depends(get_optional_user)):
    raw = body.request_id.removeprefix("otp_")
    try:
        otp_id = uuid.UUID(raw)
    except ValueError:
        raise bad_request("INVALID_CODE", "Unknown verification request.", field="request_id")
    otp = await db.get(OtpRequest, otp_id)
    if otp is None:
        raise bad_request("INVALID_CODE", "Unknown verification request.", field="request_id")
    if otp.verified_at is not None:
        raise bad_request("INVALID_CODE", "This code was already used.")
    if otp.expires_at < utcnow():
        raise gone("CODE_EXPIRED", "This code has expired. Request a new one.")
    if otp.attempts >= settings.otp_max_attempts:
        raise rate_limited("Too many incorrect attempts. Request a new code.", retry_after=60)

    if hashlib.sha256(body.code.encode()).hexdigest() != otp.code_hash:
        otp.attempts += 1
        await db.commit()
        raise bad_request("INVALID_CODE", "That code is incorrect.", field="code")

    otp.verified_at = utcnow()
    target = user or (await db.get(User, otp.user_id) if otp.user_id else None)
    if target is not None:
        # Only consider ACTIVE users when checking if the phone is taken.
        taken = (
            await db.execute(
                select(User.id).where(
                    User.phone == otp.phone,
                    User.id != target.id,
                    User.deleted_at.is_(None),
                )
            )
        ).first()
        if taken:
            raise conflict("PHONE_TAKEN", "This phone number is already linked to another account.", field="phone")
        # If soft-deleted users still hold this phone number, free it up
        # so the unique constraint doesn't block the target user.
        deleted_holders = (
            await db.execute(
                select(User).where(
                    User.phone == otp.phone,
                    User.id != target.id,
                    User.deleted_at.is_not(None),
                )
            )
        ).scalars().all()
        for old in deleted_holders:
            old.phone = None
        target.phone = otp.phone
        target.phone_verified_at = otp.verified_at
    await db.commit()
    return {
        "verified": True,
        "phone_verified_at": otp.verified_at.isoformat(),
        "next_step": "complete_coach_profile" if otp.purpose == "coach_verification" else None,
    }


# --------------------------------------------------------------------------- password reset & email verify
class ForgotIn(BaseModel):
    email: EmailStr


@router.post("/password/forgot")
async def password_forgot(body: ForgotIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user and user.deleted_at is None:
        token = new_opaque_token()
        db.add(PasswordReset(user_id=user.id, token_hash=hash_opaque_token(token),
                             expires_at=utcnow() + timedelta(hours=1)))
        await db.commit()
        background.add_task(get_email_provider().send, user.email, "Reset your SportyQo password",
                            f"Use this token to reset your password (valid 1 hour): {token}")
    # Same response whether or not the account exists — no enumeration.
    return {"sent": True}


class ResetIn(BaseModel):
    token: str
    new_password: str


@router.post("/password/reset")
async def password_reset(body: ResetIn, db: AsyncSession = Depends(get_db)):
    if not validate_password_strength(body.new_password):
        raise unprocessable("WEAK_PASSWORD",
                            f"Password must be at least {settings.password_min_length} characters and include a letter and a number.",
                            field="new_password")
    reset = (
        await db.execute(select(PasswordReset).where(PasswordReset.token_hash == hash_opaque_token(body.token)))
    ).scalar_one_or_none()
    if reset is None or reset.used_at is not None:
        raise bad_request("INVALID_CODE", "This reset link is invalid.", field="token")
    if reset.expires_at < utcnow():
        raise gone("CODE_EXPIRED", "This reset link has expired.")

    user = await db.get(User, reset.user_id)
    user.password_hash = hash_password(body.new_password)
    reset.used_at = utcnow()
    # revoke all sessions — force re-login everywhere
    sessions = (await db.execute(select(Session).where(Session.user_id == user.id,
                                                       Session.revoked_at.is_(None)))).scalars().all()
    for s in sessions:
        s.revoked_at = utcnow()
    await db.commit()
    return {"reset": True}


class EmailVerifyIn(BaseModel):
    token: str


@router.post("/email/verify")
async def email_verify(body: EmailVerifyIn, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(EmailVerification).where(EmailVerification.token_hash == hash_opaque_token(body.token)))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None:
        raise bad_request("INVALID_CODE", "This verification link is invalid.", field="token")
    if row.expires_at < utcnow():
        raise gone("CODE_EXPIRED", "This verification link has expired.")
    user = await db.get(User, row.user_id)
    user.email_verified_at = utcnow()
    row.used_at = utcnow()
    await db.commit()
    return {"verified": True}


@router.post("/email/resend")
async def email_resend(background: BackgroundTasks, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    if user.email_verified_at is not None:
        return {"sent": False, "already_verified": True}
    token = new_opaque_token()
    db.add(EmailVerification(user_id=user.id, token_hash=hash_opaque_token(token),
                             expires_at=utcnow() + timedelta(days=2)))
    await db.commit()
    background.add_task(get_email_provider().send, user.email, "Verify your SportyQo email",
                        f"Verify your email with this token: {token}")
    return {"sent": True}
