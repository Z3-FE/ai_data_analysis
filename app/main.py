from fastapi import FastAPI

from app.api.routers.index import api_router

app = FastAPI(title="AI Data Analysis", version="0.1.0")
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
