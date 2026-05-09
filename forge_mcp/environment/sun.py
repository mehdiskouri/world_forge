"""Low-precision solar position (NREL/NOAA SPA, ~0.1 deg accuracy).

Implements the U.S. Naval Observatory low-precision formula (suitable
between 1950-2050) returning the apparent direction of the sun for a
given UTC instant and observer latitude/longitude.

Sign conventions:
    * latitude:  +north / -south, range [-90, 90] degrees.
    * longitude: +east  / -west,  range [-180, 180] degrees.
    * azimuth:   degrees clockwise from geographic North in [0, 360).
    * elevation: degrees above the horizon in [-90, 90].
    * world vector: right-handed with X=East, Y=North, Z=Up; unit length;
      points *toward* the apparent sun position.

Reference:
    Low-precision formulae for the Sun's coordinates and the equation of
    time, USNO Astronomical Almanac (truncated NREL SPA).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["SunDirection", "compute_sun_direction"]


_LATITUDE_DEG_MAX = 90.0
_LONGITUDE_DEG_MAX = 180.0
_J2000_JD = 2451545.0
_DEG_PER_HOUR = 15.0
_FULL_CIRCLE_DEG = 360.0


@dataclass(frozen=True, slots=True)
class SunDirection:
    """Apparent sun direction in observer-local horizontal coordinates."""

    azimuth_deg: float
    elevation_deg: float
    vector: tuple[float, float, float]


def _julian_day(when_utc: datetime) -> float:
    """Compute the Julian Day number for a tz-aware UTC instant."""
    year = when_utc.year
    month = when_utc.month
    day = when_utc.day
    hour = when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0
    if month <= 2:  # noqa: PLR2004 - January/February shift in JD formula
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    jd_int = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1))
    return jd_int + day + b - 1524.5 + hour / 24.0


def compute_sun_direction(
    latitude_deg: float,
    longitude_deg: float,
    when_utc: datetime,
) -> SunDirection:
    """Return the apparent sun direction at ``(latitude_deg, longitude_deg, when_utc)``.

    Args:
        latitude_deg: Observer latitude in degrees, +north, range [-90, 90].
        longitude_deg: Observer longitude in degrees, +east, range [-180, 180].
        when_utc: A timezone-aware ``datetime`` whose tzinfo represents UTC.

    Returns:
        A :class:`SunDirection` with azimuth (CW from North), elevation, and
        a unit world-vector with X=East, Y=North, Z=Up.

    Raises:
        ValueError: If latitude/longitude are out of range, or if
            ``when_utc`` is naive (missing ``tzinfo``).
    """
    if when_utc.tzinfo is None:
        msg = "when_utc must be timezone-aware (UTC); got naive datetime"
        raise ValueError(msg)
    if not -_LATITUDE_DEG_MAX <= latitude_deg <= _LATITUDE_DEG_MAX:
        msg = f"latitude_deg out of range [-90, 90]: {latitude_deg}"
        raise ValueError(msg)
    if not -_LONGITUDE_DEG_MAX <= longitude_deg <= _LONGITUDE_DEG_MAX:
        msg = f"longitude_deg out of range [-180, 180]: {longitude_deg}"
        raise ValueError(msg)

    # Days since J2000.0 (using the UTC instant; precision is ample for the
    # ~0.1 deg target, which absorbs the UT1-UTC delta).
    jd = _julian_day(when_utc)
    n = jd - _J2000_JD

    # Mean longitude and mean anomaly of the sun (degrees).
    mean_longitude_deg = (280.460 + 0.9856474 * n) % _FULL_CIRCLE_DEG
    mean_anomaly_deg = (357.528 + 0.9856003 * n) % _FULL_CIRCLE_DEG
    g = math.radians(mean_anomaly_deg)

    # Ecliptic longitude (apparent), then obliquity of the ecliptic.
    ecliptic_longitude_deg = (
        mean_longitude_deg + 1.915 * math.sin(g) + 0.020 * math.sin(2.0 * g)
    )
    lam = math.radians(ecliptic_longitude_deg)
    obliquity_deg = 23.439 - 0.0000004 * n
    eps = math.radians(obliquity_deg)

    # Right ascension and declination of the sun.
    right_ascension = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    declination = math.asin(math.sin(eps) * math.sin(lam))

    # Greenwich mean sidereal time in hours, then local sidereal time.
    gmst_hours = (18.697374558 + 24.06570982441908 * n) % 24.0
    lst_hours = (gmst_hours + longitude_deg / _DEG_PER_HOUR) % 24.0
    lst_rad = math.radians(lst_hours * _DEG_PER_HOUR)

    # Hour angle (radians); positive west of the meridian.
    hour_angle = lst_rad - right_ascension

    lat = math.radians(latitude_deg)
    sin_el = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination,
    ) * math.cos(hour_angle)
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = math.asin(sin_el)

    # Azimuth measured clockwise from North in [0, 360).
    azimuth = math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(lat) - math.tan(declination) * math.cos(lat),
    )
    azimuth_deg = (math.degrees(azimuth) + 180.0) % _FULL_CIRCLE_DEG
    elevation_deg = math.degrees(elevation)

    az_rad = math.radians(azimuth_deg)
    cos_el = math.cos(elevation)
    vector = (
        cos_el * math.sin(az_rad),  # X = East
        cos_el * math.cos(az_rad),  # Y = North
        math.sin(elevation),  # Z = Up
    )
    return SunDirection(
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
        vector=vector,
    )
