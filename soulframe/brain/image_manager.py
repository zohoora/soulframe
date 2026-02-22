"""Image manager for Soul Frame.

Scans the gallery directory for image packages (subdirectories containing a
``metadata.json``), parses them into ``ImageMetadata`` dataclasses, and
provides sequential cycling through the collection.
"""

import json
import logging
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
            shape_raw = r.get("shape", {})
            shape = RegionShape(
                shape_type=shape_raw.get("type", "polygon"),
                points_normalized=[
                    tuple(pt) for pt in shape_raw.get("points_normalized", [])
                ],
            )

            gaze_trigger = GazeTrigger(
                dwell_time_ms=r.get("gaze_trigger", {}).get(
                    "dwell_time_ms", config.GAZE_DWELL_MS
                ),
                min_confidence=r.get("gaze_trigger", {}).get(
                    "min_confidence", config.GAZE_MIN_CONFIDENCE
                ),
            )

            heartbeat: Optional[HeartbeatConfig] = None
            if "heartbeat" in r:
                hb = r["heartbeat"]
                dist = hb.get("intensity_by_distance", {})
                heartbeat = HeartbeatConfig(
                    file=hb.get("file", ""),
                    loop=hb.get("loop", True),
                    bass_boost=hb.get("bass_boost", True),
                    fade_in_ms=hb.get("fade_in_ms", 2000),
                    max_distance_cm=dist.get("max_distance_cm", 150.0),
                    min_distance_cm=dist.get("min_distance_cm", 30.0),
                    curve=dist.get("curve", "exponential"),
                )

            visual_effects: List[VisualEffect] = []
            for ve in r.get("visual_effects", []):
                visual_effects.append(
                    VisualEffect(
                        effect_type=ve.get("type", "breathing"),
                        params=ve.get("params", {}),
                        trigger=ve.get("trigger", "on_gaze_dwell"),
                        fade_in_ms=ve.get("fade_in_ms", 3000),
                    )
                )

            region = Region(
                id=r.get("id", ""),
                label=r.get("label", ""),
                shape=shape,
                gaze_trigger=gaze_trigger,
                heartbeat=heartbeat,
                visual_effects=visual_effects,
            )
            regions.append(region)

        # --- Parse ambient audio ------------------------------------------
        ambient: Optional[AmbientAudioConfig] = None
        audio_raw = raw.get("audio", {})
        if "ambient" in audio_raw:
            aa = audio_raw["ambient"]
            ambient = AmbientAudioConfig(
                file=aa.get("file", ""),
                loop=aa.get("loop", True),
                fade_in_distance_cm=aa.get("fade_in_distance_cm", 200.0),
                fade_in_complete_cm=aa.get("fade_in_complete_cm", 100.0),
                fade_curve=aa.get("fade_curve", "ease_in_out"),
            )

        # --- Parse image info ---------------------------------------------
        image_raw = raw.get("image", {})
        interaction_raw = raw.get("interaction", {})
        transitions_raw = raw.get("transitions", {})

        metadata = ImageMetadata(
            version=raw.get("version", 1),
            id=raw.get("id", json_path.parent.name),
            title=raw.get("title", ""),
            image_filename=image_raw.get("filename", "image.jpg"),
            image_width=image_raw.get("width", 1920),
            image_height=image_raw.get("height", 1080),
            ambient=ambient,
            regions=regions,
            min_interaction_distance_cm=interaction_raw.get(
                "min_interaction_distance_cm", config.PRESENCE_DISTANCE_CM
            ),
            close_interaction_distance_cm=interaction_raw.get(
                "close_interaction_distance_cm", config.CLOSE_INTERACTION_DISTANCE_CM
            ),
            fade_in_ms=transitions_raw.get("fade_in_ms", config.DEFAULT_FADE_IN_MS),
            fade_out_ms=transitions_raw.get("fade_out_ms", config.DEFAULT_FADE_OUT_MS),
            audio_crossfade_ms=transitions_raw.get(
                "audio_crossfade_ms", config.DEFAULT_AUDIO_CROSSFADE_MS
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
        return img_dir / img.image_filename

    def get_audio_path(self, relative_path: str) -> Optional[Path]:
        img_dir = self.current_image_dir
        if img_dir is None:
            return None
        return img_dir / relative_path
