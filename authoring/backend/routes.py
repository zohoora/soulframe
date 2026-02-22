"""
FastAPI router for Soul Frame authoring API.
Provides CRUD operations for gallery images and their metadata.
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image as PILImage

from soulframe import config
from authoring.backend.models import (
    ImageMetadataModel,
    ImageInfoModel,
    AudioModel,
    InteractionSettingsModel,
    TransitionsModel,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

GALLERY_DIR = config.GALLERY_DIR

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aac"}

MAX_IMAGE_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB
MAX_AUDIO_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOAD_CHUNK_SIZE = 1024 * 1024             # 1 MB read chunks


def _get_image_dir(image_id: str) -> Path:
    """Return the directory path for a given image id.

    Validates that image_id does not escape the gallery directory
    (prevents path traversal attacks via crafted IDs like ``../../etc``).
    """
    # Reject path separators, traversal components, and hidden directories
    if "/" in image_id or "\\" in image_id or image_id.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid image ID")

    path = (GALLERY_DIR / image_id).resolve()

    # Ensure the resolved path is actually inside GALLERY_DIR
    gallery_resolved = GALLERY_DIR.resolve()
    if not str(path).startswith(str(gallery_resolved) + os.sep) and path != gallery_resolved:
        raise HTTPException(status_code=400, detail="Invalid image ID")

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Image '{image_id}' not found")
    return path


async def _read_metadata(image_dir: Path) -> dict:
    """Read and parse metadata.json from an image directory."""
    meta_path = image_dir / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        async with aiofiles.open(str(meta_path), "r") as f:
            content = await f.read()
        return json.loads(content)
    except (json.JSONDecodeError, ValueError, OSError):
        logger.warning("Failed to read metadata.json in %s", image_dir.name)
        return {}


async def _write_metadata(image_dir: Path, data: dict) -> None:
    """Write metadata dict to metadata.json atomically."""
    meta_path = image_dir / "metadata.json"
    tmp_path = image_dir / "metadata.json.tmp"
    async with aiofiles.open(str(tmp_path), "w") as f:
        await f.write(json.dumps(data, indent=2))

    # Blocking fsync + rename are offloaded to thread pool to avoid
    # stalling the event loop.
    def _sync_and_rename() -> None:
        fd = os.open(str(tmp_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(meta_path))

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_and_rename)


async def _find_image_file(image_dir: Path) -> Optional[str]:
    """Find the main image file in a directory.

    Prefers the filename specified in metadata.json, falls back to
    scanning for any image file by extension.
    """
    # Try metadata first
    meta = await _read_metadata(image_dir)
    if meta:
        meta_filename = meta.get("image", {}).get("filename", "")
        if meta_filename:
            resolved = (image_dir / meta_filename).resolve()
            if (str(resolved).startswith(str(image_dir.resolve()) + os.sep)
                    and resolved.is_file()):
                return meta_filename
            elif meta_filename:
                logger.warning(
                    "Metadata filename escapes image dir: %s", meta_filename
                )

    # Fallback: scan for any image file
    for ext in sorted(ALLOWED_IMAGE_EXTENSIONS):
        for file in sorted(image_dir.iterdir()):
            if file.suffix.lower() == ext and file.is_file():
                return file.name
    return None


def _make_thumbnail_url(image_id: str) -> str:
    """Build the thumbnail/image URL for an image entry."""
    return f"/api/images/{image_id}/file"


# --------------------------------------------------------------------------- #
# GET /api/images — list all gallery images
# --------------------------------------------------------------------------- #
@router.get("/images")
async def list_images():
    """List all images in the gallery with summary info."""
    if not GALLERY_DIR.exists():
        return []

    results = []
    for entry in sorted(GALLERY_DIR.iterdir()):
        if not entry.is_dir():
            continue

        image_id = entry.name
        meta = await _read_metadata(entry)
        has_metadata = bool(meta)
        title = meta.get("title", image_id) if meta else image_id
        thumbnail_url = _make_thumbnail_url(image_id)

        results.append({
            "id": image_id,
            "title": title,
            "thumbnail_url": thumbnail_url,
            "has_metadata": has_metadata,
        })

    return results


# --------------------------------------------------------------------------- #
# GET /api/images/{image_id} — full metadata for one image
# --------------------------------------------------------------------------- #
@router.get("/images/{image_id}")
async def get_image(image_id: str):
    """Return the full metadata.json contents for an image."""
    image_dir = _get_image_dir(image_id)
    meta = await _read_metadata(image_dir)
    if not meta:
        # Return a default metadata scaffold
        image_file = await _find_image_file(image_dir)
        width, height = 1920, 1080
        if image_file:
            try:
                with PILImage.open(image_dir / image_file) as img:
                    width, height = img.size
            except Exception:
                pass
        default = ImageMetadataModel(
            id=image_id,
            title=image_id,
            image=ImageInfoModel(
                filename=image_file or "image.jpg",
                width=width,
                height=height,
            ),
        )
        return default.model_dump()
    return meta


# --------------------------------------------------------------------------- #
# PUT /api/images/{image_id} — update metadata
# --------------------------------------------------------------------------- #
@router.put("/images/{image_id}")
async def update_image(image_id: str, body: ImageMetadataModel):
    """Overwrite metadata.json for the given image."""
    image_dir = _get_image_dir(image_id)
    data = body.model_dump()
    # Ensure the id field matches the URL
    data["id"] = image_id
    await _write_metadata(image_dir, data)
    return {"status": "ok", "id": image_id}


# --------------------------------------------------------------------------- #
# POST /api/images — create a new image entry
# --------------------------------------------------------------------------- #
@router.post("/images")
async def create_image(
    file: UploadFile = File(...),
    title: str = Form("Untitled"),
):
    """
    Create a new gallery entry.
    Accepts multipart form with an image file and a title.
    Creates directory, saves the image, writes initial metadata.json.
    """
    # Validate extension
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format '{ext}'. Allowed: {ALLOWED_IMAGE_EXTENSIONS}",
        )

    # Generate a slug-style id from the title
    slug = title.lower().strip().replace(" ", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    if not slug:
        slug = "image"
    image_id = f"{slug}_{uuid.uuid4().hex[:8]}"

    image_dir = GALLERY_DIR / image_id
    image_dir.mkdir(parents=True, exist_ok=True)

    # Create audio subdirectory
    (image_dir / "audio").mkdir(exist_ok=True)

    # Save the uploaded image (chunked to limit memory usage)
    dest_filename = f"image{ext}"
    dest_path = image_dir / dest_filename
    total_written = 0
    try:
        async with aiofiles.open(str(dest_path), "wb") as f:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_IMAGE_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Image file exceeds maximum size of {MAX_IMAGE_UPLOAD_BYTES // (1024*1024)} MB",
                    )
                await f.write(chunk)
    except HTTPException:
        shutil.rmtree(image_dir, ignore_errors=True)
        raise

    # Determine image dimensions
    width, height = 1920, 1080
    try:
        with PILImage.open(dest_path) as img:
            width, height = img.size
    except Exception:
        pass

    # Write initial metadata
    metadata = ImageMetadataModel(
        version=1,
        id=image_id,
        title=title,
        image=ImageInfoModel(filename=dest_filename, width=width, height=height),
        audio=AudioModel(),
        regions=[],
        interaction=InteractionSettingsModel(),
        transitions=TransitionsModel(),
    )
    await _write_metadata(image_dir, metadata.model_dump())

    return {"status": "created", "id": image_id, "title": title}


# --------------------------------------------------------------------------- #
# POST /api/images/{image_id}/audio — upload audio file
# --------------------------------------------------------------------------- #
@router.post("/images/{image_id}/audio")
async def upload_audio(image_id: str, file: UploadFile = File(...)):
    """Upload an audio file into the image's audio/ subdirectory."""
    image_dir = _get_image_dir(image_id)

    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{ext}'. Allowed: {ALLOWED_AUDIO_EXTENSIONS}",
        )

    audio_dir = image_dir / "audio"
    audio_dir.mkdir(exist_ok=True)

    safe_name = Path(file.filename).name if file.filename else f"audio{ext}"
    dest_path = (audio_dir / safe_name).resolve()
    if not str(dest_path).startswith(str(audio_dir.resolve()) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid audio filename")

    total_written = 0
    try:
        async with aiofiles.open(str(dest_path), "wb") as f:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_AUDIO_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Audio file exceeds maximum size of {MAX_AUDIO_UPLOAD_BYTES // (1024*1024)} MB",
                    )
                await f.write(chunk)
    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise

    return {
        "status": "ok",
        "filename": safe_name,
        "path": f"audio/{safe_name}",
    }


# --------------------------------------------------------------------------- #
# DELETE /api/images/{image_id} — remove an image and its directory
# --------------------------------------------------------------------------- #
@router.delete("/images/{image_id}")
async def delete_image(image_id: str):
    """Delete an image entry and its entire directory."""
    image_dir = _get_image_dir(image_id)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, shutil.rmtree, image_dir)
    return {"status": "deleted", "id": image_id}


# --------------------------------------------------------------------------- #
# GET /api/images/{image_id}/file — serve the actual image file
# --------------------------------------------------------------------------- #
@router.get("/images/{image_id}/file")
async def get_image_file(image_id: str):
    """Serve the actual image file for rendering on the Konva canvas."""
    image_dir = _get_image_dir(image_id)
    image_file = await _find_image_file(image_dir)
    if not image_file:
        raise HTTPException(status_code=404, detail="No image file found in directory")

    file_path = (image_dir / image_file).resolve()
    if not str(file_path).startswith(str(image_dir.resolve()) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid image file path")
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }
    ext = Path(image_file).suffix.lower()
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(str(file_path), media_type=media_type)
