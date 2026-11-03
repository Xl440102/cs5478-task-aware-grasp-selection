"""Candidate coverage and seed-variation audit: python -m grasp_b.coverage.

Runs only A's feasibility filter (no episode execution) for each
(generator, scene randomization, seed). Reports:

  feasible / generated, feasible grasp families, feasible per grasp kind,
  valid candidates per filter second, and how different the feasible sets of
  different seeds are (distinct sets, mean pairwise Jaccard similarity).

The v2 / legacy row is the baseline (6/80 with 4 contact families, identical
for every seed). The hollow profile uses A's sequence-scene filter.
"""
from collections import Counter
from dataclasses import asdict
import argparse
import itertools
import json
import time
from pathlib import Path

from .adaptive import AdaptiveConfig, generate_adaptive
from .models import ROOT, read_model
from .multifamily import MultiFamilyConfig, family_key, generate_multifamily

GENERATORS = ('v2', 'v3')


def build(generator, model):
    if generator == 'v2':
        candidates = generate_adaptive(model, AdaptiveConfig())
        records = [dict(id=c.id, family='side', region='body') for c in candidates]
        return candidates, records
    return generate_multifamily(model, MultiFamilyConfig())


def jaccard(a, b):
    return len(a & b)/len(a | b) if a | b else 1.


def audit(profile, generator, scene, seeds, *, task='transport_place'):
    import pybullet_data
    from grasp_a.simulation import get_feasible_candidates
    from grasp_a.sequence import get_sequence_candidates
    panda = Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
    model = read_model(profile, panda)
    candidates, records = build(generator, model)
    by_id = {c.id: c for c in candidates}
    rec = {r['id']: r for r in records}
    families = {cid: family_key(by_id[cid], rec[cid], model) for cid in by_id}
    rows, sets = [], []
    for seed in seeds:
        start = time.perf_counter()
        if profile == 'solid':
            filtered = get_feasible_candidates(seed, candidates, scene_config=scene)
        else:
            filtered = get_sequence_candidates(task, seed, candidates, scene_config=scene)
        seconds = time.perf_counter()-start
        ids = {r['id'] for r in filtered['feasible_candidates']}
        sets.append(ids)
        rejection = Counter(c['reasons'][0].split(':')[0] for c in filtered['checks']
                            if not c['feasible'])
        rows.append(dict(seed=seed, feasible=len(ids), generated=len(candidates),
                         families=len({families[i] for i in ids}),
                         by_kind=dict(sorted(Counter(rec[i]['family'] for i in ids).items())),
                         filter_seconds=round(seconds, 3),
                         valid_per_second=round(len(ids)/seconds, 2),
                         primary_rejections=dict(sorted(rejection.items())),
                         estimate=filtered['initial_object_pose_estimate'],
                         feasible_ids=sorted(ids)))
    pairs = list(itertools.combinations(range(len(sets)), 2))
    union = set().union(*sets) if sets else set()
    summary = dict(profile=profile, generator=generator,
                   scene=scene if isinstance(scene, str) else asdict(scene),
                   generated=len(candidates),
                   mean_feasible=sum(r['feasible'] for r in rows)/len(rows),
                   min_feasible=min(r['feasible'] for r in rows),
                   mean_families=sum(r['families'] for r in rows)/len(rows),
                   distinct_feasible_sets=len({frozenset(s) for s in sets}),
                   mean_pairwise_jaccard=(sum(jaccard(sets[i], sets[j]) for i, j in pairs)/len(pairs)
                                          if pairs else None),
                   union_feasible=len(union),
                   union_families=len({families[i] for i in union}),
                   always_feasible=len(set.intersection(*sets)) if sets else 0,
                   mean_valid_per_second=sum(r['valid_per_second'] for r in rows)/len(rows))
    return dict(summary=summary, seeds=rows)


def table(results):
    head = ('| profile | generator | scene | generated | feasible mean (min) | families mean | '
            'distinct sets | mean Jaccard | union feasible / families | valid per s |')
    lines = [head, '|'+'---|'*10]
    for r in results:
        s = r['summary']
        scene = s['scene'] if isinstance(s['scene'], str) else s['scene']['name']
        jac = '-' if s['mean_pairwise_jaccard'] is None else f"{s['mean_pairwise_jaccard']:.2f}"
        lines.append(f"| {s['profile']} | {s['generator']} | {scene} | {s['generated']} | "
                     f"{s['mean_feasible']:.1f} ({s['min_feasible']}) | {s['mean_families']:.1f} | "
                     f"{s['distinct_feasible_sets']}/{len(r['seeds'])} | {jac} | "
                     f"{s['union_feasible']} / {s['union_families']} | {s['mean_valid_per_second']:.1f} |")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profiles', nargs='+', choices=('solid', 'hollow'), default=['hollow'])
    parser.add_argument('--generators', nargs='+', choices=GENERATORS, default=list(GENERATORS))
    parser.add_argument('--scenes', nargs='+', default=['legacy', 'wide'])
    parser.add_argument('--seeds', nargs='+', type=int, default=list(range(10)))
    parser.add_argument('--out', type=Path, default=ROOT/'results/local/coverage.json')
    args = parser.parse_args()
    results = []
    for profile, generator, scene in itertools.product(args.profiles, args.generators, args.scenes):
        result = audit(profile, generator, scene, args.seeds)
        results.append(result)
        s = result['summary']
        print(f"{profile} {generator} {scene}: feasible {s['mean_feasible']:.1f}/{s['generated']}, "
              f"families {s['mean_families']:.1f}, distinct sets {s['distinct_feasible_sets']}",
              flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False)+'\n')
    print(table(results))
    print(args.out)


if __name__ == '__main__':
    main()
