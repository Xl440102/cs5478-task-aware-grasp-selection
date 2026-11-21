"""Five-method DEVELOPMENT Phase 1 only; no execution adapter or authorization.

The F2 registry is read and verified, never regenerated. Profiling lives outside
content-addressed decisions. Original scorers, budgets and ranking policies are
called unchanged. An episode plan is the last write-once completion marker.
"""
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
import hashlib
import json
import platform
import subprocess
import traceback

from grasp_a.contracts import Candidate, Pose, candidate_sha256
from grasp_a.control import scene_randomization
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.models import read_model
from grasp_b.multifamily import generate_multifamily, pruned_config, VERSION as GENERATOR_VERSION
from grasp_b.wrench_v2.adapter import GeneratorInfo
from grasp_b.wrench_v2.contacts import _plain
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError, digest
from grasp_b.wrench_v2.evaluation import validate_filter, random_ranking, dry_task_sets, wet_task_sets
from grasp_b.wrench_v2.evaluation_cli import filter_without_rollout
from grasp_b.wrench_v2.geometry import ObjectGeometry, read_gripper_geometry
from grasp_b.wrench_v2.m1 import development_config, prepare_candidate, score_prepared
from grasp_b.wrench_v2.m2 import score_trajectory, rank_m2_results, ExactWrenchCache, TrajectoryPolicy
from grasp_b.wrench_v2.ranking import rank_results
from grasp_b.wrench_v2.motion_prediction import PredictedPose, MotionModel, predict_motion
from grasp_b.wrench_v2.dynamics import predict_dynamics
from grasp_b.wrench_v2.tasks import ObjectParameters
from .relaxed_solver import (TaskDemands, RequiredWrench, TaskLexConfig, ExactTaskCache,
    solve_task, abstain_task, verify_stage, verify_original_scenarios)
from .relaxed_ranking import rank_task_results, NearTiePolicy

ROOT = Path(__file__).resolve().parents[2]
F2 = ROOT/'results/local/task09f2_validation_protocol_20261109'
VERSION = 'five_method_development_phase1_v1'
METHODS = ('m0_uniform_random_v1', 'm1_gws_v2', 'm2_gws_trajectory_v2',
           'relaxed_m1_task_lex_v1', 'relaxed_m2_task_lex_v1')
