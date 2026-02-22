"""
FastAPI application setup for the Soul Frame Authoring Tool.
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from soulframe import config
from authoring.backend.routes import router as api_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure the gallery directory exists on startup."""
    config.GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Soul Frame Authoring",
    description="Authoring tool for the Soul Frame interactive art installation",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow all origins for local development
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        reload=True,
    )


if __name__ == "__main__":
    main()
