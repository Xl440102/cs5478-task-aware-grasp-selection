"""Synthetic history, mock dispatcher and mock physics only."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests import test_b_canary_adapter as base
from grasp_b import continuation_adapter as ca
from grasp_b.canary_adapter import run_canary
from grasp_b.wrench_v2.episode_store import digest, StoreError


class ContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.CanaryTests.setUpClass()

    @contextmanager
    def case(self):
        helper = base.CanaryTests()
        with tempfile.TemporaryDirectory() as tmp:
            store, plan, parent, auth = helper.bundle(tmp)
            with patch('pybullet.stepSimulation', return_value=None):
                stopped = run_canary(store, parent, authorization=auth,
                                     runner=helper.runner(plan, fail_task='handover_transfer'))
            stop_ref = 'canary_runs/'+digest(stopped)+'.json'
            store.write_once(stop_ref, stopped)
            key = parent['ordered_episode_keys'][2]
            prior = dict(protocol_hash=parent['protocol_hash'], plan_hash=parent['plan_hash'],
                parent_canary_hash=parent['canary_hash'], ordered_episode_keys=[key],
                episodes={key: plan['episodes'][key]}, adapter_source_sha256=parent['adapter_sha256'],
                parent_stop_record=dict(path=str(store.path(stop_ref)),
                    payload_sha256=digest(stopped), file_sha256=ca._file_hash(store.path(stop_ref))))
            prior['continuation_hash'] = digest(prior)
            old_auth = dict(protocol_hash=parent['protocol_hash'], plan_hash=parent['plan_hash'],
                approved_episode_keys=[key], continuation_hash=prior['continuation_hash'])
            old_auth['authorization_identity_hash'] = digest(old_auth)
            files = {}
            for name, value in [('prior_plan', prior), ('prior_authorization', old_auth), ('parent', parent)]:
                path = Path(tmp)/(name+'.json'); path.write_text(json.dumps(value)); files[name] = str(path)
            anchors = dict(protocol=parent['protocol_hash'], plan=parent['plan_hash'],
                parent=parent['canary_hash'], prior_plan=prior['continuation_hash'],
                prior_authorization=old_auth['authorization_identity_hash'])
            with patch.multiple(ca, ANCHORS=anchors, EPISODE=key,
                                CANDIDATE=plan['episodes'][key]['candidate_id']):
                manifest = ca.prepare_continuation(store, history_files=files)
                req = ca.authorization_request(manifest)
                approval = ca.ContinuationAuthorization(**{k: manifest[k] for k in
                    ('protocol_hash', 'plan_hash', 'episode_key', 'continuation_hash', 'extension_source_sha256')},
                    authorization_identity_hash=req['authorization_identity_hash'],
                    approved_by='MOCK TEST ONLY', user_authorization_text=req['required_user_authorization_text'])
                yield store, plan, manifest, approval, helper

    def test_no_authorization_no_execution_or_write(self):
        with self.case() as (s, _, m, _, _):
            before = set(s.root.rglob('*.json')); runner = Mock()
            self.assertEqual(ca.run_continuation(s, m, runner=runner)['status'], 'authorization_required')
            runner.assert_not_called(); self.assertEqual(before, set(s.root.rglob('*.json')))

    def test_wrong_protocol_rejected(self):
        self.bad_approval(protocol_hash='wrong')

    def test_wrong_continuation_rejected(self):
        self.bad_approval(continuation_hash='wrong')

    def test_wrong_episode_rejected(self):
        self.bad_approval(episode_key='wrong')

    def test_wrong_plan_rejected(self):
        self.bad_approval(plan_hash='wrong')

    def test_wrong_source_rejected(self):
        self.bad_approval(extension_source_sha256='wrong')

    def test_wrong_authorization_identity_rejected(self):
        self.bad_approval(authorization_identity_hash='wrong')

    def test_generic_text_rejected(self):
        self.bad_approval(user_authorization_text='YES EXECUTE')

    def bad_approval(self, **changes):
        with self.case() as (s, _, m, a, _):
            runner = Mock()
            with self.assertRaises(ValueError):
                ca.run_continuation(s, m, authorization=replace(a, **changes), runner=runner)
            runner.assert_not_called()
            self.assertFalse(s.path('attempts/'+m['episode_key']+'.json').exists())

    def test_boolean_and_arbitrary_string_rejected(self):
        with self.case() as (s, _, m, _, _):
            runner = Mock()
            for a in (True, 'APPROVED'):
                with self.assertRaises(ValueError): ca.run_continuation(s, m, authorization=a, runner=runner)
            runner.assert_not_called()

    def test_cannot_dispatch_other_episode(self):
        with self.case() as (s, plan, m, a, _):
            bad = deepcopy(m); bad['episode_key'] = next(k for k in plan['episodes'] if k != m['episode_key'])
            runner = Mock()
            with self.assertRaises(StoreError): ca.run_continuation(s, bad, authorization=a, runner=runner)
            runner.assert_not_called()

    def test_source_change_fails_closed(self):
        with self.case() as (s, _, m, a, _):
            runner = Mock()
            with patch.object(ca, 'source_hash', return_value='0'*64), self.assertRaises(StoreError):
                ca.run_continuation(s, m, authorization=a, runner=runner)
            runner.assert_not_called()

    def cached(self, success):
        with self.case() as (s, plan, m, a, helper):
            key = m['episode_key']; job = plan['episodes'][key]
            with patch('pybullet.stepSimulation', return_value=None):
                raw = helper.runner(plan, fail_task=None if success else 'pour_water')(
                    job['task'], 3, job['candidate_id'], [], liquid='pbf')
            s.write_once('episodes/'+key+'.json', dict(episode_key=key, physical_identity=job,
                raw_outcome=raw, category='success' if success else 'task_failure'))
            runner = Mock(); result = ca.run_continuation(s, m, authorization=a, runner=runner)
            self.assertEqual(result['status'], 'already_recorded'); self.assertEqual(result['new_calls'], 0)
            self.assertEqual(result['episode_ref'], 'episodes/'+key+'.json'); runner.assert_not_called()

    def test_existing_success_not_reexecuted(self): self.cached(True)

    def test_existing_failure_not_reexecuted(self): self.cached(False)

    def test_unfinished_attempt_not_retried(self):
        with self.case() as (s, _, m, a, _):
            s.write_once('attempts/'+m['episode_key']+'.json', dict(episode_key=m['episode_key'], physical_identity=m['physical_identity']))
            runner = Mock()
            self.assertEqual(ca.run_continuation(s, m, authorization=a, runner=runner)['status'], 'missing_outcome_requires_reconciliation')
            runner.assert_not_called()

    def test_correct_mock_at_most_once_and_independent_audit_chain(self):
        with self.case() as (s, plan, m, a, helper):
            protected = {str(p): p.read_bytes() for p in s.root.rglob('*.json')}
            runner = helper.runner(plan)
            with patch('pybullet.stepSimulation', return_value=None):
                first = ca.run_continuation(s, m, authorization=a, runner=runner)
                again = ca.run_continuation(s, m, authorization=a, runner=runner)
            self.assertEqual(runner.call_count, 1); self.assertEqual(first['new_calls'], 1)
            self.assertEqual(again['new_calls'], 0)
            self.assertEqual(runner.call_args.args[:3], ('pour_water', 3, m['physical_identity']['candidate_id']))
            self.assertEqual(runner.call_args.kwargs['liquid'], 'pbf')
            for path, data in protected.items(): self.assertEqual(Path(path).read_bytes(), data)
            record = s.read('episodes/'+m['episode_key']+'.json')
            prefix = 'continuations/'+m['continuation_hash']
            completion = s.read(prefix+'/completion.json')
            authorization = s.read(prefix+'/authorizations/'+record['authorization_hash']+'.json')
            self.assertEqual(authorization['user_authorization_text'], a.user_authorization_text)
            self.assertEqual(completion['outcome_checksum'], digest(record))
            self.assertEqual(completion['parent_canary_hash'], m['parent_canary_hash'])
            self.assertEqual(completion['attempt_status'], 'finished')
            self.assertEqual(record['total_stepSimulation_calls'], 123)

    def test_infrastructure_failure_persisted_never_retried(self):
        with self.case() as (s, _, m, a, _):
            runner = Mock(side_effect=RuntimeError('MOCK infrastructure failure'))
            result = ca.run_continuation(s, m, authorization=a, runner=runner)
            self.assertTrue(result['infrastructure_failure'])
            self.assertEqual(ca.run_continuation(s, m, authorization=a, runner=runner)['status'], 'already_recorded')
            self.assertEqual(runner.call_count, 1)

    def test_corrupt_outcome_cannot_be_reused(self):
        with self.case() as (s, _, m, a, _):
            path = s.path('episodes/'+m['episode_key']+'.json'); path.write_text('{}')
            runner = Mock()
            with self.assertRaises(StoreError): ca.run_continuation(s, m, authorization=a, runner=runner)
            runner.assert_not_called()

    def test_parent_history_tampering_rejected(self):
        with self.case() as (s, _, m, a, _):
            path = Path(m['history_files']['parent']); obj = json.loads(path.read_text())
            obj['stop_on_first_failure'] = False; path.write_text(json.dumps(obj))
            runner = Mock()
            with self.assertRaises(StoreError): ca.run_continuation(s, m, authorization=a, runner=runner)
            runner.assert_not_called()

    def test_missing_completion_audit_does_not_reexecute_valid_outcome(self):
        with self.case() as (s, plan, m, a, helper):
            runner = helper.runner(plan)
            with patch('pybullet.stepSimulation', return_value=None):
                ca.run_continuation(s, m, authorization=a, runner=runner)
            s.path('continuations/'+m['continuation_hash']+'/completion.json').unlink()
            result = ca.run_continuation(s, m, authorization=a, runner=runner)
            self.assertEqual(result['status'], 'already_recorded_audit_incomplete_requires_reconciliation')
            self.assertEqual(runner.call_count, 1)

    def test_corrupt_continuation_audit_rejected_without_execution(self):
        with self.case() as (s, plan, m, a, helper):
            runner = helper.runner(plan)
            with patch('pybullet.stepSimulation', return_value=None):
                ca.run_continuation(s, m, authorization=a, runner=runner)
            s.path('continuations/'+m['continuation_hash']+'/completion.json').write_text('{}')
            with self.assertRaises(StoreError): ca.run_continuation(s, m, authorization=a, runner=runner)
            self.assertEqual(runner.call_count, 1)

    def test_no_selector_or_generator_dependency(self):
        source = Path(ca.__file__).read_text()
        self.assertNotIn('random_ranking', source)
        self.assertNotIn('generate_candidates', source)


if __name__ == '__main__':
    unittest.main()
