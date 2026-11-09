"""Single frozen Pour continuation. No selector, planning or physics at preflight.

This separate extension preserves the original canary's code hash and stop.
Authorization is a caller attestation bound to exact identities, not a security
sandbox or a substitute for obtaining the human's permission.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from grasp_a.control import SceneRandomization
from grasp_a.sequence_contracts import SequenceConfig
from grasp_b.canary_adapter import frozen_preflight, extension_sha256, canary_evidence
from grasp_b.wrench_v2.episode_store import StoreError, digest, canonical
from grasp_b.wrench_v2.pilot import classify_outcome

ROOT = Path(__file__).resolve().parents[1]
EPISODE = 'ee85b1c661c5e840f323f6f1335b385bf62e6b9e06fe9f7262ef6d1c2f39b0e5'
CANDIDATE = 'B3_db2df20cfd3cb7be'
ANCHORS = dict(
    protocol='184dd0353ce2d8473de1fdc46e9be88fb21e02f9a4b02d953356540c3db43f1c',
    plan='c653e45a8a53f4fb4ee050e3036924f7f5d3cf26f5e63c10cb46be84f652f9b4',
    parent='cecff9dd95fb75ec63eafee5640c967e0d28f344d49cf080a56037e489caf307',
    prior_plan='4251ec207aa992b3a7e4ac3dac5f175bf51e6c244880c782e10154a9f50f7aa6',
    prior_authorization='8c413999ea42abb70712f246260d0d1410ea6fde862265c0ab23c313cf4b43e2')
DEFAULT_FILES = dict(
    prior_plan=str(ROOT/'results/local/task08b1b_preflight_20261107/pour_continuation_plan.json'),
    prior_authorization=str(ROOT/'results/local/task08b1b_preflight_20261107/authorization_identity.json'),
    parent=str(ROOT/'results/local/task08b1a_preflight_20261107_final/canary_plan.json'))


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _identified(value, field, expected):
    if value.get(field) != expected or digest({k: v for k, v in value.items() if k != field}) != expected:
        raise StoreError('history identity mismatch: '+field)


def prepare_continuation(store, *, history_files=None):
    """Validate original history and return an unsigned, single-episode manifest.

    history_files only relocates original JSON records; fixed content anchors
    cannot be overridden by the caller. Nothing is saved or executed here.
    """
    files = DEFAULT_FILES if history_files is None else history_files
    if set(files) != set(DEFAULT_FILES):
        raise StoreError('three original history files required')
    history = {k: json.loads(Path(v).read_text()) for k, v in files.items()}
    prior, old_auth, parent = (history[k] for k in ('prior_plan', 'prior_authorization', 'parent'))
    for obj, field, anchor in ((prior, 'continuation_hash', 'prior_plan'),
                              (old_auth, 'authorization_identity_hash', 'prior_authorization'),
                              (parent, 'canary_hash', 'parent')):
        _identified(obj, field, ANCHORS[anchor])
    protocol, candidates, plan, _ = frozen_preflight(
        store, expected_protocol=ANCHORS['protocol'], expected_plan=ANCHORS['plan'])
    if protocol.protocol_hash != ANCHORS['protocol'] or digest(plan) != ANCHORS['plan']:
        raise StoreError('protocol/plan mismatch')
    for obj in (prior, old_auth, parent):
        if obj['protocol_hash'] != protocol.protocol_hash or obj['plan_hash'] != digest(plan):
            raise StoreError('history protocol/plan mismatch')
    if (prior['ordered_episode_keys'] != [EPISODE] or old_auth['approved_episode_keys'] != [EPISODE]
            or prior['parent_canary_hash'] != parent['canary_hash']
            or old_auth['continuation_hash'] != prior['continuation_hash']
            or parent['adapter_sha256'] != extension_sha256()
            or prior['adapter_source_sha256'] != extension_sha256()
            or not parent['stop_on_first_failure']):
        raise StoreError('parent stop/scope/source mismatch')
    job = plan['episodes'][EPISODE]
    if (digest(job) != EPISODE or prior['episodes'] != {EPISODE: job}
            or (job['task'], job['scene_seed'], job['candidate_id'], job['liquid_mode'])
            != ('pour_water', 3, CANDIDATE, 'pbf')):
        raise StoreError('only original frozen Pour/3 allowed')
    stop_ref = prior['parent_stop_record']
    stop_path = (ROOT/stop_ref['path']).resolve()
    if not stop_path.is_relative_to(store.root) or stop_path.parent != store.root/'canary_runs':
        raise StoreError('stop record must belong to original store')
    stop = store.read(str(stop_path.relative_to(store.root)))
    if digest(stop) != stop_ref['payload_sha256'] or _file_hash(stop_path) != stop_ref['file_sha256']:
        raise StoreError('parent stop changed')
    if (stop['status'] != 'stopped_canary_failure' or stop['new_calls'] != 2
            or stop['failed_key'] != parent['ordered_episode_keys'][1]
            or parent['ordered_episode_keys'][2] != EPISODE):
        raise StoreError('unexpected parent stop')
    parents = {}
    for key, category in zip(parent['ordered_episode_keys'][:2], ('success', 'task_failure')):
        record = store.read(f'episodes/{key}.json')
        if record['category'] != category or record['canary_hash'] != parent['canary_hash']:
            raise StoreError('parent outcome mismatch')
        auth = store.read('canary_authorizations/'+record['authorization_hash']+'.json')
        if (digest(auth) != record['authorization_hash'] or auth['canary_hash'] != parent['canary_hash']
                or auth['protocol_hash'] != protocol.protocol_hash or auth['plan_hash'] != digest(plan)
                or auth['approved_episode_keys'] != parent['ordered_episode_keys']
                or auth['adapter_sha256'] != extension_sha256()):
            raise StoreError('original authorization mismatch')
        parents[key] = dict(payload_sha256=digest(record), file_sha256=_file_hash(store.path(f'episodes/{key}.json')))
    data = dict(schema_version='single_pour_continuation_v1', protocol_hash=protocol.protocol_hash,
        plan_hash=digest(plan), episode_key=EPISODE, physical_identity=job,
        parent_canary_hash=parent['canary_hash'], parent_stop_record=stop_ref,
        parent_outcomes=parents, prior_continuation_hash=prior['continuation_hash'],
        prior_authorization_identity_hash=old_auth['authorization_identity_hash'],
        history_files={k: str(Path(v).resolve()) for k, v in files.items()},
        history_file_sha256={k: _file_hash(v) for k, v in files.items()},
        extension_source_sha256=source_hash(), parent_extension_sha256=extension_sha256(),
        stop_on_first_failure=True, scope='only_original_pour_seed3',
        execution_authorized=False)
    return dict(data, continuation_hash=digest(data))


def authorization_request(manifest):
    """The final identity and exact statement required from a future caller."""
    identity = {k: manifest[k] for k in ('protocol_hash', 'plan_hash', 'episode_key',
        'continuation_hash', 'extension_source_sha256', 'parent_canary_hash',
        'parent_extension_sha256', 'prior_continuation_hash', 'prior_authorization_identity_hash')}
    identity['parent_stop_payload_sha256'] = manifest['parent_stop_record']['payload_sha256']
    identity['schema_version'] = 'single_pour_authorization_v1'
    identity_hash = digest(identity)
    statement = 'Authorize exactly one original frozen Pour/3 PBF episode; preserve the parent canary stop.\n'+canonical(identity).decode()
    return dict(identity=identity, authorization_identity_hash=identity_hash,
                required_user_authorization_text=statement, approval_status='pending')


@dataclass(frozen=True, slots=True)
class ContinuationAuthorization:
    protocol_hash: str
    plan_hash: str
    episode_key: str
    continuation_hash: str
    extension_source_sha256: str
    authorization_identity_hash: str
    approved_by: str
    user_authorization_text: str


def run_continuation(store, manifest, *, authorization=None, runner=None):
    """Dispatch at most ONE original job, only after explicit bound approval.

    runner injection is for mock tests. An existing result of any category is
    never rerun; a started job without a result always requires reconciliation.
    """
    expected = prepare_continuation(store, history_files=manifest['history_files'])
    if digest(expected) != digest(manifest):
        raise StoreError('continuation manifest/source mismatch')
    request = authorization_request(expected)
    if authorization is None:
        return dict(status='authorization_required', new_calls=0, request=request)
    if not isinstance(authorization, ContinuationAuthorization):
        raise ValueError('structured subset-bound authorization required')
    for field in ('protocol_hash', 'plan_hash', 'episode_key', 'continuation_hash', 'extension_source_sha256'):
        if getattr(authorization, field) != expected[field]:
            raise ValueError('authorization mismatch: '+field)
    if (authorization.authorization_identity_hash != request['authorization_identity_hash']
            or authorization.user_authorization_text != request['required_user_authorization_text']
            or not isinstance(authorization.approved_by, str) or not authorization.approved_by.strip()):
        raise ValueError('complete explicit user authorization required')
    key = expected['episode_key']; job = expected['physical_identity']
    try:
        cached = store.read(f'episodes/{key}.json')
    except FileNotFoundError:
        cached = None
    if cached is not None:
        # frozen_preflight already checked identity/classification/checksum.
        if cached.get('continuation_hash') == expected['continuation_hash']:
            prefix = 'continuations/'+expected['continuation_hash']
            try:
                completion = store.read(prefix+'/completion.json')
                saved_auth = store.read(prefix+'/authorizations/'+cached['authorization_hash']+'.json')
                saved_manifest = store.read(prefix+'/manifest.json')
                dispatch = store.read(prefix+'/dispatch.json')
            except FileNotFoundError:
                return dict(status='already_recorded_audit_incomplete_requires_reconciliation',
                            new_calls=0, episode_ref=f'episodes/{key}.json')
            if (completion['outcome_checksum'] != digest(cached)
                    or completion['raw_outcome_checksum'] != (digest(cached['raw_outcome']) if cached['raw_outcome'] is not None else None)
                    or digest(saved_auth) != cached['authorization_hash']
                    or saved_auth['authorization_identity_hash'] != request['authorization_identity_hash']
                    or saved_auth['user_authorization_text'] != request['required_user_authorization_text']
                    or digest(saved_manifest) != digest(expected)
                    or dispatch['physical_identity'] != job
                    or completion['authorization_hash'] != cached['authorization_hash']):
                raise StoreError('continuation audit chain mismatch')
        return dict(status='already_recorded', new_calls=0, category=cached['category'],
                    episode_ref=f'episodes/{key}.json', outcome_checksum=digest(cached))
    try:
        store.read(f'attempts/{key}.json')
    except FileNotFoundError:
        pass
    else:
        return dict(status='missing_outcome_requires_reconciliation', new_calls=0)
    auth_hash = digest(asdict(authorization))
    prefix = 'continuations/'+expected['continuation_hash']
    store.write_once(prefix+'/manifest.json', expected)
    store.write_once(prefix+'/authorizations/'+auth_hash+'.json', asdict(authorization))
    # Keep the base attempt schema compatible with the frozen EpisodeStore.
    if not store.write_once(f'attempts/{key}.json', dict(episode_key=key, physical_identity=job), exclusive=True):
        return dict(status='missing_outcome_requires_reconciliation', new_calls=0)
    dispatch = dict(episode_key=key, physical_identity=job,
        parent_canary_hash=expected['parent_canary_hash'], continuation_hash=expected['continuation_hash'],
        authorization_hash=auth_hash, authorization_identity_hash=request['authorization_identity_hash'],
        dispatch_timestamp=datetime.now(timezone.utc).isoformat(), attempt_status='started')
    store.write_once(prefix+'/dispatch.json', dispatch, exclusive=True)
    began = perf_counter(); raw = None; rejected = None; error = None; steps = 0; evidence = None; calls = 0
    try:
        import pybullet as p
        from grasp_a.sequence import run_sequence
        original = p.stepSimulation
        def counted(*args, **kwargs):
            nonlocal steps
            steps += 1
            return original(*args, **kwargs)
        _, candidates, _, _ = frozen_preflight(store, expected_protocol=expected['protocol_hash'], expected_plan=expected['plan_hash'])
        if digest(prepare_continuation(store, history_files=expected['history_files'])) != digest(expected):
            raise StoreError('identity changed before dispatch')
        with patch.object(p, 'stepSimulation', side_effect=counted):
            calls += 1
            raw = (run_sequence if runner is None else runner)(job['task'], job['scene_seed'], job['candidate_id'], candidates,
                config=SequenceConfig(**job['sequence_config']), scene_config=SceneRandomization(**job['scene_config']),
                observation_config=None, liquid=job['liquid_mode'], gui=job['gui'],
                media_dir=job['media_dir'], show_landmarks=job['show_landmarks'])
        category = classify_outcome(raw, job)
        required = {'schema_version', 'stages', 'stage_evaluation', 'terminal_checks', 'trajectory', 'elapsed_s', 'final_evidence'}
        if not required <= set(raw) or raw['schema_version'] != 'sequence_episode_v1' or raw.get('liquid_mode') != 'pbf':
            raise StoreError('incomplete/non-PBF outcome')
        canonical(raw)
        evidence = canary_evidence(raw, steps)
        if category == 'success':
            if (not raw.get('liquid_initial_check') or not raw.get('liquid_model')
                    or raw['liquid_model'].get('model') != 'position_based_fluid'
                    or not evidence['final_water'] or evidence['final_water'].get('finite_state') is not True
                    or evidence['pre_episode_step_difference'] != 120):
                raise StoreError('PBF initialization/terminal/step evidence missing')
    except Exception as exc:
        category = 'infrastructure_error'
        error = dict(type=type(exc).__name__, message=str(exc))
        try:
            canonical(raw); rejected = raw
        except (ValueError, TypeError):
            pass
        raw = None
    record = dict(episode_key=key, physical_identity=job, category=category, raw_outcome=raw,
        rejected_raw_outcome=rejected, error=error, wall_s=perf_counter()-began,
        continuation_hash=expected['continuation_hash'], parent_canary_hash=expected['parent_canary_hash'],
        authorization_hash=auth_hash, authorization_identity_hash=request['authorization_identity_hash'],
        canary_evidence=evidence, total_stepSimulation_calls=steps, runner_call_count=calls,
        dispatch_timestamp=dispatch['dispatch_timestamp'])
    store.write_once(f'episodes/{key}.json', record)
    if digest(store.read(f'episodes/{key}.json')) != digest(record):
        raise StoreError('outcome roundtrip mismatch')
    completion = dict(dispatch, attempt_status='finished', category=category,
        infrastructure_failure=category == 'infrastructure_error',
        outcome_ref=f'episodes/{key}.json', outcome_checksum=digest(record),
        raw_outcome_checksum=digest(raw) if raw is not None else None)
    store.write_once(prefix+'/completion.json', completion)
    return dict(status='continuation_recorded', new_calls=calls, **completion)
