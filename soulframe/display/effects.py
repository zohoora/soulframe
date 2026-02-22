"""EffectManager — manages all visual effect states for the display engine."""

import logging
import math
from typing import Dict

logger = logging.getLogger(__name__)


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b by factor t (clamped 0-1)."""
    t = max(0.0, min(1.0, t))
    return a + (b - a) * t


def _lerp_vec2(a: tuple, b: tuple, t: float) -> tuple:
    """Linear interpolation for 2D vectors."""
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


class _EffectState:
    """Internal state for a single effect with smooth parameter transitions."""

    _DEFAULT_TRANSITION_SPEED = 2.0  # units per second for lerping

    def __init__(self, defaults: dict):
        self.current = dict(defaults)
        self.target = dict(defaults)
        self.transition_speed = self._DEFAULT_TRANSITION_SPEED

    def set_params(self, params: dict):
        """Set target parameters. They will be lerped toward over time.

        If ``fade_in_ms`` is present it overrides the transition speed so
        that a full 0→1 transition takes approximately that many ms.
        """
        fade_in_ms = params.get("fade_in_ms")
        if fade_in_ms is not None:
            try:
                fade_s = float(fade_in_ms) / 1000.0
                if fade_s > 0:
                    # transition_speed is used as the exponent in
                    # 1 - e^(-speed * dt).  A speed of ~3/fade_s reaches
                    # ~95% within the requested duration.
                    self.transition_speed = 3.0 / fade_s
                else:
                    self.transition_speed = self._DEFAULT_TRANSITION_SPEED
            except (TypeError, ValueError):
                pass

        for key, value in params.items():
            if key == "fade_in_ms":
                continue  # consumed above, not a shader parameter
            if key in self.target:
                if key == "intensity":
                    value = max(0.0, min(1.0, float(value)))
                self.target[key] = value
            else:
                logger.warning("Unknown effect parameter: %s", key)

    def set_intensity(self, intensity: float):
        """Set the target intensity directly."""
        self.target["intensity"] = max(0.0, min(1.0, intensity))

    def update(self, dt: float):
        """Advance all parameters toward their targets."""
        # Exponential smoothing: factor = 1 - e^(-speed * dt)
        # This is framerate-independent — same visual result at any fps.
        factor = 1.0 - math.exp(-self.transition_speed * dt)
        factor = max(0.0, min(1.0, factor))
        for key in self.current:
            cur = self.current[key]
            tgt = self.target[key]
            if isinstance(cur, tuple) and isinstance(tgt, tuple):
                self.current[key] = _lerp_vec2(cur, tgt, factor)
            elif isinstance(cur, (int, float)) and isinstance(tgt, (int, float)):
                self.current[key] = _lerp(float(cur), float(tgt), factor)
            else:
                self.current[key] = tgt


# Default parameters for each effect type
_EFFECT_DEFAULTS = {
    "breathing": {
        "intensity": 0.0,
        "amplitude": 0.008,
        "frequency": 0.25,
        "center": (0.5, 0.5),
        "radius": 0.3,
    },
    "parallax": {
        "intensity": 0.0,
        "depth_scale": 0.01,
    },
    "kenburns": {
        "intensity": 0.0,
        "zoom_speed": 0.001,
        "pan_dir": (0.1, 0.05),
    },
    "vignette": {
        "intensity": 0.0,
        "softness": 0.45,
        "radius": 0.75,
    },
}


class EffectManager:
    """Manages all visual effect states and produces shader uniform values."""

    def __init__(self):
        self._effects: Dict[str, _EffectState] = {}
        for name, defaults in _EFFECT_DEFAULTS.items():
            self._effects[name] = _EffectState(defaults)
        logger.info("EffectManager initialized with effects: %s", list(self._effects.keys()))

    def update(self, dt: float):
        """Advance all effect animations and transitions by dt seconds."""
        for effect in self._effects.values():
            effect.update(dt)

    def set_effect(self, effect_type: str, params: dict):
        """Start or configure an effect with the given parameters.

        Args:
            effect_type: One of "breathing", "parallax", "kenburns", "vignette".
            params: Dict of parameter names to values. Unknown params are warned.
        """
        if effect_type not in self._effects:
            logger.error("Unknown effect type: %s", effect_type)
            return
        self._effects[effect_type].set_params(params)
        logger.debug("Effect '%s' configured with params: %s", effect_type, params)

    def set_intensity(self, effect_type: str, intensity: float):
        """Set the intensity of a specific effect (0-1).

        Args:
            effect_type: One of "breathing", "parallax", "kenburns", "vignette".
            intensity: Target intensity value, clamped to 0-1.
        """
        if effect_type not in self._effects:
            logger.error("Unknown effect type: %s", effect_type)
            return
        self._effects[effect_type].set_intensity(intensity)
        logger.debug("Effect '%s' intensity -> %.3f", effect_type, intensity)

    def get_uniforms(self) -> dict:
        """Return a dict of all shader uniform values for the composite shader.

        Returns:
            Dict mapping uniform names to their current values.
        """
        b = self._effects["breathing"].current
        p = self._effects["parallax"].current
        k = self._effects["kenburns"].current
        v = self._effects["vignette"].current

        return {
            # Breathing
            "u_breath_amplitude": b["amplitude"],
            "u_breath_frequency": b["frequency"],
            "u_breath_intensity": b["intensity"],
            "u_breath_center": b["center"],
            "u_breath_radius": b["radius"],
            # Parallax
            "u_parallax_intensity": p["intensity"],
            "u_parallax_depth_scale": p["depth_scale"],
            # Ken Burns
            "u_kb_intensity": k["intensity"],
            "u_kb_zoom_speed": k["zoom_speed"],
            "u_kb_pan_dir": k["pan_dir"],
            # Vignette
            "u_vignette_intensity": v["intensity"],
            "u_vignette_softness": v["softness"],
            "u_vignette_radius": v["radius"],
        }
