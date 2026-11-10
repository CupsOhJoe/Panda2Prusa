"""3MF affine transforms.

A 3MF ``transform`` attribute is 12 numbers describing a 4x3 matrix (row-major).
A homogeneous point ``[x y z 1]`` (row vector) is mapped by::

    [x' y' z' 1] = [x y z 1] * M

where M is the 4x4 matrix::

    | m0  m1  m2  0 |
    | m3  m4  m5  0 |
    | m6  m7  m8  0 |
    | m9  m10 m11 1 |

The 12 stored numbers are ``m0 m1 m2 m3 m4 m5 m6 m7 m8 m9 m10 m11`` — the first three
rows carry rotation/scale/shear, the last row (m9 m10 m11) is the translation.

Because a *component* transform maps child coordinates into the parent's space, the
world transform of a nested mesh is the product of the transforms encountered walking
down the tree, innermost-first: ``child * parent`` in this row-vector convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

IDENTITY_12 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Transform:
    """An affine transform stored as the 12-number 3MF form."""

    m: tuple[float, ...] = IDENTITY_12

    def __post_init__(self) -> None:
        if len(self.m) != 12:
            raise ValueError(f"transform needs 12 numbers, got {len(self.m)}")

    # -- parsing / formatting -------------------------------------------------
    @classmethod
    def identity(cls) -> "Transform":
        return cls(IDENTITY_12)

    @classmethod
    def parse(cls, text: str | None) -> "Transform":
        if text is None or not text.strip():
            return cls.identity()
        parts = text.replace(",", " ").split()
        if len(parts) != 12:
            raise ValueError(f"transform needs 12 numbers, got {len(parts)}: {text!r}")
        return cls(tuple(float(p) for p in parts))

    def to_string(self, precision: int = 9) -> str:
        return " ".join(_fmt_num(v, precision) for v in self.m)

    def to_matrix16(self, precision: int = 9) -> str:
        """PrusaSlicer's 4x4 row-major matrix string (column-vector convention, translation
        in the last column), as used for the ``matrix`` volume metadata. It is the
        transpose of the 3MF layout."""
        m = self.m
        rows = (
            (m[0], m[3], m[6], m[9]),
            (m[1], m[4], m[7], m[10]),
            (m[2], m[5], m[8], m[11]),
            (0.0, 0.0, 0.0, 1.0),
        )
        return " ".join(_fmt_num(v, precision) for row in rows for v in row)

    # -- math -----------------------------------------------------------------
    def _rows4(self) -> tuple[tuple[float, float, float, float], ...]:
        m = self.m
        return (
            (m[0], m[1], m[2], 0.0),
            (m[3], m[4], m[5], 0.0),
            (m[6], m[7], m[8], 0.0),
            (m[9], m[10], m[11], 1.0),
        )

    def compose(self, parent: "Transform") -> "Transform":
        """Return ``self`` followed by ``parent`` (child-to-world = child * parent)."""
        a = self._rows4()
        b = parent._rows4()
        # result = a * b  (4x4), then drop the implied last column
        res = []
        for i in range(4):
            for j in range(3):  # only first 3 columns are stored
                res.append(
                    a[i][0] * b[0][j]
                    + a[i][1] * b[1][j]
                    + a[i][2] * b[2][j]
                    + a[i][3] * b[3][j]
                )
        return Transform(tuple(res))

    def is_identity(self, tol: float = 1e-12) -> bool:
        return all(abs(x - y) <= tol for x, y in zip(self.m, IDENTITY_12))

    def apply_point(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """Map a point through the transform (row-vector convention)."""
        m = self.m
        return (
            x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11],
        )

    def det3(self) -> float:
        """Determinant of the linear 3x3 part; negative means a reflection."""
        m = self.m
        return (
            m[0] * (m[4] * m[8] - m[5] * m[7])
            - m[1] * (m[3] * m[8] - m[5] * m[6])
            + m[2] * (m[3] * m[7] - m[4] * m[6])
        )


def _fmt_num(v: float, precision: int) -> str:
    # Match slicer style: plain decimals, trailing-zero trimmed, no sci-notation
    # for readable values but keep full precision for tiny rotation terms.
    if v == 0.0:
        return "0"
    if abs(v) < 1e-4 or abs(v) >= 1e6:
        return repr(v)
    s = f"{v:.{precision}f}".rstrip("0").rstrip(".")
    return s if s else "0"


def compose_chain(transforms: Iterable[Transform]) -> Transform:
    """Compose transforms listed innermost-first (child, ..., root)."""
    result = Transform.identity()
    for t in transforms:
        result = result.compose(t)
    return result
