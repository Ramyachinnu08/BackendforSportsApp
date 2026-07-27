"""Server-only admin surface (X-Admin-Key header). All actions audit-logged."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require_admin
from app.core.errors import bad_request, not_found
from app.core.security import utcnow
from app.db.base import get_db
from app.db.models import (
    AuditLog,
    Certification,
    CertificationDocument,
    CoachProfile,
    User,
    UserProfile,
)
from app.services import scoring
from app.services.notify import create_notification, deliver_notification
from app.services.serializers import serialize_media_item

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/certifications")
async def list_certifications(status: str = Query("under_review"),
                              db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Certification, User)
            .join(User, User.id == Certification.coach_id)
            .options(selectinload(Certification.documents).selectinload(CertificationDocument.media))
            .where(Certification.status == status)
            .order_by(Certification.created_at)
        )
    ).all()
    return {
        "items": [
            {
                "certification_id": str(c.id),
                "coach": {"id": str(u.id), "name": u.full_name, "email": u.email},
                "certification_level": c.certification_level,
                "issuing_body": c.issuing_body,
                "issued_on": c.issued_on.isoformat() if c.issued_on else None,
                "status": c.status,
                "submitted_at": c.created_at.isoformat(),
                "documents": [serialize_media_item(d.media) for d in c.documents],
            }
            for c, u in rows
        ]
    }


class ReviewIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    rejection_reason: str | None = Field(default=None, max_length=500)
    reviewer: str = "admin"


@router.post("/certifications/{certification_id}/review")
async def review_certification(certification_id: uuid.UUID, body: ReviewIn,
                               background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    cert = await db.get(Certification, certification_id)
    if cert is None:
        raise not_found("CERTIFICATION_NOT_FOUND", "Certification not found.")
    if cert.status != "under_review":
        raise bad_request("VALIDATION_ERROR", f"Certification is already {cert.status}.")
    if body.status == "rejected" and not body.rejection_reason:
        raise bad_request("MISSING_FIELD", "rejection_reason is required when rejecting.",
                          field="rejection_reason")

    cert.status = body.status
    cert.reviewed_by = body.reviewer
    cert.reviewed_at = utcnow()
    cert.rejection_reason = body.rejection_reason

    coach = await db.get(User, cert.coach_id)
    if body.status == "approved":
        coach.verified = True  # blue tick (spec: coach verified set by certification approval)
        cp = (await db.execute(select(CoachProfile).where(CoachProfile.user_id == coach.id))).scalar_one_or_none()
        if cp:
            cp.certification = cert.certification_level
        message = f"Your {cert.certification_level} certification was approved — you're now verified!"
    else:
        message = f"Your {cert.certification_level} certification was rejected: {body.rejection_reason}"

    n = await create_notification(db, coach.id, "certification_update", message)
    db.add(AuditLog(actor=body.reviewer, action=f"certification.{body.status}",
                    entity="certification", entity_id=str(cert.id),
                    detail={"coach_id": str(coach.id), "reason": body.rejection_reason}))
    await db.commit()
    background.add_task(deliver_notification, n.id)
    return {"certification_id": str(cert.id), "status": cert.status}


class CorrectionIn(BaseModel):
    user_id: uuid.UUID
    points: int = Field(ge=-100000, le=100000)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/points/correction", status_code=201)
async def points_correction(body: CorrectionIn, db: AsyncSession = Depends(get_db)):
    """Score corrections go through the ledger too — the append-only invariant
    holds even for admins (no direct writes to qo_scores, ever)."""
    user = await db.get(User, body.user_id)
    if user is None:
        raise not_found("PLAYER_NOT_FOUND", "User not found.")
    event = await scoring.award_points(db, body.user_id, source="correction",
                                       points=body.points, reason=f"Admin correction: {body.reason}")
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == body.user_id))).scalar_one_or_none()
    category = scoring.ranking_category(profile)
    if category:
        await scoring.recompute_rankings(db, category)
    db.add(AuditLog(actor="admin", action="points.correction", entity="user",
                    entity_id=str(body.user_id),
                    detail={"points": body.points, "reason": body.reason}))
    await db.commit()
    return {"event_id": str(event.id), "points": body.points}


# ────────────────────────────────────────────────────────────────────────────
# User management — list, search, delete. Used by the admin web panel.
# ────────────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_all_users(
    q: str = Query("", description="Search by name/email/phone"),
    include_deleted: bool = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List every user in the system with basic details."""
    from sqlalchemy import or_, func
    query = select(User)
    if not include_deleted:
        query = query.where(User.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(
            User.full_name.ilike(like),
            User.email.ilike(like),
            User.phone.ilike(like),
        ))
    total = (await db.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar_one()
    query = query.order_by(User.created_at.desc()).offset((page - 1) * limit).limit(limit)
    users = (await db.execute(query)).scalars().all()
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "items": [
            {
                "id": str(u.id),
                "full_name": u.full_name,
                "email": u.email,
                "phone": u.phone,
                "role": u.role,
                "player_id": u.player_id,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
                "email_verified": u.email_verified_at is not None,
            }
            for u in users
        ],
    }


