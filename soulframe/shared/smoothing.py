"""Signal smoothing filters for gaze and distance data."""

import math
from typing import Optional, Tuple


class EMAFilter:
    """Exponential Moving Average filter.

    Higher alpha = less smoothing (more responsive).
    Lower alpha = more smoothing (more stable).
    """

    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._value: Optional[float] = None

    def update(self, measurement: float) -> float:
        if not math.isfinite(measurement):
            return self._value if self._value is not None else 0.0
        if self._value is None:
            self._value = measurement
        else:
            self._value = self._alpha * measurement + (1 - self._alpha) * self._value
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def reset(self) -> None:
        self._value = None


class SimpleKalmanFilter:
    """1D Kalman filter for scalar values (gaze, distance).

    process_noise: how much we expect the true value to change per step.
    measurement_noise: how noisy the sensor readings are.
    """

    def __init__(
        self, process_noise: float = 0.01, measurement_noise: float = 0.1
    ) -> None:
        self._q = process_noise
        self._r = measurement_noise
        self._x: Optional[float] = None  # estimated state
        self._p: float = 1.0             # estimation error covariance

    def update(self, measurement: float) -> float:
        if not math.isfinite(measurement):
            return self._x if self._x is not None else 0.0
        if self._x is None:
            self._x = measurement
            self._p = self._r
            return self._x

        # Predict
        self._p += self._q

        # Update
        denom = self._p + self._r
        if denom == 0.0:
            return self._x
        k = self._p / denom  # Kalman gain
        self._x += k * (measurement - self._x)
        self._p *= (1 - k)

        return self._x

    @property
    def value(self) -> Optional[float]:
        return self._x

    def reset(self) -> None:
        self._x = None
        self._p = 1.0


class GazeSmoother:
    """Smooths 2D gaze coordinates with independent filters per axis."""

    def __init__(self, alpha: float = 0.25) -> None:
        self._x_filter = EMAFilter(alpha)
        self._y_filter = EMAFilter(alpha)

    def update(self, x: float, y: float) -> Tuple[float, float]:
        sx = self._x_filter.update(x)
        sy = self._y_filter.update(y)
        return sx, sy

    def reset(self) -> None:
        self._x_filter.reset()
        self._y_filter.reset()


class DistanceSmoother:
    """Smooths distance readings with a Kalman filter."""

    def __init__(
        self, process_noise: float = 0.5, measurement_noise: float = 5.0
    ) -> None:
        self._filter = SimpleKalmanFilter(process_noise, measurement_noise)

    def update(self, distance_cm: float) -> float:
        return self._filter.update(distance_cm)

    def reset(self) -> None:
        self._filter.reset()
