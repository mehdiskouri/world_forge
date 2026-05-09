"""Environment-domain helpers (solar position, sky/fog math).

Phase 6-f Stage B introduces :mod:`forge_mcp.environment.sun`, a tz-aware
solar-position helper that resolver code (Stage C) consumes when turning an
:class:`~forge_mcp.project.schemas.EnvironmentNode` into a realized plan.
"""

from forge_mcp.environment.sun import SunDirection, compute_sun_direction

__all__ = ["SunDirection", "compute_sun_direction"]
