"""Image manager for Soul Frame.

Scans the gallery directory for image packages (subdirectories containing a
``metadata.json``), parses them into ``ImageMetadata`` dataclasses, and
provides sequential cycling through the collection.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from soulframe import config
from soulframe.shared.types import (
    ImageMetadata,
    Region,
    RegionShape,
    GazeTrigger,
    HeartbeatConfig,
    VisualEffect,
    AmbientAudioConfig,
)

logger = logging.getLogger(__name__)


def _safe_int(value, default: int) -> int:
    """Coerce *value* to int, returning *default* on failure or None."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float) -> float:
    """Coerce *value* to float, returning *default* on failure or None."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ImageManager:
    """Loads, indexes, and cycles through gallery image packages."""

    def __init__(self, gallery_dir: Optional[Path] = None) -> None:
        self._gallery_dir: Path = Path(gallery_dir) if gallery_dir else Path(config.GALLERY_DIR)
        self._images: List[ImageMetadata] = []
        self._image_dirs: List[Path] = []
        self._index: int = 0

    # ------------------------------------------------------------------
    # Scanning / parsing
    # ------------------------------------------------------------------

    def scan(self) -> int:
        """Scan the gallery directory and populate the image list.

        Returns the number of image packages found.
        """
        self._images.clear()
        self._image_dirs.clear()
        self._index = 0

        if not self._gallery_dir.is_dir():
            logger.warning("Gallery directory does not exist: %s", self._gallery_dir)
            return 0

        subdirs = sorted(p for p in self._gallery_dir.iterdir() if p.is_dir())

        for subdir in subdirs:
            meta_path = subdir / "metadata.json"
            if not meta_path.is_file():
                logger.debug("Skipping %s — no metadata.json", subdir.name)
                continue
            try:
                metadata = self._parse_metadata(meta_path)
                image_path = (subdir / metadata.image_filename).resolve()
                subdir_resolved = subdir.resolve()
                if not str(image_path).startswith(str(subdir_resolved) + os.sep):
                    logger.warning(
                        "Skipping %s — image path escapes package dir: %s",
                        subdir.name, metadata.image_filename,
                    )
                    continue
                if not image_path.is_file():
                    logger.warning(
                        "Skipping %s — image file '%s' not found",
                        subdir.name, metadata.image_filename,
                    )
                    continue
                self._images.append(metadata)
                self._image_dirs.append(subdir)
                logger.info("Loaded image package: %s", subdir.name)
            except Exception:
                logger.exception("Failed to parse metadata for %s", subdir.name)

        logger.info("Gallery scan complete: %d image(s) found", len(self._images))
        return len(self._images)

    def _parse_metadata(self, json_path: Path) -> ImageMetadata:
        """Read a ``metadata.json`` and return an ``ImageMetadata`` instance.

        Matches the schema defined in the spec (metadata.json format).
        """
        with open(json_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        # --- Parse regions ------------------------------------------------
        regions: List[Region] = []
        for r in raw.get("regions", []):
            shape_raw = r.get("shape") or {}
            raw_points = shape_raw.get("points_normalized", [])
            valid_points = []
            for pt in raw_points:
                if isinstance(pt, (list, tuple)) and len(pt) == 2:
                    try:
                        valid_points.append((float(pt[0]), float(pt[1])))
                    except (TypeError, ValueError):
                        logger.warning("Skipping invalid polygon point: %s", pt)
                else:
                    logger.warning("Skipping malformed polygon point: %s", pt)
            shape = RegionShape(
                shape_type=shape_raw.get("type", "polygon"),
                points_normalized=valid_points,
            )

            gt_raw = r.get("gaze_trigger") or {}
            try:
                dwell_val = int(gt_raw.get("dwell_time_ms", config.GAZE_DWELL_MS))
            except (TypeError, ValueError):
                logger.warning("Invalid dwell_time_ms in region '%s', using default", r.get("id", ""))
                dwell_val = config.GAZE_DWELL_MS
            try:
                conf_val = float(gt_raw.get("min_confidence", config.GAZE_MIN_CONFIDENCE))
            except (TypeError, ValueError):
                logger.warning("Invalid min_confidence in region '%s', using default", r.get("id", ""))
                conf_val = config.GAZE_MIN_CONFIDENCE
            gaze_trigger = GazeTrigger(
                dwell_time_ms=dwell_val,
                min_confidence=conf_val,
            )

            heartbeat: Optional[HeartbeatConfig] = None
            hb = r.get("heartbeat")
            if isinstance(hb, dict) and hb:
                dist = hb.get("intensity_by_distance") or {}
                try:
                    hb_fade = int(hb.get("fade_in_ms", 2000))
                except (TypeError, ValueError):
                    hb_fade = 2000
                try:
                    hb_max_dist = float(dist.get("max_distance_cm", 150.0))
                except (TypeError, ValueError):
                    hb_max_dist = 150.0
                try:
                    hb_min_dist = float(dist.get("min_distance_cm", 30.0))
                except (TypeError, ValueError):
                    hb_min_dist = 30.0
                heartbeat = HeartbeatConfig(
                    file=hb.get("file", ""),
                    loop=hb.get("loop", True),
                    bass_boost=hb.get("bass_boost", True),
                    fade_in_ms=hb_fade,
                    max_distance_cm=hb_max_dist,
                    min_distance_cm=hb_min_dist,
                    curve=str(dist.get("curve", "exponential")),
                )

            visual_effects: List[VisualEffect] = []
            for ve in (r.get("visual_effects") or []):
                visual_effects.append(
                    VisualEffect(
                        effect_type=ve.get("type", "breathing"),
                        params=ve.get("params", {}),
                        trigger=ve.get("trigger", "on_gaze_dwell"),
                        fade_in_ms=_safe_int(ve.get("fade_in_ms"), 3000),
                    )
                )

            region_id = str(r.get("id") or "").strip()
            if not region_id:
                region_id = "region_%d" % len(regions)
            # Ensure uniqueness among already-parsed regions
            seen_ids = {reg.id for reg in regions}
            if region_id in seen_ids:
                suffix = 1
                while "%s_%d" % (region_id, suffix) in seen_ids:
                    suffix += 1
                region_id = "%s_%d" % (region_id, suffix)
            region = Region(
                id=region_id,
                label=r.get("label", ""),
                shape=shape,
                gaze_trigger=gaze_trigger,
                heartbeat=heartbeat,
                visual_effects=visual_effects,
            )
            regions.append(region)

        # --- Parse ambient audio ------------------------------------------
        ambient: Optional[AmbientAudioConfig] = None
        audio_raw = raw.get("audio") or {}
        aa = audio_raw.get("ambient") or {}
        if aa:
            ambient = AmbientAudioConfig(
                file=aa.get("file", ""),
                loop=aa.get("loop", True),
                fade_in_distance_cm=_safe_float(aa.get("fade_in_distance_cm"), 200.0),
                fade_in_complete_cm=_safe_float(aa.get("fade_in_complete_cm"), 100.0),
                fade_curve=aa.get("fade_curve", "ease_in_out"),
            )

        # --- Parse image info ---------------------------------------------
        image_raw = raw.get("image") or {}
        interaction_raw = raw.get("interaction") or {}
        transitions_raw = raw.get("transitions") or {}

        metadata = ImageMetadata(
            version=raw.get("version", 1),
            id=raw.get("id", json_path.parent.name),
            title=raw.get("title", ""),
            image_filename=image_raw.get("filename", "image.jpg"),
            image_width=_safe_int(image_raw.get("width"), 1920),
            image_height=_safe_int(image_raw.get("height"), 1080),
            ambient=ambient,
            regions=regions,
            min_interaction_distance_cm=_safe_float(
                interaction_raw.get("min_interaction_distance_cm"),
                config.PRESENCE_DISTANCE_CM,
            ),
            close_interaction_distance_cm=_safe_float(
                interaction_raw.get("close_interaction_distance_cm"),
                config.CLOSE_INTERACTION_DISTANCE_CM,
            ),
            fade_in_ms=_safe_int(transitions_raw.get("fade_in_ms"), config.DEFAULT_FADE_IN_MS),
            fade_out_ms=_safe_int(transitions_raw.get("fade_out_ms"), config.DEFAULT_FADE_OUT_MS),
            audio_crossfade_ms=_safe_int(
                transitions_raw.get("audio_crossfade_ms"),
                config.DEFAULT_AUDIO_CROSSFADE_MS,
            ),
        )
        return metadata

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @property
    def current_image(self) -> Optional[ImageMetadata]:
        if not self._images:
            return None
        return self._images[self._index]

    @property
    def current_image_dir(self) -> Optional[Path]:
        if not self._image_dirs:
            return None
        return self._image_dirs[self._index]

    @property
    def image_count(self) -> int:
        return len(self._images)

    def next_image(self) -> Optional[ImageMetadata]:
        if not self._images:
            return None
        self._index = (self._index + 1) % len(self._images)
        logger.info(
            "Advanced to image %d/%d: %s",
            self._index + 1,
            len(self._images),
            self._images[self._index].title,
        )
        return self._images[self._index]

    def prev_image(self) -> Optional[ImageMetadata]:
        if not self._images:
            return None
        self._index = (self._index - 1) % len(self._images)
        logger.info(
            "Rewound to image %d/%d: %s",
            self._index + 1,
            len(self._images),
            self._images[self._index].title,
        )
        return self._images[self._index]

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def get_image_path(self) -> Optional[Path]:
        img = self.current_image
        img_dir = self.current_image_dir
        if img is None or img_dir is None:
            return None
        resolved = (img_dir / img.image_filename).resolve()
        if not str(resolved).startswith(str(img_dir.resolve()) + os.sep):
            logger.warning("Image path escapes package dir: %s", img.image_filename)
            return None
        return resolved

    def get_audio_path(self, relative_path: str) -> Optional[Path]:
        img_dir = self.current_image_dir
        if img_dir is None:
            return None
        resolved = (img_dir / relative_path).resolve()
        if not str(resolved).startswith(str(img_dir.resolve()) + os.sep):
            logger.warning("Audio path escapes package dir: %s", relative_path)
            return None
        return resolved
