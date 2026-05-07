"""Unit tests for :mod:`forge_mcp.realize.render_options`.

Covers Phase 6-d Phase A2 / A3: model validation, the option-over-tier
resolver, and the auto/legacy device-pick rules.
"""

from __future__ import annotations

import pytest
from forge_mcp.realize.render_options import (
    DEFAULT_CYCLES_SAMPLES,
    ENGINE_CYCLES,
    ENGINE_EEVEE,
    MAX_DIMENSION,
    DeviceUnavailableError,
    RenderOptions,
    resolve_render_settings,
)
from pydantic import ValidationError


class TestRenderOptionsValidation:
    """Pydantic validation rules on :class:`RenderOptions`."""

    def test_empty_payload_is_legal(self) -> None:
        options = RenderOptions()
        assert options.engine is None
        assert options.device == "AUTO"
        assert options.width is None
        assert options.height is None
        assert options.png_max_bytes is None
        assert options.cycles_samples is None

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"unknown_knob": True})

    def test_width_height_must_be_paired(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"width": 1024})
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"height": 768})
        # Pair OK
        RenderOptions.model_validate({"width": 1024, "height": 768})

    def test_max_pixel_budget_enforced(self) -> None:
        # 4096 x 4096 = 16MP exactly → OK
        RenderOptions.model_validate({"width": MAX_DIMENSION, "height": MAX_DIMENSION})
        # 4097 → per-axis cap triggers first
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"width": 4097, "height": 1024})

    def test_eevee_with_gpu_device_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"engine": "EEVEE", "device": "OPTIX"})

    def test_eevee_with_cpu_or_auto_device_ok(self) -> None:
        RenderOptions.model_validate({"engine": "EEVEE", "device": "AUTO"})
        RenderOptions.model_validate({"engine": "EEVEE", "device": "CPU"})

    def test_eevee_with_cycles_samples_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"engine": "EEVEE", "cycles_samples": 32})

    def test_cycles_samples_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"cycles_samples": 0})
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"cycles_samples": 4097})
        RenderOptions.model_validate({"cycles_samples": 32})

    def test_png_max_bytes_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"png_max_bytes": 0})
        # 32 MiB cap
        RenderOptions.model_validate({"png_max_bytes": 32 * 1024 * 1024})
        with pytest.raises(ValidationError):
            RenderOptions.model_validate({"png_max_bytes": 32 * 1024 * 1024 + 1})


class TestResolveRenderSettings:
    """Behaviour of :func:`resolve_render_settings`."""

    def test_legacy_default_picks_eevee_cpu(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions(),
            available_device_types=("OPTIX",),
            legacy_default=True,
        )
        assert resolved.engine == ENGINE_EEVEE
        assert resolved.device_type == "CPU"
        assert resolved.width == 1024  # noqa: PLR2004 - tier default
        assert resolved.height == 768  # noqa: PLR2004 - tier default
        assert resolved.cycles_samples == DEFAULT_CYCLES_SAMPLES
        assert resolved.notes == ()

    def test_auto_picks_cycles_when_gpu_available(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions(),
            available_device_types=("OPTIX", "CUDA"),
        )
        assert resolved.engine == ENGINE_CYCLES
        assert resolved.device_type == "OPTIX"

    def test_auto_falls_back_to_eevee_when_no_gpu(self) -> None:
        resolved = resolve_render_settings(
            tier_width=512,
            tier_height=384,
            tier_png_max_bytes=350_000,
            options=RenderOptions(),
            available_device_types=("CPU",),
        )
        assert resolved.engine == ENGINE_EEVEE
        assert resolved.device_type == "CPU"

    def test_explicit_cycles_with_no_gpu_falls_back_with_note(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions.model_validate({"engine": "CYCLES"}),
            available_device_types=("CPU",),
        )
        assert resolved.engine == ENGINE_CYCLES
        assert resolved.device_type == "CPU"
        assert "cycles_cpu_fallback" in resolved.notes

    def test_explicit_unavailable_device_raises(self) -> None:
        with pytest.raises(DeviceUnavailableError) as excinfo:
            resolve_render_settings(
                tier_width=1024,
                tier_height=768,
                tier_png_max_bytes=1_500_000,
                options=RenderOptions.model_validate(
                    {"engine": "CYCLES", "device": "OPTIX"},
                ),
                available_device_types=("CPU",),
            )
        assert excinfo.value.device == "OPTIX"

    def test_explicit_cpu_always_resolves(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions.model_validate(
                {"engine": "CYCLES", "device": "CPU"},
            ),
            available_device_types=("OPTIX",),
        )
        assert resolved.engine == ENGINE_CYCLES
        assert resolved.device_type == "CPU"

    def test_width_height_override_replaces_tier(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions.model_validate({"width": 1920, "height": 1080}),
            available_device_types=(),
        )
        assert (resolved.width, resolved.height) == (1920, 1080)

    def test_png_max_bytes_override_replaces_tier(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions.model_validate({"png_max_bytes": 5_000_000}),
            available_device_types=(),
        )
        assert resolved.png_max_bytes == 5_000_000  # noqa: PLR2004 - explicit override under cap

    def test_cycles_samples_override(self) -> None:
        resolved = resolve_render_settings(
            tier_width=1024,
            tier_height=768,
            tier_png_max_bytes=1_500_000,
            options=RenderOptions.model_validate(
                {"engine": "CYCLES", "cycles_samples": 256},
            ),
            available_device_types=("OPTIX",),
        )
        assert resolved.cycles_samples == 256  # noqa: PLR2004 - explicit override
