"""Geometry conventions for the current single-cylinder mug and Panda URDF."""
from dataclasses import dataclass
import math
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Geometry:
    radius: float
    height: float
    max_opening: float
    tool_offset: float


def read_geometry(mug_path: Path, panda_path: Path) -> Geometry:
    mug = ET.parse(mug_path).getroot()
    body = mug.find("./link[@name='mug']/collision")
    cylinder = body.find('geometry/cylinder') if body is not None else None
    if cylinder is None:
        raise ValueError('expected mug body collision cylinder')
    origin = body.find('origin')
    if origin is not None and any(float(x) != 0 for k in ('xyz', 'rpy')
                                  for x in origin.get(k, '0 0 0').split()):
        raise ValueError('generator requires a centred, upright mug cylinder')
    panda = ET.parse(panda_path).getroot()
    for name, axis in [('panda_finger_joint1', '0 1 0'),
                       ('panda_finger_joint2', '0 -1 0')]:
        joint = panda.find(f"./joint[@name='{name}']")
        if (joint is None or joint.find('axis').get('xyz') != axis
                or joint.find('origin').get('rpy', '0 0 0') != '0 0 0'):
            raise ValueError('unsupported Panda closing frame')
    limits = [float(panda.find(f"./joint[@name='{n}']/limit").get('upper'))
              for n in ('panda_finger_joint1', 'panda_finger_joint2')]
    tool = panda.find("./joint[@name='panda_grasptarget_hand']/origin")
    if tool.get('rpy', '0 0 0') != '0 0 0':
        raise ValueError('unsupported rotated tool frame')
    xyz = tuple(float(x) for x in tool.get('xyz').split())
    if xyz[:2] != (0.0, 0.0):
        raise ValueError('unsupported tool translation')
    result = Geometry(float(cylinder.get('radius')), float(cylinder.get('length')),
                      sum(limits), xyz[2])
    if not all(math.isfinite(x) and x > 0 for x in vars(result).values()):
        raise ValueError('geometry dimensions must be finite and positive')
    return result


def tool_orientation(yaw_deg: float, roll_deg: float):
    """Rz(mug yaw) Ry(90deg) Rz(tool roll), quaternion xyzw.

    Tool +Z points inward along (cos(yaw), sin(yaw), 0). Tool Y is
    the closing axis; roll rotates it around the approach axis.
    """
    y, r = math.radians(yaw_deg) / 2, math.radians(roll_deg) / 2
    s = math.sqrt(0.5)
    q = (s * math.sin(r-y), s * math.cos(y-r),
         s * math.sin(y+r), s * math.cos(y+r))
    norm = math.sqrt(sum(x*x for x in q))
    return tuple(x / norm for x in q)


def approach_axis(yaw_deg: float):
    yaw = math.radians(yaw_deg)
    return (math.cos(yaw), math.sin(yaw), 0.0)


def geometry_score(candidate_geometry: dict):
    """Reserved scoring interface. No validated quality model in v0.1."""
    return 0.0
