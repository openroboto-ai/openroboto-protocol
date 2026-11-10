"""Pure, versioned primitive/convex geometry shared by authors and workers.

Geometry, contacts, mass and attribution are content-addressed together. This
catalog defines candidate assets, not a claim of manipulation coverage. Workers
compile these exact geometries; no mesh files or arbitrary physics are accepted.
Verify with tests/test_tabletop_v2.py; this module performs no I/O.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from typing import Any

__all__ = ["asset_catalog", "canonical_digest", "rotate_vector", "support_local"]


def canonical_digest(value: Any) -> str:
    """SHA256 of the exact normative UTF-8 JSON representation."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def rotate_vector(quaternion: list[float], vector: list[float]) -> list[float]:
    """Rotate a local vector by a normalized xyzw quaternion without I/O."""
    x, y, z, w = quaternion
    vx, vy, vz = vector
    cross = [y * vz - z * vy, z * vx - x * vz, x * vy - y * vx]
    return [
        vx + 2 * (w * cross[0] + y * cross[2] - z * cross[1]),
        vy + 2 * (w * cross[1] + z * cross[0] - x * cross[2]),
        vz + 2 * (w * cross[2] + x * cross[1] - y * cross[0]),
    ]


def support_local(geometry: dict[str, Any], direction: list[float]) -> float:
    """Exact support of the registered local collision solid in any direction."""
    kind = geometry["type"]
    if kind == "mesh":
        return float(
            max(
                sum(a * b for a, b in zip(p, direction, strict=True))
                for p in geometry["vertices"]
            )
        )
    size = geometry["size"]
    if kind == "box":
        return float(sum(abs(v) * s for v, s in zip(direction, size, strict=True)))
    norm = math.sqrt(sum(v * v for v in direction))
    if kind == "sphere":
        return float(size[0] * norm)
    if kind == "capsule":
        return float(size[0] * norm + size[1] * abs(direction[2]))
    return float(size[0] * math.hypot(*direction[:2]) + size[1] * abs(direction[2]))


def _mesh(kind: str, diameter: float, height: float) -> dict[str, Any]:
    segments, radius = 64, diameter / 2
    if kind == "prism":
        vertices = [
            [x, y, z]
            for z in [-0.02, 0.02]
            for x, y in [(-0.03, -0.025), (0.03, -0.025), (0, 0.025)]
        ]
        faces = [
            [0, 2, 1],
            [3, 4, 5],
            [0, 1, 4],
            [0, 4, 3],
            [1, 2, 5],
            [1, 5, 4],
            [2, 0, 3],
            [2, 3, 5],
        ]
    else:
        vertices = [
            [
                radius * math.cos(2 * math.pi * i / segments),
                radius * math.sin(2 * math.pi * i / segments),
                -height / 2,
            ]
            for i in range(segments)
        ]
        if kind == "cone":
            vertices.append([0, 0, height / 2])
            faces = [[i, (i + 1) % segments, segments] for i in range(segments)]
        else:
            vertices += [
                [
                    0.015 * math.cos(2 * math.pi * i / segments),
                    0.015 * math.sin(2 * math.pi * i / segments),
                    height / 2,
                ]
                for i in range(segments)
            ]
            faces = [
                [i, (i + 1) % segments, segments + (i + 1) % segments]
                for i in range(segments)
            ]
            faces += [
                [i, segments + (i + 1) % segments, segments + i]
                for i in range(segments)
            ]
            faces += [
                [segments, segments + i, segments + i + 1]
                for i in range(1, segments - 1)
            ]
        faces += [[0, i + 1, i] for i in range(1, segments - 1)]
    # Round the normative vertices once, before hashing or MJCF expansion.
    return {
        "type": "mesh",
        "vertices": [[float(format(v, ".12g")) for v in p] for p in vertices],
        "faces": faces,
        "collision": "convex_hull",
        "curved_surface_segments": segments if kind != "prism" else 0,
    }


def asset_catalog() -> list[dict[str, Any]]:
    """Fresh candidate records with true collision geometry and complete physics.

    Native shape sizes follow MuJoCo half-extent/radius conventions. Capsule's
    second size is its straight-segment half-length, not its total half-height.
    Mesh inertia is the uniform solid's compiled inertia, not a bounding box.
    """
    definitions = [
        ("cube-40mm-v2", "cube", {"type": "box", "size": [0.02, 0.02, 0.02]}, "box"),
        ("cube-60mm-v2", "cube", {"type": "box", "size": [0.03, 0.03, 0.03]}, "box"),
        (
            "cuboid-80x40x40mm-v2",
            "cuboid",
            {"type": "box", "size": [0.04, 0.02, 0.02]},
            "box",
        ),
        (
            "cylinder-40x60mm-v2",
            "cylinder",
            {"type": "cylinder", "size": [0.02, 0.03]},
            "axial_unsigned",
        ),
        ("sphere-40mm-v2", "sphere", {"type": "sphere", "size": [0.02]}, "isotropic"),
        ("sphere-60mm-v2", "sphere", {"type": "sphere", "size": [0.03]}, "isotropic"),
        ("cone-40x60mm-v2", "cone", _mesh("cone", 0.04, 0.06), "polygon64"),
        (
            "capsule-40x80mm-v2",
            "capsule",
            {"type": "capsule", "size": [0.02, 0.02]},
            "axial_unsigned",
        ),
        (
            "prism-60x50x40mm-v2",
            "triangular_prism",
            _mesh("prism", 0.06, 0.04),
            "prism",
        ),
        ("frustum-60x30x50mm-v2", "frustum", _mesh("frustum", 0.06, 0.05), "polygon64"),
    ]
    assets = []
    for identifier, category, geometry, symmetry in definitions:
        record = {
            "id": identifier,
            "version": "1.0.0",
            "category": category,
            "geometry": geometry,
            "visual_geometry": "same_as_collision",
            "geometry_digest": canonical_digest(geometry),
            "mass_kg": 0.08,
            "contact_dimension": 6,
            "friction": [1.0, 0.005, 0.0001],
            "solref": [0.004, 1.0],
            "solimp": [0.998, 0.998, 0.001],
            "symmetry": symmetry,
            "inertia": "uniform_solid_compiled_by_mujoco_3.17.0",
            "source": {
                "kind": "procedural",
                "generator": "openroboto-primitives/1.0.0",
            },
            "license": "Apache-2.0",
            "license_sha256": (
                "a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9"
            ),
            "attribution": "OpenRoboto procedural task assets",
            "coverage_status": "development_candidate",
        }
        if symmetry == "box":
            record["symmetry_rotations"] = _box_rotations(geometry["size"])
        record["digest"] = canonical_digest(record)
        assets.append(record)
    return assets


def _box_rotations(size: list[float]) -> list[list[list[float]]]:
    """Proper signed permutations preserving each box half-extent."""
    rotations = []
    for p in itertools.permutations(range(3)):
        if any(size[i] != size[p[i]] for i in range(3)):
            continue
        parity = (-1) ** sum(p[i] > p[j] for i in range(3) for j in range(i + 1, 3))
        for signs in itertools.product([-1, 1], repeat=3):
            if math.prod(signs) * parity != 1:
                continue
            rotations.append(
                [
                    [float(signs[i]) if j == p[i] else 0.0 for j in range(3)]
                    for i in range(3)
                ]
            )
    return rotations
