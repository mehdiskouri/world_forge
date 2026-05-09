"""Tests for :mod:`forge_mcp.environment.sun`."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from forge_mcp.environment.sun import SunDirection, compute_sun_direction

# Tolerance for the truncated NREL/USNO low-precision SPA (~0.1 deg target,
# tested at 0.5 deg to absorb leap-second + UT1-UTC drift over 1950-2050).
_TOL_DEG = 0.5


def test_london_summer_solstice_noon_is_near_south_at_60deg() -> None:
    """London 2026-06-21 12:00 UTC: ~62 deg elevation, ~180 deg azimuth (due south)."""
    result = compute_sun_direction(51.5, -0.1, datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC))
    # Expected elevation = 90 - (lat - obliquity) = 90 - (51.5 - 23.44) = 61.94 deg
    assert math.isclose(result.elevation_deg, 61.94, abs_tol=_TOL_DEG)
    # Solar noon at -0.1 longitude is ~24 s after 12:00 UTC, sun is essentially due south.
    assert math.isclose(result.azimuth_deg, 180.0, abs_tol=2.0)


def test_quito_equinox_solar_noon_is_overhead() -> None:
    """Quito (0 deg N, 78.5 deg W) at March equinox solar noon: sun nearly overhead."""
    # Solar noon at -78.5 deg longitude is at 12:00 + 78.5/15 h = 17:14 UTC.
    result = compute_sun_direction(
        0.0,
        -78.5,
        datetime(2026, 3, 20, 17, 14, 0, tzinfo=UTC),
    )
    # On the equinox at the equator, the sub-solar point is the equator,
    # so elevation is within ~2 deg of 90 (declination ~0 +/- a bit).
    assert result.elevation_deg > 87.0  # noqa: PLR2004 - documented bound


def test_london_midnight_is_below_horizon() -> None:
    """At London local midnight in mid-summer the sun is below the horizon."""
    result = compute_sun_direction(51.5, -0.1, datetime(2026, 6, 21, 0, 0, 0, tzinfo=UTC))
    assert result.elevation_deg < 0.0


def test_returned_vector_is_unit_length() -> None:
    """The world-vector field is always a unit vector."""
    result = compute_sun_direction(45.0, 10.0, datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC))
    length = math.sqrt(sum(c * c for c in result.vector))
    assert math.isclose(length, 1.0, abs_tol=1e-9)


def test_vector_axes_are_east_north_up() -> None:
    """Azimuth 90 deg should map to vector pointing East (+X), North component ~0."""
    # Construct a synthetic case: sun at azimuth=90 deg, elevation=0.
    # We achieve this by querying near the equinox at the equator at sunrise (HA = -pi/2).
    result = compute_sun_direction(0.0, 0.0, datetime(2026, 3, 20, 6, 0, 0, tzinfo=UTC))
    # X (east) should dominate, Z (up) ~0, Y (north) small.
    assert result.vector[0] > 0.9  # noqa: PLR2004 - documented bound
    assert abs(result.vector[1]) < 0.2  # noqa: PLR2004 - documented bound
    assert abs(result.vector[2]) < 0.2  # noqa: PLR2004 - documented bound


def test_is_deterministic() -> None:
    """Repeated calls with identical inputs return identical outputs."""
    args = (37.7749, -122.4194, datetime(2026, 7, 4, 20, 0, 0, tzinfo=UTC))
    a = compute_sun_direction(*args)
    b = compute_sun_direction(*args)
    assert a == b
    assert isinstance(a, SunDirection)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        compute_sun_direction(0.0, 0.0, datetime(2026, 1, 1, 12, 0, 0))  # noqa: DTZ001 - intentional


def test_latitude_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="latitude"):
        compute_sun_direction(95.0, 0.0, datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))


def test_longitude_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="longitude"):
        compute_sun_direction(0.0, 200.0, datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
