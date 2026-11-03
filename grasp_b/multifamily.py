"""Multi-family cup grasps (v3). adaptive_body_v2 and generation_v1 are unchanged.

v2 sweeps only horizontal body grasps around the full circle; most fail
pregrasp IK and the feasible set collapses to a few near-identical
candidates. v3 adds executable *kinds* of grasp rather than more samples:

  side    horizontal or downward-pitched (30/60 deg) approach to the body
  top     top-down grasp across the body diameter (0.077 m < 0.08 m aperture)
  rim     top-down pinch of the hollow cup wall, one finger in, one out
  handle  pinch of the handle's vertical bar (side) or top bar (top-down)

Every grasp keeps the tool convention used by A and B: tool +Z is the
approach direction, tool +Y the closing axis, both in the cup frame. Side and
top grasps keep a horizontal closing line through the cup axis, so B's
existing M1/M2 contact model still scores them; rim and handle grasps are
outside that model (see ``m2_supported``).

Candidates stay cup-local and seed-independent, so every method shares one
file. Robot reachability is decided per seed by A's filter, whose IK test
runs before any collision sampling.
"""
from dataclasses import asdict, dataclass
import argparse
import hashlib
import itertools
import json
import math

import numpy as np

from grasp_a.contracts import Candidate, Pose

VERSION = 'multifamily_v3'
FAMILIES = ('side', 'top', 'rim', 'handle')


@dataclass(frozen=True)
class MultiFamilyConfig:
    version: str = VERSION
    families: tuple = FAMILIES
    # side: body grasps
    side_yaw_step_deg: float = 45
    side_pitches_deg: tuple = (0, 30, 60)
    side_height_fractions: tuple = (.25, .5, .75)
    side_rolls_deg: tuple = (0, 180)
    # top: TCP depth below the body top; closing direction swept over yaw
    top_depths_m: tuple = (.015, .03)
    top_yaw_step_deg: float = 45
    # rim (hollow profile only)
    rim_azimuth_step_deg: float = 45
    rim_depth_m: float = .012
    rim_width_m: float = .03
    # handle
    handle_yaws_deg: tuple = (240, 270, 300)  # approach azimuths, pointing into the handle
    handle_pitches_deg: tuple = (0, 30)
    handle_heights_m: tuple = (-.012, 0., .012)
    handle_rolls_deg: tuple = (0, 180)
    handle_top_y_fractions: tuple = (.35, .65)
    handle_width_m: float = .035
    body_opening_margin_ratio: float = .10
    approach_radius_ratio: float = 3.5
    finger_half_height_m: float = .008
    max_candidates: int = 400

    def __post_init__(self):
        if self.version != VERSION:
            raise ValueError('unsupported generation version')
        if not self.families or any(f not in FAMILIES for f in self.families) \
                or len(set(self.families)) != len(self.families):
            raise ValueError(f'families must be a nonempty subset of {FAMILIES}')
        for key in ('side_yaw_step_deg', 'top_yaw_step_deg', 'rim_azimuth_step_deg'):
            step = getattr(self, key)
            if not 1 <= step <= 180 or not math.isclose(360/step, round(360/step)):
                raise ValueError(f'{key} must divide 360')
        if any(not 0 <= v <= 75 for v in self.side_pitches_deg + self.handle_pitches_deg):
            raise ValueError('pitches must lie in [0, 75] deg; use the top family for 90')
        if any(not 0 < v < 1 for v in self.side_height_fractions + self.handle_top_y_fractions):
            raise ValueError('fractions must lie inside (0, 1)')
        if any(r not in (0, 180) for r in self.side_rolls_deg + self.handle_rolls_deg):
            raise ValueError('rolls must be 0 or 180 (horizontal closing axis)')
        if type(self.max_candidates) is not int or self.max_candidates <= 0:
            raise ValueError('positive integer candidate budget required')


