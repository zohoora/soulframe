"""
FastAPI router for Soul Frame authoring API.
Provides CRUD operations for gallery images and their metadata.
"""

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image as PILImage

from soulframe import config
from authoring.backend.models import (
    ImageMetadataModel,
    ImageInfoModel,
    AudioModel,
    InteractionModel,
    TransitionsModel,
)

router = APIRouter(prefix="/api")

GALLERY_DIR = config.GALLERY_DIR

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aac"}


def _get_image_dir(image_id: str) -> Path:
    """Return the directory path for a given image id."""
    path = GALLERY_DIR / image_id
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Image '{image_id}' not found")
    return path


def _read_metadata(image_dir: Path) -> dict:
    """Read and parse metadata.json from an image directory."""
    meta_path = image_dir / "metadata.json"
    if not meta_path.exists():
        return {}
    with open(meta_path, "r") as f:
        return json.load(f)


async def _write_metadata(image_dir: Path, data: dict) -> None:
    """Write metadata dict to metadata.json in the image directory."""
    meta_path = image_dir / "metadata.json"
    async with aiofiles.open(str(meta_path), "w") as f:
        await f.write(json.dumps(data, indent=2))


def _find_image_file(image_dir: Path) -> str | None:
    """Find the main image file in a directory."""
    for ext in ALLOWED_IMAGE_EXTENSIONS:
        for file in image_dir.iterdir():
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
        meta = _read_metadata(entry)
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
    meta = _read_metadata(image_dir)
    if not meta:
        # Return a default metadata scaffold
        image_file = _find_image_file(image_dir)
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

    # Save the uploaded image
    dest_filename = f"image{ext}"
    dest_path = image_dir / dest_filename
    async with aiofiles.open(str(dest_path), "wb") as f:
        content = await file.read()
        await f.write(content)

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
        interaction=InteractionModel(),
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
    dest_path = audio_dir / safe_name

    async with aiofiles.open(str(dest_path), "wb") as f:
        content = await file.read()
        await f.write(content)

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
    shutil.rmtree(image_dir)
    return {"status": "deleted", "id": image_id}


# --------------------------------------------------------------------------- #
# GET /api/images/{image_id}/file — serve the actual image file
# --------------------------------------------------------------------------- #
@router.get("/images/{image_id}/file")
async def get_image_file(image_id: str):
    """Serve the actual image file for rendering on the Konva canvas."""
    image_dir = _get_image_dir(image_id)
    image_file = _find_image_file(image_dir)
    if not image_file:
        raise HTTPException(status_code=404, detail="No image file found in directory")

    file_path = image_dir / image_file
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
