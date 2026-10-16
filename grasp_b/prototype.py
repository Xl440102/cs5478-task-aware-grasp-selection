"""Experimental geometry/random reference selection. python -m grasp_b.prototype --execute

Creates new local artifacts every run. Execution is optional, AFTER selection.
No official method numbering, task scoring, or A/C interface modifications.
"""
from dataclasses import asdict, replace
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from .generation import DEFAULT_CONFIG, ROOT, generate_candidates, write_candidates
from .geometry import read_geometry
from .features import equivalent_pairs
from .scoring import GEOMETRY_SCORE_VERSION, normalized_weights, score_geometry
from .selectors import deterministic_geometry_ranking, validate_public, validate_ranking

DEFAULT_SCORING = ROOT / 'configs/part_b/scoring_experimental_v1.json'


def assign_geometry_scores(candidates, geometry, weights):
    """Return new immutable Candidates, full audit and explicit exclusions."""
    normalized_weights(weights)
    if len({c.id for c in candidates}) != len(candidates):
        raise ValueError('duplicate candidate IDs')
    rows = [score_geometry(c, geometry, weights) for c in candidates]
    scored = [replace(c, geometry_score=r['score']) for c, r in zip(candidates, rows)
              if r['status'] == 'ok']
    return scored, rows


def select_geometry(public):
    """Public-only selection; no sidecar, poses or feasibility diagnostics."""
    ranking = deterministic_geometry_ranking(public, score_version=GEOMETRY_SCORE_VERSION)
    validate_ranking(ranking, public)
    if not ranking:
        return {'selected_id': None, 'ranking': [], 'scope': 'experimental_smoke_only'}
    selected = ranking[0]
    if selected not in {r['id'] for r in public}:
        raise ValueError('selection absent from current feasible set')
    return {'selected_id': selected, 'ranking': ranking, 'scope': 'experimental_smoke_only'}


def select_random(public, *, task_id, seed):
    """Reuse C's local-RNG reference, preserving the ordered feasible list.

    Same ordered input + selection seed reproduces the full permutation.
    Score values are ignored by C's random selection algorithm.
    """
    allowed = validate_public(public)
    if type(seed) is not int:
        raise ValueError('selection seed must be an integer')
    if not allowed:
        return {'selected_id': None, 'ranking': [], 'scope': 'experimental_smoke_only'}
    from grasp_c.selectors import random_selector
    selection = random_selector(task_id, public, seed)
    validate_ranking(selection['ranking'], public)
    if selection['selected_id'] not in allowed or selection['ranking'][0] != selection['selected_id']:
        raise ValueError('random selection must match a feasible first-ranked ID')
    return dict(selection, scope='experimental_smoke_only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scoring-config', type=Path, default=DEFAULT_SCORING)
    parser.add_argument('--seeds', type=int, nargs='+', default=[0, 1, 2])
    parser.add_argument('--task', choices=('pour_prep', 'handover_prep'), default='handover_prep')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--method', choices=('geometry', 'random'), default='geometry')
    args = parser.parse_args()
    config = json.loads(args.scoring_config.read_text())
    if config['version'] != GEOMETRY_SCORE_VERSION or config['scope'] != 'experimental_smoke_only_not_formal_M1':
        parser.error('requires the experimental scoring version/scope')
    normalized_weights(config['weights'])
    import pybullet_data
    from grasp_a.simulation import get_feasible_candidates, run_episode
    from grasp_a.control import ObservationConfig
    mug = ROOT / 'assets/mug.urdf'
    panda = Path(pybullet_data.getDataPath()) / 'franka_panda/panda.urdf'
    geometry = read_geometry(mug, panda)
    candidates, _ = generate_candidates(json.loads(DEFAULT_CONFIG.read_text()), geometry)
    scored, breakdown = assign_geometry_scores(candidates, geometry, config['weights'])
    local = ROOT / 'results/local'
    local.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f'{args.method}_prototype_', dir=local))
    scored = write_candidates(output/'candidates.scored.json', scored)
    observation_config = ObservationConfig()
    report = dict(scope=config['scope'], method=args.method, task_id=args.task,
                  selection_seed_rule='selection seed = scene seed; preserve ordered feasible input',
                  observation_config=asdict(observation_config),
                  version=GEOMETRY_SCORE_VERSION, config=config,
                  geometry=asdict(geometry), breakdown=breakdown,
                  excluded_ids=[r['candidate_id'] for r in breakdown if r['status'] != 'ok'],
                  source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                 (mug, panda, DEFAULT_CONFIG, args.scoring_config,
                                  Path(__file__), ROOT/'grasp_b/scoring.py', ROOT/'grasp_b/features.py',
                                  ROOT/'grasp_b/selectors.py', ROOT/'grasp_c/selectors.py')},
                  equivalent_pairs=equivalent_pairs(scored),
                  rounded_score_histogram={}, trials=[])
    for c in scored:
        key = f'{c.geometry_score:.6f}'
        report['rounded_score_histogram'][key] = report['rounded_score_histogram'].get(key, 0)+1
    for seed in args.seeds:
        filtered = get_feasible_candidates(seed, scored, observation_config=observation_config)
        public = filtered['feasible_candidates']
        selection = (select_geometry(public) if args.method == 'geometry'
                     else select_random(public, task_id=args.task, seed=seed))
        repeated = (select_geometry(public) if args.method == 'geometry'
                    else select_random(public, task_id=args.task, seed=seed))
        if selection != repeated:
            raise ValueError('selection was not reproducible')
        trial = dict(seed=seed, filter=filtered, selection=selection,
                     reproducibility_verified=True, episode=None)
        report['trials'].append(trial)
    # Persist all score/selection decisions BEFORE any episode can run.
    path = output/'report.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    if args.execute:
        for trial in report['trials']:
            selected = trial['selection']['selected_id']
            if selected is not None:
                trial['episode'] = run_episode(args.task, trial['seed'], selected, scored,
                                               observation_config=observation_config)
                path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(output)
    for c, row in zip(candidates, breakdown):
        print(c.id, row['score'], {k:r['score'] for k,r in row['breakdown'].items()})
    for trial in report['trials']:
        print('seed', trial['seed'], trial['selection'],
              'success', trial['episode']['success'] if trial['episode'] else None)


if __name__ == '__main__':
    main()
