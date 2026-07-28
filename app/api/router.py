from fastapi import APIRouter

from app.api.routers.agent import router as agent_router
from app.api.routers.meta import router as meta_router
from app.api.routers.ping import router as ping_router

api_router = APIRouter()
api_router.include_router(meta_router)
api_router.include_router(ping_router)
api_router.include_router(agent_router)
