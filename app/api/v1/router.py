"""Aggregate v1 router."""
from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    coaches,
    config_api,
    leagues,
    matches,
    media,
    notifications,
    players,
    posts,
    social,
    users,
    ws,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(players.router)
api_router.include_router(coaches.router)
api_router.include_router(leagues.router)
api_router.include_router(matches.router)
api_router.include_router(posts.router)
api_router.include_router(social.router)
api_router.include_router(media.router)
api_router.include_router(notifications.router)
api_router.include_router(config_api.router)
api_router.include_router(admin.router)
api_router.include_router(admin._admin_panel_router)
api_router.include_router(ws.router)
