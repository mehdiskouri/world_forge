"""Spike 4 eval set: 10 free-text prompts paired with expected descriptors.

These are *manually extracted* ground-truth pairs covering the v1 design
space. The Phase 5 ``forge.plan`` skill is evaluated against this set;
Phase 1 only asserts that every expected payload validates and round-trips.

Coverage matrix (terrain primary x hydrology presence):

| terrain primary    | has_stream | stream_character    |
|--------------------|------------|---------------------|
| alpine_valley      | true       | alpine_creek        |
| rolling_hills      | false      | none                |
| desert_mesa        | false      | none                |
| boreal_lowland     | true       | meandering_river    |
| volcanic_cone      | false      | none                |
| coastal_cliffs     | false      | none                |
| canyon             | true       | dry_wash            |
| plains             | false      | none                |
| alpine_peaks       | false      | none                |
| marsh              | true       | meandering_river    |
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvalPair:
    """A free-text prompt and the descriptor an ideal agent should extract."""

    prompt: str
    expected: dict[str, object]


EVAL_PAIRS: tuple[EvalPair, ...] = (
    EvalPair(
        prompt="rugged alpine valley with a fast creek running through it",
        expected={
            "terrain": {
                "primary": "alpine_valley",
                "elevation_band": [1800.0, 2900.0],
                "ruggedness": 0.8,
                "notes": "rugged with prominent valley floor",
            },
            "hydrology": {"has_stream": True, "stream_character": "alpine_creek"},
        },
    ),
    EvalPair(
        prompt="gentle rolling foothills, no water",
        expected={
            "terrain": {
                "primary": "rolling_hills",
                "elevation_band": [200.0, 600.0],
                "ruggedness": 0.25,
            },
            "hydrology": {"has_stream": False, "stream_character": "none"},
        },
    ),
    EvalPair(
        prompt="flat-topped desert mesa rising from the plain",
        expected={
            "terrain": {
                "primary": "desert_mesa",
                "elevation_band": [400.0, 1100.0],
                "ruggedness": 0.55,
            },
            "hydrology": {"has_stream": False},
        },
    ),
    EvalPair(
        prompt="boreal lowland with a slow meandering river",
        expected={
            "terrain": {
                "primary": "boreal_lowland",
                "elevation_band": [80.0, 240.0],
                "ruggedness": 0.2,
            },
            "hydrology": {
                "has_stream": True,
                "stream_character": "meandering_river",
            },
        },
    ),
    EvalPair(
        prompt="volcanic cone, dry, steep flanks",
        expected={
            "terrain": {
                "primary": "volcanic_cone",
                "elevation_band": [500.0, 2400.0],
                "ruggedness": 0.85,
            },
        },
    ),
    EvalPair(
        prompt="dramatic coastal cliffs above the sea",
        expected={
            "terrain": {
                "primary": "coastal_cliffs",
                "elevation_band": [0.0, 350.0],
                "ruggedness": 0.7,
            },
        },
    ),
    EvalPair(
        prompt="deep canyon with an intermittent dry wash",
        expected={
            "terrain": {
                "primary": "canyon",
                "elevation_band": [600.0, 1500.0],
                "ruggedness": 0.9,
            },
            "hydrology": {"has_stream": True, "stream_character": "dry_wash"},
        },
    ),
    EvalPair(
        prompt="open grassy plains",
        expected={
            "terrain": {
                "primary": "plains",
                "elevation_band": [120.0, 200.0],
                "ruggedness": 0.05,
            },
        },
    ),
    EvalPair(
        prompt="snowy alpine peaks",
        expected={
            "terrain": {
                "primary": "alpine_peaks",
                "elevation_band": [2400.0, 3800.0],
                "ruggedness": 0.95,
                "notes": "permanent snowfields",
            },
        },
    ),
    EvalPair(
        prompt="reedy marsh fed by a slow river",
        expected={
            "terrain": {
                "primary": "marsh",
                "elevation_band": [10.0, 40.0],
                "ruggedness": 0.1,
            },
            "hydrology": {
                "has_stream": True,
                "stream_character": "meandering_river",
            },
        },
    ),
)
