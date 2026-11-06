"""Versioned frozen subset dispatch; no generation, selection or planning.

At-most-once automatic dispatch, not exactly-once transactions. This is an
auditable caller-authorization boundary, not a hostile-Python security sandbox.
The old canary/continuation sources and stop histories remain untouched.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from time import perf_counter
import traceback
from unittest.mock import patch

from grasp_a.control import SceneRandomization
from grasp_a.sequence_contracts import SequenceConfig, task_spec
from grasp_b.canary_adapter import frozen_preflight, canary_evidence
from grasp_b.continuation_adapter import prepare_continuation
from grasp_b.wrench_v2.episode_store import EpisodeStore, StoreError, digest, canonical
from grasp_b.wrench_v2.pilot import classify_outcome, failure_diagnostics

ROOT = Path(__file__).resolve().parents[1]
SCOPE_DIR = ROOT/'results/local/task08b2a_remaining_plan_20261107'
SCOPE_HASHES = {'A': '80e1f0ec2d160d99cb7d213629bea5eaf59def2ed9cd2da878da30b7e3c09898',
                'B': '72e68afb533e888e392dec6b3595fa3625c803d4b1413135587eee0e8728252d'}
PLANNING_AUTH = {'A': '31735a90364a9cf8edbbe0eb9090428462def3e53f2fc559b15b0a25855db657',
                 'B': '506d27ae638927cc9ecf4e3becd79172d2e8151b2598c77bd51684892dea9b07'}
POLICY_HASH = 'cd9283991594a8ad824d9e18369774275d2f5529e6cc5f6c99e35252f85302bc'
SUCCESS_KEYS = {'sequence_complete', 'lift_achieved', 'no_drop', 'no_unintended_collision',
                'stage_outcomes', 'full_terminal_window', 'terminal_outcome', 'fluid_density_valid'}


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _identified(obj, field, expected):
    if obj.get(field) != expected or digest({k: v for k, v in obj.items() if k != field}) != expected:
        raise StoreError('planning identity mismatch: '+field)


def _load(store, batch_id, scope_dir):
    if batch_id not in ('A', 'B') or not Path(scope_dir).is_dir():
        raise StoreError('unknown batch or missing planning directory')
    artifacts = EpisodeStore(scope_dir)
    scopes = {k: artifacts.read('batch_'+k+'.json') for k in ('A', 'B')}
    policy = artifacts.read('execution_policy.json')
    if policy['policy_hash'] != POLICY_HASH or digest(policy['policy']) != POLICY_HASH:
        raise StoreError('execution policy mismatch')
    protocol, candidates, plan, _ = frozen_preflight(store)
    history = prepare_continuation(store)  # read-only original canary/auth chain
    all_keys = []
    for label, scope in scopes.items():
        _identified(scope, 'batch_scope_hash', SCOPE_HASHES[label])
        auth = artifacts.read('batch_'+label+'_authorization_scope.json')
        _identified(auth, 'planning_authorization_identity_hash', PLANNING_AUTH[label])
        keys = scope['ordered_episode_keys']
        if (scope['batch'] != label or scope['protocol_hash'] != protocol.protocol_hash
                or scope['plan_hash'] != digest(plan) or scope['candidate_pool_hash'] != protocol.candidate_pool_hash
                or scope['resource_identity'] != digest(protocol.resources)
                or scope['execution_policy_hash'] != POLICY_HASH
                or auth['batch_scope_hash'] != scope['batch_scope_hash']
                or auth['ordered_episode_keys'] != keys or len(keys) != (10 if label == 'A' else 5)
                or len(set(keys)) != len(keys)):
            raise StoreError('batch scope/physics mismatch')
        if (scope['parent_canary_hash'] != history['parent_canary_hash']
                or scope['parent_stop_record'] != history['parent_stop_record']
                or scope['completed_continuation_hash'] != history['continuation_hash']):
            raise StoreError('parent execution history mismatch')
        for path, sha in scope['source_hashes'].items():
            if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != sha:
                raise StoreError('frozen helper source changed')
        for key, checksums in scope['baseline_completed'].items():
            record = store.read('episodes/'+key+'.json')
            if (digest(record) != checksums['payload_sha256']
                    or hashlib.sha256(store.path('episodes/'+key+'.json').read_bytes()).hexdigest() != checksums['file_sha256']):
                raise StoreError('baseline outcome changed')
        for key in keys:
            job = scope['episodes'][key]
            links = [l for l in plan['method_links'] if l['episode_key'] == key]
            if (job != plan['episodes'].get(key) or digest(job) != key
                    or scope['method_links'][key] != links
                    or (job['task'] == 'pour_water') != (label == 'B')):
                raise StoreError('job or method link mismatch')
        all_keys.extend(keys)
    baseline = scopes['A']['baseline_completed']
    if (baseline != scopes['B']['baseline_completed'] or len(baseline) != 3
            or len(set(all_keys)) != 15 or set(all_keys) & set(baseline)
            or set(all_keys) | set(baseline) != set(plan['episodes'])):
        raise StoreError('18 = 3 + 15 coverage mismatch')
    return protocol, candidates, scopes, policy['policy']


def _manifest(protocol, scopes, policy, batch_id, scope_dir):
    scope = scopes[batch_id]
    data = dict(version='frozen_batch_manifest_v1', batch_id=batch_id,
        protocol_hash=protocol.protocol_hash, plan_hash=scope['plan_hash'],
        ordered_episode_keys=scope['ordered_episode_keys'], episodes=scope['episodes'],
        method_links=scope['method_links'], baseline_completed=scope['baseline_completed'],
        batch_scope_hash=scope['batch_scope_hash'], planning_authorization_identity=PLANNING_AUTH[batch_id],
        policy_version=policy['version'], policy_hash=POLICY_HASH, resource_identity=digest(protocol.resources),
        adapter_source_sha256=source_hash(), helper_source_hashes=scope['source_hashes'],
        scope_dir=str(Path(scope_dir).resolve()), max_new_calls=len(scope['ordered_episode_keys']),
        parent_canary_hash=scope['parent_canary_hash'], parent_stop_record=scope['parent_stop_record'],
        execution_authorized=False)
    if batch_id == 'B':
        data['required_batch_A_manifest_hash'] = _manifest(protocol, scopes, policy, 'A', scope_dir)['manifest_hash']
    return dict(data, manifest_hash=digest(data))


def prepare_batch(store, batch_id, *, scope_dir=SCOPE_DIR):
    """Read-only manifest construction. Never calls the legacy bundle loader."""
    protocol, _, scopes, policy = _load(store, batch_id, scope_dir)
    return _manifest(protocol, scopes, policy, batch_id, scope_dir)


def authorization_request(manifest):
    identity = {k: manifest[k] for k in ('protocol_hash', 'plan_hash', 'batch_id',
        'ordered_episode_keys', 'manifest_hash', 'adapter_source_sha256', 'resource_identity',
        'policy_version', 'policy_hash', 'batch_scope_hash', 'max_new_calls')}
    identity['version'] = 'batch_authorization_v1'
    return dict(identity=identity, authorization_identity_hash=digest(identity), approval_status='pending',
        required_user_authorization_text='Authorize only this frozen ordered batch under its declared stop policy.\n'+canonical(identity).decode())


@dataclass(frozen=True, slots=True)
class BatchAuthorization:
    protocol_hash: str
    plan_hash: str
    batch_id: str
    ordered_episode_keys: tuple
    manifest_hash: str
    adapter_source_sha256: str
    resource_identity: str
    policy_version: str
    authorization_identity_hash: str
    approved_by: str
    user_authorization_text: str

    def __post_init__(self):
        object.__setattr__(self, 'ordered_episode_keys', tuple(self.ordered_episode_keys))


def validate_outcome(raw, job):
    """Check failure completeness, identities and fluid numerics before classification.

    Missing terminal samples after an early task failure are not malformed data.
    This validates A evidence; it never changes A success thresholds or labels.
    """
    canonical(raw)  # rejects every NaN/Inf, including nested telemetry
    category = classify_outcome(raw, job)
    if category == 'infrastructure_error':
        raise StoreError('A simulation_error/simulation_invalid')
    required = {'schema_version', 'stages', 'stage_evaluation', 'terminal_checks', 'trajectory',
        'elapsed_s', 'final_evidence', 'success_checks', 'task_spec', 'task_spec_sha256',
        'primitive_log', 'ik_diagnostics', 'feasibility', 'collision', 'collision_events', 'dropped'}
    if not required <= set(raw) or raw['schema_version'] != 'sequence_episode_v1':
        raise StoreError('missing complete A outcome schema')
    expected_spec = task_spec(job['task'], SequenceConfig(**job['sequence_config']))
    if digest(raw['task_spec']) != digest(expected_spec) or raw['task_spec_sha256'] != digest(expected_spec):
        raise StoreError('task specification mismatch')
    stages = raw['stages']; ids = [r['id'] for r in expected_spec['stages']]
    if (not isinstance(stages, list) or [r['id'] for r in stages] != ids
            or set(raw['stage_evaluation']) != set(ids)):
        raise StoreError('incomplete/duplicate stage rows')
    for row in stages:
        if row['control_status'] not in ('completed', 'failed', 'not_executed'):
            raise StoreError('invalid control status')
        if row['control_status'] != 'not_executed':
            if row['start_s'] is None or row['end_s'] is None or row['end_s'] < row['start_s']:
                raise StoreError('invalid stage time')
        physical = raw['stage_evaluation'][row['id']]
        if (type(physical.get('physical_pass')) is not bool
                or not isinstance(physical.get('physical_checks'), dict)
                or any(type(v) is not bool for v in physical['physical_checks'].values())
                or (physical.get('evidence') is not None and not isinstance(physical['evidence'], dict))):
            raise StoreError('malformed stage physical evidence')
    checks = raw['success_checks']
    if (not SUCCESS_KEYS <= set(checks) or any(type(v) is not bool for v in checks.values())
            or not isinstance(raw['trajectory'], list) or not isinstance(raw['terminal_checks'], dict)
            or not isinstance(raw['final_evidence'], dict) or raw['elapsed_s'] < 0):
        raise StoreError('malformed measurements/checks')
    if not checks['fluid_density_valid']:
        raise StoreError('invalid fluid density')
    if raw['feasibility'].get('candidate_id') != job['candidate_id']:
        raise StoreError('feasibility identity mismatch')
    rejected = raw['status'] == 'rejected'
    if rejected and (raw['feasibility'].get('feasible') is not False or not raw['feasibility'].get('reasons')):
        raise StoreError('rejection without explicit feasibility evidence')
    if job['task'] == 'pour_water':
        if raw.get('liquid_mode') != 'pbf':
            raise StoreError('non-PBF execution')
        if not rejected:
            initial = raw.get('liquid_initial_check')
            model = raw.get('liquid_model') or {}
            if not initial or model.get('model') != 'position_based_fluid':
                raise StoreError('missing PBF initialization')
            def fluid_valid(water):
                return (isinstance(water, dict) and water.get('finite_state') is True
                    and 0 <= water['accounting_error_kg'] < 1e-9
                    and 0 <= water['max_compression'] <= .10
                    and water['initial_mass_kg'] == job['sequence_config']['water_mass_kg']
                    and water['fluid_sample_count'] == job['sequence_config']['fluid_particles'])
            if (not fluid_valid(initial) or initial['spill_ratio'] > .01
                    or not fluid_valid(raw['final_evidence'].get('water'))
                    or raw['max_fluid_compression'] > .10
                    or any(not fluid_valid(e.get('water')) for e in raw['trajectory'])):
                raise StoreError('nonfinite/malformed/invalid PBF numerics')
    if category == 'success':
        if (any(r['control_status'] != 'completed' for r in stages)
                or any(not r['physical_pass'] for r in raw['stage_evaluation'].values())
                or not raw['terminal_checks'] or not all(raw['terminal_checks'].values())
                or raw.get('terminal_samples', 0) < round(job['sequence_config']['terminal_window_s']*240)):
            raise StoreError('success inconsistent with physical/terminal evidence')
    return category


def _existing(store, manifest, key):
    try:
        record = store.read('episodes/'+key+'.json')
    except FileNotFoundError:
        if store.path('attempts/'+key+'.json').exists():
            raise StoreError('started_without_verifiable_outcome: '+key)
        return None
    if digest(record['physical_identity']) != digest(manifest['episodes'][key]) or record['episode_key'] != key:
        raise StoreError('cached identity mismatch')
    if record['category'] == 'infrastructure_error':
        raise StoreError('cached infrastructure_error requires reconciliation: '+key)
    if validate_outcome(record['raw_outcome'], manifest['episodes'][key]) != record['category']:
        raise StoreError('cached classification mismatch')
    if record.get('batch_manifest_hash') == manifest['manifest_hash']:
        prefix = 'batches/'+manifest['manifest_hash']
        completion = store.read(prefix+'/episodes/'+key+'.json')
        auth = store.read(prefix+'/authorizations/'+record['authorization_hash']+'.json')
        request = authorization_request(manifest)
        if (completion['outcome_checksum'] != digest(record)
                or completion['raw_outcome_checksum'] != digest(record['raw_outcome'])
                or completion['authorization_hash'] != record['authorization_hash']
                or digest(auth) != record['authorization_hash']
                or auth['authorization_identity_hash'] != request['authorization_identity_hash']
                or auth['user_authorization_text'] != request['required_user_authorization_text']
                or digest(store.read(prefix+'/manifest.json')) != digest(manifest)
                or record['method_links'] != manifest['method_links'][key]):
            raise StoreError('cached batch audit mismatch')
    return record


def _summary(store, manifest):
    rows = []; methods = Counter(); physical = Counter()
    for key in manifest['ordered_episode_keys']:
        try:
            record = store.read('episodes/'+key+'.json')
            category = record['category']; checksum = digest(record)
        except FileNotFoundError:
            category = 'started_without_outcome' if store.path('attempts/'+key+'.json').exists() else 'not_started'
            checksum = None
        links = manifest['method_links'][key]
        rows.append(dict(episode_key=key, category=category, outcome_checksum=checksum, method_links=links))
        physical[category] += 1
        for link in links:
            methods[link['method']+'/'+category] += 1
    return dict(version='batch_summary_v1', batch_id=manifest['batch_id'], manifest_hash=manifest['manifest_hash'],
        independent_episode_denominator=len(rows), method_link_denominator=sum(len(r['method_links']) for r in rows),
        physical_counts=dict(physical), method_link_counts=dict(methods), episodes=rows,
        metric_scope='partial_batch; full pilot per-method per-task denominator remains 3 seeds')


def _gate_B(store, manifest):
    if manifest['batch_id'] != 'B':
        return None
    a = prepare_batch(store, 'A', scope_dir=manifest['scope_dir'])
    for key in a['ordered_episode_keys']:
        if _existing(store, a, key) is None:
            raise StoreError('Batch B requires resolved Batch A')
    completion = store.read('batches/'+a['manifest_hash']+'/completion.json')
    if completion != _summary(store, a) or a['manifest_hash'] != manifest['required_batch_A_manifest_hash']:
        raise StoreError('Batch A release audit mismatch')
    return dict(batch_A_manifest=a['manifest_hash'], batch_A_completion_sha256=digest(completion))


def inspect_batch(store, manifest):
    """Read-only readiness; an unsigned manifest does not grant execution."""
    expected = prepare_batch(store, manifest['batch_id'], scope_dir=manifest['scope_dir'])
    if digest(expected) != digest(manifest):
        raise StoreError('batch manifest/source mismatch')
    recorded = sum(_existing(store, expected, key) is not None for key in expected['ordered_episode_keys'])
    pending = [p.stem for p in (store.root/'attempts').glob('*.json') if not store.path('episodes/'+p.name).exists()]
    if pending:
        raise StoreError('unverifiable started attempts: '+','.join(pending))
    blockers = []
    try:
        release = _gate_B(store, expected)
    except (StoreError, FileNotFoundError) as exc:
        release = None
        blockers.append(str(exc))
    return dict(batch_id=manifest['batch_id'], manifest_hash=manifest['manifest_hash'],
        planned_episode_count=len(manifest['ordered_episode_keys']), cached_outcome_count=recorded,
        remaining_count=len(manifest['ordered_episode_keys'])-recorded, unresolved_attempt_count=0,
        baseline_outcome_count=len(manifest['baseline_completed']), blockers=blockers,
        release_attestation=release, mechanism_ready=True, ready_for_physical_execution=not blockers,
        execution_authorized=False, new_physical_calls=0)


def run_batch(store, manifest, *, authorization=None, runner=None):
    """Serial, source-bound subset execution. No generic all-plan execution path."""
    expected = prepare_batch(store, manifest['batch_id'], scope_dir=manifest['scope_dir'])
    if digest(expected) != digest(manifest):
        raise StoreError('batch manifest/source mismatch')
    request = authorization_request(expected)
    if authorization is None:
        return dict(status='authorization_required', new_calls=0, request=request)
    if not isinstance(authorization, BatchAuthorization):
        raise ValueError('structured batch-specific authorization required')
    for field in ('protocol_hash', 'plan_hash', 'batch_id', 'manifest_hash', 'adapter_source_sha256', 'resource_identity', 'policy_version'):
        if getattr(authorization, field) != expected[field]:
            raise ValueError('authorization mismatch: '+field)
    if (authorization.ordered_episode_keys != tuple(expected['ordered_episode_keys'])
            or authorization.authorization_identity_hash != request['authorization_identity_hash']
            or authorization.user_authorization_text != request['required_user_authorization_text']
            or not isinstance(authorization.approved_by, str) or not authorization.approved_by.strip()):
        raise ValueError('exact ordered keys and full explicit authorization required')
    # Validate the entire approved cache before any dispatch, including late keys.
    for key in expected['ordered_episode_keys']:
        _existing(store, expected, key)
    for path in (store.root/'attempts').glob('*.json'):
        if not store.path('episodes/'+path.name).exists():
            raise StoreError('unresolved attempt anywhere in frozen store: '+path.stem)
    release = _gate_B(store, expected)
    prefix = 'batches/'+expected['manifest_hash']; auth_hash = digest(asdict(authorization))
    store.write_once(prefix+'/manifest.json', expected)
    store.write_once(prefix+'/authorizations/'+auth_hash+'.json', asdict(authorization))
    started = datetime.now(timezone.utc).isoformat(); run_id = digest(dict(timestamp=started, authorization=auth_hash))
    calls = 0; reused = 0; status = 'complete'; stop = None
    for key in expected['ordered_episode_keys']:
        try:
            if digest(prepare_batch(store, expected['batch_id'], scope_dir=expected['scope_dir'])) != digest(expected):
                raise StoreError('frozen identity changed')
            if digest(_gate_B(store, expected)) != digest(release):
                raise StoreError('Batch A release changed during Batch B')
            if _existing(store, expected, key) is not None:
                reused += 1
                continue
            job = expected['episodes'][key]
            if not store.write_once('attempts/'+key+'.json', dict(episode_key=key, physical_identity=job), exclusive=True):
                raise StoreError('attempt already claimed; reconcile before retry')
            stamp = datetime.now(timezone.utc).isoformat()
            store.write_once(prefix+'/dispatch/'+key+'.json', dict(episode_key=key, physical_identity=job,
                manifest_hash=expected['manifest_hash'], authorization_hash=auth_hash,
                policy_hash=expected['policy_hash'], method_links=expected['method_links'][key],
                dispatch_timestamp=stamp, attempt_status='started', release_attestation=release), exclusive=True)
            began = perf_counter(); raw = None; error = None; rejected = None; forensic = None; steps = 0; evidence = None
            try:
                import pybullet as p
                from grasp_a.sequence import run_sequence
                _, candidates, _, _ = frozen_preflight(store)
                if source_hash() != expected['adapter_source_sha256']:
                    raise StoreError('adapter source changed before A call')
                for path, sha in expected['helper_source_hashes'].items():
                    if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != sha:
                        raise StoreError('helper source changed before A call')
                original = p.stepSimulation
                def counted(*args, **kwargs):
                    nonlocal steps
                    steps += 1
                    return original(*args, **kwargs)
                with patch.object(p, 'stepSimulation', side_effect=counted):
                    calls += 1
                    raw = (run_sequence if runner is None else runner)(job['task'], job['scene_seed'], job['candidate_id'], candidates,
                        config=SequenceConfig(**job['sequence_config']), scene_config=SceneRandomization(**job['scene_config']),
                        observation_config=None, liquid=job['liquid_mode'], gui=job['gui'],
                        media_dir=job['media_dir'], show_landmarks=job['show_landmarks'])
                if digest(prepare_batch(store, expected['batch_id'], scope_dir=expected['scope_dir'])) != digest(expected):
                    raise StoreError('frozen identity changed during execution')
                category = validate_outcome(raw, job)
                evidence = canary_evidence(raw, steps)
                initialized = job['task'] == 'pour_water' and raw['status'] != 'rejected'
                if steps-round(raw['elapsed_s']*240) != (120 if initialized else 0):
                    raise StoreError('physical step accounting mismatch')
            except Exception as exc:
                category = 'infrastructure_error'
                error = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
                try:
                    canonical(raw); rejected = raw
                except (ValueError, TypeError):
                    forensic = repr(raw)
                raw = None
            record = dict(episode_key=key, physical_identity=job, category=category, raw_outcome=raw,
                error=error, rejected_raw_outcome=rejected, nonfinite_forensic_repr=forensic,
                batch_manifest_hash=expected['manifest_hash'], batch_id=expected['batch_id'],
                policy_hash=expected['policy_hash'], authorization_hash=auth_hash,
                method_links=expected['method_links'][key], dispatch_timestamp=stamp,
                wall_s=perf_counter()-began, total_stepSimulation_calls=steps, canary_evidence=evidence,
                failure_diagnostics=failure_diagnostics(raw) if raw else None)
            store.write_once('episodes/'+key+'.json', record)
            if digest(store.read('episodes/'+key+'.json')) != digest(record):
                raise StoreError('episode readback checksum mismatch')
            store.write_once(prefix+'/episodes/'+key+'.json', dict(episode_key=key, category=category,
                outcome_checksum=digest(record), raw_outcome_checksum=digest(raw) if raw else None,
                authorization_hash=auth_hash, attempt_status='finished'))
            if category == 'infrastructure_error':
                raise StoreError('infrastructure_error: '+key)
        except Exception as exc:
            status = 'stopped_infrastructure'; stop = dict(episode_key=key, type=type(exc).__name__, message=str(exc))
            break
    summary = _summary(store, expected)
    if status == 'complete':
        store.write_once(prefix+'/completion.json', summary)
    result = dict(status=status, new_calls=calls, reused=reused, stop=stop, summary=summary,
                  run_id=run_id, dispatch_started_utc=started)
    store.write_once(prefix+'/runs/'+run_id+'.json', result)
    return result