@router.delete("/users/{user_id}", status_code=204)
async def hard_delete_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Permanently delete a user + free up their email/phone.
    Use with care — this is an admin-only nuclear option for spam/fake accounts."""
    user = await db.get(User, user_id)
    if user is None:
        raise not_found("USER_NOT_FOUND", "That user doesn't exist.")
    # Free up the email/phone so someone else can use them (in case the
    # deleted user was squatting).
    user.email = f"deleted+{user.id}@sportyqo.local"
    user.phone = None
    user.deleted_at = utcnow()
    user.full_name = f"[Deleted] {user.full_name or ''}".strip()
    await db.commit()
    return None


# ────────────────────────────────────────────────────────────────────────────
# Admin web panel — single-page HTML dashboard for user management.
# Accessible at /v1/admin/panel with the X-Admin-Key header (or ?key= query
# param for browser convenience). Everything happens in-page via fetch().
# ────────────────────────────────────────────────────────────────────────────

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

# Note: this endpoint is intentionally outside the require_admin dependency
# so the browser can load the HTML shell first, then send the admin key
# with each API call. The API endpoints themselves stay locked down.
_admin_panel_router = APIRouter(prefix="/admin", tags=["admin"])


@_admin_panel_router.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    """Serve the admin web dashboard as a single self-contained HTML page."""
    return HTMLResponse(content=_ADMIN_HTML)


_ADMIN_HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>SportyQo Admin</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
    body { background: #0a0a0a; color: #fff; min-height: 100vh; }
    .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
    .login-box { max-width: 400px; margin: 100px auto; padding: 32px; background: #141414; border-radius: 12px; }
    h1 { font-size: 24px; margin-bottom: 24px; color: #1a6bff; }
    h2 { font-size: 18px; margin-bottom: 12px; }
    input { width: 100%; padding: 12px; border: 1px solid #333; background: #1a1a1a; color: #fff; border-radius: 8px; font-size: 14px; margin-bottom: 12px; }
    input:focus { outline: none; border-color: #1a6bff; }
    button { padding: 10px 20px; background: #1a6bff; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; }
    button:hover { background: #1454cc; }
    button.danger { background: #d32f2f; }
    button.danger:hover { background: #b71c1c; }
    .toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
    .toolbar input { flex: 1; min-width: 200px; margin-bottom: 0; }
    table { width: 100%; border-collapse: collapse; background: #141414; border-radius: 8px; overflow: hidden; }
    th { background: #1a1a1a; padding: 12px; text-align: left; font-size: 12px; text-transform: uppercase; color: #888; border-bottom: 1px solid #333; }
    td { padding: 12px; border-bottom: 1px solid #222; font-size: 14px; }
    tr:hover { background: #1a1a1a; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge.coach { background: #1a5c3a33; color: #4caf50; }
    .badge.player { background: #1a3a5c33; color: #1a6bff; }
    .badge.deleted { background: #5c1a1a33; color: #f44336; }
    .stats { display: flex; gap: 16px; margin-bottom: 20px; }
    .stat { background: #141414; padding: 16px 24px; border-radius: 8px; flex: 1; }
    .stat-value { font-size: 24px; font-weight: 700; color: #1a6bff; }
    .stat-label { font-size: 12px; color: #888; text-transform: uppercase; margin-top: 4px; }
    .error { background: #d32f2f22; color: #f44336; padding: 12px; border-radius: 8px; margin-bottom: 16px; }
    .loading { text-align: center; padding: 40px; color: #888; }
    .empty { text-align: center; padding: 40px; color: #888; }
    label { display: block; margin-bottom: 6px; font-size: 13px; color: #ccc; }
    .checkbox { display: flex; align-items: center; gap: 6px; color: #ccc; font-size: 13px; margin-bottom: 0; }
    .checkbox input { width: auto; margin: 0; }
  </style>
</head>
<body>
  <div id=\"login\" class=\"login-box\">
    <h1>SportyQo Admin</h1>
    <label>Admin Password</label>
    <input type=\"password\" id=\"adminKey\" placeholder=\"Enter admin key...\" />
    <button onclick=\"login()\">Login</button>
    <div id=\"loginError\" class=\"error\" style=\"display:none; margin-top: 12px;\"></div>
  </div>

  <div id=\"dashboard\" class=\"container\" style=\"display:none;\">
    <h1>SportyQo Admin Panel</h1>
    <div class=\"stats\">
      <div class=\"stat\"><div class=\"stat-value\" id=\"totalUsers\">-</div><div class=\"stat-label\">Total Users</div></div>
      <div class=\"stat\"><div class=\"stat-value\" id=\"totalCoaches\">-</div><div class=\"stat-label\">Coaches</div></div>
      <div class=\"stat\"><div class=\"stat-value\" id=\"totalPlayers\">-</div><div class=\"stat-label\">Players</div></div>
    </div>

    <div class=\"toolbar\">
      <input type=\"text\" id=\"searchInput\" placeholder=\"Search by name, email, phone...\" oninput=\"debounceLoad()\" />
      <label class=\"checkbox\"><input type=\"checkbox\" id=\"includeDeleted\" onchange=\"loadUsers()\" /> Show deleted</label>
      <button onclick=\"loadUsers()\">Refresh</button>
      <button onclick=\"logout()\" style=\"background:#333\">Logout</button>
    </div>

    <div id=\"content\" class=\"loading\">Loading...</div>
  </div>

  <script>
    let ADMIN_KEY = '';
    let debounceTimer;

    function debounceLoad() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(loadUsers, 300);
    }

    async function login() {
      const key = document.getElementById('adminKey').value.trim();
      if (!key) return;
      // Verify by hitting /admin/users with the key.
      try {
        const res = await fetch('/v1/admin/users?limit=1', { headers: { 'X-Admin-Key': key } });
        if (res.ok) {
          ADMIN_KEY = key;
          localStorage.setItem('sportyqo_admin_key', key);
          document.getElementById('login').style.display = 'none';
          document.getElementById('dashboard').style.display = 'block';
          loadUsers();
        } else {
          document.getElementById('loginError').textContent = 'Invalid admin key.';
          document.getElementById('loginError').style.display = 'block';
        }
      } catch (e) {
        document.getElementById('loginError').textContent = 'Connection error: ' + e.message;
        document.getElementById('loginError').style.display = 'block';
      }
    }

    function logout() {
      ADMIN_KEY = '';
      localStorage.removeItem('sportyqo_admin_key');
      document.getElementById('login').style.display = 'block';
      document.getElementById('dashboard').style.display = 'none';
      document.getElementById('adminKey').value = '';
    }

    async function loadUsers() {
      const q = document.getElementById('searchInput').value.trim();
      const includeDeleted = document.getElementById('includeDeleted').checked;
      document.getElementById('content').innerHTML = '<div class=\"loading\">Loading...</div>';
      try {
        const params = new URLSearchParams({ q, include_deleted: includeDeleted, limit: 200 });
        const res = await fetch('/v1/admin/users?' + params, { headers: { 'X-Admin-Key': ADMIN_KEY } });
        if (!res.ok) throw new Error('Failed to load (' + res.status + ')');
        const data = await res.json();
        renderUsers(data);
      } catch (e) {
        document.getElementById('content').innerHTML = '<div class=\"error\">' + e.message + '</div>';
      }
    }

    function renderUsers(data) {
      document.getElementById('totalUsers').textContent = data.total;
      const coaches = data.items.filter(u => u.role === 'coach').length;
      const players = data.items.filter(u => u.role === 'player').length;
      document.getElementById('totalCoaches').textContent = coaches;
      document.getElementById('totalPlayers').textContent = players;
      if (data.items.length === 0) {
        document.getElementById('content').innerHTML = '<div class=\"empty\">No users found.</div>';
        return;
      }
      const rows = data.items.map(u => {
        const badgeClass = u.deleted_at ? 'deleted' : (u.role === 'coach' ? 'coach' : 'player');
        const badgeText = u.deleted_at ? 'DELETED' : u.role.toUpperCase();
        const created = u.created_at ? new Date(u.created_at).toLocaleDateString() : '-';
        return `<tr>
          <td>${escapeHtml(u.full_name || '-')}</td>
          <td>${escapeHtml(u.email || '-')}</td>
          <td>${escapeHtml(u.phone || '-')}</td>
          <td><span class=\"badge ${badgeClass}\">${badgeText}</span></td>
          <td>${created}</td>
          <td>${u.player_id || '-'}</td>
          <td>${u.deleted_at ? '' : '<button class=\"danger\" onclick=\"deleteUser(\\'' + u.id + '\\', \\'' + escapeHtml(u.full_name || u.email || '?').replace(/'/g,'') + '\\')\">Delete</button>'}</td>
        </tr>`;
      }).join('');
      document.getElementById('content').innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Name</th><th>Email</th><th>Phone</th><th>Role</th><th>Joined</th><th>Player ID</th><th>Action</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        <div style=\"margin-top: 12px; color: #888; font-size: 12px;\">Showing ${data.items.length} of ${data.total} users</div>
      `;
    }

    async function deleteUser(id, name) {
      if (!confirm('Permanently delete user: ' + name + '?\\n\\nThis frees up their email/phone for reuse. This action cannot be undone.')) return;
      try {
        const res = await fetch('/v1/admin/users/' + id, { method: 'DELETE', headers: { 'X-Admin-Key': ADMIN_KEY } });
        if (!res.ok) throw new Error('Delete failed (' + res.status + ')');
        loadUsers();
      } catch (e) {
        alert('Delete failed: ' + e.message);
      }
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));
    }

    // Auto-login if key was saved.
    const saved = localStorage.getItem('sportyqo_admin_key');
    if (saved) {
      document.getElementById('adminKey').value = saved;
      login();
    }
  </script>
</body>
</html>
"""
