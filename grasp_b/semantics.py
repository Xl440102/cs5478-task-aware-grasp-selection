"""Selector-visible semantic and relational grasp descriptions (semantic_v1).

Computed from the known cup geometry in the cup frame (handle along +Y,
opening at +Z), never from outcomes. The text is placed in the public
``approach`` field so the five-field contract is unchanged, e.g.

  part=handle outer bar; from=side, horizontal; side=from handle side;
  closing=across handle; mouth=clear; handle=occupied by this grasp

Fields:
  part     which part the fingers contact (body low/mid/high, body top, rim,
           handle outer bar, handle top bar)
  from     approach direction (above / side + pitch)
  side     where the gripper comes from relative to the handle
  closing  whether the closing axis points along the handle axis (a finger
           may land on the handle) or across it
  mouth    whether the hand sits over the opening (blocks pouring)
  handle   whether the handle is left free for another hand
"""
import math

import numpy as np

VERSION = 'semantic_v1'


def describe(candidate, record, model):
    from .features import tool_axes
    _, closing, approach = (np.asarray(v, float) for v in tool_axes(candidate.grasp_pose.quaternion))
    pos = np.asarray(candidate.grasp_pose.position, float)
    (_, _, z0), (_, _, z1) = model.body_bounds
    family = record['family']
    if family == 'handle':
        part = 'handle top bar' if approach[2] < -0.9 else 'handle outer bar'
    elif family == 'rim':
        part = 'rim (one finger inside the cup)'
    elif family == 'top':
        part = 'body top'
    else:
        f = (pos[2]-z0)/(z1-z0)
        part = 'body low' if f < 0.4 else ('body mid' if f < 0.65 else 'body high')
    pitch = math.degrees(math.asin(max(-1., min(1., -approach[2]))))
    direction = 'above' if pitch > 75 else f'side, {"horizontal" if pitch < 5 else f"{pitch:.0f} deg down"}'
    horizontal = -approach[:2]  # where the gripper comes from, cup frame
    if np.linalg.norm(horizontal) < 1e-6:
        side = 'from above'
    else:
        c = float(horizontal @ np.array((0., 1.))/np.linalg.norm(horizontal))
        side = 'from handle side' if c > 0.7 else ('opposite the handle' if c < -0.7 else 'beside the handle')
    along = abs(float(closing[1])) > 0.7
    closing_text = 'along handle axis (a finger may land on the handle)' if along else 'across handle axis'
    mouth = 'hand over the opening (blocks pouring)' if family in ('top', 'rim') or (
        family == 'handle' and approach[2] < -0.9) else 'clear'
    occupied = family == 'handle' or (family == 'top' and along) or side == 'from handle side'
    handle = 'occupied by this grasp' if occupied else 'left free'
    return dict(version=VERSION, part=part, direction=direction, side=side,
                closing=closing_text, mouth=mouth, handle=handle)


def semantic_label(candidate, record, model):
    d = describe(candidate, record, model)
    return (f"part={d['part']}; from={d['direction']}; side={d['side']}; "
            f"closing={d['closing']}; mouth={d['mouth']}; handle={d['handle']}")
