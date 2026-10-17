"""
Operational profiles and cost-function weight sets.
"""

THRESHOLD = 1.5  # Score > THRESHOLD -> downgrade tier or defer

PROFILE_WEIGHTS: dict = {
    "gaming": dict(
        vram=3.0, cpu=3.0, battery=1.0, latency=0.5, privacy=1.0,
    ),
    "productivity": dict(
        vram=1.0, cpu=1.0, battery=0.5, latency=1.0, privacy=1.0,
    ),
    "battery_saver": dict(
        vram=1.0, cpu=1.5, battery=3.0, latency=0.5, privacy=1.0,
    ),
}

_active_profile = "productivity"


def set_profile(name: str) -> None:
    global _active_profile
    if name not in PROFILE_WEIGHTS:
        raise ValueError(f"Unknown profile: '{name}'. Valid: {list(PROFILE_WEIGHTS)}")
    _active_profile = name
    print(f"[Governor] Profile set to: {name}")


def get_profile() -> str:
    return _active_profile


def get_weights(profile: str = None) -> dict:
    p = profile or _active_profile
    return PROFILE_WEIGHTS.get(p, PROFILE_WEIGHTS["productivity"])
