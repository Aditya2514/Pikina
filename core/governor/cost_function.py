"""
Resource Governor — normalized cost function.
Score > THRESHOLD -> downgrade tier or defer the action.
"""
from .profiles import get_weights, THRESHOLD


def cost(
    vram_mb: float           = 0,
    vram_ceiling_mb: float   = 4096,
    cpu_pct: float           = 0,
    battery_drain_pct_min: float = 0,
    battery_threshold: float = 1.0,
    latency_ms: float        = 0,
    latency_budget_ms: float = 2000,
    privacy_flag: int        = 0,
    profile: str             = None,
) -> float:
    """
    Compute a normalized resource cost score.
    All ratios are clamped to [0, ∞) — no negative contributions.
    """
    weights = get_weights(profile)

    vram_r    = vram_mb / vram_ceiling_mb     if vram_ceiling_mb   else 0
    cpu_r     = cpu_pct / 100
    bat_r     = battery_drain_pct_min / battery_threshold if battery_threshold else 0
    lat_r     = latency_ms / latency_budget_ms if latency_budget_ms else 0

    score = (
        weights["vram"]    * max(0, vram_r)  +
        weights["cpu"]     * max(0, cpu_r)   +
        weights["battery"] * max(0, bat_r)   +
        weights["latency"] * max(0, lat_r)   +
        weights["privacy"] * privacy_flag
    )
    return round(score, 4)


def should_downgrade(score: float) -> bool:
    """Returns True if score exceeds the configured threshold."""
    return score > THRESHOLD
