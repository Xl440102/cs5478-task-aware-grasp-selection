"""Deterministic mug-local candidate generation and read-only A feasibility CLI.

Run: python -m grasp_b.generation --check
Outputs default to ignored results/local/part_b_v1; scores are placeholders.
"""
from dataclasses import asdict
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path

from grasp_a.contracts import Candidate, Pose, load_candidates
from .geometry import approach_axis, geometry_score, read_geometry, tool_orientation

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'configs/part_b/generation_v1.json'


def generate_candidates(config: dict, geometry):
    if config['version'] != 'body_v0.1':
        raise ValueError('unsupported generator version')
    axes = []
    for field in ('yaw_deg', 'roll_deg', 'height_m', 'pregrasp_offset_m'):
        values = [float(x) for x in config[field]]
        if not values or not all(math.isfinite(x) for x in values):
            raise ValueError(f'{field} must contain finite values')
        if field in ('yaw_deg', 'roll_deg'):
            values = [x % 360 for x in values]
        if len(set(values)) != len(values):
            raise ValueError(f'duplicate {field}')
        axes.append(sorted(values))
    margin = float(config['opening_margin_m'])
    width = 2 * geometry.radius + margin
    if not math.isfinite(margin) or margin < 0 or not 0.005 <= width <= geometry.max_opening:
        raise ValueError('opening margin exceeds Panda aperture')
    candidates, records = [], []
    for yaw, roll, height, distance in itertools.product(*axes):
        if abs(height) >= geometry.height / 2 or distance <= 0:
            raise ValueError('height must be inside body and offset positive')
        record = dict(yaw_deg=yaw, roll_deg=roll, height_m=height,
                      pregrasp_offset_m=distance, gripper_width_m=width)
        identity = dict(version=config['version'], **record)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()[:16]
        cid = f'B1_{digest}'
        axis = approach_axis(yaw)
        quat = tool_orientation(yaw, roll)
        grasp = (0.0, 0.0, height)
        pre = tuple(x-distance*a for x, a in zip(grasp, axis))
        candidate = Candidate(cid, 'body', f'tool_z_yaw_{yaw:g}_roll_{roll:g}',
                              width, geometry_score(record), Pose(grasp, quat), Pose(pre, quat))
        candidates.append(candidate)
        records.append(dict(id=cid, approach_axis_mug=axis, **record))
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError('candidate ID collision')
    return candidates, records


def write_candidates(path: Path, candidates):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(c) for c in candidates], indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')
    return load_candidates(path)


def diversity(records):
    return {key: sorted({r[key] for r in records}) for key in
            ('yaw_deg', 'roll_deg', 'height_m', 'pregrasp_offset_m', 'gripper_width_m')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'results/local/part_b_v1')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    # This development CLI deliberately confines artifacts to the local area.
    output = args.out_dir.resolve()
    if not output.is_relative_to((ROOT / 'results/local').resolve()):
        parser.error('--out-dir must be under results/local')
    import pybullet_data
    mug_path = ROOT / 'assets/mug.urdf'
    panda_path = Path(pybullet_data.getDataPath()) / 'franka_panda/panda.urdf'
    config = json.loads(args.config.read_text())
    geometry = read_geometry(mug_path, panda_path)
    candidates, records = generate_candidates(config, geometry)
    candidates = write_candidates(output / 'candidates.json', candidates)
    report = dict(config=config, geometry=asdict(geometry), candidates=records,
                  score_status='placeholder_zero_do_not_use_for_ranking',
                  source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (mug_path, panda_path)}, checks=[])
    if args.check:
        from grasp_a.simulation import get_feasible_candidates
        for seed in config['development_seeds']:
            filtered = get_feasible_candidates(seed, candidates)
            ids = {c['id'] for c in filtered['feasible_candidates']}
            filtered['feasible_diversity'] = diversity([r for r in records if r['id'] in ids])
            report['checks'].append(filtered)
            print(f'seed {seed}: {len(ids)}/{len(candidates)} feasible', flush=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(f'wrote {len(candidates)} candidates to {output}')


if __name__ == '__main__':
    main()
