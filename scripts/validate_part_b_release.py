"""Run the reviewed source-release suite with real HiGHS and static geometry.

Historical frozen-study tests skip explicitly when their local bundles are absent.
Model clients are mocked by their tests; network and physical stepping are guarded.
"""
import ast
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import argparse
parser=argparse.ArgumentParser(description='Portable Part B and offline A/C contract regressions; no rollout or network.')
parser.add_argument('--output',type=Path,required=True,help='JSON result path, preferably under /tmp; a sibling .log is written')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1];out=args.output.resolve();out.parent.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(root));os.chdir(root)
start=time.perf_counter()
syntax=list(root.glob('grasp_a/*.py'))+list(root.glob('grasp_b/**/*.py'))+list(root.glob('grasp_c/*.py'))+list(root.glob('tests/*.py'))
for p in syntax:ast.parse(p.read_text(),filename=str(p))
imports=[p for p in root.glob('grasp_b/contact_refinement/*.py') if p.name!='__init__.py']+list(root.glob('grasp_b/wrench_v2/*.py'))
for p in imports:importlib.import_module('.'.join(p.relative_to(root).with_suffix('').parts))
# Explicitly reviewed offline modules; no discovery of physics benchmarks.
modules = ['tests.test_annotations',
 'tests.test_b_batch_adapter',
 'tests.test_b_bullet_alignment',
 'tests.test_b_canary_adapter',
 'tests.test_b_collision_regions',
 'tests.test_b_contact_geometry_validation',
 'tests.test_b_contact_refinement_diagnostics',
 'tests.test_b_contact_regions',
 'tests.test_b_continuation_adapter',
 'tests.test_b_features',
 'tests.test_b_generation',
 'tests.test_b_m0_random',
 'tests.test_b_m1_static',
 'tests.test_b_m2_common',
 'tests.test_b_m2_handover',
 'tests.test_b_m2_pouring',
 'tests.test_b_m2_transport',
 'tests.test_b_models',
 'tests.test_b_motion',
 'tests.test_b_numerical_oracles',
 'tests.test_b_phase1',
 'tests.test_b_phase2',
 'tests.test_b_phase2_authorization',
 'tests.test_b_regression_identity',
 'tests.test_b_scoring',
 'tests.test_b_task_relaxed_ranking',
 'tests.test_b_task_relaxed_solver',
 'tests.test_b_validation_protocol',
 'tests.test_b_wrench_adapter',
 'tests.test_b_wrench_constraints',
 'tests.test_b_wrench_contacts',
 'tests.test_b_wrench_diagnostics',
 'tests.test_b_wrench_dynamics',
 'tests.test_b_wrench_evaluation',
 'tests.test_b_wrench_geometry',
 'tests.test_b_wrench_gws',
 'tests.test_b_wrench_m1',
 'tests.test_b_wrench_m1_validation',
 'tests.test_b_wrench_m2',
 'tests.test_b_wrench_m2_integration',
 'tests.test_b_wrench_motion_prediction',
 'tests.test_b_wrench_performance',
 'tests.test_b_wrench_pilot',
 'tests.test_b_wrench_ranking',
 'tests.test_b_wrench_solver',
 'tests.test_b_wrench_task_validation',
 'tests.test_b_wrench_tasks',
 'tests.test_b_wrench_wet_integration',
 'tests.test_b_wrench_wet_payload',
 'tests.test_c_api_workflow',
 'tests.test_c_b_integration',
 'tests.test_c_evaluation',
 'tests.test_c_gemini',
 'tests.test_c_metrics_alignment',
 'tests.test_c_providers',
 'tests.test_c_rules',
 'tests.test_c_selectors',
 'tests.test_c_sequence_metrics',
 'tests.test_c_sequence_runner',
 'tests.test_c_sequence_trial',
 'tests.test_c_table_evaluation',
 'tests.test_contracts']
counts={'physical_step':0,'sequence_episode':0,'prep_episode':0,'network':0}
def forbid(kind):
 def call(*a,**kw):counts[kind]+=1;raise AssertionError('release validation forbids '+kind)
 return call
with ExitStack() as stack:
 for target,kind in [('pybullet.stepSimulation','physical_step'),('grasp_a.sequence.run_sequence','sequence_episode'),('grasp_a.simulation.run_episode','prep_episode'),('socket.socket.connect','network')]:
  stack.enter_context(patch(target,side_effect=forbid(kind)))
 suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
 def cases(value):
  for item in value:
   if isinstance(item,unittest.TestSuite):yield from cases(item)
   else:yield item
 excluded={
  'tests.test_annotations.AnnotationTests.test_both_video_modes_coexist_and_physics_is_identical':'real video/physics replay outside source-integration scope',
  'tests.test_b_features.AdapterTests.test_reviewed_wrapper_or_explicit_unavailable':'real preparation episode outside source-integration scope',
  'tests.test_c_sequence_trial.SequenceTrialIntegrationTests.test_real_transport_uses_sequence_track_and_preserves_physical_status':'real sequence episode outside source-integration scope',
 }
 all_cases=list(cases(suite));deferred=[{'id':t.id(),'reason':excluded[t.id()]} for t in all_cases if t.id() in excluded]
 suite=unittest.TestSuite(t for t in all_cases if t.id() not in excluded)
 with (out.with_suffix('.log')).open('w') as log:
  result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
value={'root':str(root),'modules':modules,'syntax_files':len(syntax),'imported_modules':len(imports),'tests':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),'failed':len(result.failures),'errors':len(result.errors),'skipped':[(str(t),reason) for t,reason in result.skipped],'deferred':deferred,'guards':counts,'seconds':time.perf_counter()-start,'failures':[(str(t),detail) for t,detail in result.failures],'error_details':[(str(t),detail) for t,detail in result.errors]}
out.write_text(json.dumps(value,indent=2));print(json.dumps({k:v for k,v in value.items() if k not in ['modules','failures','error_details','skipped']},indent=2));print('SKIPS',len(result.skipped));print('ERRORS',[(x,y[-1600:]) for x,y in result.errors]);print('FAILURES',[(x,y[-1600:]) for x,y in result.failures]);sys.exit(not result.wasSuccessful())