TASKS = ('transport_place', 'handover_transfer', 'pour_water')
SEEDS = (5, 6, 7)
REGISTRY_HASH = 'ce413ffac155e950ab0de0b065d7f6355a37685d021bc59e6e6262c81c93bee5'
DRAFT_HASH = '31a7ac678f64cbee689196a0f5ac90c5e8778bf5321f0647d858ba4f171d7501'
# Explicit observational fields only. time_s/duration_s are PHYSICAL INPUTS,
# not profiling, and must never be removed from identities.
OBSERVATIONAL = frozenset(('runtime_s', 'timings_s', 'wall_s', 'elapsed_s',
    'ranking_assembly_s', 'wall_clock_s'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_payload(value):
    value = _plain(value)
    if isinstance(value, dict):
        return {k: stable_payload(v) for k, v in value.items() if k not in OBSERVATIONAL}
    if isinstance(value, list):
        return [stable_payload(v) for v in value]
    return value


def seal(payload, field):
    p = _plain(payload)
    if field in p:
        raise ValueError('identity field already present')
    return dict(p, **{field: digest(p)})


def verify_seal(payload, field):
    if payload.get(field) != digest({k: v for k, v in payload.items() if k != field}):
        raise StoreError('identity mismatch: '+field)


def _read(path):
    def unique(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise StoreError('duplicate JSON key')
            d[k] = v
        return d
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(StoreError('nonfinite JSON')))


def load_registry(directory=F2):
    """Strict current-source F2 read. No historical loader/selector/outcomes."""
    directory = Path(directory)
    for name, expected in _read(directory/'checksums.json').items():
        if sha(directory/name) != expected:
            raise StoreError('F2 artifact mismatch: '+name)
    registry, draft = _read(directory/'method_registry.json'), _read(directory/'protocol_draft.json')
    for payload, field, expected in ((registry, 'registry_hash', REGISTRY_HASH), (draft, 'protocol_hash', DRAFT_HASH)):
        verify_seal(payload, field)
        if payload[field] != expected:
            raise StoreError('unregistered F2 identity')
    for name, expected in registry['source_files'].items():
        if sha(ROOT/name) != expected:
            raise StoreError('frozen method source mismatch: '+name)
    resources = draft['physical_config']['resources']
    for name, expected in resources['file_sha256'].items():
        if sha(name) != expected:
            raise StoreError('frozen resource mismatch: '+name)
    actual = dict(python=platform.python_version(), **{n: version(n) for n in ('numpy', 'scipy', 'pybullet')})
    if actual != resources['environment']:
        raise StoreError('dependency identity mismatch')
    if set(m['method_id'] for m in registry['methods'].values()) != set(METHODS):
        raise StoreError('exact frozen five methods required')
    if development_config().to_dict() != registry['common']['physics']:
        raise StoreError('physics configuration mismatch')
    if _plain(TrajectoryPolicy()) != registry['common']['scope_policy']:
        raise StoreError('required trajectory scope mismatch')
    if asdict(MotionModel(samples_per_stage=64)) != registry['common']['motion']:
        raise StoreError('motion configuration mismatch')
    return registry, draft


def validate_scope(tasks, seeds, selector_seed):
    if (not tasks or len(set(tasks)) != len(tasks) or not set(tasks) <= set(TASKS)
        or not seeds or len(set(seeds)) != len(seeds)
        or any(type(s) is not int or s not in SEEDS for s in seeds)
        or type(selector_seed) is not int or selector_seed != 2305478):
        raise ValueError('only registered development tasks/seeds 5/6/7 and selector 2305478 allowed')


@contextmanager
def no_execution():
    """Serial process guards; A static waypoint replay remains permitted."""
    import pybullet
    import grasp_a.sequence as sequence
    import grasp_a.simulation as simulation
    from grasp_a.fluid import PositionBasedFluid
    targets = [(pybullet, 'stepSimulation'), (sequence, 'run_sequence'),
               (simulation, 'run_episode'), (simulation.MugScene, 'run_episode'),
               (sequence.SequenceScene, 'run_sequence'), (sequence.SequenceScene, 'initialize_liquid'),
               (PositionBasedFluid, '__init__')]
    guards = {}
    with ExitStack() as stack:
        for obj, name in targets:
            # API identity is validated before entering this guard.
            guard = stack.enter_context(patch.object(obj, name,
                side_effect=RuntimeError('physical execution forbidden in Phase 1: '+name)))
            guards[f'{obj.__name__}.{name}'] = guard
        yield guards
        if any(g.call_count for g in guards.values()):
            raise RuntimeError('physical execution attempted')


def demands_for(prepared, task_set, motion, dynamics, trajectory, *, context_hash, object_hash, source_hash):
    """Copy original required loads, not strict LP outcomes/projected wrenches.

    M2 scope decisions come from the unchanged strict scorer. Even when strict
    equality LP is infeasible, the original analytical wrench remains required.
    """
    m1 = tuple(RequiredWrench(s.scenario_id, s.required_contact_wrench) for s in task_set.scenarios)
    dynamic_by_id = {f'{m.stage}:{i:05d}:{m.side}': d for i, (m, d) in enumerate(zip(motion.samples, dynamics.samples))}
    m2 = []
    for sample in trajectory.samples:
        if not sample.required:
            continue
        d = dynamic_by_id.get(sample.sample_id)
        if d is None:
            m2.append(RequiredWrench(sample.sample_id, None, 'unknown_motion'))
        elif d.status == 'complete' and d.required_contact_wrench_mug is not None:
            m2.append(RequiredWrench(sample.sample_id, d.required_contact_wrench_mug))
        else:
            status = d.status if d.status in ('missing_inertia', 'numerical_error') else 'unknown_motion'
            m2.append(RequiredWrench(sample.sample_id, None, status))
    return tuple(TaskDemands(candidate_id=prepared.spec.candidate_id, candidate_hash=prepared.spec.candidate_hash,
        task=task_set.task_name, method=method, object_identity=object_hash,
        common_context_identity=context_hash, source_identity=source_hash,
        scenarios=scenarios, reference_point_m=prepared.config.reference_point_m)
        for method, scenarios in zip(METHODS[3:], (m1, tuple(m2))))


def score_relaxed(prepared, demands, config, cache):
    uncertainty = dict(contact_status=prepared.spec.status.value,
        contact_spec_hash=digest(prepared.spec.to_dict()),
        simultaneous_independent_loading='not_established',
        collision_region_evidence='not_used_to_add_load_bearing_contacts')
    if prepared.status:
        allowed = {'geometry_invalid', 'geometry_unsupported', 'geometry_unknown', 'missing_parameters', 'numerical_error'}
        if prepared.status not in allowed:
            raise StoreError('unhandled invalid prepared model: '+prepared.status)
        return abstain_task(demands, config, status=prepared.status, reason=prepared.reason, uncertainty=uncertainty), None
    result = solve_task(prepared.primitives, prepared.constraints, demands,
        config=config, uncertainty=uncertainty, cache=cache)
    value = result.to_dict()
    if value['status'] == 'scored_conditional':
        a, b, bounds = prepared.primitives.matrix, prepared.constraints.matrix, prepared.constraints.bounds_n
        certificates = {name: verify_stage(a, b, bounds, value['unique_required_wrenches'], config, value[name]['record'])
                        for name in ('stage1', 'stage2')}
        original = verify_original_scenarios(a, b, bounds, demands, config, result)
        if not all(c['valid'] for c in certificates.values()):
            raise StoreError('independent original matrix certificate rejected')
        return result, dict(stages=certificates, original=original)
    return result, None


def assert_ranking(ranking, ids):
    if len(ranking) != len(ids) or len(set(ranking)) != len(ranking) or set(ranking) != set(ids):
        raise StoreError('incomplete/duplicate/foreign ranking')


def make_decision(*, method, task, seed, ids, candidates, ranking, selected, statuses,
                  scores, provenance, family_by_id, near_tie=None):
    assert_ranking(ranking, ids)
    if set(statuses) != set(ids) or set(scores) != set(ids):
        raise StoreError('missing score/status entries')
    eligible = set(ids) if method == METHODS[0] else {cid for cid, s in statuses.items() if s in ('scored', 'scored_conditional')}
    if selected is not None and (selected not in eligible or ranking[0] != selected):
        raise StoreError('selected candidate is not a complete top-ranked score')
    if (selected is None) != (not eligible):
        raise StoreError('incorrect strict/relaxed abstention')
    p = dict(version=VERSION, method_id=method, task=task, scene_seed=seed, selector_seed=2305478,
        feasible_ids=list(ids), candidate_hashes={cid: candidate_sha256([candidates[cid]]) for cid in ids},
        ranking=list(ranking), selected_id=selected,
        selected_family=None if selected is None else family_by_id[selected],
        selection_status='no_complete_score' if selected is None else ('random_choice' if method == METHODS[0] else 'complete_score_choice'),
        status_by_id=statuses, status_counts=dict(Counter(statuses.values())), scores=scores,
        near_tie=near_tie or dict(status='not_applicable'), provenance=provenance,
        outcome_information_used=False, execution_authorized=False)
    return seal(stable_payload(p), 'decision_hash')


def validate_decision(d):
    verify_seal(d, 'decision_hash')
    if d['method_id'] not in METHODS or d['outcome_information_used'] is not False or d['execution_authorized'] is not False:
        raise StoreError('invalid decision method/boundary')
    validate_scope((d['task'],), (d['scene_seed'],), d['selector_seed'])
    assert_ranking(d['ranking'], d['feasible_ids'])
    if set(d['candidate_hashes']) != set(d['feasible_ids']):
        raise StoreError('incomplete candidate identities')
    eligible = set(d['feasible_ids']) if d['method_id'] == METHODS[0] else {cid for cid, state in d['status_by_id'].items() if state in ('scored', 'scored_conditional')}
    if (d['selected_id'] is None) != (not eligible) or (d['selected_id'] is not None and d['selected_id'] not in eligible):
        raise StoreError('selection completeness/abstention mismatch')
    if set(d['status_by_id']) != set(d['feasible_ids']) or set(d['scores']) != set(d['feasible_ids']):
        raise StoreError('incomplete decision')
    if d['selected_id'] is not None and d['selected_id'] != d['ranking'][0]:
        raise StoreError('invalid selection')
    return True


def build_plan(decisions, *, protocol, catalog, feasible_pools):
    """No selectors/outcomes. New non-executable namespace, physical-only dedup."""
    verify_seal(protocol, 'protocol_hash')
    # Public from_dict validates but re-normalizes quaternions. Never use its
    # transformed floats to fingerprint a frozen input: hash original JSON,
    # with precisely the public candidate_sha256 canonicalization.
    raw_candidates = catalog['candidates']
    for raw in raw_candidates:
        validated = Candidate.from_dict(raw)
        if set(raw) != set(asdict(validated)) or any(set(raw[k]) != {'position', 'quaternion'} for k in ('grasp_pose', 'pregrasp_pose')):
            raise StoreError('candidate schema mismatch')
    def raw_hash(records):
        return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    if len({c['id'] for c in raw_candidates}) != len(raw_candidates) or raw_hash(raw_candidates) != catalog['pool_hash'] or catalog['candidate_hashes'] != {c['id']: raw_hash([c]) for c in raw_candidates}:
        raise StoreError('catalog identity mismatch')
    episodes, links = {}, []
    cells = {(t, s) for t in protocol['tasks'] for s in protocol['scene_seeds']}
    actual = {(d['task'], d['scene_seed'], d['method_id']) for d in decisions}
    if len(decisions) != len(cells)*5 or actual != {(t, s, m) for t, s in cells for m in METHODS}:
        raise StoreError('plan cannot commit incomplete decisions')
    by_id = {c['id']: c for c in catalog['candidates']}
    for task, seed in sorted(cells):
        rows = [d for d in decisions if (d['task'], d['scene_seed']) == (task, seed)]
        pool = feasible_pools[f'{task}/{seed}']
        for d in sorted(rows, key=lambda d: METHODS.index(d['method_id'])):
            validate_decision(d)
            if d['provenance'].get('protocol_hash') != protocol['protocol_hash']:
                raise StoreError('decision/protocol identity mismatch')
            if d['provenance'].get('candidate_pool_hash') != catalog['pool_hash']:
                raise StoreError('decision/catalog identity mismatch')
            if d['feasible_ids'] != pool['feasible_ids'] or d['provenance']['filter_hash'] != pool['filter_hash']:
                raise StoreError('methods do not share frozen feasible pool')
            key = None
            if d['selected_id'] is not None:
                cid = d['selected_id']
                if d['candidate_hashes'][cid] != catalog['candidate_hashes'][cid]:
                    raise StoreError('candidate identity mismatch')
                physical = dict(namespace='five_method_development_physical_identity_v1',
                    task=task, scene_seed=seed, candidate_id=cid, candidate_hash=catalog['candidate_hashes'][cid],
                    candidate_pool_hash=catalog['pool_hash'], physical_config=protocol['physical_config'],
                    observation=pool['filter_record']['initial_object_pose_estimate'],
                    feasibility_identity=pool['filter_hash'], replicate_index=0)
                key = digest(physical)
                episodes[key] = physical
            links.append(dict(task=task, scene_seed=seed, method_id=d['method_id'], decision_hash=d['decision_hash'],
                episode_key=key, abstention_reason='no_complete_score' if key is None else None))
    return seal(dict(version=VERSION, namespace='development_phase1_non_executable_plan_v1',
        protocol_hash=protocol['protocol_hash'], registry_hash=protocol['registry_hash'],
        candidate_pool_hash=catalog['pool_hash'], decision_hashes=[d['decision_hash'] for d in decisions],
        episodes=episodes, ordered_episode_keys=list(episodes), method_links=links,
        phase1_complete=True, execution_authorized=False, final_phase1_authorized=False), 'plan_hash')


def verify_store(store):
    registry = store.read('method_registry.json')
    verify_seal(registry, 'registry_hash')
    protocol, catalog, pools = (store.read(n) for n in ('protocol.json', 'candidate_pool.json', 'feasible_pools.json'))
    verify_seal(protocol, 'protocol_hash')
    if registry['registry_hash'] != protocol['registry_hash']:
        raise StoreError('registry/protocol identity mismatch')
    plan = store.read('episode_plan.json'); verify_seal(plan, 'plan_hash')
    decisions = [store.read('decisions/'+h+'.json') for h in plan['decision_hashes']]
    rebuilt = build_plan(decisions, protocol=protocol, catalog=catalog, feasible_pools=pools)
    if rebuilt != plan or store.read('method_links.json') != plan['method_links']:
        raise StoreError('frozen plan reconstruction failed')
    return dict(valid=True, cells=len(pools), decisions=len(decisions), unique_episodes=len(plan['episodes']),
                decision_hashes=plan['decision_hashes'], plan_hash=plan['plan_hash'])


def run_phase1(output, *, tasks=TASKS, seeds=SEEDS, selector_seed=2305478):
    validate_scope(tasks, seeds, selector_seed)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    if git('branch', '--show-current') != 'feature/part-b-contact-refinement':
        raise StoreError('wrong branch')
    registry, draft = load_registry()
    output = Path(output)
    if output.exists():
        raise FileExistsError('independent NEW directory required')
    output.mkdir(parents=True)
    store = EpisodeStore(output); began = perf_counter(); runtime = dict(cells=[], preparation={})
    try:
        with no_execution() as guards:
            import pybullet_data
            cfg, seq = development_config(), SequenceConfig()
            scene = scene_randomization('wide_strict_v2')
            if _plain(asdict(seq)) != draft['physical_config']['sequence_config'] or _plain(asdict(scene)) != draft['physical_config']['scene_config']:
                raise StoreError('frozen A execution/filter configuration mismatch')
            panda = Path(pybullet_data.getDataPath())/'franka_panda/panda.urdf'
            t = perf_counter(); model = read_model('hollow', panda)
            candidates, records = generate_multifamily(model, pruned_config())
            runtime['preparation']['candidate_generation_s'] = perf_counter()-t
            if len(candidates) != 116 or candidate_sha256(candidates) != registry['common']['pool_hash']:
                raise StoreError('F2 candidate pool mismatch')
            by_id = {c.id: c for c in candidates}
            geometry, gripper = ObjectGeometry.from_model(model), read_gripper_geometry(panda)
            infos = {r['id']: GeneratorInfo('grasp_b.multifamily', GENERATOR_VERSION, r['family'], r['id']) for r in records}
            implementation = {str(p.relative_to(ROOT)): sha(p) for p in (Path(__file__), Path(__file__).with_name('phase1_cli.py'))}
            protocol = seal(dict(version=VERSION, registry_hash=registry['registry_hash'], parent_protocol_hash=draft['protocol_hash'],
                tasks=list(tasks), scene_seeds=list(seeds), selector_seed=selector_seed,
                methods=list(METHODS), physical_config=draft['physical_config'], prediction_models=draft['prediction_models'],
                source_files=registry['source_files'], implementation_sources=implementation,
                git_head=git('rev-parse', 'HEAD'), execution_authorized=False, final_phase1_authorized=False,
                boundary='development static selection only; no physical authorization; rigid-water prediction differs from PBF'), 'protocol_hash')
            catalog = dict(pool_hash=candidate_sha256(candidates), candidates=[asdict(c) for c in candidates],
                candidate_hashes={c.id: candidate_sha256([c]) for c in candidates}, generator_records=records)
            store.write_once('method_registry.json', registry)
            store.write_once('protocol.json', protocol)
            store.write_once('candidate_pool.json', catalog)
            prepared, pools, decisions, checks = {}, {}, [], []
            lex = TaskLexConfig(**registry['methods']['M1_relaxed_joint_lex_v1']['solver']['config'])
            tie = NearTiePolicy(**registry['methods']['M1_relaxed_joint_lex_v1']['ranking']['settings'])
            for task in tasks:
                prediction = draft['prediction_models'][task]
                body = ObjectParameters(**prediction['object_parameters'])
                if digest(_plain(body)) != prediction['combined_object_hash']:
                    raise StoreError('prediction object identity mismatch')
                tws = (wet_task_sets(body, cfg) if task == 'pour_water' else dry_task_sets(body, cfg))[task]
                for seed in seeds:
                    cell_started = perf_counter(); cell = f'{task}/{seed}'
                    print('Phase1 filter', cell, flush=True)
                    record, filter_metrics = filter_without_rollout(task, seed, candidates, sequence_config=seq, scene_config=scene)
                    ids = validate_filter(candidates, record)
                    stable_record = stable_payload(record)
                    pool = dict(feasible_ids=list(ids), filter_hash=digest(stable_record), filter_record=stable_record,
                                candidate_pool_hash=catalog['pool_hash'])
                    pools[cell] = pool
                    store.write_once('filters/'+task+'_'+str(seed)+'.json', pool)
                    # Save full observational filter evidence separately; never read it as a decision.
                    store.write_once('observations/filter_'+task+'_'+str(seed)+'.json', dict(record=record, metrics=filter_metrics))
                    profile = dict(task=task, scene_seed=seed, a_filter_calls=1, feasible_count=len(ids),
                        filter=filter_metrics, contact_preparation_s=0., contact_cache_hits=0, candidates=[], ranking_s=0.)
                    strict1, strict2, relaxed1, relaxed2 = [], [], [], []
                    certificates = {}; diagnostics = {}; lex_cache = ExactTaskCache()
                    pose = Pose.from_dict(record['initial_object_pose_estimate'])
                    estimate = PredictedPose(pose.position, pose.quaternion)
                    provenance = dict(protocol_hash=protocol['protocol_hash'], registry_hash=registry['registry_hash'],
                        filter_hash=pool['filter_hash'], candidate_pool_hash=catalog['pool_hash'],
                        load_model=prediction, reference_frame='mug', reference_point_m=cfg.reference_point_m,
                        physics_hash=digest(cfg.to_dict()), source_identity=digest(dict(frozen=registry['source_files'], implementation=implementation)))
                    context_hash = digest(provenance)
                    for cid in ids:
                        t = perf_counter()
                        if cid not in prepared:
                            prepared[cid] = prepare_candidate(by_id[cid], geometry, gripper, generator_info=infos[cid], config=cfg)
                            profile['contact_preparation_s'] += perf_counter()-t
                        else:
                            profile['contact_cache_hits'] += 1
                        p = prepared[cid]; ct = dict(candidate_id=cid, preparation=dict(p.timings_s))
                        t = perf_counter(); one = score_prepared(p, tws); ct['strict_m1_s'] = perf_counter()-t
                        t = perf_counter(); motion = predict_motion(by_id[cid], task, initial_object_pose=estimate,
                            estimate_source='A initial_object_pose_estimate; pre-execution seeded sensor proxy, not measured trajectory',
                            model=MotionModel(samples_per_stage=64), sequence_config=seq)
                        ct['motion_s'] = perf_counter()-t
                        t = perf_counter(); dynamics = predict_dynamics(motion, body, reference_point_m=cfg.reference_point_m,
                            gravity_world_m_s2=(0, 0, -9.81), source='F2 frozen runtime object and pre-execution analytical prediction')
                        ct['dynamics_s'] = perf_counter()-t
                        cache = ExactWrenchCache()
                        t = perf_counter(); two = score_trajectory(p, motion, dynamics, policy=TrajectoryPolicy(), cache=cache)
                        ct['strict_m2_s'] = perf_counter()-t
                        strict1.append(one); strict2.append(two)
                        demands = demands_for(p, tws, motion, dynamics, two, context_hash=context_hash,
                            object_hash=prediction['combined_object_hash'], source_hash=provenance['source_identity'])
                        relaxed = []
                        for name, demand in zip(('relaxed_m1', 'relaxed_m2'), demands):
                            t = perf_counter(); value, cert = score_relaxed(p, demand, lex, lex_cache)
                            ct[name+'_s'] = perf_counter()-t
                            relaxed.append(value); certificates[cid+'/'+name] = cert
                        relaxed1.append(relaxed[0]); relaxed2.append(relaxed[1])
                        ct['strict_m1_lp_calls'] = sum(r.diagnostics.get('linprog_calls', 0) for _, r in one.scenario_results)
                        ct['strict_m2_workload'] = _plain(two.timings_s)
                        ct['relaxed_m1_lp_calls'] = relaxed[0].payload.get('lp_calls', 0)
                        ct['relaxed_m2_lp_calls'] = relaxed[1].payload.get('lp_calls', 0)
                        profile['candidates'].append(ct)
                        # Raw result checksums bind their own runtime fields: preserve them
                        # intact in evidence, while decisions contain deterministic projections.
                        diagnostics[cid] = dict(strict_m1=one.to_dict(), strict_m2=two.to_dict(),
                            relaxed_m1=relaxed[0].to_dict(), relaxed_m2=relaxed[1].to_dict())
                    t = perf_counter()
                    rankings = [random_ranking(ids, selector_seed), rank_results(strict1, ids), rank_m2_results(strict2, ids),
                                rank_task_results(relaxed1, ids, policy=tie), rank_task_results(relaxed2, ids, policy=tie)]
                    score_rows = [None, strict1, strict2, relaxed1, relaxed2]
                    cell_decisions = []
                    for method, ranking, values in zip(METHODS, rankings, score_rows):
                        if values is None:
                            scores = {cid: {} for cid in ids}; statuses = {cid: 'random_ranked' for cid in ids}; near = None
                        else:
                            scores = {}
                            for value in values:
                                raw = value.to_dict()
                                cid = raw['candidate_id']
                                if method in METHODS[3:]:
                                    scores[cid] = {k: raw.get(k) for k in ('status', 'risk', 'utilization', 'risk_interval', 'utilization_interval', 'delta_scaled', 'input_hash')}
                                    scores[cid]['stage2_realized_residual'] = max(raw.get('per_original_scenario_residual_stage2') or [0.]) if raw['status'] == 'scored_conditional' else None
                                else:
                                    scores[cid] = {k: raw.get(k) for k in ('status', 'worst_utilization', 'score', 'worst_scenario_ids', 'worst_samples', 'geometry_status', 'reason')}
                                    scores[cid]['predicted_over_capacity'] = raw['worst_utilization'] is not None and raw['worst_utilization'] > 1
                            statuses = {cid: s['status'] for cid, s in scores.items()}
                            near = None
                            if method in METHODS[1:3]:
                                selected = ranking['selected_id']
                                best = scores[selected]['worst_utilization'] if selected else None
                                ids_near = [cid for cid in ranking['ranking'] if scores[cid]['status'] == 'scored' and abs(scores[cid]['worst_utilization']-best) <= cfg.tolerances.utilization_absolute+cfg.tolerances.relative*max(1., abs(best))] if selected else []
                                near = dict(status='near_tie_diagnostic_only' if len(ids_near)>1 else 'single_at_frozen_configuration' if selected else 'no_selection', ids=ids_near, selection_unchanged=True)
                            if method in METHODS[3:]:
                                near = {k: ranking[k] for k in ('policy', 'tie_groups', 'top_primary_near_tie_ids', 'top_both_near_tie_ids')}
                                near['status'] = ('near_tie' if len(near['top_primary_near_tie_ids']) > 1 else 'single_at_frozen_k_not_sensitivity_qualified') if ranking['selected_id'] else 'no_selection'
                        d = make_decision(method=method, task=task, seed=seed, ids=ids, candidates=by_id,
                            ranking=ranking['ranking'], selected=ranking['selected_id'], statuses=statuses,
                            scores=scores, provenance=provenance, family_by_id={r['id']: r['family'] for r in records}, near_tie=near)
                        store.write_once('decisions/'+d['decision_hash']+'.json', d); decisions.append(d); cell_decisions.append(d)
                    profile['ranking_s'] = perf_counter()-t
                    profile['lex_cache'] = dict(hits=lex_cache.hits, misses=lex_cache.misses)
                    profile['wall_s'] = perf_counter()-cell_started; runtime['cells'].append(profile)
                    store.write_once('diagnostics/'+task+'_'+str(seed)+'.json', dict(candidates=diagnostics, certificates=certificates))
                    checks.append(dict(cell=cell, feasible=len(ids), complete_rankings=all(validate_decision(d) for d in cell_decisions),
                        numerical_errors=sum(d['status_counts'].get('numerical_error', 0) for d in cell_decisions)))
                    print('Phase1 frozen', cell, [(d['method_id'], d['selected_id']) for d in cell_decisions], flush=True)
            store.write_once('feasible_pools.json', pools)
            # Revalidate sources AFTER solving too; never silently accept changes during a run.
            load_registry()
            if any(sha(ROOT/n) != h for n, h in implementation.items()):
                raise StoreError('implementation changed during run')
            verification = dict(version=VERSION, cells=checks, a_filter_calls=len(pools),
                guards={k: g.call_count for k, g in guards.items()}, physical_execution_calls=0,
                all_decisions_verified=all(validate_decision(d) for d in decisions),
                decision_count=len(decisions), execution_authorized=False, final_phase1_authorized=False)
            store.write_once('verification.json', verification)
            plan = build_plan(decisions, protocol=protocol, catalog=catalog, feasible_pools=pools)
            store.write_once('method_links.json', plan['method_links'])
            runtime['wall_s'] = perf_counter()-began
            store.write_once('runtime.json', runtime)
            store.write_once('debug_log.json', [])
            # Last write-once marker. No consumer may treat partial directories as frozen.
            store.write_once('episode_plan.json', plan)
            verified = verify_store(store)
            return dict(**verified, numerical_errors=sum(c['numerical_errors'] for c in checks),
                        engineering_evaluation_complete=len(pools) == 9, final_phase1_authorized=False)
    except BaseException as exc:
        store.write_once('failure.json', dict(exception=type(exc).__name__, message=str(exc), traceback=traceback.format_exc(),
            command=__import__('sys').argv, git_head=git('rev-parse', 'HEAD'), git_diff=git('diff', '--stat'),
            classification='requires_evidence_based_debugging', runtime=runtime))
        raise
