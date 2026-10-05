"""Minimal 3D math: quaternions (i, j, k, w) and row-major 4x4 matrices as nested lists."""
from __future__ import annotations

import math

Mat4 = list[list[float]]


def identity() -> Mat4:
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def quat_conjugate(q):
    i, j, k, w = q
    return (-i, -j, -k, w)


def quat_to_mat3(q):
    x, y, z, w = quat_normalize(q)
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def mat3_to_quat(m):
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    return quat_normalize(q)


def trs(translation, rotation, scale: float = 1.0) -> Mat4:
    r = quat_to_mat3(rotation)
    m = identity()
    for row in range(3):
        for col in range(3):
            m[row][col] = r[row][col] * scale
        m[row][3] = float(translation[row])
    return m


def scale_matrix(s: float) -> Mat4:
    m = identity()
    for i in range(3):
        m[i][i] = s
    return m


def mul(a: Mat4, b: Mat4) -> Mat4:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def invert(m: Mat4) -> Mat4:
    """General 4x4 inverse (Gauss-Jordan)."""
    a = [row[:] + [1.0 if r == c else 0.0 for c in range(4)] for r, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("matrix is singular")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(4):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [v - f * pv for v, pv in zip(a[r], a[col])]
    return [row[4:] for row in a]


def transform_point(m: Mat4, p):
    return tuple(m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] for r in range(3))


def transform_dir(m: Mat4, d):
    return tuple(m[r][0] * d[0] + m[r][1] * d[1] + m[r][2] * d[2] for r in range(3))


def normalize(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def decompose(m: Mat4):
    """Split a rigid(+uniform scale) matrix into (translation, quaternion, scale)."""
    sx = math.sqrt(sum(m[r][0] ** 2 for r in range(3))) or 1.0
    r3 = [[m[r][c] / sx for c in range(3)] for r in range(3)]
    return (m[0][3], m[1][3], m[2][3]), mat3_to_quat(r3), sx


def blend(mats_weights) -> Mat4:
    """Weighted sum of matrices (linear blend skinning)."""
    out = [[0.0] * 4 for _ in range(4)]
    for m, w in mats_weights:
        for r in range(4):
            for c in range(4):
                out[r][c] += m[r][c] * w
    return out
