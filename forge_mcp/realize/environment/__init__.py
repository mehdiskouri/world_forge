"""Phase 6-f Stage C: resolver + plan IR for environments.

Mirrors :mod:`forge_mcp.realize.material`: a pure-function resolver
turns project state plus a scope id into a deterministic
:class:`ResolvedEnvironment` plan, with sun position computed from
:mod:`forge_mcp.environment.sun`.
"""

from forge_mcp.realize.environment.defaults import (
    EnvironmentParameterError,
    default_environment_node,
    validate_environment_parameters,
)
from forge_mcp.realize.environment.plan import (
    ResolvedEnvironment,
    compute_environment_plan_id,
)
from forge_mcp.realize.environment.resolver import (
    EnvironmentResolverError,
    resolve_environment,
    resolve_for_node,
)

__all__ = [
    "EnvironmentParameterError",
    "EnvironmentResolverError",
    "ResolvedEnvironment",
    "compute_environment_plan_id",
    "default_environment_node",
    "resolve_environment",
    "resolve_for_node",
    "validate_environment_parameters",
]