def orientation(approach, closing):
    """Quaternion xyzw of the tool frame with Z=approach and Y=closing."""
    z = np.asarray(approach, float)
    z /= np.linalg.norm(z)
    y = np.asarray(closing, float)
    y = y - (y @ z)*z
    y /= np.linalg.norm(y)
    m = np.column_stack((np.cross(y, z), y, z))
    w = math.sqrt(max(0., 1+m[0, 0]+m[1, 1]+m[2, 2]))/2
    if w > 1e-6:
        q = ((m[2, 1]-m[1, 2])/(4*w), (m[0, 2]-m[2, 0])/(4*w), (m[1, 0]-m[0, 1])/(4*w), w)
    else:  # 180 deg rotation; pick the dominant diagonal
        i = int(np.argmax(np.diag(m)))
        j, k = (i+1) % 3, (i+2) % 3
        s = math.sqrt(max(0., 1+m[i, i]-m[j, j]-m[k, k]))*2
        q = [0., 0., 0., (m[k, j]-m[j, k])/s]
        q[i], q[j], q[k] = s/4, (m[j, i]+m[i, j])/s, (m[k, i]+m[i, k])/s
    q = np.asarray(q)
    q /= np.linalg.norm(q)
    if q[3] < 0:
        q = -q
    return tuple(float(v) for v in q)


def side_axes(yaw_deg, pitch_deg, roll_deg):
    """Approach azimuth yaw, tilted pitch deg below horizontal; horizontal closing.

    pitch 0 reproduces ``geometry.tool_orientation(yaw, roll)``.
    """
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    approach = (math.cos(yaw)*math.cos(pitch), math.sin(yaw)*math.cos(pitch), -math.sin(pitch))
    closing = (-math.sin(yaw), math.cos(yaw), 0.)
    if roll_deg == 180:
        closing = tuple(-v for v in closing)
    return approach, closing


def _sweep(step):
    return [i*step for i in range(round(360/step))]


