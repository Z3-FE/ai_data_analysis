from fastapi import APIRouter

router = APIRouter(tags=["ping"])


@router.get("/ping")
def ping() -> dict[str, str]:
    return {"message": "pong"}

