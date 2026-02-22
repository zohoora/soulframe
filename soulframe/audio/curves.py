"""
Distance-to-volume mapping curves for spatial audio.

Each curve maps a distance (in cm) to a volume level (0.0 to 1.0).
At or beyond max_dist the volume is 0.0; at or within min_dist the
volume is 1.0.  Between the two boundaries the curve determines how
quickly the volume falls off.
"""

from __future__ import annotations

import math
from typing import Callable, Dict


# ---------------------------------------------------------------------------
# Curve implementations
# ---------------------------------------------------------------------------

def linear_curve(distance_cm: float, max_dist: float, min_dist: float) -> float:
    """Straight-line falloff from 1.0 at *min_dist* to 0.0 at *max_dist*."""
    if max_dist <= min_dist:
        return 1.0 if distance_cm <= min_dist else 0.0
    if distance_cm <= min_dist:
        return 1.0
    if distance_cm >= max_dist:
        return 0.0
    t = (distance_cm - min_dist) / (max_dist - min_dist)
    return max(0.0, min(1.0, 1.0 - t))


def ease_in_out_curve(distance_cm: float, max_dist: float, min_dist: float) -> float:
    """Smoothstep (Hermite) falloff — gentle near the extremes."""
    if max_dist <= min_dist:
        return 1.0 if distance_cm <= min_dist else 0.0
    if distance_cm <= min_dist:
        return 1.0
    if distance_cm >= max_dist:
        return 0.0
    t = (distance_cm - min_dist) / (max_dist - min_dist)
    # smoothstep: 3t^2 - 2t^3, but we want volume to *decrease*
    smooth = t * t * (3.0 - 2.0 * t)
    return max(0.0, min(1.0, 1.0 - smooth))


def ease_in_curve(distance_cm: float, max_dist: float, min_dist: float) -> float:
    """Quadratic ease-in — volume drops slowly near min_dist, faster near max_dist."""
    if max_dist <= min_dist:
        return 1.0 if distance_cm <= min_dist else 0.0
    if distance_cm <= min_dist:
        return 1.0
    if distance_cm >= max_dist:
        return 0.0
    t = (distance_cm - min_dist) / (max_dist - min_dist)
    return max(0.0, min(1.0, 1.0 - t * t))


def ease_out_curve(distance_cm: float, max_dist: float, min_dist: float) -> float:
    """Quadratic ease-out — volume drops quickly near min_dist, slowly near max_dist."""
    if max_dist <= min_dist:
        return 1.0 if distance_cm <= min_dist else 0.0
    if distance_cm <= min_dist:
        return 1.0
    if distance_cm >= max_dist:
        return 0.0
    t = (distance_cm - min_dist) / (max_dist - min_dist)
    inv = 1.0 - t
    return max(0.0, min(1.0, inv * inv))


def exponential_curve(distance_cm: float, max_dist: float, min_dist: float) -> float:
    """Exponential falloff — drops quickly then tapers."""
    if max_dist <= min_dist:
        return 1.0 if distance_cm <= min_dist else 0.0
    if distance_cm <= min_dist:
        return 1.0
    if distance_cm >= max_dist:
        return 0.0
    t = (distance_cm - min_dist) / (max_dist - min_dist)
    # Normalized exponential: reaches exactly 0.0 at max_dist
    # Formula: (e^(-5t) - e^(-5)) / (1 - e^(-5))
    raw = math.exp(-5.0 * t)
    floor = math.exp(-5.0)
    vol = (raw - floor) / (1.0 - floor)
    return max(0.0, min(1.0, vol))


# ---------------------------------------------------------------------------
# Curve look-up
# ---------------------------------------------------------------------------

_CURVES: Dict[str, Callable[..., float]] = {
    "linear": linear_curve,
    "ease_in": ease_in_curve,
    "ease_out": ease_out_curve,
    "ease_in_out": ease_in_out_curve,
    "smoothstep": ease_in_out_curve,
    "exponential": exponential_curve,
    "exp": exponential_curve,
}


def get_curve(name: str) -> Callable[[float, float, float], float]:
    """Return a curve function by its short name.

    Recognised names: ``linear``, ``ease_in``, ``ease_out``,
    ``ease_in_out`` / ``smoothstep``, ``exponential`` / ``exp``.

    Raises ``ValueError`` for unknown names.
    """
    try:
        return _CURVES[name]
    except KeyError:
        raise ValueError(
            f"Unknown curve '{name}'. "
            f"Available curves: {', '.join(sorted(_CURVES))}"
        ) from None
