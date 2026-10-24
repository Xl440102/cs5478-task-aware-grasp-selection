"""Explicit, versioned profiles for the two A cup assets; no simulator queries.

Collision envelopes include rotated primitive shapes. This is deliberately a
single-link, known-cup model, not a generic mesh or articulated URDF loader.
"""
from dataclasses import dataclass, asdict
import hashlib
import itertools
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from .geometry import Geometry

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {'solid': 'mug.urdf', 'hollow': 'mug_hollow.urdf'}


def rotation(rpy):
    x, y, z = rpy
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    return np.array([[cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx],
                     [sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx], [-sy, cy*sx, cy*cx]])


def vector(element, key, default='0 0 0'):
    result = tuple(float(x) for x in (element.get(key, default) if element is not None else default).split())
    if len(result) != 3 or not all(math.isfinite(x) for x in result):
        raise ValueError('finite xyz/rpy vector required')
    return result


@dataclass(frozen=True)
class Shape:
    kind: str
    position: tuple
    rpy: tuple
    dimensions: tuple
    region: str

    def bounds(self):
        r = np.abs(rotation(self.rpy))
        if self.kind == 'box':
            extent = r @ (np.array(self.dimensions)/2)
        else:
            # Exact AABB of a finite cylinder with arbitrary axis.
            axis = rotation(self.rpy)[:, 2]
            radius, length = self.dimensions
            extent = radius*np.sqrt(np.maximum(0, 1-axis*axis))+length/2*np.abs(axis)
        return np.array(self.position)-extent, np.array(self.position)+extent


@dataclass(frozen=True)
class Model:
    profile: str
    version: str
    geometry: Geometry
    mass_kg: float
    inertia_kg_m2: tuple
    shapes: tuple
    body_bounds: tuple
    handle_bounds: tuple
    asset_sha256: str
    panda_sha256: str
    asset_path: str

    def manifest(self):
        return asdict(self)


def read_model(profile, panda_path, *, asset_path=None):
    if profile not in PROFILES:
        raise ValueError('profile must be solid or hollow')
    path = Path(asset_path) if asset_path else ROOT/'assets'/PROFILES[profile]
    root = ET.parse(path).getroot()
    if len(root.findall('link')) != 1 or root.findall('joint'):
        raise ValueError('only known single-link cup profiles supported')
    link = root.find("link[@name='mug']")
    if link is None:
        raise ValueError('missing mug link')
    shapes = []
    for collision in link.findall('collision'):
        origin = collision.find('origin')
        pos, rpy = vector(origin, 'xyz'), vector(origin, 'rpy')
        primitive = list(collision.find('geometry'))
        if len(primitive) != 1 or primitive[0].tag not in ('box', 'cylinder'):
            raise ValueError('only box/cylinder collision shapes supported')
        p = primitive[0]
        dims = tuple(float(x) for x in p.get('size').split()) if p.tag == 'box' else (float(p.get('radius')), float(p.get('length')))
        if len(dims) != (3 if p.tag == 'box' else 2) or not all(math.isfinite(x) and x > 0 for x in dims):
            raise ValueError('positive finite shape dimensions required')
        # Profile convention: walls and bottom surround the origin; the handle
        # is the three boxes beyond the body cylinder. Scale from bottom radius.
        shapes.append(Shape(p.tag, pos, rpy, dims, 'unclassified'))
    cylinders = [s for s in shapes if s.kind == 'cylinder']
    if len(cylinders) != 1:
        raise ValueError('profile requires one body/bottom cylinder')
    reference_radius = cylinders[0].dimensions[0]
    from dataclasses import replace
    shapes = tuple(replace(s, region='handle' if s.kind == 'box' and s.position[1] > 1.4*reference_radius else 'body') for s in shapes)
    def bounds(region):
        boxes = [s.bounds() for s in shapes if s.region == region]
        if not boxes:
            raise ValueError('profile requires body and handle geometry')
        return (tuple(map(float,np.min([b[0] for b in boxes], axis=0))), tuple(map(float,np.max([b[1] for b in boxes], axis=0))))
    body, handle = bounds('body'), bounds('handle')
    if not np.allclose(np.array(body[0])[:2], -np.array(body[1])[:2], atol=1e-6):
        raise ValueError('profile requires body centred in XY')
    radius = max(abs(v) for b in body for v in b[:2])
    height = body[1][2]-body[0][2]
    inertial = link.find('inertial')
    com = vector(inertial.find('origin'), 'xyz')
    mass = float(inertial.find('mass').get('value'))
    tensor = inertial.find('inertia')
    # Rotate inertial tensor to mug coordinates, retaining full tensor.
    ir = rotation(vector(inertial.find('origin'), 'rpy'))
    tensor_matrix = np.array([[float(tensor.get('i'+a+b, tensor.get('i'+b+a, '0'))) for b in 'xyz'] for a in 'xyz'])
    inertia = ir @ tensor_matrix @ ir.T
    if not math.isfinite(mass) or mass <= 0 or not np.isfinite(inertia).all() or np.min(np.linalg.eigvalsh(inertia)) <= 0:
        raise ValueError('positive mass and inertia required')
    panda_path = Path(panda_path)
    panda = ET.parse(panda_path).getroot()
    opening = 0
    for name, expected in [('panda_finger_joint1', (0,1,0)), ('panda_finger_joint2', (0,-1,0))]:
        joint = panda.find(f"joint[@name='{name}']")
        if vector(joint.find('axis'), 'xyz') != expected or vector(joint.find('origin'), 'rpy') != (0,0,0):
            raise ValueError('unsupported Panda closing convention')
        opening += float(joint.find('limit').get('upper'))
    tool = panda.find("joint[@name='panda_grasptarget_hand']/origin")
    offset = vector(tool, 'xyz')
    if offset[:2] != (0,0) or vector(tool, 'rpy') != (0,0,0) or not 0 < opening <= .08 or offset[2] <= 0:
        raise ValueError('unsupported Panda aperture/tool frame')
    geometry = Geometry(radius, height, opening, offset[2], com)
    return Model(profile, 'cup_primitives_v2', geometry, mass, tuple(tuple(row) for row in inertia), shapes,
                 body, handle, hashlib.sha256(path.read_bytes()).hexdigest(),
                 hashlib.sha256(panda_path.read_bytes()).hexdigest(), str(path.resolve()))
