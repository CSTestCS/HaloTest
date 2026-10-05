"""Color helpers: hex parsing and perceptual (CIELAB) nearest-swatch matching.

Each game exposes a fixed palette, so a free-form universal color has to be
snapped to the closest swatch the game actually offers.
"""
from __future__ import annotations

import math


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6 or any(c not in "0123456789abcdefABCDEF" for c in v):
        raise ValueError(f"invalid hex color: {value!r}")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _srgb_to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    # sRGB -> XYZ (D65)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.dist(rgb_to_lab(a), rgb_to_lab(b))


def nearest_swatch(target_hex: str, palette: list[dict]) -> tuple[dict, float]:
    """Return (swatch, deltaE) for the palette entry closest to target_hex."""
    if not palette:
        raise ValueError("palette is empty")
    target = hex_to_rgb(target_hex)
    best = min(palette, key=lambda s: delta_e(target, hex_to_rgb(s["hex"])))
    return best, delta_e(target, hex_to_rgb(best["hex"]))


def hex_to_unit_rgb(value: str) -> list[float]:
    """Tag color fields store real RGB in [0, 1]."""
    return [round(c / 255.0, 4) for c in hex_to_rgb(value)]
