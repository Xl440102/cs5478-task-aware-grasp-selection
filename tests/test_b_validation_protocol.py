"""Protocol/metrics/mock-only tests. No physical episode or new selection."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock,patch

from grasp_b.contact_refinement.validation_protocol import (ProtocolDraft,VERSION,plan_design,mock_permit,run_mock_plan,
 paired_statistics,holm,outcome_denominators,MockPermit)
from grasp_b.contact_refinement.evaluation_report import METHODS,StaticDecision
from grasp_b.wrench_v2.episode_store import EpisodeStore,StoreError,digest

ROOT=Path(__file__).resolve().parents[1]

def example():
 # Synthetic contract records, never historical successes or model evidence.
 rows=json.loads((ROOT/'tests/fixtures/part_b_protocol_decisions.json').read_text())
 return [StaticDecision(r).to_dict() for r in rows]

CONFIG=dict(scene_config={'name':'frozen_fixture'},sequence_config={'water_mass_kg':.1},resources={'sha256':'fixture'},
 liquid_mode='pbf',observation_policy='frozen estimated pose',replicate_index=0)


def success(physical):return dict(mock_only=True,physical_identity=physical,success=True)


class ProtocolTests(unittest.TestCase):
 def draft(self):return ProtocolDraft(dict(version=VERSION,physical_execution_enabled=False,methods=METHODS,development_seeds=[5,6,7],final_candidate_seeds=[547800,547801],liquid_mode='pbf'))
 def test_draft_immutable_identity_and_seed_disjointness(self):
  d=self.draft();self.assertEqual(ProtocolDraft.from_json(json.dumps(d.to_dict())).to_dict(),d.to_dict())
  with self.assertRaises(TypeError):d.payload['liquid_mode']='proxy'
  value=d.to_dict();value['liquid_mode']='proxy'
  with self.assertRaises(ValueError):ProtocolDraft(value)
  value=d.to_dict();value.pop('protocol_hash');value['final_candidate_seeds']=[5]
  with self.assertRaises(ValueError):ProtocolDraft(value)
 def test_five_shared_pool_and_prediction_not_physical_identity(self):
  rows=example();p=plan_design(rows,CONFIG,protocol_hash=self.draft().to_dict()['protocol_hash'])
  changed=json.loads(json.dumps(rows))
  for d in changed:
   d.pop('decision_hash');d['provenance']['prediction_diagnostic']='different decision model, same physical job'
  q=plan_design(changed,CONFIG,protocol_hash=p['protocol_hash'])
  self.assertEqual(p['episodes'],q['episodes']);self.assertNotEqual(p['plan_hash'],q['plan_hash'])
  self.assertEqual(len(p['method_links']),5);self.assertLess(len(p['episodes']),5)
 def test_physical_configuration_and_scene_isolation(self):
  rows=example();p=plan_design(rows,CONFIG,protocol_hash='0'*64)
  q=plan_design(rows,dict(CONFIG,replicate_index=1),protocol_hash='0'*64)
  self.assertTrue(set(p['episodes']).isdisjoint(q['episodes']))
  with self.assertRaises(ValueError):plan_design(rows,dict(CONFIG,liquid_mode='proxy'),protocol_hash='0'*64)
 def test_tuple_config_normalized_before_hash_and_store_roundtrip(self):
  config=dict(CONFIG,scene_config={'x_range_m':(-.04,.04)})
  p=plan_design(example(),config,protocol_hash='0'*64)
  self.assertEqual(p,json.loads(json.dumps(p)))
  runner=Mock(side_effect=success)
  with tempfile.TemporaryDirectory() as tmp:
   a=run_mock_plan(p,EpisodeStore(tmp),permit=mock_permit(p),runner=runner)
   b=run_mock_plan(p,EpisodeStore(tmp),permit=mock_permit(p),runner=runner)
   self.assertEqual(b['calls'],0);self.assertEqual(b['reused'],a['calls'])
 def test_incomplete_or_mismatched_method_pool_rejected(self):
  rows=example()
  with self.assertRaises(ValueError):plan_design(rows[:-1],CONFIG,protocol_hash='0'*64)
  rows[-1].pop('decision_hash');rows[-1]['feasible_ids']=rows[-1]['feasible_ids'][:-1]
  with self.assertRaises(ValueError):plan_design(rows,CONFIG,protocol_hash='0'*64)
 def test_no_authorization_no_runner_calls(self):
  p=plan_design(example(),CONFIG,protocol_hash='0'*64);runner=Mock(side_effect=success)
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(StoreError):run_mock_plan(p,EpisodeStore(tmp),runner=runner)
  self.assertEqual(runner.call_count,0)
 def test_wrong_authorization_and_real_runner_rejected(self):
  p=plan_design(example(),CONFIG,protocol_hash='0'*64);runner=Mock(side_effect=success)
  wrong=MockPermit('1'*64,p['plan_hash'],tuple(p['ordered_episode_keys']),'2'*64)
  with tempfile.TemporaryDirectory() as tmp:
   with self.assertRaises(StoreError):run_mock_plan(p,EpisodeStore(tmp),permit=wrong,runner=runner)
   with self.assertRaises(StoreError):run_mock_plan(p,EpisodeStore(tmp),permit=mock_permit(p),runner=success)
  self.assertEqual(runner.call_count,0)
 def test_duplicate_reuse_and_restart_no_reexecution(self):
  p=plan_design(example(),CONFIG,protocol_hash='0'*64);runner=Mock(side_effect=success)
  with tempfile.TemporaryDirectory() as tmp:
   store=EpisodeStore(tmp);a=run_mock_plan(p,store,permit=mock_permit(p),runner=runner);b=run_mock_plan(p,store,permit=mock_permit(p),runner=runner)
   self.assertEqual(a['calls'],len(p['episodes']));self.assertEqual(b['calls'],0);self.assertEqual(b['reused'],len(p['episodes']))
  self.assertEqual(runner.call_count,len(p['episodes']))
 def test_abstention_no_dispatch_invalid_relaxed_not_scored(self):
  rows=example()
  for d in rows:
   if d['method_label']=='M0_random':continue
   d.pop('decision_hash');d['selected_id']=None;d['selected_family']=None;d['selection_status']='no_complete_score';d['near_tie_ids']=[]
   for score in d['scores'].values():score.update(status='geometry_invalid',residual=None,utilization=None)
  p=plan_design(rows,CONFIG,protocol_hash='0'*64);self.assertEqual(sum(l['episode_key'] is None for l in p['method_links']),4)
  runner=Mock(side_effect=success)
  with tempfile.TemporaryDirectory() as tmp:r=run_mock_plan(p,EpisodeStore(tmp),permit=mock_permit(p),runner=runner)
  self.assertEqual(r['calls'],1);self.assertEqual(r['no_selection'],4)
 def test_missing_or_corrupt_outcome_and_dangling_attempt_stop(self):
  p=plan_design(example(),CONFIG,protocol_hash='0'*64);runner=Mock(return_value=None)
  with tempfile.TemporaryDirectory() as tmp:
   store=EpisodeStore(tmp)
   with self.assertRaises(StoreError):run_mock_plan(p,store,permit=mock_permit(p),runner=runner)
   runner.reset_mock()
   with self.assertRaises(StoreError):run_mock_plan(p,store,permit=mock_permit(p),runner=runner)
   self.assertEqual(runner.call_count,0)
  with tempfile.TemporaryDirectory() as tmp:
   store=EpisodeStore(tmp);key=p['ordered_episode_keys'][0];store.path('mock_episodes/'+key+'.json').parent.mkdir();store.path('mock_episodes/'+key+'.json').write_text('{broken')
   with self.assertRaises(StoreError):run_mock_plan(p,store,permit=mock_permit(p),runner=runner)
   self.assertEqual(runner.call_count,0)
 def test_historical_store_refused_unchanged(self):
  p=plan_design(example(),CONFIG,protocol_hash='0'*64);runner=Mock(side_effect=success)
  with tempfile.TemporaryDirectory() as tmp:
   store=EpisodeStore(tmp);store.write_once('protocol.json',{'original':True});before=store.path('protocol.json').read_bytes()
   with self.assertRaises(StoreError):run_mock_plan(p,store,permit=mock_permit(p),runner=runner)
   self.assertEqual(store.path('protocol.json').read_bytes(),before);self.assertFalse(store.path('mock_protocol.json').exists())
  self.assertEqual(runner.call_count,0)
 def test_outcomes_never_enter_selector_and_fixed_ranking(self):
  rows=example();before=digest(rows);p=plan_design(rows,CONFIG,protocol_hash='0'*64)
  with patch('grasp_a.sequence.run_sequence',side_effect=AssertionError('NO EXECUTION')) as execute,patch('pybullet.stepSimulation',side_effect=AssertionError('NO STEP')) as step:
   runner=Mock(side_effect=lambda physical:dict(success(physical),success=False))
   with tempfile.TemporaryDirectory() as tmp:r=run_mock_plan(p,EpisodeStore(tmp),permit=mock_permit(p),runner=runner)
  self.assertEqual(r['calls'],len(p['episodes']));self.assertEqual(digest(rows),before);self.assertEqual(execute.call_count,step.call_count,0)
 def test_near_tie_decision_ids_are_not_rechosen(self):
  rows=example();a=plan_design(rows,CONFIG,protocol_hash='0'*64);b=plan_design(rows,CONFIG,protocol_hash='0'*64)
  self.assertEqual(a,b)
  for d in rows:self.assertEqual(d['selected_id'],d['ranking'][0])


class StatisticalDesignTests(unittest.TestCase):
 def test_discordance_exact_mcnemar_and_effect_ci(self):
  r=paired_statistics([1]*7+[0]*23,[0]*30)
  self.assertAlmostEqual(r['p_exact'],.015625);self.assertEqual(r['discordant_pairs'],7);self.assertAlmostEqual(r['risk_difference'],7/30)
  self.assertLessEqual(r['confidence_interval'][0],r['risk_difference']);self.assertGreaterEqual(r['confidence_interval'][1],r['risk_difference'])
 def test_identical_shared_episode_is_no_discordance(self):
  r=paired_statistics([1,0,1],[1,0,1]);self.assertEqual(r['p_exact'],1.);self.assertEqual(r['discordant_pairs'],0)
 def test_infrastructure_not_silently_zero(self):
  self.assertEqual(paired_statistics([1,None],[0,1])['status'],'incomplete')
  r=outcome_denominators(['success','task_failure','no_selection','missing_outcome','infrastructure_error'])
  self.assertEqual(r['selected'],4);self.assertIsNone(r['end_to_end_success']);self.assertEqual(r['end_to_end_identification_bounds'],[.2,.6])
 def test_no_selection_primary_zero_distinct_physical_failure(self):
  r=outcome_denominators(['success','task_failure','no_selection']);self.assertEqual(r['end_to_end_success'],1/3);self.assertEqual(r['observed_conditional_success'],.5);self.assertEqual(r['valid_executed'],2)
 def test_holm_predeclared_family(self):
  self.assertEqual(holm([.01,.04,.02]),[.03,.04,.04])

if __name__=='__main__':unittest.main()