def _make(kind, region, label, grasp, approach, closing, width, distance):
    quat = orientation(approach, closing)
    a = np.asarray(approach, float)/np.linalg.norm(approach)
    pre = tuple(float(v) for v in np.asarray(grasp)-distance*a)
    pose, pre_pose = Pose(tuple(float(v) for v in grasp), quat), Pose(pre, quat)
    identity = dict(version=VERSION, kind=kind, pose=asdict(pose), pre=asdict(pre_pose),
                    width=round(width, 9))
    cid = 'B3_'+hashlib.sha256(json.dumps(identity, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()[:16]
    candidate = Candidate(cid, region, label, float(width), 0., pose, pre_pose)
    return candidate, dict(id=cid, family=kind, region=region,
                           approach_axis_mug=tuple(float(v) for v in a),
                           closing_axis_mug=tuple(float(v) for v in closing))


def generate_multifamily(model, config=MultiFamilyConfig()):
    """Return (candidates, records). Records are B-internal family metadata."""
    g = model.geometry
    (bx0, by0, bz0), (bx1, by1, bz1) = model.body_bounds
    (hx0, hy0, hz0), (hx1, hy1, hz1) = model.handle_bounds
    distance = max(g.tool_offset, config.approach_radius_ratio*g.radius)
    body_width = 2*g.radius*(1+config.body_opening_margin_ratio)
    if body_width > g.max_opening:
        raise ValueError('body wider than Panda aperture')
    out = []
    families = set(config.families)
    if 'side' in families:
        usable = bz1-bz0-2*config.finger_half_height_m
        for f, yaw, pitch, roll in itertools.product(
                sorted(config.side_height_fractions), _sweep(config.side_yaw_step_deg),
                sorted(config.side_pitches_deg), sorted(config.side_rolls_deg)):
            z = bz0+config.finger_half_height_m+f*usable
            approach, closing = side_axes(yaw, pitch, roll)
            out.append(_make('side', 'body', f'side_yaw_{yaw:g}_pitch_{pitch:g}_roll_{roll:g}_h_{f:g}',
                             (0., 0., z), approach, closing, body_width, distance))
    if 'top' in families:
        for depth, yaw in itertools.product(sorted(config.top_depths_m),
                                            _sweep(config.top_yaw_step_deg)):
            y = math.radians(yaw)
            out.append(_make('top', 'body', f'top_down_closing_yaw_{yaw:g}_depth_{depth*1000:g}mm',
                             (0., 0., bz1-depth), (0., 0., -1.), (math.cos(y), math.sin(y), 0.),
                             body_width, distance))
    if 'rim' in families and model.profile == 'hollow':
        wall = [s for s in model.shapes if s.kind == 'box' and s.region == 'body']
        if not wall:
            raise ValueError('hollow profile requires wall boxes')
        rim_radius = float(np.mean([math.hypot(*s.position[:2]) for s in wall]))
        for az in _sweep(config.rim_azimuth_step_deg):
            a = math.radians(az)
            radial = (math.cos(a), math.sin(a), 0.)
            grasp = (rim_radius*radial[0], rim_radius*radial[1], bz1-config.rim_depth_m)
            out.append(_make('rim', 'rim', f'rim_top_down_azimuth_{az:g}', grasp,
                             (0., 0., -1.), radial, config.rim_width_m, distance))
    if 'handle' in families:
        # Vertical outer bar: the handle box nearest +Y. Pinch it across X.
        bars = [s for s in model.shapes if s.region == 'handle']
        outer = max(bars, key=lambda s: s.position[1])
        upper = max(bars, key=lambda s: s.position[2])
        ox, oy, oz = outer.position
        for yaw, pitch, h, roll in itertools.product(
                sorted(config.handle_yaws_deg), sorted(config.handle_pitches_deg),
                sorted(config.handle_heights_m), sorted(config.handle_rolls_deg)):
            approach, closing = side_axes(yaw, pitch, roll)
            out.append(_make('handle', 'handle',
                             f'handle_side_yaw_{yaw:g}_pitch_{pitch:g}_roll_{roll:g}_z_{h*1000:g}mm',
                             (ox, oy, oz+h), approach, closing, config.handle_width_m, distance))
        # Upper horizontal bar between body and outer bar, approached top-down.
        y_lo, y_hi = by1, outer.position[1]-outer.dimensions[1]/2
        for frac, roll in itertools.product(sorted(config.handle_top_y_fractions),
                                            sorted(config.handle_rolls_deg)):
            y = y_lo+frac*(y_hi-y_lo)
            closing = (1., 0., 0.) if roll == 0 else (-1., 0., 0.)
            out.append(_make('handle', 'handle',
                             f'handle_top_down_y_{frac:g}_roll_{roll:g}',
                             (upper.position[0], y, upper.position[2]), (0., 0., -1.),
                             closing, config.handle_width_m, distance))
    if len(out) > config.max_candidates:
        raise ValueError(f'{len(out)} candidates exceed budget {config.max_candidates}')
    if len({c.id for c, _ in out}) != len(out):
        raise ValueError('candidate ID collision')
    return [c for c, _ in out], [r for _, r in out]


def m2_supported(record):
    """B's M1/M2 contact model covers body grasps with a horizontal closing line."""
    return record['region'] == 'body'


def family_key(candidate, record, model):
    """Coarse grasp family for diversity metrics; not robot or outcome equivalence.

    Body grasps reuse B's unordered contact pair (cup-frame contact points).
    Rim/handle grasps use the rounded TCP position and unsigned closing line.
    """
    if record['region'] == 'body':
        from .diagnostics import contact_key
        return 'body:'+json.dumps(contact_key(candidate, model), separators=(',', ':'))
    axis = np.asarray(record['closing_axis_mug'])
    first = next(v for v in axis if abs(v) > 1e-6)
    axis = axis*(1 if first > 0 else -1)
    pos = tuple(round(float(v), 3) for v in candidate.grasp_pose.position)
    return f"{record['region']}:{pos}:{tuple(round(float(v), 3) for v in axis)}"


def main():
    import pybullet_data
    from pathlib import Path
    from .generation import write_candidates
    from .models import ROOT, read_model
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', choices=('solid', 'hollow'), default='hollow')
    parser.add_argument('--families', nargs='+', choices=FAMILIES, default=list(FAMILIES))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    panda = Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
    model = read_model(args.profile, panda)
    candidates, records = generate_multifamily(model, MultiFamilyConfig(families=tuple(args.families)))
    write_candidates(args.out, candidates)
    args.out.with_suffix('.families.json').write_text(json.dumps(records, indent=2)+'\n')
    print(f'wrote {len(candidates)} {VERSION} candidates for {args.profile} to {args.out}')


if __name__ == '__main__':
    main()
