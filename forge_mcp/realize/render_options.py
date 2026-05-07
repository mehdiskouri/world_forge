"""User-facing render options for ``forge.generate_region`` / ``forge.render_view``.

Phase 6-d (AGENT/dev_phases/phase6_d_render_options.md). The MCP tools
accept an optional ``render_options`` dict that is validated here into
a frozen :class:`RenderOptions` model; :func:`resolve_render_settings`
then folds the user knobs over the per-tier defaults to produce a
fully-resolved :class:`ResolvedRender` ready to drive the realizer's
``render_preview`` macro.

The host probes the running Blender once via the adapter's ``ping``
response (see :func:`scripts.blender.adapter._probe_render_devices`)
and caches the result on the realizer engine; this module is told the
result via :func:`resolve_render_settings` and never imports ``bpy``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


# --- Constants ---------------------------------------------------------------


ENGINE_EEVEE: str = "BLENDER_EEVEE"
"""Blender 5.0 enum identifier for the rasterised EEVEE Next engine."""

ENGINE_CYCLES: str = "CYCLES"
"""Blender 5.0 enum identifier for the path-traced Cycles engine."""

DEVICE_AUTO: str = "AUTO"
"""Sentinel: pick the best available device from the host probe."""

DEVICE_CPU: str = "CPU"
"""Force the host CPU regardless of available accelerators."""

GPU_DEVICE_TYPES: tuple[str, ...] = ("OPTIX", "CUDA", "HIP", "METAL")
"""Cycles compute backends that count as a GPU for AUTO selection."""

ALL_DEVICE_TYPES: tuple[str, ...] = (DEVICE_CPU, *GPU_DEVICE_TYPES)
"""Every device-type string accepted on the wire (excluding ``AUTO``)."""

MAX_DIMENSION: int = 4096
"""Per-axis pixel ceiling for ``width`` / ``height`` overrides."""

MAX_PIXEL_BUDGET: int = MAX_DIMENSION * MAX_DIMENSION
"""Total pixel ceiling (``width * height``) — 16 megapixels."""

MAX_PNG_BYTES: int = 32 * 1024 * 1024
"""Upper bound on the user-supplied ``png_max_bytes`` override."""

MAX_CYCLES_SAMPLES: int = 4096
"""Upper bound on Cycles ``samples`` override."""

DEFAULT_CYCLES_SAMPLES: int = 64
"""Cycles default sample count when no user override is supplied."""


# --- Error code constants (surfaced through the MCP envelope) ---------------


ERROR_INVALID_RENDER_OPTIONS: str = "invalid_render_options"
"""Validation failure on the user-supplied ``render_options`` payload."""

ERROR_DEVICE_UNAVAILABLE: str = "device_unavailable"
"""User asked for a non-AUTO device that the host probe did not find."""


class InvalidRenderOptionsError(ValueError):
    """Raised when ``render_options`` fails Pydantic validation or post-checks."""


class DeviceUnavailableError(RuntimeError):
    """Raised when a non-``AUTO`` device is requested but the host has none."""

    def __init__(self, device: str, available: Sequence[str]) -> None:
        """Capture the requested device plus the host's actual menu."""
        self.device = device
        self.available = tuple(available)
        super().__init__(
            f"device {device!r} is not available on this host"
            f" (available: {sorted(self.available)})",
        )


# --- Public model -----------------------------------------------------------


EngineLiteral = Literal["EEVEE", "CYCLES"]
DeviceLiteral = Literal["AUTO", "CPU", "OPTIX", "CUDA", "HIP", "METAL"]


