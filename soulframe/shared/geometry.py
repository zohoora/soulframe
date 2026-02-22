"""Geometry helpers — point-in-polygon hit testing for gaze regions."""

from typing import List, Tuple

Point = Tuple[float, float]


def point_in_polygon(px: float, py: float, polygon: List[Point]) -> bool:
    """Ray-casting algorithm for point-in-polygon test.

    All coordinates are normalized 0.0–1.0.
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def region_hit_test(
    gaze_x: float, gaze_y: float, points_normalized: List[Point]
) -> bool:
    """Test if a gaze point falls within a region polygon."""
    return point_in_polygon(gaze_x, gaze_y, points_normalized)
