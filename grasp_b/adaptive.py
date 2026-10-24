"""Geometry-scaled body candidates. Legacy generation_v1 remains unchanged."""
from dataclasses import dataclass, asdict
import hashlib
import itertools
import json
import math
from grasp_a.contracts import Candidate, Pose
from .geometry import tool_orientation, approach_axis


@dataclass(frozen=True)
class AdaptiveConfig:
    version: str = 'adaptive_body_v2'
    height_fractions: tuple = (.15, .35, .5, .65, .85)
    yaw_step_deg: float = 45
    rolls_deg: tuple = (0, 180)
    opening_margin_ratio: float = .10
    approach_radius_ratio: float = 3.5
    finger_half_height_m: float = .008
    max_candidates: int = 160

    def __post_init__(self):
        if self.version != 'adaptive_body_v2':
            raise ValueError('unsupported generation version')
        if not self.height_fractions or any(type(v) not in (int,float) or not math.isfinite(v) or not 0 < v < 1 for v in self.height_fractions):
            raise ValueError('height fractions must be inside (0,1)')
        if len(set(self.height_fractions)) != len(self.height_fractions):
            raise ValueError('duplicate heights')
        for v in (self.yaw_step_deg, self.approach_radius_ratio, self.finger_half_height_m):
            if type(v) not in (int,float) or not math.isfinite(v) or v <= 0:
                raise ValueError('positive finite sampling parameters required')
        if not 1 <= self.yaw_step_deg <= 180 or not math.isclose(360/self.yaw_step_deg, round(360/self.yaw_step_deg)):
            raise ValueError('yaw step must divide 360')
        if not self.rolls_deg or any(v not in (0,180) for v in self.rolls_deg) or len(set(self.rolls_deg)) != len(self.rolls_deg):
            raise ValueError('horizontal closing rolls 0/180 only')
        if type(self.max_candidates) is not int or self.max_candidates <= 0 or not math.isfinite(self.opening_margin_ratio) or self.opening_margin_ratio < 0:
            raise ValueError('invalid budget/opening margin')


def generate_adaptive(model, config=AdaptiveConfig()):
    g = model.geometry
    low, high = model.body_bounds[0][2], model.body_bounds[1][2]
    available = high-low-2*config.finger_half_height_m
    width = 2*g.radius*(1+config.opening_margin_ratio)
    if available <= 0 or not .005 <= width <= g.max_opening:
        raise ValueError('body outside supported finger height/aperture')
    axes = (sorted(config.height_fractions), range(round(360/config.yaw_step_deg)), sorted(config.rolls_deg))
    count = math.prod(len(a) for a in axes)
    if count > config.max_candidates:
        raise ValueError('candidate budget exceeded; coarsen explicit sampling')
    candidates = []
    # Start beyond both the TCP/tool-frame offset and the sampled radial
    # clearance. The previous additive rule overshot short-reach candidates
    # (.175 m for this mug) and reduced A-feasible coverage.
    distance = max(g.tool_offset, config.approach_radius_ratio*g.radius)
    for f, yi, roll in itertools.product(*axes):
        yaw = yi*config.yaw_step_deg
        h = low+config.finger_half_height_m+f*available
        pose = Pose((0.,0.,h), tool_orientation(yaw,roll))
        pre = Pose(tuple(p-distance*a for p,a in zip(pose.position, approach_axis(yaw))),pose.quaternion)
        identity = dict(version=config.version, pose=asdict(pose), pre=asdict(pre), width=width)
        cid = 'B2_'+hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]
        candidates.append(Candidate(cid,'body',f'tool_z_yaw_{yaw:g}_roll_{roll:g}',width,0.,pose,pre))
    return candidates
