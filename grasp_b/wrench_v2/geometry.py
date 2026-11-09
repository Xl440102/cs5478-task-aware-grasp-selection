"""Static URDF primitive/OBJ queries. No Bullet client or physics stepping.

Intersections are exact for the declared primitives, up to the stated numeric
tolerance. They do not reproduce Bullet collision margins/convexification.
Finger coverage uses actual inward-facing mesh triangles, not a rectangular
pad invented from the full finger AABB. Finite samples are NOT contact area.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
from numbers import Real
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from grasp_b.models import Model, Shape, rotation

TOLERANCE_M = 1e-7
GEOMETRY_VERSION = "static_primitive_mesh_v1"


class GeometryError(ValueError):
    """An unsupported or degenerate static geometry description."""


def vector3(value):
    if not isinstance(value, (list, tuple, np.ndarray)) or np.shape(value) != (3,):
        raise GeometryError("expected_finite_vector3")
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) for v in value):
        raise GeometryError("expected_real_vector3_without_coercion")
    a = np.asarray(value, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all():
        raise GeometryError("expected_finite_vector3")
    return a


def unit(value):
    a = vector3(value)
    if not math.isclose(float(np.linalg.norm(a)), 1., abs_tol=1e-8, rel_tol=0.):
        raise GeometryError("non_unit_direction")
    return a


def quaternion_matrix(xyzw):
    if not isinstance(xyzw, (list, tuple, np.ndarray)) or np.shape(xyzw) != (4,):
        raise GeometryError("invalid_unit_xyzw_quaternion")
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) for v in xyzw):
        raise GeometryError("expected_real_xyzw_without_coercion")
    q = np.asarray(xyzw, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or not math.isclose(
            float(np.linalg.norm(q)), 1., abs_tol=1e-8, rel_tol=0.):
        raise GeometryError("invalid_unit_xyzw_quaternion")
    x, y, z, w = q
    return np.array(((1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)),
                     (2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)),
                     (2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))))


@dataclass(frozen=True)
class Primitive:
    primitive_id: str
    shape: Shape
    asset_sha256: str = ""
    model_version: str = ""


@dataclass(frozen=True)
class SurfaceHit:
    t_m: float
    position_m: tuple
    normal_outward: tuple
    primitive_id: str
    surface_id: str
    region: str
    ambiguous: bool = False


@dataclass(frozen=True)
class ObjectGeometry:
    profile: str
    primitives: tuple[Primitive, ...]
    reference_point_m: tuple
    asset_sha256: str
    model_version: str

    @classmethod
    def from_model(cls, model: Model):
        if model.version != "cup_primitives_v2" or model.profile not in ("solid", "hollow"):
            raise GeometryError("unsupported_object_profile")
        if hashlib.sha256(Path(model.asset_path).read_bytes()).hexdigest() != model.asset_sha256:
            raise GeometryError("object_asset_hash_mismatch")
        primitives = []
        for i, shape in enumerate(model.shapes):
            if shape.kind not in ("box", "cylinder") or shape.region not in ("body", "handle"):
                raise GeometryError("unsupported_collision_primitive")
            if len(shape.dimensions) != (3 if shape.kind == "box" else 2) or any(
                    not math.isfinite(v) or v <= 0 for v in shape.dimensions):
                raise GeometryError("invalid_primitive_dimensions")
            vector3(shape.position)
            vector3(shape.rpy)
            primitives.append(Primitive(f"{shape.region}:{shape.kind}:{i}", shape,
                                        model.asset_sha256, model.version))
        return cls(model.profile, tuple(primitives), tuple(vector3(model.geometry.com)),
                   model.asset_sha256, model.version)

    def primitive(self, primitive_id):
        return next(p for p in self.primitives if p.primitive_id == primitive_id)


def _local_reference(primitive, point, direction=None):
    shape = primitive.shape
    r = rotation(shape.rpy)
    p = r.T @ (vector3(point)-np.asarray(shape.position))
    return (p, r) if direction is None else (p, r.T @ unit(direction), r)


@lru_cache(maxsize=4096)
def _primitive_frame(version, primitive):
    r, center = rotation(primitive.shape.rpy), np.array(primitive.shape.position)
    r.setflags(write=False)
    center.setflags(write=False)
    return r, center


def _local(primitive, point, direction=None):
    r, center = _primitive_frame(GEOMETRY_VERSION, primitive)
    p = r.T @ (vector3(point)-center)
    return (p, r) if direction is None else (p, r.T @ unit(direction), r)


def contains(primitive, point, *, strict=False, tolerance=TOLERANCE_M, reference=False):
    p, _ = (_local_reference if reference else _local)(primitive, point)
    shape = primitive.shape
    margin = tolerance if strict else -tolerance
    if shape.kind == "box":
        return bool(np.all(np.abs(p) < np.asarray(shape.dimensions)/2-margin))
    if shape.kind == "cylinder":
        radius, length = shape.dimensions
        return math.hypot(*p[:2]) < radius-margin and abs(p[2]) < length/2-margin
    raise GeometryError("unsupported_collision_primitive")


def intersect_line(primitive, origin, direction, *, tolerance=TOLERANCE_M, reference=False):
    """All boundary crossings of p(t)=origin+t*unit(direction), t in metres.

    Edge/corner/tangent or face-coincident crossings retain an ambiguity flag;
    the representative normal must not be promoted to a unique surface normal.
    """
    p, d, r = (_local_reference if reference else _local)(primitive, origin, direction)
    return _intersect_local(primitive, origin, direction, p, d, r, tolerance)


def _intersect_local(primitive, origin, direction, p, d, r, tolerance):
    """Shared intersection arithmetic; reference path keeps original transforms."""
    s = primitive.shape
    results = []

    def hit(t, normal, face, ambiguous=False):
        point = np.asarray(origin)+t*np.asarray(direction)
        results.append(SurfaceHit(float(t), tuple(map(float, point)),
                                  tuple(map(float, r@normal)), primitive.primitive_id,
                                  primitive.primitive_id+":"+face, s.region, ambiguous))

    if s.kind == "box":
        half = np.asarray(s.dimensions)/2
        lo, hi = -math.inf, math.inf
        enters, exits = [], []
        coincident = False
        for axis in range(3):
            if abs(d[axis]) < 1e-12:
                if abs(p[axis]) > half[axis]+tolerance:
                    return ()
                coincident |= abs(abs(p[axis])-half[axis]) <= tolerance
                continue
            a, b = (-half[axis]-p[axis])/d[axis], (half[axis]-p[axis])/d[axis]
            sign = 1. if d[axis] > 0 else -1.
            a, b = min(a, b), max(a, b)
            if a > lo+tolerance:
                lo, enters = a, [(axis, -sign)]
            elif abs(a-lo) <= tolerance:
                enters.append((axis, -sign))
            if b < hi-tolerance:
                hi, exits = b, [(axis, sign)]
            elif abs(b-hi) <= tolerance:
                exits.append((axis, sign))
        if lo > hi+tolerance or not enters or not exits:
            return ()
        for t, faces in ((lo, enters), (hi, exits)):
            axis, sign = faces[0]
            n = np.zeros(3)
            n[axis] = sign
            hit(t, n, "xyz"[axis]+("+" if sign > 0 else "-"),
                coincident or len(faces) != 1 or hi-lo <= tolerance)
    elif s.kind == "cylinder":
        radius, length = s.dimensions
        a = float(d[:2]@d[:2])
        b = float(2*p[:2]@d[:2])
        c = float(p[:2]@p[:2]-radius*radius)
        if a > 1e-14:
            disc = b*b-4*a*c
            if disc >= -1e-14:
                roots = sorted({(-b-math.sqrt(max(0., disc)))/(2*a),
                                (-b+math.sqrt(max(0., disc)))/(2*a)})
                for t in roots:
                    q = p+t*d
                    if abs(q[2]) <= length/2+tolerance:
                        n = np.array((q[0], q[1], 0.))/radius
                        hit(t, n, "side", disc <= 1e-14 or abs(abs(q[2])-length/2) <= tolerance)
        if abs(d[2]) > 1e-12:
            for sign in (-1., 1.):
                t = (sign*length/2-p[2])/d[2]
                q = p+t*d
                radial = math.hypot(*q[:2])
                if radial <= radius+tolerance:
                    hit(t, np.array((0., 0., sign)), "cap"+("+" if sign > 0 else "-"),
                        abs(radial-radius) <= tolerance)
    else:
        raise GeometryError("unsupported_collision_primitive")
    return tuple(sorted(results, key=lambda h: h.t_m))


@lru_cache(maxsize=32)
def _object_arrays(version, geometry):
    rotations = np.array([_primitive_frame(version, p)[0] for p in geometry.primitives])
    centers = np.array([p.shape.position for p in geometry.primitives])
    boxes = np.array([p.shape.kind == "box" for p in geometry.primitives])
    half = np.array([np.array(p.shape.dimensions)/2 if p.shape.kind == "box" else
                     (p.shape.dimensions[0], p.shape.dimensions[0], p.shape.dimensions[1]/2)
                     for p in geometry.primitives])
    for array in (rotations, centers, boxes, half):
        array.setflags(write=False)
    return rotations, centers, boxes, half


def containing_primitives(geometry, points, *, strict=False, tolerance=TOLERANCE_M):
    """All point/solid membership checks, vectorized without AABB substitution."""
    rotations, centers, boxes, half = _object_arrays(GEOMETRY_VERSION, geometry)
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise GeometryError("expected_finite_points_n_by_3")
    local = np.einsum("qnj,nji->qni", points[:, None, :]-centers[None, :, :], rotations)
    margin = tolerance if strict else -tolerance
    result = np.all(np.abs(local) < half[None, :, :]-margin, axis=2)
    cylinders = ~boxes
    if np.any(cylinders):
        p = local[:, cylinders, :]
        result[:, cylinders] = ((np.hypot(p[:, :, 0], p[:, :, 1]) < half[cylinders, 0]-margin) &
                                 (np.abs(p[:, :, 2]) < half[cylinders, 2]-margin))
    return result


def union_intersections(geometry, origin, direction, *, reference=False):
    """Identical union checks with scalar reference or cached batch transforms."""
    if reference:
        hits = [h for p in geometry.primitives for h in intersect_line(p, origin, direction, reference=True)]
        exposed = [h for h in hits if not any(
            p.primitive_id != h.primitive_id and contains(p, h.position_m, strict=True, reference=True)
            for p in geometry.primitives)]
    else:
        origin, direction = vector3(origin), unit(direction)
        rotations, centers, _, _ = _object_arrays(GEOMETRY_VERSION, geometry)
        local_origins = np.einsum("nj,nji->ni", origin-centers, rotations)
        local_directions = np.einsum("j,nji->ni", direction, rotations)
        hits = [h for i, p in enumerate(geometry.primitives) for h in
                _intersect_local(p, origin, direction, local_origins[i], local_directions[i], rotations[i], TOLERANCE_M)]
        if not hits:
            return ()
        membership = containing_primitives(geometry, [h.position_m for h in hits], strict=True)
        indices = {p.primitive_id: i for i, p in enumerate(geometry.primitives)}
        for i, h in enumerate(hits):
            membership[i, indices[h.primitive_id]] = False
        exposed = [h for h, covered in zip(hits, np.any(membership, axis=1)) if not covered]
    exposed.sort(key=lambda h: h.t_m)
    groups = []
    for h in exposed:
        if groups and abs(groups[-1][0].t_m-h.t_m) <= TOLERANCE_M:
            groups[-1].append(h)
        else:
            groups.append([h])
    out = []
    if not reference and groups:
        step = 4*TOLERANCE_M*np.asarray(direction)
        probes = [np.asarray(g[0].position_m)+sign*step for g in groups for sign in (-1, 1)]
        internal = np.any(containing_primitives(geometry, probes, strict=True,
                                                tolerance=TOLERANCE_M/10), axis=1).reshape(-1, 2).all(axis=1)
    for index, group in enumerate(groups):
        h = group[0]
        # A touching-box seam inside the union is not a contact surface.
        point = np.asarray(h.position_m)
        step = 4*TOLERANCE_M*np.asarray(direction)
        if ((reference and all(any(contains(p, point+sign*step, strict=True, tolerance=TOLERANCE_M/10,
                                            reference=True) for p in geometry.primitives) for sign in (-1, 1))) or
                (not reference and internal[index])):
            continue
        out.append(SurfaceHit(h.t_m, h.position_m, h.normal_outward, h.primitive_id,
                              h.surface_id, h.region, h.ambiguous or len(group) > 1))
    return tuple(out)


def on_surface(primitive, point, normal_outward, surface_id, *, tolerance=TOLERANCE_M, reference=False):
    """Validate a named face, its extent, and its outward normal, not an AABB."""
    p, r = (_local_reference if reference else _local)(primitive, point)
    n = r.T@unit(normal_outward)
    if not surface_id.startswith(primitive.primitive_id+":"):
        return False
    face = surface_id[len(primitive.primitive_id)+1:]
    shape = primitive.shape
    if shape.kind == "box":
        if face not in ("x+", "x-", "y+", "y-", "z+", "z-"):
            return False
        axis, sign = "xyz".index(face[0]), (1. if face[1] == "+" else -1.)
        half = np.asarray(shape.dimensions)/2
        expected = np.zeros(3)
        expected[axis] = sign
        return bool(abs(p[axis]-sign*half[axis]) <= tolerance and
                    np.all(np.abs(p) <= half+tolerance) and np.linalg.norm(n-expected) <= 1e-7)
    radius, length = shape.dimensions
    if face == "side":
        expected = np.array((p[0], p[1], 0.))/radius
        return (abs(math.hypot(*p[:2])-radius) <= tolerance and
                abs(p[2]) <= length/2+tolerance and np.linalg.norm(n-expected) <= 1e-7)
    if face in ("cap+", "cap-"):
        sign = 1. if face == "cap+" else -1.
        return (abs(p[2]-sign*length/2) <= tolerance and math.hypot(*p[:2]) <= radius+tolerance
                and np.linalg.norm(n-np.array((0., 0., sign))) <= 1e-7)
    return False


def _xyz(element, key="xyz"):
    return vector3(tuple(float(v) for v in (element.get(key, "0 0 0") if element is not None
                                           else "0 0 0").split()))


@dataclass(frozen=True)
class FingerMesh:
    finger_id: str
    triangles: tuple
    joint_origin_tool_m: tuple
    collision_rpy: tuple
    lower_m: float
    upper_m: float
    sha256: str

    def inner_face_reference(self, tool_x, tool_z):
        """Mesh inward face at tool x,z for joint q=0; returns y or None.

        Barycentric triangle projection tests actual mesh coverage. A root,
        side or tip face with |normal_y| < .9 is excluded from the declared
        near-flat finger contact face; this threshold is recorded in provenance.
        """
        sign = 1 if self.finger_id == "left" else -1
        values = []
        for a, b, c in transform_finger_mesh(self):
            n = np.cross(b-a, c-a)
            norm = np.linalg.norm(n)
            if norm <= 1e-15 or sign*n[1]/norm > -.9:
                continue
            matrix = np.array(((b[0]-a[0], c[0]-a[0]), (b[2]-a[2], c[2]-a[2])))
            if abs(np.linalg.det(matrix)) < 1e-15:
                continue
            u, v = np.linalg.solve(matrix, (tool_x-a[0], tool_z-a[2]))
            if u >= -1e-9 and v >= -1e-9 and u+v <= 1+1e-9:
                values.append(float(a[1]+u*(b[1]-a[1])+v*(c[1]-a[1])))
        return (min(values) if sign == 1 else max(values)) if values else None

    def inner_face(self, tool_x, tool_z):
        values = []
        for a, b, c, matrix in prepare_finger(self):
            u, v = np.linalg.solve(matrix, (tool_x-a[0], tool_z-a[2]))
            if u >= -1e-9 and v >= -1e-9 and u+v <= 1+1e-9:
                values.append(float(a[1]+u*(b[1]-a[1])+v*(c[1]-a[1])))
        return (min(values) if self.finger_id == "left" else max(values)) if values else None


@lru_cache(maxsize=32)
def _finger_faces(version, finger):
    """Cache depends on mesh hash AND vertices/transforms/limits, not a path."""
    sign = 1 if finger.finger_id == "left" else -1
    faces = []
    for a, b, c in transform_finger_mesh(finger):
        n = np.cross(b-a, c-a)
        norm = np.linalg.norm(n)
        if norm <= 1e-15 or sign*n[1]/norm > -.9:
            continue
        matrix = np.array(((b[0]-a[0], c[0]-a[0]), (b[2]-a[2], c[2]-a[2])))
        if abs(np.linalg.det(matrix)) < 1e-15:
            continue
        for array in (a, b, c, matrix):
            array.setflags(write=False)
        faces.append((a, b, c, matrix))
    return tuple(faces)


def prepare_finger(finger):
    return _finger_faces(GEOMETRY_VERSION, finger)


def transform_finger_mesh(finger):
    """Uncached transform, also used to profile the original repeated work."""
    r, root = rotation(finger.collision_rpy), np.array(finger.joint_origin_tool_m)
    return tuple(tuple(root+r@np.array(v) for v in triangle) for triangle in finger.triangles)


@dataclass(frozen=True)
class GripperGeometry:
    fingers: tuple[FingerMesh, ...]
    panda_sha256: str
    palm_sha256: str
    # x,z mesh-supported rays and each finger's inner y at q=0.
    pad_samples: tuple

    @property
    def max_opening_m(self):
        return 2*min(f.upper_m for f in self.fingers)

    def finger(self, name):
        return next(f for f in self.fingers if f.finger_id == name)


def _mesh(urdf, root, link_name):
    collisions = root.findall(f"link[@name='{link_name}']/collision")
    if len(collisions) != 1:
        raise GeometryError("expected_single_collision_mesh")
    collision = collisions[0]
    mesh = collision.find("geometry/mesh")
    if mesh is None:
        raise GeometryError("expected_collision_mesh")
    uri = mesh.get("filename", "")
    if not uri.startswith("package://"):
        raise GeometryError("unsupported_mesh_uri")
    path = urdf.parent/uri[len("package://"):]
    scale = vector3(tuple(float(v) for v in mesh.get("scale", "1 1 1").split()))
    if np.any(scale <= 0):
        raise GeometryError("invalid_mesh_scale")
    vertices, triangles = [], []
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts and parts[0] == "v":
            vertices.append(tuple(vector3(tuple(map(float, parts[1:4])))*scale))
        elif parts and parts[0] == "f":
            indices = [int(v.split("/")[0]) for v in parts[1:]]
            if any(i <= 0 or i > len(vertices) for i in indices):
                raise GeometryError("unsupported_obj_face_index")
            for i in range(1, len(indices)-1):
                triangles.append(tuple(vertices[j-1] for j in (indices[0], indices[i], indices[i+1])))
    if not triangles:
        raise GeometryError("empty_collision_mesh")
    origin = collision.find("origin")
    return (tuple(triangles), _xyz(origin), _xyz(origin, "rpy"),
            hashlib.sha256(path.read_bytes()).hexdigest())


def _read_gripper_geometry(panda_urdf, *, reference=False):
    """Read existing Panda joint transforms and triangle meshes without Bullet.

    Nine fixed geometry-derived rays cover a small mesh-supported neighborhood
    of TCP. They predict sampled first contacts, not a full swept mesh check.
    """
    path = Path(panda_urdf)
    root = ET.parse(path).getroot()
    tool = root.find("joint[@name='panda_grasptarget_hand']")
    if (tool is None or tool.get("type") != "fixed" or tool.find("parent") is None or
            tool.find("parent").get("link") != "panda_hand" or
            np.linalg.norm(_xyz(tool.find("origin"), "rpy")) > 1e-12):
        raise GeometryError("unsupported_tcp_transform")
    tcp = _xyz(tool.find("origin"))
    fingers = []
    for name, index, sign in (("left", 1, 1), ("right", 2, -1)):
        joint = root.find(f"joint[@name='panda_finger_joint{index}']")
        if joint is None or joint.get("type") != "prismatic" or not np.allclose(
                _xyz(joint.find("axis")), (0., sign, 0.), atol=1e-12, rtol=0.):
            raise GeometryError("unsupported_finger_closing_axis")
        if joint.find("parent").get("link") != "panda_hand" or np.linalg.norm(_xyz(joint.find("origin"), "rpy")) > 1e-12:
            raise GeometryError("unsupported_finger_joint_transform")
        triangles, xyz, rpy, sha = _mesh(path, root, f"panda_{name}finger")
        if np.linalg.norm(xyz) > 1e-12:
            raise GeometryError("unsupported_collision_translation")
        limit = joint.find("limit")
        lo, hi = float(limit.get("lower")), float(limit.get("upper"))
        if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo < hi):
            raise GeometryError("invalid_finger_joint_limits")
        fingers.append(FingerMesh(name, triangles, tuple(_xyz(joint.find("origin"))-tcp),
                                  tuple(rpy), lo, hi, sha))
    _, _, _, palm_sha = _mesh(path, root, "panda_hand")
    # Sampling bounds are mesh-derived; membership is checked against triangles.
    left = fingers[0]
    transformed = [np.asarray(left.joint_origin_tool_m)+rotation(left.collision_rpy)@np.array(v)
                   for t in left.triangles for v in t]
    low, high = np.min(transformed, axis=0), np.max(transformed, axis=0)
    hx, hz = min(-low[0], high[0])/2, min(-low[2], high[2])/2
    if hx <= 0 or hz <= 0:
        raise GeometryError("tcp_not_in_finger_mesh_projection")
    samples = []
    for x in (-hx, 0., hx):
        for z in (-hz, 0., hz):
            ys = tuple((f.inner_face_reference if reference else f.inner_face)(float(x), float(z)) for f in fingers)
            if any(y is None for y in ys):
                raise GeometryError("sampling_ray_outside_inward_finger_mesh")
            samples.append((float(x), float(z), *ys))
    return GripperGeometry(tuple(fingers), hashlib.sha256(path.read_bytes()).hexdigest(),
                           palm_sha, tuple(samples))


def _gripper_key(path):
    root = ET.parse(path).getroot()
    meshes = []
    for link in ("panda_leftfinger", "panda_rightfinger", "panda_hand"):
        mesh = root.find(f"link[@name='{link}']/collision/geometry/mesh")
        if mesh is None or not mesh.get("filename", "").startswith("package://"):
            raise GeometryError("unsupported_collision_mesh")
        mesh_path = path.parent/mesh.get("filename")[len("package://"):]
        meshes.append(hashlib.sha256(mesh_path.read_bytes()).hexdigest())
    return (GEOMETRY_VERSION, hashlib.sha256(path.read_bytes()).hexdigest(),
            hashlib.sha256(ET.tostring(root)).hexdigest(), *meshes)


@lru_cache(maxsize=16)
def _gripper_asset(path, key):
    result = _read_gripper_geometry(path)
    if ((result.panda_sha256, *(f.sha256 for f in result.fingers), result.palm_sha256) !=
            (key[1], *key[3:])):
        raise GeometryError("gripper_assets_changed_during_preprocessing")
    return result


def read_gripper_geometry(panda_urdf, *, reference=False):
    path = Path(panda_urdf).resolve()
    if reference:
        return _read_gripper_geometry(path, reference=True)
    return _gripper_asset(path, _gripper_key(path))


def clear_geometry_caches():
    """For reproducible cold/warm profiling; no on-disk cache or file writes."""
    for cache in (_primitive_frame, _object_arrays, _finger_faces, _gripper_asset):
        cache.cache_clear()


def geometry_cache_info():
    return {name: dict(cache.cache_info()._asdict()) for name, cache in (
        ("primitive_frames", _primitive_frame), ("object_arrays", _object_arrays),
        ("finger_faces", _finger_faces), ("gripper_assets", _gripper_asset))}
