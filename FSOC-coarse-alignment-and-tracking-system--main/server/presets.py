"""Built-in evaluation scenarios.

Each preset is a partial configuration merged over the defaults when loaded.
``applies_to`` states honestly which input modes consume the preset:
the 3D simulation uses every parameter; the video benchmark uses only the
disturbance block (and only when "apply scenario disturbances to video" is on),
because target motion and beacon layout are fixed by the uploaded footage.
"""

from __future__ import annotations

from typing import Any

PRESETS: list[dict[str, Any]] = [
    {
        "id": "baseline_clear",
        "name": "Clear Sky Baseline",
        "description": "Single circular beacon, no disturbances. Reference run for SIH limits.",
        "config": {
            "targets": {"count": 1, "beacons": [{"pattern": "circular", "speed": 1.0}]},
            "disturbances": {"atmosphere": {"condition": "clear", "intensity": 0.0}},
        },
    },
    {
        "id": "fog_moving_jitter",
        "name": "Fog + Moving Beacon + Camera Jitter",
        "description": "Figure-8 beacon in moderate fog with ±8 px camera jitter.",
        "config": {
            "targets": {"count": 2, "beacons": [{"pattern": "figure8", "speed": 1.2}]},
            "disturbances": {"atmosphere": {"condition": "fog", "intensity": 0.45}, "jitter": {"amplitude_px": 8}},
        },
    },
    {
        "id": "haze_platform_circular",
        "name": "Haze + Platform Circular Motion",
        "description": "Straight-line beacon, haze, circular platform sway ±12 px.",
        "config": {
            "targets": {"count": 1, "beacons": [{"pattern": "straight", "speed": 1.0}]},
            "disturbances": {"atmosphere": {"condition": "haze", "intensity": 0.6}, "platform": {"pattern": "circular", "amplitude_px": 12, "period_s": 3.0}},
        },
    },
    {
        "id": "rain_random",
        "name": "Rain + Random Beacon",
        "description": "Random-walk beacon in rain with salt & pepper sensor noise.",
        "config": {
            "targets": {"count": 1, "beacons": [{"pattern": "random", "speed": 1.0}]},
            "disturbances": {"atmosphere": {"condition": "rain", "intensity": 0.55}, "noise": {"salt_pepper": 0.3}},
        },
    },
    {
        "id": "low_light_poisson",
        "name": "Low Light + Poisson Noise",
        "description": "Night-time circular beacon with photon-limited shot noise.",
        "config": {
            "targets": {"count": 1, "beacons": [{"pattern": "circular", "speed": 1.0}]},
            "disturbances": {"atmosphere": {"condition": "low_light", "intensity": 0.65}, "noise": {"poisson": 0.5}},
        },
    },
    {
        "id": "multi_beacon_stress",
        "name": "Multi-Beacon Stress Test",
        "description": "Four beacons with different paths, Gaussian noise, random platform motion and turbulence.",
        "config": {
            "targets": {"count": 4, "decoy_count": 6},
            "disturbances": {
                "noise": {"gaussian": 0.35},
                "platform": {"pattern": "random", "amplitude_px": 10, "period_s": 4.0},
                "turbulence": {"strength": 0.4},
            },
        },
    },
    {
        "id": "sih_max_disturbance",
        "name": "SIH Reference Envelope (±20 px)",
        "description": "Figure-8 beacon at the ±20 px jitter / platform reference limits.",
        "config": {
            "targets": {"count": 1, "beacons": [{"pattern": "figure8", "speed": 1.0}]},
            "disturbances": {"jitter": {"amplitude_px": 20}, "platform": {"pattern": "figure8", "amplitude_px": 20, "period_s": 4.0}, "noise": {"gaussian": 0.2}},
        },
    },
]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    return next((preset for preset in PRESETS if preset["id"] == preset_id), None)
