"""
FastAPI application setup for the Soul Frame Authoring Tool.
"""

import hmac
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from soulframe import config
from authoring.backend.routes import router as api_router

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Methods that mutate state and require API key (when configured).
_MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure the gallery directory exists on startup."""
    config.GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    if config.AUTHORING_API_KEY:
        logger.info("Authoring API key is configured — mutating requests require X-Api-Key header")
    else:
        logger.warning(
            "No SOULFRAME_API_KEY set — authoring API is unauthenticated. "
            "Set the env var to require an API key for mutating requests."
        )
    yield


app = FastAPI(
    title="Soul Frame Authoring",
    description="Authoring tool for the Soul Frame interactive art installation",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — restrict to localhost origins by default
# ---------------------------------------------------------------------------
_cors_origins = os.environ.get("SOULFRAME_CORS_ORIGINS", "").strip()
if _cors_origins:
    _allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    _allowed_origins = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        f"http://localhost:{config.AUTHORING_PORT}",
        f"http://127.0.0.1:{config.AUTHORING_PORT}",
    ]
    # Deduplicate while preserving order
    _allowed_origins = list(dict.fromkeys(_allowed_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Api-Key"],
)


# ---------------------------------------------------------------------------
# API key middleware — protects mutating endpoints when a key is configured
# ---------------------------------------------------------------------------
@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    api_key = config.AUTHORING_API_KEY
    if (
        api_key
        and request.method in _MUTATING_METHODS
        and request.url.path.startswith("/api/")
    ):
        provided = request.headers.get("X-Api-Key", "")
        if not hmac.compare_digest(provided, api_key):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid or missing API key"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# API routes (must be registered before the catch-all static mount)
# ---------------------------------------------------------------------------
app.include_router(api_router)

# ---------------------------------------------------------------------------
# Serve the frontend SPA at /
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def main():
    """Run the authoring server via uvicorn."""
    import uvicorn

    uvicorn.run(
        "authoring.backend.app:app",
        host=config.AUTHORING_HOST,
        port=config.AUTHORING_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