class RenderOptions(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """User-facing render-knob bundle for ``forge.generate_region``.

    Every field is optional; an empty payload (``{}``) reproduces the
    pre-Phase-6-d behaviour byte-for-byte. Validation is total —
    invalid combinations (EEVEE + GPU device, EEVEE + ``cycles_samples``,
    width without height, oversize pixel budget) are rejected at parse
    time so the MCP tools never reach the realizer with a bad payload.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    engine: EngineLiteral | None = None
    device: DeviceLiteral = "AUTO"
    width: int | None = Field(default=None, gt=0, le=MAX_DIMENSION)
    height: int | None = Field(default=None, gt=0, le=MAX_DIMENSION)
    png_max_bytes: int | None = Field(default=None, gt=0, le=MAX_PNG_BYTES)
    cycles_samples: int | None = Field(default=None, ge=1, le=MAX_CYCLES_SAMPLES)

    @model_validator(mode="after")
    def _check_combinations(self) -> RenderOptions:
        """Reject impossible engine/device/sample combinations."""
        if (self.width is None) != (self.height is None):
            msg = "width and height must be supplied together"
            raise ValueError(msg)
        if self.width is not None and self.height is not None:
            pixels = self.width * self.height
            if pixels > MAX_PIXEL_BUDGET:
                msg = (
                    f"width*height={pixels} exceeds the {MAX_PIXEL_BUDGET}-pixel"
                    f" cap (max {MAX_DIMENSION}x{MAX_DIMENSION})"
                )
                raise ValueError(msg)
        if self.engine == "EEVEE":
            if self.device not in {"AUTO", "CPU"}:
                msg = (
                    "EEVEE has no compute-device selector; pass device='AUTO'"
                    " (or omit it) when engine='EEVEE'"
                )
                raise ValueError(msg)
            if self.cycles_samples is not None:
                msg = "cycles_samples is only meaningful when engine='CYCLES'"
                raise ValueError(msg)
        return self


# --- Resolved settings ------------------------------------------------------


class ResolvedRender(BaseModel):  # type: ignore[explicit-any]  # pydantic stubs leak Any
    """Final per-render settings after folding user options over tier defaults.

    Fed straight into :class:`forge_mcp.realize.macros.RenderPreviewInputs`;
    every field is wire-ready (``engine`` is the Blender 5.0 enum,
    ``device_type`` is one of :data:`ALL_DEVICE_TYPES`).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    engine: str
    device_type: str
    width: int = Field(gt=0, le=MAX_DIMENSION)
    height: int = Field(gt=0, le=MAX_DIMENSION)
    png_max_bytes: int = Field(gt=0, le=MAX_PNG_BYTES)
    cycles_samples: int = Field(ge=1, le=MAX_CYCLES_SAMPLES)
    notes: tuple[str, ...] = ()


# --- Resolution helper ------------------------------------------------------


def _select_auto_device(available_types: Iterable[str]) -> str:
    """Pick the highest-priority GPU backend present on the host, else CPU."""
    present = set(available_types)
    for candidate in GPU_DEVICE_TYPES:
        if candidate in present:
            return candidate
    return DEVICE_CPU


def resolve_render_settings(  # noqa: PLR0913 - one resolve site, all named keyword args
    *,
    tier_width: int,
    tier_height: int,
    tier_png_max_bytes: int,
    options: RenderOptions,
    available_device_types: Sequence[str],
    legacy_default: bool = False,
) -> ResolvedRender:
    """Fold ``options`` over the per-tier defaults to produce render settings.

    Decision rules (locked in the Phase 6-d plan):

    * If ``legacy_default`` is True and ``options.engine`` is unset:
      preserve the pre-Phase-6-d behaviour and resolve to EEVEE / CPU.
      This is the path taken when the MCP caller omits
      ``render_options`` entirely so byte-for-byte artifact stability
      is maintained.
    * Otherwise, if ``options.engine`` is unset: pick CYCLES when the
      host probe reports any non-CPU device, else EEVEE.
    * If the user forces CYCLES on a CPU-only host the function
      silently falls back to ``device_type='CPU'`` and records a note
      in the returned :class:`ResolvedRender` (no error — the agent
      should still get a render).
    * A non-``AUTO`` device that the host does not advertise raises
      :class:`DeviceUnavailableError`.
    * EEVEE always resolves to ``device_type='CPU'`` (the field is
      ignored at the adapter for EEVEE; the canonical value keeps the
      trace consistent).

    Args:
        tier_width: Default render width for the chosen resolution tier.
        tier_height: Default render height for the chosen resolution tier.
        tier_png_max_bytes: Per-tier PNG byte ceiling.
        options: Validated user options (use :class:`RenderOptions` ``()``
            for the no-override case).
        available_device_types: ``device['type']`` values from the
            adapter ``ping`` probe — typically a subset of
            :data:`ALL_DEVICE_TYPES`. ``CPU`` is implicit and always
            considered available.
        legacy_default: ``True`` when the caller passed no
            ``render_options`` at all; preserves the pre-Phase-6-d
            EEVEE/CPU defaults.

    Returns:
        A :class:`ResolvedRender` with every field populated.

    Raises:
        DeviceUnavailableError: ``options.device`` is non-``AUTO`` and
            absent from ``available_device_types`` (and not ``CPU``).
    """
    notes: list[str] = []
    device_set = {*available_device_types, DEVICE_CPU}

    if options.engine == "EEVEE":
        engine = ENGINE_EEVEE
        device_type = DEVICE_CPU
    elif options.engine == "CYCLES":
        engine = ENGINE_CYCLES
        device_type = _resolve_cycles_device(options.device, device_set, notes)
    elif legacy_default:
        # No user-supplied render_options: preserve the pre-Phase-6-d
        # EEVEE/CPU defaults so generate_region's artifacts stay
        # byte-identical to the previous release.
        engine = ENGINE_EEVEE
        device_type = DEVICE_CPU
    else:
        # engine unset → pick by available hardware
        auto = _select_auto_device(available_device_types)
        if auto == DEVICE_CPU:
            engine = ENGINE_EEVEE
            device_type = DEVICE_CPU
        else:
            engine = ENGINE_CYCLES
            device_type = _resolve_cycles_device(options.device, device_set, notes)

    width = options.width if options.width is not None else tier_width
    height = options.height if options.height is not None else tier_height
    png_max_bytes = (
        options.png_max_bytes if options.png_max_bytes is not None else tier_png_max_bytes
    )
    cycles_samples = (
        options.cycles_samples if options.cycles_samples is not None else DEFAULT_CYCLES_SAMPLES
    )

    return ResolvedRender(
        engine=engine,
        device_type=device_type,
        width=width,
        height=height,
        png_max_bytes=png_max_bytes,
        cycles_samples=cycles_samples,
        notes=tuple(notes),
    )


def _resolve_cycles_device(
    requested: str,
    device_set: set[str],
    notes: list[str],
) -> str:
    """Resolve a Cycles device request against the host probe."""
    if requested == DEVICE_AUTO:
        # AUTO + CYCLES → best GPU available, else CPU (with a note).
        gpu = _select_auto_device(device_set)
        if gpu == DEVICE_CPU:
            notes.append("cycles_cpu_fallback")
        return gpu
    if requested == DEVICE_CPU:
        return DEVICE_CPU
    if requested not in device_set:
        raise DeviceUnavailableError(requested, sorted(device_set))
    return requested
